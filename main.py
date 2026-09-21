"""Pipeline bout-en-bout : fetch réel des sources du registre (config.py),
extraction LLM, puis mise à jour de l'état (dédup) et écriture de la base
JSON propre destinée au futur site (data/site_data.json).

Nécessite une connexion internet et une clé API (ANTHROPIC_API_KEY ou
DASHSCOPE_API_KEY selon PROVIDER, cf. extract.py).

Pour rejouer l'extraction sans requête réseau vers les sites sources
(données déjà capturées dans test_data/), utiliser test_extraction.py à la
place — mais noter que test_extraction.py n'écrit PAS l'état (c'est un outil
de vérification de l'extraction, pas le pipeline de production).

Usage :
    python main.py
"""

from __future__ import annotations

import datetime
import json

from config import SOURCES
from extract import DRY_RUN, extract
from fetch import fetch_page
from state import build_site_data, load_state, save_state, update_state

STATE_PATH = "data/state.json"
SITE_DATA_PATH = "data/site_data.json"


def run() -> None:
    today = datetime.date.today().isoformat()
    toutes_commissions: list[dict] = []
    sources_traitees: set[str] = set()

    for source in SOURCES:
        print(f"\n{'=' * 70}\n{source.region} — {source.organisme}\n{source.url}\n{'=' * 70}")

        try:
            content = fetch_page(source.url, use_jina=source.needs_jina)
        except Exception as exc:  # noqa: BLE001 - on veut logguer et continuer
            print(f"  [ERREUR fetch] {exc}")
            continue

        try:
            result = extract(content, url=source.url, region=source.region)
        except Exception as exc:  # noqa: BLE001
            print(f"  [ERREUR extraction] {exc}")
            continue

        # La source est considérée "traitée" dès que le fetch ET l'extraction
        # ont réussi — c'est ce qui autorise la détection de clôture (une
        # commission connue sur cette source mais absente du résultat).
        sources_traitees.add(source.url)

        if result.aucune_date_trouvee:
            print(f"  Aucune date trouvée en HTML. PDF détecté : {result.lien_pdf_calendrier}")
            # TODO une fois validé : fetch_pdf_text(result.lien_pdf_calendrier)
            #      puis extract() une 2e fois sur le texte du PDF.
        else:
            commissions_dump = [c.model_dump() for c in result.commissions]
            toutes_commissions.extend(commissions_dump)
            print(json.dumps(commissions_dump, indent=2, ensure_ascii=False))

    print(f"\n{'=' * 70}\nMise à jour de l'état ({len(toutes_commissions)} commissions extraites, "
          f"{len(sources_traitees)}/{len(SOURCES)} sources traitées avec succès)\n{'=' * 70}")

    if DRY_RUN:
        # GARDE-FOU CRITIQUE : en mode DRY_RUN, extract() ne renvoie que des
        # résultats factices (0 commission par source). Mettre à jour l'état
        # dans ces conditions marquerait CHAQUE commission déjà connue comme
        # 'clos' à tort (absente du run factice) — corruption silencieuse de
        # data/state.json. On n'écrit donc RIEN sur l'état/le site en dry run.
        print("\nDRY_RUN actif : état et site_data.json NON modifiés "
              "(les résultats de ce run sont factices).")
        return

    state = load_state(STATE_PATH)
    state, changements = update_state(state, toutes_commissions, today=today, processed_url_sources=sources_traitees)
    save_state(STATE_PATH, state)

    site_data = build_site_data(state, generated_at=today)
    with open(SITE_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(site_data, f, indent=2, ensure_ascii=False)

    if changements:
        print(f"\n{len(changements)} changement(s) à notifier :")
        for c in changements:
            com = c["commission"]
            label = com.get("session_label") or com.get("etape_filiere")
            print(f"  [{c['statut'].upper()}] {com['region']} — {com['fonds']} — {label}")
    else:
        print("\nAucun changement à notifier.")

    print(f"\nÉtat sauvegardé -> {STATE_PATH}")
    print(f"Base site sauvegardée -> {SITE_DATA_PATH} ({len(site_data['aides'])} aides au total)")


if __name__ == "__main__":
    run()
