"""Tests de la 2e chance d'extraction quand verify.py retire des dates
inventées — 100% hors-ligne (les appels au LLM sont simulés), zéro coût.

Cas réel : Normandie Session 3 - 2027. Le texte dit "Présélection le
22/06/27, Plénière le 06/07/27"; Qwen renvoie 16/06 et 30/06 (inventées),
le filtre les retire, et la vraie plénière était perdue. La 2e passe doit
pouvoir la récupérer.

Usage :
    python test_retry.py
"""

from __future__ import annotations

import os

os.environ["PROVIDER"] = "qwen"
os.environ["DRY_RUN"] = "false"

import budget  # noqa: E402
import extract  # noqa: E402

BUDGET_TEST = "data/budget_test.json"
budget.BUDGET_PATH = BUDGET_TEST
# Les fonctions de budget.py ont BUDGET_PATH en valeur par défaut figée :
# on force le chemin de test pour ne pas polluer le vrai data/budget.json.
_orig_verifier, _orig_enregistrer = extract.verifier_budget, extract.enregistrer_depense
extract.verifier_budget = lambda c: _orig_verifier(c, BUDGET_TEST)
extract.enregistrer_depense = lambda c: _orig_enregistrer(c, BUDGET_TEST)

URL = "https://inscriptionsfondsaide.normandieimages.net/fonds-num/26"
TEXTE = """Session 3 - 2027
rendez-vous préalables : du 19/04 au 30/04 2027
Après accord :
Dépôt des dossiers du 12/05/27 au 19/05/27
Présélection le 22/06/27
Plénière le 06/07/27"""


def sortie(presel: str, pleniere: str) -> dict:
    def e(label, fin, debut=None):
        return {"label": label, "date_debut": debut, "date_fin": fin, "heure_limite": None}

    return {
        "commissions": [{
            "region": "Normandie", "fonds": "Fonds Normandie Images", "organisme": "Normandie Images",
            "typologie_oeuvre": "court métrage", "etape_filiere": "production",
            "session_label": "Session 3 - 2027",
            "etapes": [
                e("rendez-vous préalables", "2027-04-30", "2027-04-19"),
                e("Dépôt des dossiers", "2027-05-19", "2027-05-12"),
                e("Présélection", presel),
                e("Plénière", pleniere),
            ],
            "actions_prealables": [], "conditions_eliminatoires": [],
            "montant_min_eur": None, "montant_max_eur": None, "lien_formulaire": None,
            "url_source": URL,
        }],
    }


def brancher(sorties: list):
    """Remplace l'appel Qwen : renvoie les sorties dans l'ordre et mémorise
    les prompts reçus."""
    prompts: list[str] = []

    def faux(user_prompt):
        prompts.append(user_prompt)
        i = len(prompts) - 1
        s = sorties[i]
        if isinstance(s, Exception):
            raise s
        return s, 0.001

    extract._extract_qwen = faux
    return prompts


def dates(result) -> dict:
    return {e.label: e.date_fin for e in result.commissions[0].etapes}


def nettoyer() -> None:
    if os.path.exists(BUDGET_TEST):
        os.remove(BUDGET_TEST)


def run_2e_passe_corrige() -> None:
    nettoyer()
    prompts = brancher([
        sortie("2027-06-16", "2027-06-30"),   # 1re passe : 2 dates inventées
        sortie("2027-06-22", "2027-07-06"),   # 2e passe : les vraies
    ])
    result = extract.extract(TEXTE, url=URL, region="Normandie")
    d = dates(result)

    assert len(prompts) == 2, f"attendu 2 appels, obtenu {len(prompts)}"
    assert "N'EXISTENT PAS" in prompts[1] and "16/06/2027" in prompts[1] and "30/06/2027" in prompts[1], \
        "le 2e prompt doit lister les dates inventées"
    assert "CORRECTION" not in prompts[0], "le 1er prompt ne doit pas contenir de correction"
    assert d["Présélection"] == "2027-06-22" and d["Plénière"] == "2027-07-06", d
    assert len(d) == 4
    print("2e passe corrige : la plénière du 06/07/2027 est récupérée, 2 appels : OK\n")


def run_2e_passe_pas_meilleure() -> None:
    nettoyer()
    prompts = brancher([
        sortie("2027-06-16", "2027-06-30"),   # 1re passe : 2 inventées
        sortie("2027-06-17", "2027-06-29"),   # 2e passe : 2 autres inventées
    ])
    result = extract.extract(TEXTE, url=URL, region="Normandie")
    d = dates(result)
    assert len(prompts) == 2
    assert set(d) == {"rendez-vous préalables", "Dépôt des dossiers"}, d  # aucune date inventée ne passe
    print("2e passe toujours fausse : aucune date inventée ne passe, 2 appels : OK\n")


def run_2e_passe_plante() -> None:
    nettoyer()
    prompts = brancher([
        sortie("2027-06-16", "2027-06-30"),
        RuntimeError("erreur réseau simulée"),
    ])
    result = extract.extract(TEXTE, url=URL, region="Normandie")
    d = dates(result)
    assert len(prompts) == 2
    assert set(d) == {"rendez-vous préalables", "Dépôt des dossiers"}, d
    print("2e passe qui plante : résultat de la 1re passe conservé, pas de crash : OK\n")


def run_2e_passe_structure_illisible() -> None:
    """Cas réel Cinémas 93 : la 2e passe renvoyait 'commissions' comme une
    chaîne inexploitable et était pourtant retenue -> toute la source perdue.
    Elle doit être écartée et la 1re passe conservée."""
    nettoyer()
    prompts = brancher([
        sortie("2027-06-16", "2027-06-30"),
        {"commissions": "ceci n'est pas du JSON {", "aucune_date_trouvee": False},
    ])
    result = extract.extract(TEXTE, url=URL, region="Normandie")
    assert len(prompts) == 2
    assert set(dates(result)) == {"rendez-vous préalables", "Dépôt des dossiers"}
    print("2e passe illisible : écartée, 1re passe conservée, source non perdue : OK\n")


def run_2e_passe_moins_d_etapes() -> None:
    """Une 2e passe qui 'corrige' en rendant beaucoup moins d'étapes n'est
    pas meilleure : on ne perd pas de dates valides pour en retirer une."""
    nettoyer()
    seule = sortie("2027-06-22", "2027-07-06")
    seule["commissions"][0]["etapes"] = seule["commissions"][0]["etapes"][:1]  # 1 seule étape
    prompts = brancher([sortie("2027-06-16", "2027-07-06"), seule])  # 1re : 1 date inventée, 3 conservées
    result = extract.extract(TEXTE, url=URL, region="Normandie")
    assert len(prompts) == 2
    assert len(dates(result)) == 3, dates(result)
    print("2e passe avec moins d'étapes : non retenue, dates valides conservées : OK\n")


def run_pas_de_2e_passe_si_tout_va_bien() -> None:
    nettoyer()
    prompts = brancher([sortie("2027-06-22", "2027-07-06")])
    result = extract.extract(TEXTE, url=URL, region="Normandie")
    assert len(prompts) == 1, f"aucun appel supplémentaire attendu, obtenu {len(prompts)}"
    assert len(dates(result)) == 4
    print("Aucune date retirée : un seul appel (pas de surcoût) : OK\n")


def run_budget_bloque_la_2e_passe() -> None:
    """Si le budget interdit la 2e passe, elle ne part pas et la 1re passe
    est conservée sans crash."""
    nettoyer()
    appels = {"n": 0}

    def verifier_qui_bloque_le_2e(cout):
        appels["n"] += 1
        if appels["n"] >= 2:
            raise budget.BudgetExceededError("budget simulé dépassé")

    prompts = brancher([sortie("2027-06-16", "2027-06-30"), sortie("2027-06-22", "2027-07-06")])
    extract.verifier_budget = verifier_qui_bloque_le_2e
    try:
        result = extract.extract(TEXTE, url=URL, region="Normandie")
    finally:
        extract.verifier_budget = lambda c: _orig_verifier(c, BUDGET_TEST)
    assert len(prompts) == 1, "la 2e passe bloquée par le budget ne doit pas partir"
    assert set(dates(result)) == {"rendez-vous préalables", "Dépôt des dossiers"}
    print("Budget dépassé : la 2e passe est bloquée, la 1re est conservée, pas de crash : OK\n")


if __name__ == "__main__":
    try:
        run_2e_passe_corrige()
        run_2e_passe_pas_meilleure()
        run_2e_passe_plante()
        run_2e_passe_structure_illisible()
        run_2e_passe_moins_d_etapes()
        run_pas_de_2e_passe_si_tout_va_bien()
        run_budget_bloque_la_2e_passe()
    finally:
        nettoyer()
    print("TOUS LES TESTS RETRY SONT PASSÉS.")
