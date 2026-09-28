"""Test de la logique de dédup/état — 100% hors-ligne, aucun appel API,
aucun coût. Simule 4 runs successifs sur des commissions synthétiques pour
vérifier les 4 statuts (nouveau/modifié/inchangé/clos) et la résistance à la
variabilité de formulation du LLM (le texte de 'fonds' peut varier d'un run
à l'autre sans que ça déclenche une fausse alerte).

Usage :
    python test_state.py
"""

from __future__ import annotations

from state import STATUT_CLOS, STATUT_MODIFIE, STATUT_NOUVEAU, build_site_data, update_state


def commission(url: str, session: str, date_fin: str, fonds: str = "Fonds X") -> dict:
    return {
        "region": "Bretagne",
        "fonds": fonds,
        "organisme": "Région Bretagne",
        "typologie_oeuvre": "court métrage",
        "etape_filiere": "production",
        "session_label": session,
        "etapes": [{"label": "Dépôt", "date_debut": None, "date_fin": date_fin, "heure_limite": None}],
        "actions_prealables": [],
        "conditions_eliminatoires": [],
        "montant_min_eur": None,
        "montant_max_eur": None,
        "lien_formulaire": None,
        "url_source": url,
        "date_extraction": "2026-09-15",
    }


def commission_multi(url: str, session: str, dates: list[str], typologie: str = "court métrage") -> dict:
    """Variante avec plusieurs dates — utilisée pour tester la
    correspondance floue par chevauchement de dates."""
    return {
        "region": "Paris",
        "fonds": "Fonds de soutien au court métrage",
        "organisme": "Ville de Paris",
        "typologie_oeuvre": typologie,
        "etape_filiere": "production",
        "session_label": session,
        "etapes": [
            {"label": f"étape {i}", "date_debut": None, "date_fin": d, "heure_limite": None}
            for i, d in enumerate(dates)
        ],
        "actions_prealables": [],
        "conditions_eliminatoires": [],
        "montant_min_eur": None,
        "montant_max_eur": None,
        "lien_formulaire": None,
        "url_source": url,
        "date_extraction": "2026-09-15",
    }


def run_correspondance_floue() -> None:
    """Scénario observé en conditions réelles : le LLM reformule
    session_label d'un run à l'autre (ex: 'Troisième session' devient
    'Troisième session 2026') alors que les dates réelles n'ont pas bougé
    — la clé exacte échoue, la correspondance floue par dates doit prendre
    le relais pour éviter un faux clos+nouveau."""
    url = "https://paris.fr/x"
    state: dict = {}

    print("Run A — 'Troisième session' (sans année)")
    runA = [commission_multi(url, "Troisième session", ["2026-09-01", "2026-11-30", "2026-12-31"])]
    state, changements = update_state(state, runA, today="2026-09-15", processed_url_sources={url})
    assert [c["statut"] for c in changements] == [STATUT_NOUVEAU]
    print("  ->", [c["statut"] for c in changements], "(attendu : nouveau)\n")

    print("Run B — 'Troisième session 2026' (reformulé), MÊMES dates")
    runB = [commission_multi(url, "Troisième session 2026", ["2026-09-01", "2026-11-30", "2026-12-31"])]
    state, changements = update_state(state, runB, today="2026-09-27", processed_url_sources={url})
    statuts = [c["statut"] for c in changements]
    assert statuts == [], statuts
    print("  ->", statuts, "(attendu : [] — reconnue comme la même commission malgré la reformulation)\n")

    print("Run C — une vraie date change (doit rester détecté)")
    runC = [commission_multi(url, "Troisième session 2026", ["2026-09-01", "2026-11-30", "2027-01-15"])]
    state, changements = update_state(state, runC, today="2026-10-01", processed_url_sources={url})
    statuts = [c["statut"] for c in changements]
    assert statuts == [STATUT_MODIFIE], statuts
    print("  ->", statuts, "(attendu : [modifie])\n")

    print("Run D — une commission SANS RAPPORT ne doit pas être fusionnée à tort")
    runD = [
        commission_multi(url, "Troisième session 2026", ["2026-09-01", "2026-11-30", "2027-01-15"]),
        commission_multi(url, "Nouveau fonds jamais vu", ["2027-06-01"], typologie="documentaire"),
    ]
    state, changements = update_state(state, runD, today="2026-10-15", processed_url_sources={url})
    statuts = sorted(c["statut"] for c in changements)
    assert statuts == [STATUT_NOUVEAU], statuts
    print("  ->", statuts, "(attendu : [nouveau] — la commission sans rapport, pas de faux positif)\n")

    print("Tous les tests de correspondance floue sont passés.\n")


def run_doublon_intra_run() -> None:
    """Scénario observé en conditions réelles : le LLM renvoie DEUX FOIS la
    même commission dans une seule réponse (contenu identique, étapes dans
    un ordre différent) — sans garde-fou, la 2e occurrence écrase
    silencieusement la mise à jour de la 1ère (perte du first_seen
    d'origine, faux NOUVEAU en plus du MODIFIE légitime)."""
    url = "https://bfc.fr/x"
    state: dict = {}

    def commission(dates_ordre: list[str]) -> dict:
        return {
            "region": "Bourgogne-Franche-Comté", "fonds": "x", "organisme": "x",
            "typologie_oeuvre": "court métrage", "etape_filiere": "production", "session_label": None,
            "etapes": [{"label": "étape", "date_debut": None, "date_fin": d, "heure_limite": None} for d in dates_ordre],
            "actions_prealables": [], "conditions_eliminatoires": [],
            "montant_min_eur": None, "montant_max_eur": None, "lien_formulaire": None,
            "url_source": url, "date_extraction": "2026-09-27",
        }

    print("Run 1 — une commission normale")
    state, changements = update_state(state, [commission(["2027-02-28", "2026-07-31"])], today="2026-09-15", processed_url_sources={url})
    assert [c["statut"] for c in changements] == [STATUT_NOUVEAU]
    assert len(state) == 1
    print("  ->", [c["statut"] for c in changements], "| entrées:", len(state), "\n")

    print("Run 2 — le LLM renvoie la MÊME commission deux fois (ordre des étapes différent)")
    run2 = [commission(["2027-02-28", "2026-07-31"]), commission(["2026-07-31", "2027-02-28"])]
    state, changements = update_state(state, run2, today="2026-09-27", processed_url_sources={url})
    statuts = [c["statut"] for c in changements]
    assert statuts == [], statuts
    assert len(state) == 1, f"doublon a créé une 2e entrée : {len(state)}"
    first_seen = list(state.values())[0]["first_seen"]
    assert first_seen == "2026-09-15", f"first_seen écrasé par le doublon : {first_seen}"
    print("  ->", statuts, "| entrées:", len(state), "| first_seen préservé:", first_seen, "\n")

    print("Tous les tests de doublon intra-run sont passés.\n")


def run_pas_de_fausse_cloture_sur_dates_passees() -> None:
    """Scénario observé en conditions réelles sur le CNC : une page
    redécoupe ses commissions différemment d'un run à l'autre (12 sessions
    séparées -> 1 seule combinée). Les anciennes sessions dont TOUTES les
    dates étaient déjà passées ne doivent PAS générer d'alerte CLOS (leur
    disparition de la page est normale, pas une vraie nouvelle) — seule une
    session encore À VENIR qui disparaît doit rester signalée."""
    url = "https://cnc.fr/x"

    def commission(session: str | None, date_fin: str) -> dict:
        return {
            "region": "National", "fonds": "AVR1", "organisme": "CNC",
            "typologie_oeuvre": "court métrage", "etape_filiere": "production", "session_label": session,
            "etapes": [{"label": "Dépôt", "date_debut": None, "date_fin": date_fin, "heure_limite": None}],
            "actions_prealables": [], "conditions_eliminatoires": [],
            "montant_min_eur": None, "montant_max_eur": None, "lien_formulaire": None,
            "url_source": url, "date_extraction": "2026-09-27",
        }

    print("Run 1 — plusieurs sessions passées + une future, séparées")
    state: dict = {}
    run1 = [
        commission("Session 1 - 2026", "2026-01-08"),  # passée
        commission("Session 2 - 2026", "2026-02-17"),  # passée
        commission("Session 6 - 2026", "2026-09-03"),  # passée
        commission("Session 1 - 2027", "2026-11-20"),  # future
    ]
    state, _ = update_state(state, run1, today="2026-09-27", processed_url_sources={url})
    print(f"  -> {len(state)} sessions enregistrées\n")

    print("Run 2 — le LLM combine tout en UNE commission (redécoupage CNC-like)")
    run2 = [commission(None, "2026-11-20")]  # reprend seulement la date future
    state, changements = update_state(state, run2, today="2026-09-27", processed_url_sources={url})
    statuts = [c["statut"] for c in changements]
    assert STATUT_CLOS not in statuts, f"fausse(s) alerte(s) CLOS générée(s) pour des sessions déjà passées : {statuts}"
    print("  ->", statuts, "(attendu : aucun CLOS malgré la disparition des 3 sessions passées)\n")

    print("Run 3 — contrôle : une session encore À VENIR qui disparaît reste signalée")
    state2: dict = {}
    state2, _ = update_state(state2, [commission("Session 1 - 2027", "2026-12-20")], today="2026-09-27", processed_url_sources={url})
    state2, changements2 = update_state(state2, [], today="2026-10-05", processed_url_sources={url})
    statuts2 = [c["statut"] for c in changements2]
    assert statuts2 == [STATUT_CLOS], statuts2
    print("  ->", statuts2, "(attendu : [clos] — une vraie clôture reste signalée)\n")

    print("Tous les tests de fausse clôture sont passés.\n")


def run() -> None:
    url = "https://example.fr/bretagne"
    state: dict = {}

    print("Run 1 — deux commissions inédites")
    run1 = [commission(url, "Session 1", "2026-10-01"), commission(url, "Session 2", "2027-01-15")]
    state, changements = update_state(state, run1, today="2026-09-15", processed_url_sources={url})
    statuts = sorted(c["statut"] for c in changements)
    assert statuts == [STATUT_NOUVEAU, STATUT_NOUVEAU], statuts
    print("  ->", statuts, "(attendu : 2x nouveau)\n")

    print("Run 2 — rien ne change")
    run2 = [commission(url, "Session 1", "2026-10-01"), commission(url, "Session 2", "2027-01-15")]
    state, changements = update_state(state, run2, today="2026-09-22", processed_url_sources={url})
    assert changements == [], changements
    print("  ->", changements, "(attendu : aucune alerte)\n")

    print("Run 3 — Session 1 change de date, Session 2 disparaît, Session 3 apparaît")
    run3 = [commission(url, "Session 1", "2026-10-15"), commission(url, "Session 3", "2027-05-01")]
    state, changements = update_state(state, run3, today="2026-09-29", processed_url_sources={url})
    statuts = sorted(c["statut"] for c in changements)
    assert statuts == sorted([STATUT_MODIFIE, STATUT_NOUVEAU, STATUT_CLOS]), statuts
    print("  ->", statuts, "(attendu : modifie + nouveau + clos)\n")

    print("Run 4 — Session 1 identique mais 'fonds' reformulé par le LLM (Session 3 absente -> clôturée)")
    run4 = [commission(url, "Session 1", "2026-10-15", fonds="FACCA - Fonds X (libellé différent)")]
    state, changements = update_state(state, run4, today="2026-10-06", processed_url_sources={url})
    statuts = sorted(c["statut"] for c in changements)
    assert statuts == [STATUT_CLOS], statuts
    print("  ->", statuts, "(attendu : seulement clos pour Session 3 — Session 1 ne doit PAS ressortir)\n")

    site_data = build_site_data(state, generated_at="2026-10-06")
    print(f"{len(site_data['aides'])} aides dans la base finale :")
    for a in site_data["aides"]:
        print(f"  - {a['session_label']:12s} | clos={a['clos']!s:5s} | first_seen={a['first_seen']} | last_updated={a['last_updated']}")

    print("\nTous les tests sont passés.")


def run_site_masque_les_fiches_redondantes() -> None:
    """Une fiche 'non retrouvée' dont toutes les dates sont déjà affichées par
    une fiche active de la même source n'est pas envoyée au site (doublon
    d'un redécoupage). Une fiche non retrouvée avec une date qui n'apparaît
    nulle part ailleurs, elle, reste visible."""
    url = "https://bfc.fr/x"

    def com(session, dates):
        return {"region": "R", "fonds": "F", "organisme": "O", "typologie_oeuvre": "t", "etape_filiere": "production",
                "session_label": session,
                "etapes": [{"label": "d", "date_debut": None, "date_fin": d, "heure_limite": None} for d in dates],
                "url_source": url}

    def entree(c, clos):
        return {"commission": c, "etapes_hash": "h", "first_seen": "2026-09-01", "last_updated": "2026-09-01",
                "last_checked": "2026-09-01", "clos": clos}

    state = {
        "actif": entree(com(None, ["2027-02-28", "2027-07-31"]), False),
        "doublon": entree(com("Session 1", ["2027-02-28", "2027-07-31"]), True),   # mêmes dates -> masqué
        "sous_ensemble": entree(com("Session 2", ["2027-07-31"]), True),            # incluse dans l'actif -> masquée
        "unique": entree(com("Session 3", ["2027-09-15"]), True),                   # date absente ailleurs -> visible
        "autre_source": entree({**com("X", ["2027-02-28"]), "url_source": "https://autre.fr"}, True),  # autre source -> visible
    }
    ids = sorted(a["id"] for a in build_site_data(state, "2026-09-28")["aides"])
    assert ids == ["actif", "autre_source", "unique"], ids
    assert "doublon" in state and "sous_ensemble" in state, "state.json n'est pas modifié"
    print("Site : doublons/sous-ensembles non retrouvés masqués, date unique et autre source conservées : OK\n")


def run_site_supplante_les_anciennes_versions() -> None:
    """Données réelles de Guy : l'ancienne fiche Normandie Session 3 (dates
    inventées 16/06 et 30/06, marquée non retrouvée) restait affichée à côté
    de la version corrigée ; des fiches sans date encombraient l'état."""
    def com(fonds, session, dates, url="https://normandie.fr/x"):
        return {"region": "Normandie", "fonds": fonds, "organisme": "NI", "typologie_oeuvre": "t", "etape_filiere": "production",
                "session_label": session, "url_source": url,
                "etapes": [{"label": "d", "date_debut": None, "date_fin": d, "heure_limite": None} for d in dates]}

    def entree(c, clos):
        return {"commission": c, "etapes_hash": "h", "first_seen": "2026-09-01", "last_updated": "2026-09-01",
                "last_checked": "2026-09-01", "clos": clos}

    FN = "Fonds Numérique Normandie Images"
    state = {
        "s3_nouveau": entree(com(FN, "Session 3 - 2027", ["2027-04-30", "2027-05-19", "2027-06-22", "2027-07-06"]), False),
        "s3_inventee": entree(com(FN, "Session 3 - 2027", ["2027-04-30", "2027-05-19", "2027-06-16", "2027-06-30"]), True),   # ancienne version
        "s1_nouveau": entree(com(FN, "Session 1 - 2027", ["2026-09-04", "2026-09-24", "2026-10-27", "2026-11-19"]), False),
        "s1_ancien": entree(com(FN, "Session 1 - 2027", ["2026-09-04", "2026-09-17", "2026-10-27", "2026-11-19"]), True),     # ancienne version
        "sans_date": entree(com(FN, "Session 1", []), False),                                                                  # rien à afficher
        "autre_fonds": entree(com("Fonds de soutien au court métrage", "Session X", ["2027-04-30", "2027-05-19"]), True),     # autre dispositif : visible
        "occitanie_vide": entree(com("Aide Occitanie", "Animation - 1er juin", [], url="https://occ.fr"), False),              # active sans date
        "occitanie_ancien": entree(com("Aide Occitanie", None, ["2026-12-01", "2027-02-01"], url="https://occ.fr"), True),    # seule à porter les dates : visible
    }
    ids = sorted(a["id"] for a in build_site_data(state, "2026-09-28")["aides"])
    assert ids == ["autre_fonds", "occitanie_ancien", "s1_nouveau", "s3_nouveau"], ids
    print("Site : anciennes versions supplantées et fiches sans date masquées ; autre dispositif et seule fiche porteuse de dates conservés : OK\n")


if __name__ == "__main__":
    run()
    run_correspondance_floue()
    run_doublon_intra_run()
    run_pas_de_fausse_cloture_sur_dates_passees()
    run_site_masque_les_fiches_redondantes()
    run_site_supplante_les_anciennes_versions()
