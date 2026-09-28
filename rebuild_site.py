"""Régénère data/site_data.json à partir de data/state.json, SANS appeler
l'API ni les sites sources (coût : 0). Utile après une évolution des règles
d'affichage de state.build_site_data (fiches supplantées, sans date...) pour
voir l'effet immédiatement, sans relancer un run complet.

Usage :
    python rebuild_site.py
"""

from __future__ import annotations

import datetime
import json

from state import build_site_data, load_state

STATE_PATH = "data/state.json"
SITE_DATA_PATH = "data/site_data.json"


def main() -> None:
    state = load_state(STATE_PATH)
    if not state:
        print(f"{STATE_PATH} est vide ou absent : lance d'abord python main.py.")
        return
    site_data = build_site_data(state, generated_at=datetime.date.today().isoformat())
    with open(SITE_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(site_data, f, indent=2, ensure_ascii=False)
    masquees = len(state) - len(site_data["aides"])
    print(f"{len(site_data['aides'])} aides écrites dans {SITE_DATA_PATH} "
          f"({masquees} fiche(s) de l'état non envoyées au site : sans date, ou supplantées).")


if __name__ == "__main__":
    main()
