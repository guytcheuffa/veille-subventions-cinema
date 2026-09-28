"""Tests du suivi par date individuelle (alerts.py) — 100% hors-ligne,
aucun appel API, aucun coût.

Usage :
    python test_alerts.py
"""

from __future__ import annotations

from alerts import STATUT_NOUVELLE, STATUT_VIENT_DE_FERMER, echeances_proches, update_watch

URL = "https://cnc.fr/x"


def commission(fonds: str, session: str | None, dates_labels: list[tuple[str, str]]) -> dict:
    return {
        "region": "National", "fonds": fonds, "organisme": "CNC", "session_label": session,
        "etapes": [{"label": l, "date_fin": d} for l, d in dates_labels],
        "url_source": URL,
    }


def run_pas_de_bruit_sur_redecoupage() -> None:
    """Le scénario réel observé sur le CNC : des sessions se redécoupent
    différemment d'un run à l'autre. Le suivi par date ne doit générer
    AUCUNE fausse alerte dans ce cas, contrairement au suivi par commission."""
    print("Run 1 — 3 sessions séparées (2 passées, 1 future)")
    watch: dict = {}
    run1 = [
        commission("AVR1", "Session 5", [("Dépôt", "2026-06-17")]),
        commission("AVR1", "Session 6", [("Dépôt", "2026-09-03")]),
        commission("AVR1", "Session 1-2027", [("Dépôt", "2026-11-20")]),
    ]
    watch, alertes = update_watch(watch, run1, today="2026-09-27", processed_url_sources={URL})
    assert [a["type"] for a in alertes] == [STATUT_NOUVELLE], alertes
    print("  ->", [a["type"] for a in alertes], "(seule la date future génère une alerte)\n")

    print("Run 2 — le LLM combine tout en UNE commission (redécoupage)")
    run2 = [commission("AVR1", None, [("Dépôt", "2026-11-20")])]
    watch, alertes = update_watch(watch, run2, today="2026-09-27", processed_url_sources={URL})
    assert alertes == [], alertes
    print("  ->", alertes, "(aucun bruit malgré le redécoupage)\n")

    print("Run 3 — la date future disparaît pour de vrai")
    watch, alertes = update_watch(watch, [], today="2026-10-05", processed_url_sources={URL})
    assert [a["type"] for a in alertes] == [STATUT_VIENT_DE_FERMER], alertes
    print("  ->", [a["type"] for a in alertes], "(vraie fermeture toujours détectée)\n")

    print("Tous les tests 'pas de bruit sur redécoupage' sont passés.\n")


def run_nouvelle_date_detectee() -> None:
    """Une vraie nouvelle date qui apparaît doit être signalée — y compris
    quand elle appartient à une commission déjà connue (une session ajoute
    une échéance supplémentaire)."""
    print("Run 1 — une première date")
    watch: dict = {}
    watch, alertes = update_watch(watch, [commission("Fonds X", "S1", [("Dépôt", "2026-12-01")])], today="2026-09-27", processed_url_sources={URL})
    assert [a["type"] for a in alertes] == [STATUT_NOUVELLE], alertes
    print("  ->", [a["type"] for a in alertes], "\n")

    print("Run 2 — une deuxième date apparaît sur la même commission")
    watch, alertes = update_watch(
        watch,
        [commission("Fonds X", "S1", [("Dépôt", "2026-12-01"), ("Commission", "2027-01-15")])],
        today="2026-10-01", processed_url_sources={URL},
    )
    assert [a["type"] for a in alertes] == [STATUT_NOUVELLE], alertes
    assert alertes[0]["date_fin"] == "2027-01-15"
    print("  ->", [a["type"] for a in alertes], "pour la date", alertes[0]["date_fin"], "\n")

    print("Tous les tests 'nouvelle date détectée' sont passés.\n")


def run_dates_passees_ignorees() -> None:
    """Une date déjà passée qui apparaît ou disparaît ne doit JAMAIS
    générer d'alerte — aucune valeur informative pour un visiteur."""
    print("Une date déjà passée apparaît pour la première fois")
    watch: dict = {}
    watch, alertes = update_watch(watch, [commission("Fonds X", "S1", [("Dépôt", "2026-01-01")])], today="2026-09-27", processed_url_sources={URL})
    assert alertes == [], alertes
    print("  ->", alertes, "(pas d'alerte pour une date déjà passée)\n")

    print("Cette même date disparaît ensuite")
    watch, alertes = update_watch(watch, [], today="2026-10-01", processed_url_sources={URL})
    assert alertes == [], alertes
    print("  ->", alertes, "(pas d'alerte non plus à sa disparition)\n")

    print("Tous les tests 'dates passées ignorées' sont passés.\n")


def run_echeances_proches() -> None:
    """echeances_proches() retourne les échéances actives dans une fenêtre
    de N jours, triées chronologiquement."""
    watch: dict = {}
    watch, _ = update_watch(
        watch,
        [commission("Fonds X", "S1", [
            ("Dépôt A", "2026-10-01"),   # dans la fenêtre 7j depuis le 2026-09-27
            ("Dépôt B", "2026-10-20"),   # hors fenêtre
            ("Dépôt C", "2026-09-29"),   # dans la fenêtre
        ])],
        today="2026-09-27", processed_url_sources={URL},
    )
    proches = echeances_proches(watch, today="2026-09-27", jours=7)
    dates = [e["date_fin"] for e in proches]
    assert dates == ["2026-09-29", "2026-10-01"], dates
    print("echeances_proches() : OK ->", dates, "(triées, hors-fenêtre exclue)\n")


if __name__ == "__main__":
    run_pas_de_bruit_sur_redecoupage()
    run_nouvelle_date_detectee()
    run_dates_passees_ignorees()
    run_echeances_proches()
    print("TOUS LES TESTS ALERTS.PY SONT PASSÉS.")
