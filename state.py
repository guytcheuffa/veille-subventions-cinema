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

CORRESPONDANCE EN DEUX TEMPS. L'identifiant "naturel" d'une commission
(make_key) est calculé à partir de champs stables (session_label,
typologie_oeuvre, etape_filiere) plutôt que de texte libre comme
'fonds'/'organisme'. Mais même ces champs "stables" varient parfois d'un
run à l'autre sur un modèle comme Qwen Flash (ex: 'Troisième session' vs
'Troisième session 2026', ou un même dispositif redécoupé en 4 commissions
par genre un run puis en 2 par étape_filiere le run suivant) — observé en
conditions réelles. Pour absorber ce cas SANS perdre l'historique
(first_seen, etc.), update_state() se rabat sur une CORRESPONDANCE FLOUE
(_trouver_correspondance_floue) quand la clé exacte ne trouve rien : elle
recherche, parmi les commissions connues de la même url_source, celle dont
les dates de dépôt se recoupent le mieux. Si le recoupement dépasse un
seuil de confiance, les deux sont considérées comme la même commission
malgré le redécoupage/la reformulation. Voir test_state.py ->
run_correspondance_floue() pour un scénario reproduisant le cas réel
observé.
"""

from __future__ import annotations

import hashlib
import json
import re
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
    de DATES — volontairement restreint à (date_debut, date_fin,
    heure_limite), SANS le texte de 'label'. Le libellé d'une étape est du
    texte libre reformulé par le LLM d'un run à l'autre sans changement de
    fond (ex: 'Comité de lecture' vs 'Comité de lecture - date communiquée
    après validation') — l'inclure dans le hash déclenchait des
    'modifie' à tort alors que la date réelle n'avait pas bougé."""
    etapes = commission.get("etapes", [])
    normalise = sorted(
        (e.get("date_debut") or "", e.get("date_fin") or "", e.get("heure_limite") or "")
        for e in etapes
    )
    return hashlib.sha1(json.dumps(normalise, sort_keys=True).encode("utf-8")).hexdigest()


def _dates_de(commission: dict) -> set[str]:
    """Ensemble des dates de fin (non nulles) de toutes les étapes d'une
    commission — sert de 'empreinte' pour la correspondance floue, plus
    stable que le texte libre (labels, typologie) reformulé d'un run à
    l'autre."""
    return {
        e.get("date_fin")
        for e in commission.get("etapes", [])
        if isinstance(e, dict) and e.get("date_fin")
    }


def _toutes_dates_passees(commission: dict, today: str) -> bool:
    """True si toutes les dates connues d'une commission sont déjà passées
    par rapport à `today` (ou si la commission n'a aucune date). Sert à ne
    pas déclencher de fausse alerte CLOS quand une session disparaît d'une
    page simplement parce qu'elle est obsolète — pas parce qu'elle a fermé
    prématurément. Une commission qui a encore au moins une échéance à
    venir et qui disparaît reste, elle, une vraie alerte à émettre."""
    dates = _dates_de(commission)
    if not dates:
        return True
    return all(d < today for d in dates)


# Seuils de la correspondance floue : au moins 1 date en commun, ET au
# moins un tiers des dates de la commission la plus petite des deux qui se
# recoupent. Volontairement permissif sur le nombre absolu (1 suffit, une
# session n'a parfois qu'une seule date) mais strict sur le ratio pour
# éviter un faux rapprochement entre deux commissions sans rapport qui
# partageraient une date par coïncidence.
_FUZZY_MIN_DATES_PARTAGEES = 1
_FUZZY_MIN_RATIO = 1 / 3


def _score_correspondance(dates_a: set[str], dates_b: set[str]) -> tuple[int, float]:
    """Retourne (nombre de dates partagées, ratio de recoupement) entre
    deux ensembles de dates. Le ratio est calculé par rapport au plus petit
    des deux ensembles (recoupement partiel accepté dans les deux sens :
    une commission qui a gagné ou perdu une étape reste reconnue)."""
    partagees = dates_a & dates_b
    plus_petit = min(len(dates_a), len(dates_b)) or 1
    return len(partagees), len(partagees) / plus_petit


def _trouver_correspondance_floue(
    commission: dict, state: dict, deja_revendiquees: set[str]
) -> str | None:
    """Cherche, parmi les entrées de `state` sur la MÊME url_source et pas
    déjà revendiquées ce run, celle dont les dates se recoupent le mieux
    avec `commission` — utilisé quand la clé exacte (make_key) ne trouve
    rien, pour absorber les cas où le LLM a redécoupé/reformulé la même
    commission différemment d'un run à l'autre (ex: 4 commissions par genre
    devenues 2 commissions par étape_filiere). Retourne la clé retenue, ou
    None si rien ne dépasse le seuil de confiance."""
    url = commission.get("url_source", "")
    dates_cible = _dates_de(commission)
    if not dates_cible:
        return None  # rien à recouper si la commission elle-même n'a pas de date

    meilleure_cle = None
    meilleur_score = (0, 0.0)
    for cle, entree in state.items():
        if cle in deja_revendiquees:
            continue
        if entree["commission"].get("url_source", "") != url:
            continue
        score = _score_correspondance(dates_cible, _dates_de(entree["commission"]))
        if score > meilleur_score:
            meilleur_score = score
            meilleure_cle = cle

    nb_partagees, ratio = meilleur_score
    if nb_partagees >= _FUZZY_MIN_DATES_PARTAGEES and ratio >= _FUZZY_MIN_RATIO:
        return meilleure_cle
    return None


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

    Pour chaque commission, la correspondance avec l'état existant se fait
    en deux temps :
    1. Clé exacte (make_key) — le cas normal, rapide et fiable.
    2. Si aucune clé exacte ne correspond, REPLI EN CORRESPONDANCE FLOUE
       (_trouver_correspondance_floue) : recherche parmi les entrées de la
       même url_source celle dont les dates se recoupent le mieux. Ça
       absorbe les cas où le LLM redécoupe différemment la même commission
       d'un run à l'autre (ex: 4 commissions par genre devenues 2 par
       étape_filiere) — sans ce repli, chaque redécoupage génère un faux
       CLOS + NOUVEAU alors que rien n'a réellement changé.
    Si aucune des deux ne trouve de correspondance, la commission est
    traitée comme réellement nouvelle (clé exacte utilisée comme identité).

    `processed_url_sources` doit contenir l'ensemble des url_source
    effectivement traitées dans ce run (pas seulement celles ayant produit
    des commissions) — c'est ce qui permet de distinguer "clos" (une
    commission connue sur une source qu'on a bien re-vérifiée, mais qui a
    disparu) de "on n'a simplement pas vérifié cette source cette fois".

    Retourne (state_mis_a_jour, changements) où `changements` est la liste
    des commissions NOUVEAU / MODIFIE / CLOS de ce run (jamais INCHANGE) —
    c'est cette liste qui doit servir de base à une notification.
    """
    revendiquees_ce_run: set[str] = set()
    changements: list[dict] = []

    for commission in commissions:
        cle_exacte = make_key(commission)
        nouveau_hash = _etapes_hash(commission)

        # Doublon INTRA-RUN : le LLM a parfois renvoyé deux fois la même
        # commission dans une seule réponse (contenu identique ou quasi,
        # ex: étapes dans un ordre différent) — observé en conditions
        # réelles sur Bourgogne-Franche-Comté. Si la clé exacte de cette
        # commission a déjà été revendiquée PLUS TÔT DANS CE MÊME RUN, ce
        # n'est pas une commission différente à comparer/fusionner : c'est
        # un doublon du run à ignorer purement et simplement, pour ne pas
        # écraser la mise à jour qu'on vient de faire une ligne plus haut.
        if cle_exacte in revendiquees_ce_run:
            continue

        if cle_exacte in state:
            cle = cle_exacte
        else:
            cle = _trouver_correspondance_floue(commission, state, revendiquees_ce_run)

        if cle is None:
            # Ni clé exacte ni correspondance floue : réellement nouvelle.
            revendiquees_ce_run.add(cle_exacte)
            state[cle_exacte] = {
                "commission": commission,
                "etapes_hash": nouveau_hash,
                "first_seen": today,
                "last_updated": today,
                "last_checked": today,
                "clos": False,
            }
            changements.append(
                {"key": cle_exacte, "statut": STATUT_NOUVEAU, "commission": commission}
            )
            continue

        revendiquees_ce_run.add(cle)
        entree = state[cle]
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
            changements.append({"key": cle, "statut": statut, "commission": commission})

    # Détecte les commissions closes : connues sur une source qu'on a
    # effectivement re-vérifiée ce run, mais ni retrouvées par clé exacte
    # ni rapprochées par correspondance floue.
    #
    # On distingue deux cas :
    #  - la commission avait encore au moins une échéance À VENIR et
    #    disparaît quand même -> vraie clôture, mérite une alerte.
    #  - toutes ses dates étaient déjà PASSÉES -> sa disparition de la page
    #    est normale (la page ne référence plus une session obsolète), pas
    #    une vraie nouvelle : on marque quand même clos=True en interne
    #    (site_data.json reste correct), mais SANS l'ajouter aux
    #    changements à notifier — ça évite le bruit observé en pratique
    #    (ex: le CNC qui redécoupe ses sessions différemment d'un run à
    #    l'autre génère sinon des dizaines de fausses alertes CLOS).
    for cle, entree in state.items():
        url = entree["commission"].get("url_source", "")
        if url in processed_url_sources and cle not in revendiquees_ce_run and not entree.get("clos"):
            entree["clos"] = True
            entree["last_checked"] = today
            if not _toutes_dates_passees(entree["commission"], today):
                changements.append({"key": cle, "statut": STATUT_CLOS, "commission": entree["commission"]})

    return state, changements


def _mots_fonds(fonds: str | None) -> set[str]:
    return {m for m in re.findall(r"\w+", (fonds or "").lower()) if len(m) > 2}


def _fonds_similaires(a: str | None, b: str | None) -> bool:
    """Même dispositif malgré des reformulations du LLM (ex. « Fonds de
    soutien aux Nouveaux Médias » / « Fonds d'aide aux projets nouveaux
    médias ») ; distinct de « Fonds de soutien au court métrage »."""
    ma, mb = _mots_fonds(a), _mots_fonds(b)
    if not ma or not mb:
        return True
    return len(ma & mb) / len(ma | mb) >= 0.5


def _est_supplantee(c: dict, actives: list[dict]) -> bool:
    """Une fiche « non retrouvée » est supplantée si une fiche active de la
    même source et du même dispositif porte déjà toutes ses dates, ou en est
    une ancienne version (recouvrement fort de dates, mais pas identique :
    dates corrigées ou modifiées depuis)."""
    dates = _dates_de(c)
    candidates = [a for a in actives
                  if a.get("url_source") == c.get("url_source") and _fonds_similaires(a.get("fonds"), c.get("fonds"))]
    if not dates or not candidates:
        return False
    if dates <= set().union(*[_dates_de(a) for a in candidates]):
        return True
    for a in candidates:
        da = _dates_de(a)
        partagees = len(dates & da)
        if partagees >= _FUZZY_MIN_DATES_PARTAGEES and partagees / (min(len(dates), len(da)) or 1) >= _FUZZY_MIN_RATIO:
            return True
    return False


def build_site_data(state: dict, generated_at: str) -> dict:
    """Construit le JSON 'propre' destiné au site : une liste plate des
    commissions connues, enrichies des métadonnées de suivi.

    N'est pas envoyé au site :
      - une fiche sans aucune date (rien à afficher) ;
      - une fiche « non retrouvée » (clos) SUPPLANTÉE par une fiche active du
        même dispositif : doublon d'un redécoupage, ou ancienne version dont
        les dates ont été corrigées depuis (ex: les dates inventées de
        Normandie Session 3 restaient affichées « à vérifier »).
    Une fiche non retrouvée avec des dates qu'aucune fiche active ne porte
    reste visible, marquée « à vérifier ». Rien n'est supprimé de state.json."""
    actives = [e["commission"] for e in state.values() if not e.get("clos", False)]

    aides = []
    for key, entree in state.items():
        clos = entree.get("clos", False)
        c = entree["commission"]
        if not _dates_de(c):
            continue
        if clos and _est_supplantee(c, actives):
            continue
        aide = dict(c)
        aide["id"] = key
        aide["first_seen"] = entree["first_seen"]
        aide["last_updated"] = entree["last_updated"]
        aide["clos"] = clos
        aides.append(aide)

    aides.sort(key=lambda a: (a["region"], a["organisme"], a.get("session_label") or ""))

    return {"generated_at": generated_at, "aides": aides}
