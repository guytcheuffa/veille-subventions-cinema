"""Extraction structurée : texte brut (markdown) -> ExtractionResult.

Supporte deux providers, sélectionnables via la variable d'environnement
PROVIDER ("anthropic" par défaut, ou "qwen") — conforme au cahier des
charges qui mentionne "type GPT-4o-mini ou Claude Haiku". Les deux utilisent
un tool call forcé sur le schéma Pydantic pour garantir une sortie JSON
valide (pas de parsing fragile de texte libre).

Anthropic : SDK `anthropic`, tool_choice={"type": "tool", ...}
Qwen (QwenCloud / DashScope) : SDK `openai` pointé sur leur endpoint
compatible OpenAI, tool_choice={"type": "function", ...}
"""

from __future__ import annotations

import datetime
import json
import os

from pydantic import ValidationError

from prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from schema import Commission, ExtractionResult

# ---------------------------------------------------------------------------
# Configuration provider
# ---------------------------------------------------------------------------

PROVIDER = os.environ.get("PROVIDER", "anthropic").lower()  # "anthropic" | "qwen"

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
QWEN_MODEL = "qwen3.8-flash"  # le plus économique, cf. docs.qwencloud.com/pricing
QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

# Tarifs indicatifs (pour l'estimation en mode DRY_RUN uniquement — vérifier
# les prix à jour sur console.anthropic.com / qwencloud.com/pricing/api).
PRIX_PAR_PROVIDER = {
    # (prix input $/Mtok, prix output $/Mtok)
    "anthropic": (1.0, 5.0),
    "qwen": (0.14, 0.42),
}
OUTPUT_ESTIME_TOKENS = 800  # taille moyenne d'un ExtractionResult en JSON

# Active le mode simulation : aucun appel réseau, juste la construction du
# prompt + une estimation de coût. Mettre à "false" (ou ne pas définir) pour
# un vrai appel API.
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() in ("1", "true", "yes")

# Le schéma JSON est dérivé directement du modèle Pydantic pour ne jamais
# désynchroniser le prompt et la validation.
TOOL_NAME = "extraction_result"
TOOL_DESCRIPTION = "Enregistre le résultat structuré de l'extraction."
TOOL_SCHEMA = ExtractionResult.model_json_schema()


def _get_anthropic_client():
    import anthropic

    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


def _get_qwen_client():
    from openai import OpenAI

    api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get(
        "QWENCLOUD_API_KEY"
    )
    return OpenAI(api_key=api_key, base_url=QWEN_BASE_URL)


def _estimer_tokens(texte: str) -> int:
    """Estimation grossière (~4 caractères/token) — suffisant pour un ordre
    de grandeur de coût, pas pour une facturation précise."""
    return max(1, len(texte) // 4)


def _dry_run_result(user_prompt: str, url: str, region: str) -> ExtractionResult:
    """Ne fait AUCUN appel réseau. Affiche ce qui serait envoyé et une
    estimation de coût pour le provider actif, puis retourne un
    ExtractionResult vide bidon."""
    prix_in, prix_out = PRIX_PAR_PROVIDER.get(PROVIDER, PRIX_PAR_PROVIDER["anthropic"])
    tokens_in = _estimer_tokens(SYSTEM_PROMPT + user_prompt)
    cout_estime = (
        tokens_in * prix_in + OUTPUT_ESTIME_TOKENS * prix_out
    ) / 1_000_000

    print(f"  [DRY_RUN][{PROVIDER}] {region} — {url}")
    print(f"  [DRY_RUN] ~{tokens_in} tokens en entrée (system + user prompt)")
    print(f"  [DRY_RUN] coût estimé pour cet appel : ${cout_estime:.5f}")
    print("  [DRY_RUN] aucun appel réseau effectué — résultat factice retourné")

    return ExtractionResult(
        commissions=[], aucune_date_trouvee=False, lien_pdf_calendrier=None
    )


def _extract_anthropic(user_prompt: str) -> dict:
    client = _get_anthropic_client()
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        tools=[
            {
                "name": TOOL_NAME,
                "description": TOOL_DESCRIPTION,
                "input_schema": TOOL_SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": user_prompt}],
    )
    tool_use_block = next(
        block for block in response.content if block.type == "tool_use"
    )
    return tool_use_block.input


def _extract_qwen(user_prompt: str) -> dict:
    client = _get_qwen_client()
    response = client.chat.completions.create(
        model=QWEN_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": TOOL_NAME,
                    "description": TOOL_DESCRIPTION,
                    "parameters": TOOL_SCHEMA,
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
        # Le mode "thinking" de Qwen est incompatible avec un tool_choice
        # forcé (erreur "does not support being set to required or object
        # in thinking mode") — on le désactive explicitement. Sur l'API
        # QwenCloud/DashScope, ce paramètre se passe à la racine d'extra_body
        # (pas nested dans chat_template_kwargs, contrairement à un serveur
        # vLLM/SGLang auto-hébergé).
        extra_body={"enable_thinking": False},
    )
    tool_call = response.choices[0].message.tool_calls[0]
    return json.loads(tool_call.function.arguments)


_TOP_LEVEL_FIELDS = set(ExtractionResult.model_fields.keys())
_COMMISSION_FIELDS = set(Commission.model_fields.keys())
_TOP_LEVEL_ONLY_FIELDS = ("aucune_date_trouvee", "lien_pdf_calendrier")


def _reparer_structure(raw_output: dict, today: str, url: str, region: str) -> dict:
    """Filet de sécurité : certains modèles (observé sur Qwen Flash) placent
    parfois 'aucune_date_trouvee' / 'lien_pdf_calendrier' à l'intérieur d'un
    élément de 'commissions' au lieu du niveau racine, ou renvoient la chaîne
    "None" au lieu du JSON null. On corrige ces deux écarts structurels
    connus avant validation, sans toucher au reste du contenu extrait.

    'url' et 'region' sont connus avec certitude côté code (ce sont les
    paramètres mêmes envoyés au LLM) — on les impose donc systématiquement
    sur chaque commission plutôt que de faire confiance à ce que le modèle
    a recopié, ce qui règle à la fois le cas où 'url_source' est totalement
    omis (observé sur Occitanie) et la variabilité de formulation de
    'region' d'un run à l'autre (ex: 'National' vs 'National (CNC)'), qui
    fragiliserait sinon la clé de dédup dans state.py.

    Ne remplace pas le prompt (règle 8 de SYSTEM_PROMPT) qui reste la
    première ligne de défense — ceci est le rattrapage pour les cas où le
    modèle ne la respecte pas."""

    if not isinstance(raw_output, dict):
        return raw_output

    reparee = dict(raw_output)

    # 1. Convertir la chaîne "None" en JSON null, récursivement mais en
    #    surface (suffisant pour les champs optionnels concernés ici).
    def _nettoyer_none(obj):
        if isinstance(obj, dict):
            return {k: _nettoyer_none(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_nettoyer_none(v) for v in obj]
        return None if obj == "None" else obj

    reparee = _nettoyer_none(reparee)

    # 2. Fusionner les commissions scindées en deux entrées consécutives dans
    #    la liste — observé sur Qwen Flash sur des pages denses (CNC) : une
    #    entrée ne contient QUE 'etapes' (et éventuellement
    #    actions_prealables/montants), l'entrée voisine contient le reste des
    #    champs obligatoires mais PAS 'etapes'. On fusionne le fragment dans
    #    son voisin (avant ou après) avant toute autre validation.
    commissions = reparee.get("commissions")
    if isinstance(commissions, list):
        _CORE_COMMISSION_FIELDS = {
            "region", "fonds", "organisme", "typologie_oeuvre",
            "etape_filiere", "url_source",
        }
        _FRAGMENT_ONLY_FIELDS = {
            "etapes", "actions_prealables", "conditions_eliminatoires",
            "montant_min_eur", "montant_max_eur", "lien_formulaire",
            "session_label",
        }

        def _est_fragment(d):
            return (
                isinstance(d, dict)
                and not (d.keys() & _CORE_COMMISSION_FIELDS)
                and (d.keys() <= _FRAGMENT_ONLY_FIELDS)
            )

        fusionnees = []
        i = 0
        while i < len(commissions):
            item = commissions[i]
            if _est_fragment(item):
                voisin = None
                # Cherche le voisin le plus proche (après, puis avant) qui a
                # les champs requis mais pas déjà 'etapes' rempli.
                for j in (i + 1, i - 1):
                    if 0 <= j < len(commissions) and isinstance(commissions[j], dict):
                        candidat = commissions[j]
                        if (candidat.keys() & _CORE_COMMISSION_FIELDS) and not candidat.get("etapes"):
                            voisin = candidat
                            break
                if voisin is not None:
                    for k, v in item.items():
                        if not voisin.get(k):
                            voisin[k] = v
                    # Le voisin sera ajouté à son propre tour d'itération ;
                    # on saute juste le fragment ici.
                    i += 1
                    continue
            fusionnees.append(item)
            i += 1
        reparee["commissions"] = fusionnees
        commissions = fusionnees

    # 3. Remonter les champs top-level égarés dans le premier élément de
    #    'commissions' qui les contient.
    if isinstance(commissions, list):
        for champ in _TOP_LEVEL_ONLY_FIELDS:
            if champ in reparee:
                continue  # déjà présent au bon endroit
            for commission in commissions:
                if isinstance(commission, dict) and champ in commission:
                    reparee[champ] = commission.pop(champ)
                    break

    # 4. Cas inverse : un champ propre à Commission (ex: 'date_extraction') a
    #    été placé au niveau racine au lieu de dans chaque élément de
    #    'commissions' — observé sur Qwen Flash. On le redescend dans chaque
    #    commission qui ne l'a pas déjà.
    if isinstance(commissions, list):
        for champ in list(reparee.keys()):
            if champ in _TOP_LEVEL_FIELDS:
                continue
            if champ in _COMMISSION_FIELDS:
                valeur = reparee.pop(champ)
                for commission in commissions:
                    if isinstance(commission, dict) and champ not in commission:
                        commission[champ] = valeur

    # 5. 'url_source' et 'region' sont connus avec certitude côté code : on
    #    les impose systématiquement (écrase toute valeur du modèle) plutôt
    #    que de dépendre du LLM pour les recopier fidèlement.
    if isinstance(commissions, list):
        for commission in commissions:
            if isinstance(commission, dict):
                commission["url_source"] = url
                commission["region"] = region

    # 6. Filtre les entrées 'etapes' sans 'date_fin' exploitable (null ou
    #    absente) : le schéma exige une chaîne pour ce champ, et une étape
    #    sans date n'a de toute façon aucune valeur informative — observé
    #    sur Normandie où le modèle a créé des entrées placeholder au lieu
    #    de laisser 'etapes' vide malgré aucune_date_trouvee=true.
    if isinstance(commissions, list):
        for commission in commissions:
            if isinstance(commission, dict) and isinstance(commission.get("etapes"), list):
                commission["etapes"] = [
                    e for e in commission["etapes"]
                    if isinstance(e, dict) and e.get("date_fin")
                ]

    # 7. Champ 'date_extraction' totalement absent (ni au top-level, ni dans
    #    aucune commission) : on comble avec la date du jour connue côté
    #    code plutôt que de dépendre du modèle pour cette valeur mécanique.
    if isinstance(commissions, list):
        for commission in commissions:
            if isinstance(commission, dict):
                commission.setdefault("date_extraction", today)

    # 8. Valeur par défaut raisonnable si toujours absent après réparation
    #    (évite un crash pour un champ que le modèle aurait simplement omis).
    reparee.setdefault("aucune_date_trouvee", False)
    reparee.setdefault("lien_pdf_calendrier", None)

    return reparee


def extract(content: str, url: str, region: str) -> ExtractionResult:
    """Envoie le contenu markdown d'une page au LLM (provider sélectionné via
    la variable d'environnement PROVIDER) et retourne un ExtractionResult
    validé. Lève ValidationError si la sortie du modèle ne respecte pas le
    schéma (ce qui ne devrait arriver que rarement grâce au tool call forcé).

    Si DRY_RUN vaut "true", aucun appel réseau n'est effectué : le prompt
    est construit et son coût estimé, mais le résultat retourné est factice
    (utile pour tester la plomberie du pipeline sans dépenser un centime)."""

    today = datetime.date.today().isoformat()
    user_prompt = USER_PROMPT_TEMPLATE.format(
        url=url, region=region, today=today, content=content
    )

    if DRY_RUN:
        return _dry_run_result(user_prompt, url, region)

    if PROVIDER == "qwen":
        raw_output = _extract_qwen(user_prompt)
    elif PROVIDER == "anthropic":
        raw_output = _extract_anthropic(user_prompt)
    else:
        raise ValueError(
            f"PROVIDER inconnu : '{PROVIDER}'. Valeurs acceptées : 'anthropic', 'qwen'."
        )

    try:
        return ExtractionResult.model_validate(_reparer_structure(raw_output, today, url, region))
    except ValidationError:
        # On affiche le JSON brut (avant réparation) pour pouvoir diagnostiquer
        # un écart de schéma que la réparation automatique n'aurait pas couvert
        print(json.dumps(raw_output, indent=2, ensure_ascii=False))
        raise
