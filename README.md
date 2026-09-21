# Veille des aides régionales au cinéma — POC

Squelette du pipeline d'extraction pour la mission de veille des dates de dépôt
des fonds d'aide régionaux au cinéma. Ce POC couvre **10 sources** (voir
`config.py`) : les 4 premières testées manuellement (Région Sud, AURA,
Normandie x2), plus 6 ajoutées ensuite (CNC — national, Paris, Bretagne,
Grand Est, Pays de la Loire, Occitanie), et une dizaine de patterns
structurels différents.

## Ce qui est fait

- `schema.py` — schéma Pydantic de sortie structurée (`Commission`,
  `PeriodeCle`, `ActionPrealable`, `ExtractionResult`), pensé pour couvrir :
  date simple récurrente, sessions multi-étapes, fenêtres de dates (`du X au
  Y`), et absence totale de date en HTML (renvoi vers un PDF).
- `prompts.py` — prompt système qui interdit explicitement au LLM d'halluciner
  une date, distingue date ponctuelle vs fenêtre, et repère les actions
  préalables obligatoires (RDV, etc.) conditionnelles ou systématiques.
- `extract.py` — appel à l'API (Anthropic ou Qwen, cf. section dédiée
  ci-dessous) avec **tool call forcé** sur le schéma Pydantic, plus un filet
  de sécurité (`_reparer_structure`) qui corrige automatiquement plusieurs
  écarts structurels observés en pratique sur Qwen Flash (champs égarés,
  commissions scindées en fragments, valeurs manquantes).
- `fetch.py` — récupération du contenu : fetch direct (`httpx`) ou via
  **Jina Reader** (`r.jina.ai`) pour les sites protégés par un WAF anti-bot,
  + fallback d'extraction de texte PDF natif (`pypdf`).
- `config.py` — registre des **10 sources** testées (9 régionales + le CNC,
  aide nationale hors périmètre strict), avec pour chacune : URL, besoin ou
  non de Jina Reader, fixture offline associée, et notes sur le piège
  structurel qu'elle illustre.
- **`state.py`** — gestion d'état et déduplication : compare chaque
  extraction au dernier état connu (`data/state.json`) et classe chaque
  commission en `nouveau` / `modifie` / `inchange` / `clos`. Seuls les 3
  premiers statuts (jamais `inchange`) doivent déclencher une notification —
  c'est le point explicitement demandé par le cahier des charges ("ne pas
  notifier les commissions inchangées ou closes"). Produit aussi
  `data/site_data.json`, la base JSON propre et plate destinée au futur site
  (filtres, sélection, export calendrier).
- `test_extraction.py` — rejoue l'extraction sur les 10 textes déjà capturés
  dans `test_data/` (aucune requête vers les sites sources, seulement vers
  l'API) — **c'est le point d'entrée à lancer en premier** pour valider le
  LLM.
- **`test_state.py`** — test de la logique de dédup, **100% hors-ligne,
  aucun appel API, zéro coût** — à lancer à tout moment pour vérifier ou
  faire évoluer `state.py` sans dépendre du LLM.
- `main.py` — pipeline complet (fetch réel + extraction + mise à jour de
  l'état + écriture de `data/site_data.json`) sur les 10 sources du
  registre — à lancer une fois l'extraction validée en offline.

## Ce qui est volontairement hors scope de ce POC

Ces points sont identifiés mais pas encore codés (à faire une fois
l'extraction et la dédup validées) :

- Fallback PDF branché automatiquement dans `main.py` (le code existe dans
  `fetch.py`, mais le déclenchement conditionnel n'est pas encore relié —
  concerne au moins Normandie et Bretagne, qui n'ont aucune date en HTML).
- Site web statique lisant `data/site_data.json` (filtres par
  région/typologie, sélection, export `.ics` côté client).
- Workflow GitHub Actions + GitHub Secrets.
- Extension aux régions métropolitaines manquantes (ALCA Nouvelle-Aquitaine,
  Pictanovo Hauts-de-France, Ciclic Centre-Val de Loire, etc.).

## Installation

```bash
python -m venv venv
source venv/bin/activate  # ou venv\Scripts\activate sous Windows
pip install -r requirements.txt
cp .env.example .env      # puis renseigne ta clé dans .env
```

Le projet ne charge pas automatiquement `.env` (pas de `python-dotenv` pour
rester minimal) — exporte la variable avant de lancer les scripts :

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # macOS/Linux
$env:ANTHROPIC_API_KEY="sk-ant-..."        # PowerShell
```

## Choisir le provider LLM (Anthropic ou Qwen)

Le cahier des charges accepte "type GPT-4o-mini ou Claude Haiku" — `extract.py`
supporte les deux, sélectionnables via la variable `PROVIDER` :

```powershell
# Anthropic (par défaut) — nécessite ANTHROPIC_API_KEY
$env:PROVIDER="anthropic"
$env:ANTHROPIC_API_KEY="sk-ant-..."

# Qwen (QwenCloud/DashScope) — nécessite DASHSCOPE_API_KEY
$env:PROVIDER="qwen"
$env:DASHSCOPE_API_KEY="sk-ws-..."
```

Les deux passent par un tool call forcé sur le même schéma Pydantic
(`schema.py`), donc le résultat est structurellement identique quel que soit
le provider choisi — pratique pour comparer la qualité d'extraction des deux
sur les mêmes 4 fixtures.

## Lancer en local

**0. Dry run (0 centime dépensé)** — vérifie toute la plomberie (lecture des
fixtures, construction du prompt, validation Pydantic) sans appeler l'API.
Affiche aussi une estimation grossière du coût par appel :

```bash
export DRY_RUN=true
python test_extraction.py
unset DRY_RUN   # ou $env:DRY_RUN="false" sous PowerShell, avant l'étape suivante
```

**1. Tester l'extraction hors-ligne (recommandé en premier)** — rejoue le LLM
sur les 4 textes déjà capturés, sans dépendre de la disponibilité des sites :

```bash
python test_extraction.py
```

Compare la sortie JSON à ce qui est décrit dans les notes de `config.py` pour
chaque source — en particulier :
- Région Sud : le RDV préalable doit ressortir comme **conditionnel**.
- AURA : les sessions "date communiquée ultérieurement" ne doivent **pas**
  avoir de date inventée.
- Normandie (page principale) : `aucune_date_trouvee` doit être `true`, avec
  `lien_pdf_calendrier` rempli.
- Normandie (sous-domaine) : 3 sessions × 4 étapes, avec de vraies **fenêtres**
  (`date_debut` + `date_fin`) sur le RDV et le dépôt.

**2. Tester la dédup/état (recommandé, aucun coût)** — logique pure, aucun
appel API, vérifie les 4 statuts (nouveau/modifié/inchangé/clos) sur des
données synthétiques :

```bash
python test_state.py
```

**3. Tester le pipeline complet (fetch réel + extraction + dédup)** :

```bash
python main.py
```

Écrit/actualise `data/state.json` (l'état persistant, à committer dans le
repo — c'est la mémoire d'un run à l'autre) et `data/site_data.json` (la
base JSON propre, plate, destinée au futur site).

## Structure

```
veille-cinema/
├── config.py              # registre des 10 sources testées
├── schema.py               # modèles Pydantic
├── prompts.py               # prompt système + template utilisateur
├── extract.py                # appel API Anthropic/Qwen (tool call forcé)
├── fetch.py                   # fetch direct / Jina Reader / PDF
├── state.py                    # dédup/état + génération de site_data.json
├── main.py                      # pipeline complet (réseau)
├── test_extraction.py            # test LLM offline (fixtures, coûte des tokens)
├── test_state.py                  # test dédup offline (0 coût, 0 API)
├── test_data/                      # 10 pages capturées manuellement
│   ├── region_sud.txt
│   ├── aura.txt
│   ├── normandie_principale.txt
│   ├── normandie_sousdomaine.txt
│   ├── cnc_avr.txt
│   ├── paris.txt
│   ├── bretagne.txt
│   ├── grand_est.txt
│   ├── pays_de_la_loire.txt
│   └── occitanie.txt
├── data/                            # généré par main.py (à committer)
│   ├── state.json                    # état persistant (mémoire des runs)
│   └── site_data.json                # base propre pour le futur site
├── requirements.txt
├── .env.example
└── .gitignore
```
