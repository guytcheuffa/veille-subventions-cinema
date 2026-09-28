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

import ast
import datetime
import json
import os

from pydantic import ValidationError

from budget import BudgetExceededError, enregistrer_depense, verifier_budget
from prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from schema import Commission, ExtractionResult
from verify import filtrer_dates_non_confirmees, normaliser_annees, recuperer_dates_du_libelle

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


def _extract_anthropic(user_prompt: str) -> tuple[dict, float]:
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
    prix_in, prix_out = PRIX_PAR_PROVIDER["anthropic"]
    cout_reel = (
        response.usage.input_tokens * prix_in
        + response.usage.output_tokens * prix_out
    ) / 1_000_000
    return tool_use_block.input, cout_reel


class SortieIllisible(Exception):
    """Le modèle a renvoyé des arguments d'outil qu'aucun décodage ne sait lire."""


def _decoder_json_tolerant(texte: str):
    """Décode du JSON un peu abîmé, cas fréquent avec Qwen : caractères de
    contrôle dans les chaînes (strict=False), ou syntaxe Python (None, True,
    apostrophes) que ast.literal_eval lit sans rien exécuter. Retourne None
    si rien ne fonctionne."""
    try:
        return json.loads(texte, strict=False)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    try:
        return ast.literal_eval(texte.strip())
    except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError, AttributeError):
        return None


def _extract_qwen(user_prompt: str) -> tuple[dict, float]:
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
    prix_in, prix_out = PRIX_PAR_PROVIDER["qwen"]
    cout_reel = (
        response.usage.prompt_tokens * prix_in
        + response.usage.completion_tokens * prix_out
    ) / 1_000_000
    decode = _decoder_json_tolerant(tool_call.function.arguments)
    if not isinstance(decode, dict):
        # L'appel a déjà coûté de l'argent : on l'enregistre avant de signaler
        # l'échec (sinon le garde-fou budgétaire sous-estimerait la dépense).
        enregistrer_depense(cout_reel)
        raise SortieIllisible(f"arguments de l'outil illisibles : {tool_call.function.arguments[:60]!r}")
    return decode, cout_reel


_TOP_LEVEL_FIELDS = set(ExtractionResult.model_fields.keys())
_COMMISSION_FIELDS = set(Commission.model_fields.keys())
_TOP_LEVEL_ONLY_FIELDS = ("aucune_date_trouvee", "lien_pdf_calendrier")


def _combler_textes_manquants(commissions) -> None:
    """Remplace par une valeur neutre les champs texte obligatoires à null."""
    if not isinstance(commissions, list):
        return
    for c in commissions:
        if not isinstance(c, dict):
            continue
        for champ in ("fonds", "organisme", "typologie_oeuvre"):
            if c.get(champ) is None:
                c[champ] = "non précisé"
        if c.get("etape_filiere") is None:
            c["etape_filiere"] = "autre"
        for etape in c.get("etapes") or []:
            if isinstance(etape, dict) and etape.get("label") is None:
                etape["label"] = "Échéance"
        for action in c.get("actions_prealables") or []:
            if not isinstance(action, dict):
                continue
            for champ in ("type_action", "condition_declenchement", "consequence_si_non_respect"):
                if action.get(champ) is None:
                    action[champ] = "non précisé"
            fenetre = action.get("fenetre")
            if isinstance(fenetre, dict) and fenetre.get("label") is None:
                fenetre["label"] = "Échéance"


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

    # 0. Cas observé sur Qwen : le champ 'commissions' contient parfois un
    #    ExtractionResult ENTIER imbriqué au lieu d'une vraie liste de
    #    Commission — sous deux formes rencontrées en pratique :
    #      (a) comme une CHAÎNE contenant du JSON, ex:
    #          {"commissions": "[{\"commissions\": [...], \"aucune_date_
    #          trouvee\": false, ...}]"}
    #      (b) comme un OBJET JSON imbriqué directement (sans passer par une
    #          chaîne), ex: {"commissions": [{"aucune_date_trouvee": false,
    #          "commissions": [...], "lien_pdf_calendrier": null}]}
    #    On détecte et déballe les deux formes avant toute autre réparation.
    def _est_extraction_result_imbriquee(d):
        return isinstance(d, dict) and "commissions" in d and (
            "aucune_date_trouvee" in d or "lien_pdf_calendrier" in d
        )

    commissions_val = reparee.get("commissions")

    if isinstance(commissions_val, str):
        decode = _decoder_json_tolerant(commissions_val)

        if isinstance(decode, list) and len(decode) == 1 and _est_extraction_result_imbriquee(decode[0]):
            # La chaîne contenait tout un ExtractionResult imbriqué.
            reparee = dict(decode[0])
        elif isinstance(decode, dict) and "commissions" in decode:
            reparee = dict(decode)
        elif isinstance(decode, list):
            # La chaîne contenait directement la liste de commissions.
            reparee["commissions"] = decode
        # Sinon : on laisse tel quel (échouera à la validation comme avant,
        # mais sans crash, et le JSON brut reste affiché pour diagnostic).

    elif isinstance(commissions_val, list) and len(commissions_val) == 1 and _est_extraction_result_imbriquee(commissions_val[0]):
        # Même imbrication, mais déjà sous forme d'objet JSON natif (pas de
        # chaîne à décoder) — observé sur Paris et Pays de la Loire.
        reparee = dict(commissions_val[0])

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

    # 6bis. Textes obligatoires renvoyés à null par le modèle (ex: Ciclic,
    #    'consequence_si_non_respect': null) : le schéma exige une chaîne, on
    #    met une valeur neutre plutôt que de perdre toute la source.
    _combler_textes_manquants(commissions)

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
    (utile pour tester la plomberie du pipeline sans dépenser un centime).

    GARDE-FOU BUDGET : avant tout appel réel, vérifie que le coût ESTIMÉ de
    l'appel ne ferait pas dépasser le plafond mensuel (BUDGET_LIMIT_USD,
    2$ par défaut — cf. budget.py). Si c'est le cas, lève
    BudgetExceededError SANS envoyer l'appel. Après un appel réussi, le
    coût RÉEL (basé sur les tokens effectivement consommés) est enregistré
    dans data/budget.json. Ce garde-fou est indépendant du provider — utile
    en particulier pour Qwen, qui facture en post-payé (contrairement à
    Anthropic, où le solde prépayé constitue déjà un plafond dur)."""

    today = datetime.date.today().isoformat()
    user_prompt = USER_PROMPT_TEMPLATE.format(
        url=url, region=region, today=today, content=content
    )

    if DRY_RUN:
        return _dry_run_result(user_prompt, url, region)

    raw_output, reparee, dates_retirees = _passe_extraction(
        user_prompt, content, today, url, region
    )

    deuxieme_passe_faite = bool(dates_retirees)
    if dates_retirees:
        _log_dates_retirees(dates_retirees, "1re passe")

        # DEUXIÈME CHANCE : le filtre sait retirer une date inventée mais pas
        # retrouver la vraie (ex: Normandie Session 3 perdait sa plénière du
        # 06/07/27). On relance UNE fois, en disant au modèle quelles dates
        # n'existent pas dans le texte — uniquement pour les sources où le
        # filtre a réellement retiré quelque chose, donc un appel de plus de
        # temps en temps, pas un run doublé.
        try:
            prompt_correctif = user_prompt + _message_correctif(dates_retirees)
            raw2, reparee2, retirees2 = _passe_extraction(
                prompt_correctif, content, today, url, region
            )
            try:
                ExtractionResult.model_validate(reparee2)
                valide2 = True
            except ValidationError:
                valide2 = False
            if not valide2:
                print("  [verify] 2e passe inutilisable (structure invalide) — résultat de la 1re passe conservé.")
            elif _score(reparee2, retirees2) < _score(reparee, dates_retirees):
                print(
                    f"  [verify] 2e passe meilleure : {len(retirees2)} date(s) "
                    f"retirée(s) au lieu de {len(dates_retirees)}, "
                    f"{_nb_etapes(reparee2)} étape(s) conservée(s) au lieu de {_nb_etapes(reparee)}."
                )
                raw_output, reparee, dates_retirees = raw2, reparee2, retirees2
                if dates_retirees:
                    _log_dates_retirees(dates_retirees, "2e passe")
            else:
                print("  [verify] 2e passe pas meilleure — résultat de la 1re passe conservé.")
        except Exception as exc:  # noqa: BLE001 — la 2e chance ne doit jamais casser l'extraction
            print(f"  [verify] 2e passe abandonnée ({type(exc).__name__}: {exc}) — résultat de la 1re passe conservé.")

    if _est_vide(reparee) and not deuxieme_passe_faite:
        print("  [retry] réponse sans aucune date alors que la source n'est pas déclarée sans date — nouvel essai unique")
        try:
            raw2, reparee2, retirees2 = _passe_extraction(user_prompt + _MESSAGE_VIDE, content, today, url, region)
            if not _est_vide(reparee2):
                _valider_avec_sauvetage(reparee2)  # n'est retenue que si elle est exploitable
                raw_output, reparee, dates_retirees = raw2, reparee2, retirees2
                print("  [retry] 2e réponse exploitable — retenue.")
        except Exception as exc:  # noqa: BLE001 — jamais bloquant
            print(f"  [retry] 2e essai abandonné ({type(exc).__name__}: {exc})")

    if _est_vide(reparee):
        # Toujours sans aucune date : traitée comme « aucune date trouvée »
        # (comme Bretagne) plutôt que comme un échec, et sans laisser des
        # commissions vides polluer l'état. Le lien PDF éventuel est conservé
        # pour le fallback PDF de main.py.
        reparee = {**reparee, "commissions": [], "aucune_date_trouvee": True}
        print("  [verify] toujours sans date — traitée comme « aucune date trouvée ».")

    try:
        return _valider_avec_sauvetage(reparee)
    except ValidationError:
        # On affiche le JSON brut (avant réparation) pour pouvoir diagnostiquer
        # un écart de schéma que même le sauvetage n'a pas su corriger
        print(json.dumps(raw_output, indent=2, ensure_ascii=False))
        raise


# ---------------------------------------------------------------------------
# Passe d'extraction + vérification, et 2e chance sur dates non confirmées
# ---------------------------------------------------------------------------


def _appeler_llm(user_prompt: str) -> dict:
    """Un appel réel au provider, avec garde-fou budget avant et
    enregistrement du coût réel après."""
    prix_in, prix_out = PRIX_PAR_PROVIDER.get(PROVIDER, PRIX_PAR_PROVIDER["anthropic"])
    tokens_in_estimes = _estimer_tokens(SYSTEM_PROMPT + user_prompt)
    cout_estime = (
        tokens_in_estimes * prix_in + OUTPUT_ESTIME_TOKENS * prix_out
    ) / 1_000_000
    verifier_budget(cout_estime)  # lève BudgetExceededError si dépassement

    if PROVIDER == "qwen":
        try:
            raw_output, cout_reel = _extract_qwen(user_prompt)
        except SortieIllisible as exc:
            print(f"  [retry] {exc} — nouvel essai unique")
            verifier_budget(cout_estime)
            raw_output, cout_reel = _extract_qwen(user_prompt)
    elif PROVIDER == "anthropic":
        raw_output, cout_reel = _extract_anthropic(user_prompt)
    else:
        raise ValueError(
            f"PROVIDER inconnu : '{PROVIDER}'. Valeurs acceptées : 'anthropic', 'qwen'."
        )

    total_depense = enregistrer_depense(cout_reel)
    print(f"  [budget] +${cout_reel:.5f} cet appel — ${total_depense:.4f} dépensés ce mois")
    return raw_output


def _passe_extraction(user_prompt: str, content: str, today: str, url: str, region: str):
    """Appel LLM + réparation structurelle + vérification des dates contre le
    texte source. Retourne (raw_output, dict réparé/filtré, dates retirées)."""
    raw_output = _appeler_llm(user_prompt)
    reparee = _reparer_structure(raw_output, today, url, region)

    # Filet contre l'hallucination de dates : le JSON est valide (ni crash ni
    # erreur Pydantic), seule la comparaison au texte source le révèle
    # (voir verify.py).
    reparee, depuis_libelle = recuperer_dates_du_libelle(reparee, content)
    for c in depuis_libelle:
        print(f"  [verify] date reprise du libellé : {c['fonds']} / {c['label']} : {c['avant']} -> {c['apres']}")
    reparee, dates_retirees = filtrer_dates_non_confirmees(reparee, content)

    reparee, nb_doublons = _dedoublonner_commissions(reparee)
    if nb_doublons:
        print(f"  [verify] {nb_doublons} commission(s) en doublon fusionnée(s) (mêmes dates, même fonds)")

    # Années : le texte source fait foi ; à défaut, prochaine occurrence d'un
    # calendrier qui se répète ("chaque année"...). Voir verify.py.
    reparee, annees_corrigees = normaliser_annees(reparee, content, today)
    for c in annees_corrigees:
        print(f"  [verify] année corrigée : {c['fonds']} / {c['label']} : {c['avant']} -> {c['apres']}")
    return raw_output, reparee, dates_retirees


def _log_dates_retirees(dates_retirees: list[dict], passe: str) -> None:
    print(f"  [verify] {len(dates_retirees)} date(s) retirée(s) ({passe}) — absente(s) du texte source :")
    for d in dates_retirees:
        print(f"    - {d['fonds']} / {d['label']} : {d['date_fin']}")


def _date_fr(date_iso: str | None) -> str:
    try:
        d = datetime.date.fromisoformat(date_iso)
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    except (TypeError, ValueError):
        return str(date_iso)


def _message_correctif(dates_retirees: list[dict]) -> str:
    lignes = "\n".join(
        f"- {d['fonds']} / {d['label']} : {_date_fr(d['date_fin'])}" for d in dates_retirees
    )
    return (
        "\n\nCORRECTION NÉCESSAIRE : une extraction précédente de ce même texte "
        "contenait des dates qui N'EXISTENT PAS dans le texte ci-dessus (elles "
        "avaient été inventées) :\n"
        f"{lignes}\n"
        "Relis attentivement le texte et refais l'extraction complète. Ne renvoie "
        "QUE des dates qui figurent explicitement dans le texte : pour chaque "
        "étape concernée, retrouve dans le texte la date réellement écrite et "
        "recopie-la telle qu'elle apparaît. Si une étape n'a réellement pas de "
        "date dans le texte, omets-la."
    )


def _nb_etapes(reparee: dict) -> int:
    commissions = reparee.get("commissions") if isinstance(reparee, dict) else None
    if not isinstance(commissions, list):
        return 0
    return sum(len(c.get("etapes") or []) for c in commissions if isinstance(c, dict))


def _score(reparee: dict, dates_retirees: list[dict]) -> tuple[int, int]:
    """Plus petit = meilleur : d'abord PLUS d'étapes confirmées conservées (une
    2e passe qui « corrige » en rendant moins de dates n'est pas meilleure),
    puis moins de dates inventées retirées."""
    return (-_nb_etapes(reparee), len(dates_retirees))


# ---------------------------------------------------------------------------
# Sauvetage : ne pas perdre toute une source pour un seul champ fautif
# ---------------------------------------------------------------------------

_TEXTES_COMMISSION = ("region", "fonds", "organisme", "typologie_oeuvre", "etape_filiere", "url_source", "date_extraction")
_OPTIONNELS_COMMISSION = ("session_label", "montant_min_eur", "montant_max_eur", "lien_formulaire")
_TEXTES_ACTION = ("type_action", "condition_declenchement", "consequence_si_non_respect")


def _naviguer(racine, chemin):
    cible = racine
    for cle in chemin:
        cible = cible[cle]
    return cible


def _corriger_erreur(reparee: dict, loc: tuple) -> str | None:
    """Corrige LA plus petite partie fautive désignée par `loc` (chemin
    d'erreur Pydantic) et retourne une description pour le log, ou None si
    on ne sait pas la corriger. On préfère perdre une étape, une fenêtre ou
    une valeur plutôt que toute la source."""
    try:
        if "fenetre" in loc:
            i = loc.index("fenetre")
            _naviguer(reparee, loc[:i])["fenetre"] = None
            return "fenêtre de date invalide retirée (l'action préalable est conservée)"

        if "etapes" in loc and loc.index("etapes") + 1 < len(loc) and isinstance(loc[loc.index("etapes") + 1], int):
            i = loc.index("etapes")
            liste = _naviguer(reparee, loc[: i + 1])
            etape = liste.pop(loc[i + 1])
            return f"étape invalide retirée ({etape.get('label') if isinstance(etape, dict) else etape})"

        if "conditions_eliminatoires" in loc and isinstance(loc[-1], int):
            liste = _naviguer(reparee, loc[:-1])
            liste.pop(loc[-1])
            return "condition invalide retirée"

        if "actions_prealables" in loc:
            i = loc.index("actions_prealables")
            action = _naviguer(reparee, loc[: i + 2])
            champ = loc[i + 2] if len(loc) > i + 2 else None
            if champ in _TEXTES_ACTION:
                action[champ] = "non précisé"
                return f"texte '{champ}' invalide remplacé par « non précisé »"
            if champ == "lien_reservation":
                action[champ] = None
                return "lien de réservation invalide retiré"
            _naviguer(reparee, loc[: i + 1]).pop(loc[i + 1])
            return "action préalable invalide retirée"

        if len(loc) >= 3 and loc[0] == "commissions" and isinstance(loc[1], int):
            commission = reparee["commissions"][loc[1]]
            champ = loc[2]
            if champ in _TEXTES_COMMISSION:
                commission[champ] = "autre" if champ == "etape_filiere" else "non précisé"
                return f"texte '{champ}' invalide remplacé par une valeur neutre"
            if champ in _OPTIONNELS_COMMISSION:
                commission[champ] = None
                return f"valeur '{champ}' invalide retirée"
    except (KeyError, IndexError, TypeError, AttributeError):
        return None
    return None


def _valider_avec_sauvetage(reparee: dict, max_corrections: int = 30) -> ExtractionResult:
    """Valide ; si un champ précis est fautif, le corrige (une erreur à la
    fois, puis on revalide) au lieu de rejeter toute la source. Chaque
    correction est affichée. Lève ValidationError si une erreur ne peut pas
    être corrigée localement."""
    for _ in range(max_corrections):
        try:
            return ExtractionResult.model_validate(reparee)
        except ValidationError as exc:
            description = None
            for erreur in exc.errors():
                description = _corriger_erreur(reparee, tuple(erreur["loc"]))
                if description:
                    print(f"  [sauvetage] {'.'.join(str(x) for x in erreur['loc'])} : {description}")
                    break
            if not description:
                raise
    return ExtractionResult.model_validate(reparee)


def _dedoublonner_commissions(reparee: dict) -> tuple[dict, int]:
    """Fusionne les commissions strictement identiques d'une même extraction :
    même fonds, même étape de filière et EXACTEMENT les mêmes dates. Cas réel
    (Bourgogne) : la même page renvoyée 3 fois, sous les libellés de session
    « null », « Session 1 » et « Session 2 », donc 3 fiches identiques sur le
    site. On garde de préférence celle sans libellé de session inventé."""
    commissions = reparee.get("commissions") if isinstance(reparee, dict) else None
    if not isinstance(commissions, list):
        return reparee, 0

    def cle(c):
        if not isinstance(c, dict):
            return None
        dates = frozenset((e.get("date_debut"), e.get("date_fin")) for e in (c.get("etapes") or [])
                          if isinstance(e, dict) and e.get("date_fin"))
        return (c.get("fonds"), c.get("etape_filiere"), dates) if dates else None

    groupes: dict = {}
    for i, c in enumerate(commissions):
        k = cle(c)
        if k is not None:
            groupes.setdefault(k, []).append(i)

    a_retirer: set[int] = set()
    for indices in groupes.values():
        if len(indices) < 2:
            continue
        gardee = next((i for i in indices if not commissions[i].get("session_label")), indices[0])
        a_retirer.update(i for i in indices if i != gardee)

    if a_retirer:
        reparee["commissions"] = [c for i, c in enumerate(commissions) if i not in a_retirer]
    return reparee, len(a_retirer)


def _est_vide(reparee: dict) -> bool:
    """Réponse vide alors que la source n'est pas déclarée « sans date » :
    typiquement {"commissions": null} (cas réel Ciclic)."""
    if not isinstance(reparee, dict) or reparee.get("aucune_date_trouvee"):
        return False
    commissions = reparee.get("commissions")
    if commissions in (None, []):
        return True
    # Des commissions mais plus AUCUNE date (ex: Normandie page principale :
    # sessions inventées dont toutes les dates ont été retirées ; Occitanie :
    # étapes vides) : rien à afficher, on traite comme une réponse vide.
    return isinstance(commissions, list) and all(
        not any(isinstance(et, dict) and et.get("date_fin") for et in (c.get("etapes") or []))
        for c in commissions if isinstance(c, dict)
    )


_MESSAGE_VIDE = (
    "\n\nCORRECTION NÉCESSAIRE : ta réponse précédente était VIDE (aucune commission), "
    "alors que ce texte contient des échéances. Relis-le et extrais TOUTES les dates "
    "qu'il contient, y compris celles déjà passées. Si le texte ne contient vraiment "
    "aucune date, réponds aucune_date_trouvee=true et commissions=[]. Si le "
    "calendrier n'existe que dans un PDF lié, renseigne lien_pdf_calendrier avec son URL."
)
