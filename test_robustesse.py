"""Tests des sorties abîmées de Qwen observées en conditions réelles, 100%
hors-ligne (appels LLM simulés), zéro coût.

  - Ciclic      : 'consequence_si_non_respect': null (champ texte obligatoire)
  - Cinémas 93  : 'commissions' renvoyé comme chaîne JSON (contrôle/apostrophes)
  - Pictanovo   : arguments de l'outil illisibles (« Expecting value: line 1
                  column 17 ») -> décodage tolérant, puis un nouvel essai

Usage :
    python test_robustesse.py
"""

from __future__ import annotations

import os

os.environ["PROVIDER"] = "qwen"
os.environ["DRY_RUN"] = "false"

import extract  # noqa: E402
from schema import ExtractionResult  # noqa: E402

TEST_BUDGET = "data/budget_test_rob.json"
_v, _e = extract.verifier_budget, extract.enregistrer_depense
extract.verifier_budget = lambda c: _v(c, TEST_BUDGET)
extract.enregistrer_depense = lambda c: _e(c, TEST_BUDGET)

URL = "https://ex.fr/page"


def commission(**over) -> dict:
    c = {
        "region": "X", "fonds": "F", "organisme": "O", "typologie_oeuvre": "t", "etape_filiere": "production",
        "session_label": "S1", "etapes": [{"label": "Dépôt", "date_debut": None, "date_fin": "2026-11-01", "heure_limite": None}],
        "actions_prealables": [], "conditions_eliminatoires": [], "url_source": URL,
    }
    c.update(over)
    return c


def run_champ_texte_null_ciclic() -> None:
    raw = {"commissions": [commission(actions_prealables=[{
        "type_action": "solliciter l'accueil de tournage", "condition_declenchement": "systématique",
        "consequence_si_non_respect": None, "fenetre": None, "lien_reservation": None,
    }])], "aucune_date_trouvee": False, "lien_pdf_calendrier": "None"}
    r = ExtractionResult.model_validate(extract._reparer_structure(raw, "2026-09-28", URL, "X"))
    assert r.commissions[0].actions_prealables[0].consequence_si_non_respect == "non précisé"
    assert r.lien_pdf_calendrier is None
    print("Ciclic : champ texte à null -> 'non précisé', la source n'est plus perdue : OK\n")


def run_commissions_en_chaine_abimee() -> None:
    import json
    inner = json.dumps([commission(fonds="Fonds avec\nretour à la ligne")], ensure_ascii=False)
    # un vrai saut de ligne dans une valeur : json.loads strict le refuse
    inner = inner.replace("\\n", "\n")
    try:
        json.loads(inner)
        strict_refuse = False
    except json.JSONDecodeError:
        strict_refuse = True
    assert strict_refuse, "le cas de test doit être invalide pour un décodage strict"
    r = ExtractionResult.model_validate(extract._reparer_structure({"commissions": inner}, "2026-09-28", URL, "X"))
    assert len(r.commissions) == 1
    print("Cinémas 93 : chaîne JSON avec saut de ligne brut -> décodée quand même : OK\n")


def run_decodage_tolerant() -> None:
    d = extract._decoder_json_tolerant("{'commissions': None, 'aucune_date_trouvee': True}")
    assert d == {"commissions": None, "aucune_date_trouvee": True}, d
    assert extract._decoder_json_tolerant("{\"a\": \"x\ny\"}") == {"a": "x\ny"}
    assert extract._decoder_json_tolerant("n'importe quoi {") is None
    assert extract._decoder_json_tolerant("__import__('os').system('echo pwn')") is None  # rien n'est exécuté
    print("Décodage tolérant : syntaxe Python OK, contrôle OK, texte inexploitable -> None, rien d'exécuté : OK\n")


def run_sortie_illisible_puis_ok() -> None:
    appels = {"n": 0}

    def faux(prompt):
        appels["n"] += 1
        if appels["n"] == 1:
            raise extract.SortieIllisible("arguments illisibles simulés")
        return {"commissions": [commission()], "aucune_date_trouvee": False}, 0.001

    extract._extract_qwen = faux
    r = extract.extract("Dépôt le 01/11/2026", url=URL, region="X")
    assert appels["n"] == 2 and len(r.commissions) == 1
    print("Pictanovo : sortie illisible -> un nouvel essai, la source est récupérée : OK\n")


def run_sortie_illisible_deux_fois() -> None:
    def faux(prompt):
        raise extract.SortieIllisible("toujours illisible")

    extract._extract_qwen = faux
    try:
        extract.extract("texte", url=URL, region="X")
        assert False, "aurait dû échouer proprement"
    except extract.SortieIllisible:
        pass
    print("Toujours illisible : échec propre et explicite (récapitulé par main.py), sans boucle : OK\n")


def run_paca_bout_en_bout() -> None:
    """Cas réel : sur la page PACA, le modèle avait renvoyé le 30 septembre en
    2027 alors que le 30/09/2026 était dans 2 jours. Passage complet par
    extract() avec le vrai texte de la page, à une date du jour fixée."""
    import datetime as reel

    class FausseDate(reel.date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 28)

    class FauxDatetime:
        date = FausseDate

    texte = open("test_data/region_sud.txt", encoding="utf-8").read()
    sortie = {"commissions": [commission(
        etapes=[{"label": "dépôt des dossiers", "date_debut": None, "date_fin": f, "heure_limite": None}
                for f in ("2027-01-31", "2027-04-15", "2027-09-30")])],
        "aucune_date_trouvee": False}

    extract._extract_qwen = lambda prompt: (sortie, 0.001)
    vrai, extract.datetime = extract.datetime, FauxDatetime
    try:
        r = extract.extract(texte, url=URL, region="X")
    finally:
        extract.datetime = vrai
    fins = sorted(e.date_fin for e in r.commissions[0].etapes)
    assert fins == ["2026-09-30", "2027-01-31", "2027-04-15"], fins
    print("PACA de bout en bout : le 30/09/2026 (dans 2 jours) est de retour sur le site : OK\n")


def run_sauvetage_champ_par_champ() -> None:
    """Ciclic a échoué deux fois pour deux champs différents. Un seul champ
    fautif ne doit plus faire perdre toute la source : on retire la plus
    petite partie fautive et on garde tout le reste."""
    raw = {"commissions": [commission(
        etapes=[
            {"label": "dépôt", "date_debut": None, "date_fin": "2026-11-01", "heure_limite": None},
            {"label": "comité", "date_debut": None, "date_fin": 2026, "heure_limite": None},       # date_fin non texte
        ],
        actions_prealables=[
            {"type_action": "RDV", "condition_declenchement": "systématique", "consequence_si_non_respect": "x",
             "fenetre": {"label": "RDV", "date_debut": None, "date_fin": None}, "lien_reservation": None},   # fenêtre sans date_fin
            {"type_action": "Formulaire", "condition_declenchement": ["liste", "au lieu de texte"],
             "consequence_si_non_respect": "x", "fenetre": None, "lien_reservation": None},               # texte invalide
        ],
        conditions_eliminatoires=["ok", None, "ok 2"],                                                    # None dans une liste de textes
        montant_max_eur="15 000 €",                                                                       # nombre attendu
    )], "aucune_date_trouvee": False}
    r = extract._valider_avec_sauvetage(extract._reparer_structure(raw, "2026-09-28", URL, "X"))
    c = r.commissions[0]
    assert [e.label for e in c.etapes] == ["dépôt"], "l'étape valide reste, l'étape invalide est retirée"
    assert len(c.actions_prealables) == 2, "aucune action préalable perdue"
    assert c.actions_prealables[0].fenetre is None and c.actions_prealables[0].type_action == "RDV"
    assert c.actions_prealables[1].condition_declenchement == "non précisé"
    assert c.conditions_eliminatoires == ["ok", "ok 2"]
    assert c.montant_max_eur is None
    print("Sauvetage : 5 champs fautifs différents corrigés un par un, la source et ses données valides sont conservées : OK\n")


def run_sauvetage_ne_masque_pas_une_vraie_erreur() -> None:
    from pydantic import ValidationError
    try:
        extract._valider_avec_sauvetage({"commissions": "vraiment n'importe quoi", "aucune_date_trouvee": False})
        assert False, "une structure inexploitable doit toujours échouer"
    except ValidationError:
        pass
    print("Structure réellement inexploitable : l'échec reste explicite, rien n'est inventé : OK\n")


def run_recap_donne_les_champs_fautifs() -> None:
    import main
    from pydantic import ValidationError
    try:
        ExtractionResult.model_validate({"commissions": [{"region": "X"}], "aucune_date_trouvee": False})
    except ValidationError as exc:
        raison = main._raison_courte(exc)
    assert "commissions.0.fonds" in raison and "missing" in raison, raison
    print("Récapitulatif de fin : indique le champ fautif exact (", raison[:70], "...) : OK\n")


def run_reponse_vide_ciclic() -> None:
    """Ciclic : {"commissions": null} alors que la page contient des sessions.
    Un nouvel essai, puis, si toujours vide, « aucune date trouvée » et non un
    échec de la source."""
    appels = {"n": 0}

    def faux_puis_ok(prompt):
        appels["n"] += 1
        if appels["n"] == 1:
            return {"commissions": None}, 0.001
        assert "VIDE" in prompt, "le 2e essai doit signaler que la réponse précédente était vide"
        return {"commissions": [commission()], "aucune_date_trouvee": False}, 0.001

    extract._extract_qwen = faux_puis_ok
    r = extract.extract("Dépôt le 01/11/2026", url=URL, region="X")
    assert appels["n"] == 2 and len(r.commissions) == 1
    print("Ciclic : réponse vide -> un nouvel essai, la source est récupérée : OK\n")

    appels["n"] = 0
    extract._extract_qwen = lambda prompt: (appels.__setitem__("n", appels["n"] + 1) or {"commissions": None}, 0.001)
    r = extract.extract("Dépôt le 01/11/2026", url=URL, region="X")
    assert appels["n"] == 2 and r.commissions == [] and r.aucune_date_trouvee is True
    print("Toujours vide : traitée comme « aucune date trouvée », pas comme un échec : OK\n")

    appels["n"] = 0
    extract._extract_qwen = lambda prompt: (appels.__setitem__("n", appels["n"] + 1) or {"commissions": [], "aucune_date_trouvee": True}, 0.001)
    extract.extract("Aucune date ici", url=URL, region="X")
    assert appels["n"] == 1, "une source déclarée sans date ne doit pas être retentée (pas de surcoût)"
    print("Source déclarée sans date (ex Bretagne) : un seul appel, pas de surcoût : OK\n")


def run_dedoublonnage_bourgogne() -> None:
    """Bourgogne : la même page renvoyée 3 fois (libellés null, Session 1,
    Session 2)."""
    etapes = [{"label": "dépôt", "date_debut": "2027-02-01", "date_fin": "2027-02-28", "heure_limite": None}]
    raw = {"commissions": [commission(session_label=s, etapes=[dict(e) for e in etapes]) for s in (None, "Session 1", "Session 2")],
           "aucune_date_trouvee": False}
    extract._extract_qwen = lambda prompt: (raw, 0.001)
    r = extract.extract("Dépôt du 1er au 28 février, deux dépôts par an", url=URL, region="X")
    assert len(r.commissions) == 1 and r.commissions[0].session_label is None
    print("Bourgogne : 3 fiches identiques fusionnées en 1 (celle sans libellé de session inventé) : OK\n")

    # des commissions qui diffèrent par leurs dates ne doivent JAMAIS être fusionnées
    a = commission(session_label="S1", etapes=[{"label": "d", "date_debut": None, "date_fin": "2026-11-01", "heure_limite": None}])
    b = commission(session_label="S2", etapes=[{"label": "d", "date_debut": None, "date_fin": "2027-03-01", "heure_limite": None}])
    raw = {"commissions": [a, b], "aucune_date_trouvee": False}
    extract._extract_qwen = lambda prompt: (raw, 0.001)
    r = extract.extract("Dépôts le 01/11/2026 et le 01/03/2027", url=URL, region="X")
    assert len(r.commissions) == 2
    print("Sessions aux dates différentes : jamais fusionnées : OK\n")


def run_commissions_sans_aucune_date() -> None:
    """Normandie page principale / Occitanie : des commissions existent mais
    n'ont plus aucune date. Un nouvel essai ; sinon « aucune date trouvée »
    (lien PDF conservé pour le fallback), jamais des fiches vides dans l'état."""
    vide = {"commissions": [commission(etapes=[], session_label="S1"), commission(etapes=[], session_label="S2")],
            "aucune_date_trouvee": False, "lien_pdf_calendrier": "https://x.fr/cal.pdf"}
    appels = {"n": 0}

    def puis_ok(prompt):
        appels["n"] += 1
        if appels["n"] == 1:
            return vide, 0.001
        assert "VIDE" in prompt and "lien_pdf_calendrier" in prompt
        return {"commissions": [commission()], "aucune_date_trouvee": False}, 0.001

    extract._extract_qwen = puis_ok
    r = extract.extract("Dépôt le 01/11/2026", url=URL, region="X")
    assert appels["n"] == 2 and len(r.commissions) == 1 and r.commissions[0].etapes
    print("Commissions sans date -> un nouvel essai, la source est récupérée : OK\n")

    appels["n"] = 0
    extract._extract_qwen = lambda p: (appels.__setitem__("n", appels["n"] + 1) or vide, 0.001)
    r = extract.extract("Dépôt le 01/11/2026", url=URL, region="X")
    assert appels["n"] == 2 and r.commissions == [] and r.aucune_date_trouvee is True
    assert r.lien_pdf_calendrier == "https://x.fr/cal.pdf", "le lien PDF doit rester disponible pour le fallback"
    print("Toujours sans date : « aucune date trouvée », lien PDF conservé : OK\n")

    # dates toutes inventées : 2e passe corrective déjà faite -> pas de 3e passe
    appels["n"] = 0
    inventees = {"commissions": [commission(etapes=[{"label": "x", "date_debut": None, "date_fin": "2031-04-02", "heure_limite": None}])],
                 "aucune_date_trouvee": False}
    extract._extract_qwen = lambda p: (appels.__setitem__("n", appels["n"] + 1) or inventees, 0.001)
    r = extract.extract("Aucune date ici", url=URL, region="X")
    assert appels["n"] == 2, f"pas de 3e passe attendue, {appels['n']} appels"
    assert r.commissions == [] and r.aucune_date_trouvee is True
    print("Dates toutes inventées : 2 appels maximum, puis « aucune date trouvée » : OK\n")

    import main
    assert main._a_des_dates(commission()) and not main._a_des_dates(commission(etapes=[]))
    print("main.py : une commission sans date n'entre jamais dans l'état : OK\n")


if __name__ == "__main__":
    try:
        run_champ_texte_null_ciclic()
        run_commissions_en_chaine_abimee()
        run_decodage_tolerant()
        run_sortie_illisible_puis_ok()
        run_sortie_illisible_deux_fois()
        run_paca_bout_en_bout()
        run_sauvetage_champ_par_champ()
        run_sauvetage_ne_masque_pas_une_vraie_erreur()
        run_recap_donne_les_champs_fautifs()
        run_reponse_vide_ciclic()
        run_dedoublonnage_bourgogne()
        run_commissions_sans_aucune_date()
    finally:
        if os.path.exists(TEST_BUDGET):
            os.remove(TEST_BUDGET)
    print("TOUS LES TESTS ROBUSTESSE SONT PASSÉS.")
