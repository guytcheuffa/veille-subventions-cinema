"""Vérification que les dates extraites par le LLM apparaissent réellement
dans le texte source — filet de sécurité contre l'hallucination de dates.

Contrairement aux autres filets déjà en place dans extract.py (fusion de
fragments, champs manquants, JSON imbriqué...), ce cas-ci ne provoque NI
crash NI erreur de validation Pydantic : le JSON produit est parfaitement
valide, juste factuellement faux. Observé en conditions réelles sur
Normandie (Session 3 - 2027) : "Présélection le 22/06/27, Plénière le
06/07/27" dans le texte source, ressorti comme "16/06/2027" et "30/06/2027"
dans l'extraction — deux dates plausibles mais entièrement inventées,
repérable uniquement en comparant au texte source, jamais par la structure
du JSON lui-même.
"""

from __future__ import annotations

import re
from datetime import date

MOIS_FR = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def formes_probables(date_iso: str) -> set[str]:
    """Toutes les formes textuelles plausibles sous lesquelles une date ISO
    (YYYY-MM-DD) pourrait apparaître dans un texte français source :
    jj/mm/aaaa, jj/mm/aa (avec ou sans zéro initial), jj-mm-aaaa, et
    'jj mois' / 'jj mois aaaa' / 'jj mois aa' (avec '1er' pour le 1er du
    mois, comme l'écrivent la plupart des sites officiels)."""
    d = date.fromisoformat(date_iso)
    jour, mois, annee = d.day, d.month, d.year
    annee2 = annee % 100

    jours = {str(jour), f"{jour:02d}"}
    mois_num = {str(mois), f"{mois:02d}"}
    annees = {str(annee), f"{annee2:02d}"}

    formes = set()
    for j in jours:
        for m in mois_num:
            for a in annees:
                formes.add(f"{j}/{m}/{a}")
                formes.add(f"{j}-{m}-{a}")
                formes.add(f"{j}/{m} {a}")  # ex: "30/04 2027" (fenêtres 'du X au Y AAAA')
            formes.add(f"{j}/{m}")  # jour/mois seul, année implicite par le contexte

    mois_nom = MOIS_FR[mois - 1]
    jour_texte = "1er" if jour == 1 else str(jour)
    formes.add(f"{jour_texte} {mois_nom}")
    formes.add(f"{jour_texte} {mois_nom} {annee}")
    formes.add(f"{jour_texte} {mois_nom} {annee2:02d}")

    return formes


def date_confirmee(date_iso: str | None, contenu: str) -> bool:
    """True si `date_iso` (YYYY-MM-DD) apparaît sous une forme plausible
    dans `contenu`. Une date_iso vide/None est considérée confirmée (rien
    à vérifier dans ce cas)."""
    if not date_iso:
        return True
    try:
        formes = formes_probables(date_iso)
    except ValueError:
        return False  # date_iso mal formée — de toute façon invalide ailleurs
    contenu_minuscule = contenu.lower()
    # Bornes : pas de chiffre collé avant/après, sinon "5 mai" serait trouvé
    # dans "15 mai" et "22/06/20" dans "22/06/2027".
    return any(
        re.search(rf"(?<!\d){re.escape(forme.lower())}(?!\d)", contenu_minuscule)
        for forme in formes
    )


def filtrer_dates_non_confirmees(raw_output: dict, contenu: str) -> tuple[dict, list[dict]]:
    """Parcourt les commissions d'un ExtractionResult (sous forme de dict,
    avant validation Pydantic finale) et retire toute étape dont
    'date_fin' n'a aucune trace dans le texte source — très probablement
    inventée par le LLM. Retire aussi une 'fenetre' d'action préalable non
    confirmée, sans supprimer l'action elle-même (l'obligation reste
    valide même si sa date précise n'a pas pu être confirmée).

    Retourne (raw_output modifié en place, liste des dates retirées — pour
    logging/diagnostic côté appelant)."""
    retirees: list[dict] = []
    commissions = raw_output.get("commissions")
    if not isinstance(commissions, list):
        return raw_output, retirees

    for commission in commissions:
        if not isinstance(commission, dict):
            continue

        etapes = commission.get("etapes")
        if isinstance(etapes, list):
            nouvelles_etapes = []
            for etape in etapes:
                if isinstance(etape, dict) and not date_confirmee(etape.get("date_fin"), contenu):
                    retirees.append({
                        "fonds": commission.get("fonds"),
                        "label": etape.get("label"),
                        "date_fin": etape.get("date_fin"),
                    })
                    continue
                nouvelles_etapes.append(etape)
            commission["etapes"] = nouvelles_etapes

        for action in commission.get("actions_prealables") or []:
            if not isinstance(action, dict):
                continue
            fenetre = action.get("fenetre")
            if isinstance(fenetre, dict) and not date_confirmee(fenetre.get("date_fin"), contenu):
                retirees.append({
                    "fonds": commission.get("fonds"),
                    "label": f"[action préalable] {fenetre.get('label')}",
                    "date_fin": fenetre.get("date_fin"),
                })
                action["fenetre"] = None

    return raw_output, retirees


# ---------------------------------------------------------------------------
# Années : le texte source fait foi, sinon la prochaine occurrence d'un
# calendrier qui se répète
# ---------------------------------------------------------------------------
#
# Cas réel (PACA) : "31 janvier, 15 avril et 30 septembre de chaque année" -
# sans année. Le modèle a mis le 30 septembre en 2027 alors que le 30/09/2026
# était dans 2 jours : une échéance imminente disparaissait du site. Le
# filtre ci-dessus ne le voit pas ("30 septembre" figure bien dans le texte).
#
# Règles, par date (jour + mois) :
#   1. le texte donne UNE année explicite pour ce jour/mois (ex "17 novembre
#      2025") et le modèle en a mis une autre -> on prend celle du texte ;
#   2. le texte donne plusieurs années différentes -> ambigu, on ne touche pas ;
#   3. aucune année dans le texte MAIS le texte marque une récurrence à côté
#      ("chaque année", "par an", "annuel", "calendrier permanent") -> on
#      prend la prochaine occurrence à partir d'aujourd'hui ;
#   4. aucune année et pas de récurrence (ex ALCA : "les dépôts des 7 et 21
#      septembre sont clôturés") -> on ne touche pas : ce n'est pas un
#      calendrier qui se répète, ces dates sont passées.

RECURRENCE_RE = re.compile(
    r"chaque\s+ann[ée]e|par\s+an\b|annuel\b|tous\s+les\s+ans|calendrier\s+permanent",
    re.IGNORECASE,
)
FENETRE_RECURRENCE = 300  # caractères autour de la date où chercher l'indice


def _jour_motif(jour: int) -> str:
    return r"(?:1er|0?1)" if jour == 1 else rf"0?{jour}"


def annees_explicites(jour: int, mois: int, contenu: str) -> set[int]:
    """Années écrites dans le texte juste à côté de ce jour/mois
    (jj/mm/aaaa, jj/mm/aa, jj-mm-aaaa, jj/mm aaaa, jj mois aaaa)."""
    txt = contenu.lower()
    j, m, nom = _jour_motif(jour), rf"0?{mois}", MOIS_FR[mois - 1]
    motifs = [
        rf"(?<!\d){j}/{m}/(\d{{4}}|\d{{2}})(?!\d)",
        rf"(?<!\d){j}-{m}-(\d{{4}}|\d{{2}})(?!\d)",
        rf"(?<!\d){j}/{m} (\d{{4}})(?!\d)",
        rf"(?<!\d){j} {nom} (\d{{4}})(?!\d)",
    ]
    annees: set[int] = set()
    for motif in motifs:
        for trouve in re.finditer(motif, txt):
            a = int(trouve.group(1))
            annees.add(a if a >= 100 else 2000 + a)
    return annees


def _occurrences_sans_annee(jour: int, mois: int, contenu: str):
    txt = contenu.lower()
    j, m, nom = _jour_motif(jour), rf"0?{mois}", MOIS_FR[mois - 1]
    for motif in (rf"(?<!\d){j}/{m}(?!\d)", rf"(?<!\d){j} {nom}(?!\w)"):
        yield from re.finditer(motif, txt)


def _recurrence_signalee(jour: int, mois: int, contenu: str) -> bool:
    """True si le jour/mois figure dans le texte à proximité d'un indice de
    calendrier qui se répète."""
    for occ in _occurrences_sans_annee(jour, mois, contenu):
        debut = max(0, occ.start() - FENETRE_RECURRENCE)
        fin = occ.end() + FENETRE_RECURRENCE
        if RECURRENCE_RE.search(contenu[debut:fin]):
            return True
    return False


def prochaine_occurrence(jour: int, mois: int, aujourdhui: date) -> date | None:
    """Prochaine date (aujourd'hui incluse) tombant ce jour/mois."""
    for annee in (aujourdhui.year, aujourdhui.year + 1, aujourdhui.year + 2):
        try:
            candidate = date(annee, mois, jour)
        except ValueError:  # 29 février
            continue
        if candidate >= aujourdhui:
            return candidate
    return None


def annee_a_retenir(actuelle: date, contenu: str, aujourdhui: date) -> int | None:
    """Année que devrait avoir cette date d'après le texte, ou None si on ne
    doit pas y toucher."""
    explicites = annees_explicites(actuelle.day, actuelle.month, contenu)
    if explicites:
        if actuelle.year in explicites:
            return None
        return next(iter(explicites)) if len(explicites) == 1 else None
    if _recurrence_signalee(actuelle.day, actuelle.month, contenu):
        prochaine = prochaine_occurrence(actuelle.day, actuelle.month, aujourdhui)
        if prochaine is not None and prochaine.year != actuelle.year:
            return prochaine.year
    return None


def _decaler(iso: str | None, annees: int) -> str | None:
    if not iso or annees == 0:
        return iso
    try:
        d = date.fromisoformat(iso)
        return d.replace(year=d.year + annees).isoformat()
    except ValueError:
        return iso


def _normaliser_periode(periode: dict, contenu: str, aujourdhui: date):
    """Corrige l'année de date_fin (et décale date_debut du même nombre
    d'années pour garder la fenêtre intacte). Retourne (ancienne, nouvelle)
    date_fin si modifiée, sinon None."""
    fin = periode.get("date_fin")
    if not fin:
        return None
    try:
        actuelle = date.fromisoformat(fin)
    except ValueError:
        return None
    annee = annee_a_retenir(actuelle, contenu, aujourdhui)
    if annee is None:
        return None
    try:
        nouvelle = actuelle.replace(year=annee)
    except ValueError:
        return None
    decalage = annee - actuelle.year
    periode["date_fin"] = nouvelle.isoformat()
    periode["date_debut"] = _decaler(periode.get("date_debut"), decalage)
    return fin, nouvelle.isoformat()


def normaliser_annees(raw_output: dict, contenu: str, today: str) -> tuple[dict, list[dict]]:
    """Applique les règles ci-dessus à toutes les étapes et fenêtres
    d'actions préalables. Retourne (raw_output modifié en place, liste des
    corrections pour le log)."""
    corrections: list[dict] = []
    commissions = raw_output.get("commissions")
    if not isinstance(commissions, list):
        return raw_output, corrections
    try:
        aujourdhui = date.fromisoformat(today)
    except ValueError:
        return raw_output, corrections

    for commission in commissions:
        if not isinstance(commission, dict):
            continue
        cibles = [(e, e.get("label")) for e in (commission.get("etapes") or []) if isinstance(e, dict)]
        for action in commission.get("actions_prealables") or []:
            f = action.get("fenetre") if isinstance(action, dict) else None
            if isinstance(f, dict):
                cibles.append((f, f"[action préalable] {f.get('label')}"))
        for periode, label in cibles:
            change = _normaliser_periode(periode, contenu, aujourdhui)
            if change:
                corrections.append({
                    "fonds": commission.get("fonds"), "label": label,
                    "avant": change[0], "apres": change[1],
                })
    return raw_output, corrections


# ---------------------------------------------------------------------------
# Date écrite dans le libellé de l'étape
# ---------------------------------------------------------------------------
#
# Cas réel (Cinémas 93) : libellé « Commission plénière (semaine du 12 octobre
# 2026) », date_fin 2026-10-18 (fin de semaine, inventée). Le filtre retirait
# l'étape et sa vraie date, pourtant écrite dans le libellé, était perdue.

def _date_dans_libelle(libelle: str | None) -> str | None:
    if not libelle:
        return None
    mois = "|".join(MOIS_FR)
    m = re.search(rf"(?<!\d)(1er|\d{{1,2}})\s+({mois})\s+(\d{{4}})(?!\d)", libelle.lower())
    if not m:
        return None
    try:
        jour = 1 if m.group(1) == "1er" else int(m.group(1))
        return date(int(m.group(3)), MOIS_FR.index(m.group(2)) + 1, jour).isoformat()
    except ValueError:
        return None


def recuperer_dates_du_libelle(raw_output: dict, contenu: str) -> tuple[dict, list[dict]]:
    """Pour chaque étape dont date_fin est absente du texte source : si son
    libellé contient une date complète (jour, mois, année) elle-même présente
    dans le texte, on l'utilise au lieu de perdre l'étape. À appeler AVANT
    filtrer_dates_non_confirmees. Retourne (raw_output, corrections)."""
    corrections: list[dict] = []
    commissions = raw_output.get("commissions")
    if not isinstance(commissions, list):
        return raw_output, corrections
    for commission in commissions:
        if not isinstance(commission, dict):
            continue
        for etape in commission.get("etapes") or []:
            if not isinstance(etape, dict) or date_confirmee(etape.get("date_fin"), contenu):
                continue
            candidate = _date_dans_libelle(etape.get("label"))
            if candidate and candidate != etape.get("date_fin") and date_confirmee(candidate, contenu):
                corrections.append({"fonds": commission.get("fonds"), "label": etape.get("label"),
                                    "avant": etape.get("date_fin"), "apres": candidate})
                etape["date_fin"] = candidate
    return raw_output, corrections
