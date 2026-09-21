"""Gestion d'état et déduplication.

Compare le résultat d'une extraction au dernier état connu (stocké dans
data/state.json) pour déterminer, par commission :
  - NOUVEAU   : jamais vue auparavant
  - MODIFIE   : déjà connue, mais ses dates ont changé
  - INCHANGE  : déjà connue, dates identiques (pas d'alerte à émettre)
  - CLOS      : connue lors d'un run précédent sur cette même source, mais
                absente du run actuel (probablement retirée du site source)

C'est cette classification qui permet de "ne pas notifier les commissions
inchangées ou closes et alerter uniquement sur les nouvelles sessions ou
modifications de dates" (cahier des charges).

L'identifiant d'une commission (sa "clé") est calculé à partir de champs
stables (url_source + session_label, ou url_source + etape_filiere en
l'absence de session_label) plutôt que du texte libre généré par le LLM
(ex: 'fonds', 'organisme'), qui varie légèrement d'un run à l'autre sur un
modèle comme Qwen Flash — voir les notes dans extract.py. Cette clé n'est
donc pas garantie unique à 100% dans des cas limites (ex: deux sessions
sans session_label sur la même page et la même étape_filiere), mais c'est
un compromis raisonnable pour ce POC.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

STATUT_NOUVEAU = "nouveau"
STATUT_MODIFIE = "modifie"
STATUT_INCHANGE = "inchange"
STATUT_CLOS = "clos"


def _slug(texte: str | None) -> str:
    if not texte:
        return ""
    return "".join(c.lower() if c.isalnum() else "-" for c in texte).strip("-")


def make_key(commission: dict) -> str:
    """Clé stable identifiant une commission à travers les runs.

    Combine PLUSIEURS champs (session_label, typologie_oeuvre,
    etape_filiere) plutôt qu'un seul avec repli, pour éviter les collisions
    observées en pratique : le LLM place parfois le "genre" distinctif
    (Animation/Documentaire/Fiction...) dans 'session_label', parfois dans
    'typologie_oeuvre' selon le run — s'appuyer sur un seul de ces champs
    fait collapser plusieurs commissions distinctes sur la MÊME clé (perte
    de données : chaque commission écrase la précédente dans l'état).

    Reste néanmoins approximatif face à un modèle "Flash" dont la
    formulation varie légèrement d'un run à l'autre (ex: 'Animation (CM,
    LM, AV, XR)' vs 'Animation') — ça peut encore provoquer des faux
    'clos'+'nouveau' au lieu d'une reconnaissance parfaite, mais ça n'écrase
    plus silencieusement des commissions différentes entre elles."""
    url = commission.get("url_source", "")
    parts = [
        _slug(commission.get("session_label")),
        _slug(commission.get("typologie_oeuvre")),
        _slug(commission.get("etape_filiere")),
    ]
    base = f"{url}|" + "|".join(parts)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _etapes_hash(commission: dict) -> str:
    """Hash du contenu réellement significatif pour détecter un changement
    de dates — volontairement restreint à 'etapes' (pas 'fonds'/'organisme'
    dont le libellé varie d'un run à l'autre sans changement de fond)."""
    etapes = commission.get("etapes", [])
    normalise = sorted(
        (e.get("label", ""), e.get("date_debut"), e.get("date_fin"), e.get("heure_limite"))
        for e in etapes
    )
    return hashlib.sha1(json.dumps(normalise, sort_keys=True).encode("utf-8")).hexdigest()


def load_state(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(path: str | Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def update_state(
    state: dict,
    commissions: list[dict],
    today: str,
    processed_url_sources: set[str],
) -> tuple[dict, list[dict]]:
    """Met à jour `state` (modifié en place et retourné) avec les
    commissions extraites lors de ce run.

    `processed_url_sources` doit contenir l'ensemble des url_source
    effectivement traitées dans ce run (pas seulement celles ayant produit
    des commissions) — c'est ce qui permet de distinguer "clos" (une
    commission connue sur une source qu'on a bien re-vérifiée, mais qui a
    disparu) de "on n'a simplement pas vérifié cette source cette fois".

    Retourne (state_mis_a_jour, changements) où `changements` est la liste
    des commissions NOUVEAU / MODIFIE / CLOS de ce run (jamais INCHANGE) —
    c'est cette liste qui doit servir de base à une notification.
    """
    vues_ce_run: set[str] = set()
    changements: list[dict] = []

    for commission in commissions:
        key = make_key(commission)
        vues_ce_run.add(key)
        nouveau_hash = _etapes_hash(commission)

        if key not in state:
            state[key] = {
                "commission": commission,
                "etapes_hash": nouveau_hash,
                "first_seen": today,
                "last_updated": today,
                "last_checked": today,
                "clos": False,
            }
            changements.append({"key": key, "statut": STATUT_NOUVEAU, "commission": commission})
            continue

        entree = state[key]
        etait_clos = entree.get("clos", False)
        entree["commission"] = commission
        entree["last_checked"] = today
        entree["clos"] = False

        if nouveau_hash != entree["etapes_hash"]:
            entree["etapes_hash"] = nouveau_hash
            entree["last_updated"] = today
            statut = STATUT_MODIFIE
        elif etait_clos:
            # Réapparue telle quelle après avoir été clôturée : on la
            # traite comme une nouveauté pour l'alerte (mérite d'être
            # re-signalée), mais sans réinitialiser first_seen.
            statut = STATUT_NOUVEAU
        else:
            statut = STATUT_INCHANGE

        if statut != STATUT_INCHANGE:
            changements.append({"key": key, "statut": statut, "commission": commission})

    # Détecte les commissions closes : connues sur une source qu'on a
    # effectivement re-vérifiée ce run, mais absentes du résultat.
    for key, entree in state.items():
        url = entree["commission"].get("url_source", "")
        if url in processed_url_sources and key not in vues_ce_run and not entree.get("clos"):
            entree["clos"] = True
            entree["last_checked"] = today
            changements.append({"key": key, "statut": STATUT_CLOS, "commission": entree["commission"]})

    return state, changements


def build_site_data(state: dict, generated_at: str) -> dict:
    """Construit le JSON 'propre' destiné au futur site : une liste plate
    de toutes les commissions connues (actives et closes), enrichies des
    métadonnées de suivi (first_seen/last_updated/clos)."""
    aides = []
    for key, entree in state.items():
        aide = dict(entree["commission"])
        aide["id"] = key
        aide["first_seen"] = entree["first_seen"]
        aide["last_updated"] = entree["last_updated"]
        aide["clos"] = entree.get("clos", False)
        aides.append(aide)

    aides.sort(key=lambda a: (a["region"], a["organisme"], a.get("session_label") or ""))

    return {"generated_at": generated_at, "aides": aides}
