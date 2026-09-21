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


if __name__ == "__main__":
    run()
