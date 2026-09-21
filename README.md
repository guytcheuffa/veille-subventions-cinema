# French Film Grants Tracker — POC

Extraction pipeline skeleton for tracking application deadlines of French
regional and national film production grants. This POC covers **10
sources** (see `config.py`): the first 4 tested manually (Région Sud, AURA,
Normandie x2), plus 6 added afterward (CNC — national, Paris, Bretagne,
Grand Est, Pays de la Loire, Occitanie), spanning about ten different
structural patterns.

## What's done

- `schema.py` — Pydantic schema for structured output (`Commission`,
  `PeriodeCle`, `ActionPrealable`, `ExtractionResult`), designed to cover:
  simple recurring dates, multi-step sessions, date windows (`from X to
  Y`), and total absence of dates in the HTML (pointing to a PDF instead).
- `prompts.py` — system prompt that explicitly forbids the LLM from
  hallucinating a date, distinguishes a single date from a window, and
  detects mandatory prerequisite actions (appointments, etc.) as
  conditional or systematic.
- `extract.py` — API call (Anthropic or Qwen, see dedicated section below)
  with a **forced tool call** on the Pydantic schema, plus a safety net
  (`_reparer_structure`) that automatically fixes several structural
  quirks observed in practice on Qwen Flash (misplaced fields, commissions
  split into fragments, missing values).
- `fetch.py` — content retrieval: direct fetch (`httpx`) or via **Jina
  Reader** (`r.jina.ai`) for sites protected by an anti-bot WAF, plus a
  native PDF text extraction fallback (`pypdf`).
- `config.py` — registry of the **10 tested sources** (9 regional + the
  CNC, a national grant outside the strict scope), each with: URL, whether
  it needs Jina Reader, its offline fixture, and notes on the structural
  pitfall it illustrates.
- **`state.py`** — state management and deduplication: compares each
  extraction to the last known state (`data/state.json`) and classifies
  each commission as `nouveau` (new) / `modifie` (changed) / `inchange`
  (unchanged) / `clos` (closed). Only the first 3 statuses (never
  `inchange`) should trigger a notification — this was an explicit
  requirement ("don't notify on unchanged or closed commissions"). Also
  produces `data/site_data.json`, the clean, flat JSON base meant for the
  future website (filters, selection, calendar export).
- `test_extraction.py` — replays extraction on the 10 texts already
  captured in `test_data/` (no request to the source sites, only to the
  API) — **the entry point to run first** to validate the LLM.
- **`test_state.py`** — tests the dedup logic, **100% offline, no API
  call, zero cost** — can be run anytime to verify or evolve `state.py`
  without depending on the LLM.
- `main.py` — full pipeline (real fetch + extraction + state update +
  writing `data/site_data.json`) across the 10 registry sources — to run
  once extraction has been validated offline.

## Deliberately out of scope for this POC

These points are identified but not yet implemented (to do once
extraction and dedup are validated):

- PDF fallback wired automatically into `main.py` (the code exists in
  `fetch.py`, but the conditional trigger isn't connected yet — affects at
  least Normandie and Bretagne, which have no dates in their HTML).
- Static website reading `data/site_data.json` (filters by
  region/category, selection, client-side `.ics` export).
- GitHub Actions workflow + GitHub Secrets.
- Extension to the remaining metropolitan regions (ALCA Nouvelle-Aquitaine,
  Pictanovo Hauts-de-France, Ciclic Centre-Val de Loire, etc.).

## Setup

```bash
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env      # then fill in your key in .env
```

The project doesn't load `.env` automatically (no `python-dotenv`, to
keep things minimal) — export the variable before running the scripts:

```bash
export ANTHROPIC_API_KEY=sk-ant-...        # macOS/Linux
$env:ANTHROPIC_API_KEY="sk-ant-..."        # PowerShell
```

## Choosing the LLM provider (Anthropic or Qwen)

The brief accepts "GPT-4o-mini or Claude Haiku"-type models — `extract.py`
supports both, selectable via the `PROVIDER` variable:

```powershell
# Anthropic (default) — requires ANTHROPIC_API_KEY
$env:PROVIDER="anthropic"
$env:ANTHROPIC_API_KEY="sk-ant-..."

# Qwen (QwenCloud/DashScope) — requires DASHSCOPE_API_KEY
$env:PROVIDER="qwen"
$env:DASHSCOPE_API_KEY="sk-ws-..."
```

Both go through a forced tool call on the same Pydantic schema
(`schema.py`), so the output is structurally identical regardless of the
chosen provider — handy for comparing extraction quality between the two
on the same fixtures.

## Running locally

**0. Dry run (0 cost)** — checks the whole plumbing (fixture reading,
prompt building, Pydantic validation) without calling the API. Also
prints a rough cost estimate per call:

```bash
export DRY_RUN=true
python test_extraction.py
unset DRY_RUN   # or $env:DRY_RUN="false" on PowerShell, before the next step
```

**1. Test extraction offline (recommended first)** — replays the LLM on
the 10 texts already captured, without depending on site availability:

```bash
python test_extraction.py
```

Compare the JSON output to what's described in `config.py`'s notes for
each source — in particular:
- Région Sud: the prerequisite appointment should come out as
  **conditional**.
- AURA: sessions with "date to be announced" must **not** get an invented
  date.
- Normandie (main page): `aucune_date_trouvee` (no date found) should be
  `true`, with `lien_pdf_calendrier` filled in.
- Normandie (subdomain): 3 sessions × 4 steps, with real **windows**
  (`date_debut` + `date_fin`) for the appointment and the submission.

**2. Test the dedup/state logic (recommended, zero cost)** — pure logic,
no API call, checks the 4 statuses (new/changed/unchanged/closed) on
synthetic data:

```bash
python test_state.py
```

**3. Test the full pipeline (real fetch + extraction + dedup)**:

```bash
python main.py
```

Writes/updates `data/state.json` (the persistent state, meant to be
committed to the repo — it's the memory carried from one run to the next)
and `data/site_data.json` (the clean, flat JSON base for the future
website).

## Structure

```
veille-cinema/
├── config.py              # registry of the 10 tested sources
├── schema.py               # Pydantic models
├── prompts.py               # system prompt + user template
├── extract.py                # Anthropic/Qwen API call (forced tool call)
├── fetch.py                   # direct fetch / Jina Reader / PDF
├── state.py                    # dedup/state + site_data.json generation
├── main.py                      # full pipeline (network)
├── test_extraction.py            # offline LLM test (fixtures, costs tokens)
├── test_state.py                  # offline dedup test (0 cost, 0 API)
├── test_data/                      # 10 manually captured pages
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
├── data/                            # generated by main.py (to commit)
│   ├── state.json                    # persistent state (run-to-run memory)
│   └── site_data.json                # clean base for the future website
├── requirements.txt
├── .env.example
└── .gitignore
```