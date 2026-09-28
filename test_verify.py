"""Tests de la vérification anti-hallucination de dates (verify.py) —
100% hors-ligne, aucun appel API, aucun coût.

Reproduit le cas réel découvert en production : Normandie, Session 3 -
2027, où le texte source dit "Présélection le 22/06/27, Plénière le
06/07/27" mais Qwen avait renvoyé 16/06/2027 et 30/06/2027 — deux dates
plausibles mais entièrement inventées, invisibles à toute validation
structurelle (JSON parfaitement valide).

Usage :
    python test_verify.py
"""

from __future__ import annotations

from verify import date_confirmee, filtrer_dates_non_confirmees, normaliser_annees, recuperer_dates_du_libelle

TEXTE_SOURCE_NORMANDIE = """Session 3 - 2027
rendez-vous préalables : du 19/04 au 30/04 2027
Après accord :
Dépôt des dossiers du 12/05/27 au 19/05/27
Présélection le 22/06/27
Plénière le 06/07/27"""


def run_cas_reel_normandie() -> None:
    print("Les 2 vraies dates du texte source sont confirmées")
    assert date_confirmee("2027-06-22", TEXTE_SOURCE_NORMANDIE) is True
    assert date_confirmee("2027-07-06", TEXTE_SOURCE_NORMANDIE) is True
    print("  -> OK\n")

    print("Les 2 dates inventées par Qwen ne sont PAS confirmées")
    assert date_confirmee("2027-06-16", TEXTE_SOURCE_NORMANDIE) is False
    assert date_confirmee("2027-06-30", TEXTE_SOURCE_NORMANDIE) is False
    print("  -> OK\n")

    print("Les dates de fenêtre ('du X au Y AAAA', espace avant l'année) sont confirmées")
    assert date_confirmee("2027-04-30", TEXTE_SOURCE_NORMANDIE) is True  # "30/04 2027"
    assert date_confirmee("2027-05-19", TEXTE_SOURCE_NORMANDIE) is True  # "19/05/27"
    print("  -> OK\n")

    print("Tous les tests 'cas réel Normandie' sont passés.\n")


def run_filtrage_complet() -> None:
    """Le filtrage complet doit retirer précisément les étapes inventées,
    sans toucher aux étapes réelles de la même commission."""
    raw = {
        "commissions": [{
            "fonds": "Fonds Normandie Images",
            "session_label": "Session 3 - 2027",
            "etapes": [
                {"label": "rendez-vous préalables", "date_debut": "2027-04-19", "date_fin": "2027-04-30"},
                {"label": "Dépôt des dossiers", "date_debut": "2027-05-12", "date_fin": "2027-05-19"},
                {"label": "Présélection", "date_debut": None, "date_fin": "2027-06-16"},  # inventée
                {"label": "Plénière", "date_debut": None, "date_fin": "2027-06-30"},      # inventée
            ],
            "actions_prealables": [],
        }]
    }

    reparee, retirees = filtrer_dates_non_confirmees(raw, TEXTE_SOURCE_NORMANDIE)

    dates_retirees = {r["date_fin"] for r in retirees}
    assert dates_retirees == {"2027-06-16", "2027-06-30"}, dates_retirees
    print("Dates retirées :", dates_retirees, "(attendu : les 2 inventées)\n")

    etapes_restantes = {e["date_fin"] for e in reparee["commissions"][0]["etapes"]}
    assert etapes_restantes == {"2027-04-30", "2027-05-19"}, etapes_restantes
    print("Étapes restantes :", etapes_restantes, "(attendu : les 2 vraies)\n")

    print("Tous les tests 'filtrage complet' sont passés.\n")


def run_fenetre_action_prealable() -> None:
    """Une fenêtre de date invalide dans une action préalable doit être
    retirée (mise à None), sans supprimer l'action préalable elle-même —
    l'obligation reste valide même si sa date précise n'est pas confirmée."""
    raw = {
        "commissions": [{
            "fonds": "Test",
            "etapes": [],
            "actions_prealables": [{
                "type_action": "RDV préalable",
                "condition_declenchement": "systématique",
                "fenetre": {"label": "RDV", "date_debut": None, "date_fin": "2099-01-01"},  # inventée
                "consequence_si_non_respect": "x",
                "lien_reservation": None,
            }],
        }]
    }
    reparee, retirees = filtrer_dates_non_confirmees(raw, TEXTE_SOURCE_NORMANDIE)
    assert len(retirees) == 1
    action = reparee["commissions"][0]["actions_prealables"][0]
    assert action["fenetre"] is None, "la fenêtre invalide doit être mise à None"
    assert action["type_action"] == "RDV préalable", "l'action elle-même ne doit PAS être supprimée"
    print("Fenêtre invalide retirée sans supprimer l'action préalable : OK\n")


def run_pas_de_faux_positif_sur_donnees_propres() -> None:
    """Aucune date valide ne doit jamais être retirée à tort — testé sur
    plusieurs formats réellement rencontrés dans les fixtures du projet."""
    cas = [
        ("2026-09-30", "échéance du 30 septembre 2026"),
        ("2026-01-31", "31 janvier, 15 avril et 30 septembre de chaque année"),
        ("2025-11-17", "17 novembre 2025, 10h00-18h00"),
        ("2026-09-01", "1er septembre 2026"),
        ("2027-01-19", "Date limite de dépôt des dossiers : 19 janvier 2027"),
    ]
    for date_iso, texte in cas:
        assert date_confirmee(date_iso, texte) is True, f"faux négatif sur {date_iso} / {texte!r}"
    print(f"{len(cas)} formats réels testés, aucun faux positif : OK\n")


def _com(*periodes, action_fenetre=None) -> dict:
    return {"commissions": [{
        "fonds": "F",
        "etapes": [{"label": l, "date_debut": d, "date_fin": f} for (l, d, f) in periodes],
        "actions_prealables": ([{"type_action": "x", "fenetre": action_fenetre}] if action_fenetre else []),
    }]}


def _fins(res) -> list:
    return [e["date_fin"] for e in res["commissions"][0]["etapes"]]


TEXTE_PACA = ("Respecter le calendrier permanent de dépôt des demandes : "
              "31 janvier, 15 avril et 30 septembre de chaque année.")


def run_paca_calendrier_recurrent() -> None:
    """Cas réel : le 30/09/2026 est dans 2 jours, le modèle l'avait mis en 2027."""
    raw = _com(("dépôt", None, "2027-01-31"), ("dépôt", None, "2027-04-15"), ("dépôt", None, "2027-09-30"))
    res, corr = normaliser_annees(raw, TEXTE_PACA, "2026-09-28")
    assert _fins(res) == ["2027-01-31", "2027-04-15", "2026-09-30"], _fins(res)
    assert len(corr) == 1 and corr[0]["avant"] == "2027-09-30" and corr[0]["apres"] == "2026-09-30", corr
    print("PACA : le 30 septembre redevient 2026 (dans 2 jours), les 2 autres sont inchangées : OK\n")

    raw = _com(("dépôt", None, "2026-09-30"))   # le 30/09 est passé -> la prochaine occurrence est 2027
    res, _ = normaliser_annees(raw, TEXTE_PACA, "2026-10-01")
    assert _fins(res) == ["2027-09-30"], _fins(res)
    print("PACA le 1er octobre : le 30 septembre devient 2027 (la prochaine occurrence) : OK\n")

    raw = _com(("dépôt", None, "2026-01-31"), ("dépôt", None, "2026-04-15"))   # années passées -> prochaines occurrences
    res, _ = normaliser_annees(raw, TEXTE_PACA, "2026-09-28")
    assert _fins(res) == ["2027-01-31", "2027-04-15"], _fins(res)
    print("PACA : des échéances récurrentes laissées dans le passé sont ramenées à l'occurrence à venir : OK\n")


def run_alca_pas_de_recurrence() -> None:
    """ALCA : 'les dépôts des 7 et 21 septembre sont clôturés' n'est PAS un
    calendrier qui se répète : ces dates passées ne doivent pas devenir 2027."""
    texte = ("Les dépôts des 7 et 21 septembre sont clôturés. Nous vous invitons à déposer "
             "ultérieurement pour la prochaine session, du 11 janvier 2027.")
    raw = _com(("clôturée", None, "2026-09-07"), ("clôturée", None, "2026-09-21"), ("prochaine", None, "2027-01-11"))
    res, corr = normaliser_annees(raw, texte, "2026-09-28")
    assert _fins(res) == ["2026-09-07", "2026-09-21", "2027-01-11"], _fins(res)
    assert corr == [], corr
    print("ALCA : dates passées sans indice de récurrence -> jamais transformées en 2027 : OK\n")


def run_bourgogne_fenetres_par_an() -> None:
    texte = "Deux dépôts par an :\n- du 1er au 28 février (comité de lecture en avril)\n- du 1er au 31 juillet (comité en septembre)"
    raw = _com(("dépôt 1", "2027-02-01", "2027-02-28"), ("dépôt 2", "2026-07-01", "2026-07-31"))
    res, corr = normaliser_annees(raw, texte, "2026-09-28")
    e = res["commissions"][0]["etapes"]
    assert (e[0]["date_debut"], e[0]["date_fin"]) == ("2027-02-01", "2027-02-28"), e[0]
    assert (e[1]["date_debut"], e[1]["date_fin"]) == ("2027-07-01", "2027-07-31"), e[1]   # fenêtre entière décalée
    assert len(corr) == 1
    print("Bourgogne : la fenêtre 01/07 -> 31/07 passée en 2026 est décalée en bloc en 2027 : OK\n")


def run_annee_explicite_du_texte() -> None:
    raw = _com(("dépôt", None, "2026-11-17"))
    res, _ = normaliser_annees(raw, "Session 1 | 17 novembre 2025, 10h00-18h00", "2026-09-28")
    assert _fins(res) == ["2025-11-17"], _fins(res)
    print("Année écrite une seule fois dans le texte : elle l'emporte sur celle du modèle : OK\n")

    texte = "Animation : 1er février 2026, 1er juin 2026, 1er février 2027."
    raw = _com(("dépôt", None, "2028-02-01"))
    res, corr = normaliser_annees(raw, texte, "2026-09-28")
    assert _fins(res) == ["2028-02-01"] and corr == []
    print("Plusieurs années différentes dans le texte : ambigu, on ne touche pas : OK\n")

    raw = _com(("dépôt", None, "2027-01-19"))
    res, corr = normaliser_annees(raw, "Date limite : 19 janvier 2027", "2026-09-28")
    assert _fins(res) == ["2027-01-19"] and corr == []
    print("Année déjà correcte : inchangée : OK\n")


def run_fenetre_action_prealable_et_cas_limites() -> None:
    f = {"label": "RDV", "date_debut": "2026-07-01", "date_fin": "2026-07-31"}
    raw = _com(action_fenetre=f)
    res, corr = normaliser_annees(raw, "Deux dépôts par an : du 1er au 31 juillet", "2026-09-28")
    fen = res["commissions"][0]["actions_prealables"][0]["fenetre"]
    assert (fen["date_debut"], fen["date_fin"]) == ("2027-07-01", "2027-07-31"), fen
    print("Fenêtre d'action préalable : corrigée comme une étape : OK\n")

    # cas limites : rien ne doit planter
    raw = {"commissions": [{"etapes": [{"label": "x", "date_debut": None, "date_fin": "pas-une-date"},
                                       {"label": "y", "date_debut": None, "date_fin": None},
                                       {"label": "z", "date_debut": None, "date_fin": "2028-02-29"}]}]}
    normaliser_annees(raw, "29 février chaque année", "2026-09-28")
    normaliser_annees({"commissions": "pas une liste"}, "x", "2026-09-28")
    normaliser_annees({"commissions": []}, "x", "date-invalide")
    print("Cas limites (date invalide, None, 29 février, structure inattendue) : pas de plantage : OK\n")


def run_annuellement_n_est_pas_une_recurrence() -> None:
    """Cinémas 93 : « sessions organisées annuellement » décrit un nombre de
    sessions, pas un calendrier qui se répète : le 7 septembre 2026 (passé)
    ne doit pas devenir 2027."""
    texte = ("Une à deux sessions de candidature sont organisées annuellement. "
             "Commission de présélection : du 7 septembre au 30 septembre 2026.")
    res, corr = normaliser_annees(_com(("présélection", "2026-09-07", "2026-09-30")), texte, "2026-09-28")
    assert corr == [] and _fins(res) == ["2026-09-30"]
    res, corr = normaliser_annees(_com(("x", None, "2026-09-07")), texte, "2026-09-28")
    assert corr == [] and _fins(res) == ["2026-09-07"]
    print("« annuellement » (nombre de sessions) n'est pas un indice de récurrence : OK\n")


def run_bornes_de_chiffres() -> None:
    # "22/06" reste accepté sans année (compromis assumé de date_confirmee),
    # mais la normalisation rattrape alors l'année fausse grâce au texte :
    res, _ = normaliser_annees(_com(("présélection", None, "2020-06-22")), "Présélection le 22/06/2027", "2026-09-28")
    assert _fins(res) == ["2027-06-22"], _fins(res)
    assert date_confirmee("2026-05-05", "Dépôt le 15 mai 2026") is False           # '5 mai' n'est pas dans '15 mai'
    assert date_confirmee("2026-05-15", "Dépôt le 15 mai 2026") is True
    print("Bornes de chiffres : '5 mai' ne confirme plus '15 mai' ; une année fausse est rattrapée par le texte : OK\n")


def run_date_reprise_du_libelle() -> None:
    """Cinémas 93 : libellé « semaine du 12 octobre 2026 », date_fin inventée
    2026-10-18 -> on reprend la date écrite dans le libellé au lieu de perdre
    l'étape."""
    texte = ("Annonce des résultats de la présélection : semaine du 5 octobre 2026\n"
             "Commission plénière : semaine du 12 octobre 2026\n"
             "Annonce des résultats définitifs : janvier 2027")
    raw = _com(("Annonce présélection (semaine du 5 octobre 2026)", None, "2026-10-11"),
               ("Commission plénière (semaine du 12 octobre 2026)", None, "2026-10-18"),
               ("Annonce des résultats définitifs (janvier 2027)", None, "2027-01-31"))
    raw, corr = recuperer_dates_du_libelle(raw, texte)
    assert _fins(raw) == ["2026-10-05", "2026-10-12", "2027-01-31"], _fins(raw)
    assert len(corr) == 2
    raw, retirees = filtrer_dates_non_confirmees(raw, texte)
    assert [r["date_fin"] for r in retirees] == ["2027-01-31"], retirees   # 'janvier 2027' sans jour : toujours retirée
    assert _fins(raw) == ["2026-10-05", "2026-10-12"]
    print("Cinémas 93 : 5 et 12 octobre repris du libellé, « janvier 2027 » (sans jour) reste retirée : OK\n")

    # une date de libellé absente du texte ne doit PAS être utilisée
    raw = _com(("Comité (le 3 mars 2030)", None, "2026-10-18"))
    raw, corr = recuperer_dates_du_libelle(raw, "Comité en octobre")
    assert corr == [] and _fins(raw) == ["2026-10-18"]
    # et une date déjà confirmée n'est jamais modifiée
    raw = _com(("Comité (le 3 mars 2030)", None, "2026-10-18"))
    raw, corr = recuperer_dates_du_libelle(raw, "Comité le 18 octobre 2026 ; 3 mars 2030")
    assert corr == [] and _fins(raw) == ["2026-10-18"]
    print("Date de libellé non confirmée par le texte, ou date déjà valide : jamais touchée : OK\n")


if __name__ == "__main__":
    run_cas_reel_normandie()
    run_filtrage_complet()
    run_fenetre_action_prealable()
    run_pas_de_faux_positif_sur_donnees_propres()
    run_paca_calendrier_recurrent()
    run_alca_pas_de_recurrence()
    run_bourgogne_fenetres_par_an()
    run_annee_explicite_du_texte()
    run_fenetre_action_prealable_et_cas_limites()
    run_annuellement_n_est_pas_une_recurrence()
    run_bornes_de_chiffres()
    run_date_reprise_du_libelle()
    print("TOUS LES TESTS VERIFY.PY SONT PASSÉS.")
