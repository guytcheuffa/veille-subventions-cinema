"""Lance l'extraction sur les 4 cas réels capturés dans test_data/ (aucune
requête réseau vers les sites sources — seulement vers l'API Anthropic) et
affiche le résultat pour vérification humaine.

Points de vigilance à l'œil, en comparant la sortie au texte source :
- Région Sud : le RDV préalable doit être marqué CONDITIONNEL (pas systématique).
- AURA : les sessions 2 et 3 ("date communiquée ultérieurement") doivent être
  omises ou avoir des étapes vides — jamais une date inventée.
- Normandie (page principale) : aucune_date_trouvee doit être True, avec
  lien_pdf_calendrier rempli et commissions=[].
- Normandie (sous-domaine) : 3 sessions x 4 étapes, avec des FENÊTRES
  (date_debut + date_fin) sur le RDV préalable et le dépôt des dossiers.

Usage :
    export ANTHROPIC_API_KEY=sk-ant-...
    python test_extraction.py
"""

from __future__ import annotations

import json
from pathlib import Path

from extract import extract
from config import SOURCES

TEST_DATA_DIR = Path(__file__).parent / "test_data"


def load_fixture(path: Path) -> tuple[str, str, str]:
    """Parse une fixture texte : les 2 premières lignes contiennent
    'URL: ...' et 'REGION: ...', le reste est le contenu à envoyer au LLM."""
    lines = path.read_text(encoding="utf-8").splitlines()
    url = lines[0].removeprefix("URL: ").strip()
    region = lines[1].removeprefix("REGION: ").strip()
    content = "\n".join(lines[2:]).strip()
    return content, url, region


def run() -> None:
    for source in SOURCES:
        fixture_path = TEST_DATA_DIR / Path(source.offline_fixture).name
        print(f"\n{'=' * 70}\n{source.region} — {source.organisme}\n{fixture_path.name}\n{'=' * 70}")

        content, url, region = load_fixture(fixture_path)
        result = extract(content, url=url, region=region)

        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run()
