# Film Funding Deadlines Tracker

> 🇫🇷 Version française détaillée : [README.fr.md](README.fr.md)

Automated tracker for **application deadlines of French regional and national film production grants**. An LLM extracts deadlines from official web pages into a strict schema, a deterministic layer checks the result against the source text, and a static website lets producers browse, filter and export deadlines to their calendar.

**Live demo:** https://guytcheuffa.github.io/subvention-cinema/

**Status:** working proof of concept. 13 of the 16 configured sources are read automatically; the pipeline is run by hand for now (a scheduled GitHub Actions run is the next step).

## What it does

- Fetches **16 pages from 15 funding bodies** (regions, cities, regional agencies and the national CNC), each with a different structure: recurring dates without a year, multi-step sessions, date windows, dates only in a linked PDF, one calendar per film genre.
- Extracts, for each session: fund, work type, stage (writing, development, production…), every step with its date and cut-off time, mandatory preliminary actions (e.g. a compulsory meeting), eliminatory conditions and the application link.
- Tracks state between runs so a session is reported as new, modified or gone.
- Publishes a **static site** in French (its users are French producers; no server, no build step): chronological programme grouped by month, filters by region, stage, **deadline type** (deposit / decision / preliminary step, colour-coded), **format and genre** (inferred from the official texts; aids that don't specify stay visible), free-text search, a persistent selection, and **`.ics` export** generated in the browser.

## Architecture

```
config.py (16 sources)
   │
   ▼
fetch.py ── direct fetch / Jina Reader / PDF text ──► detects blocked or wrong pages
   │
   ▼
extract.py ── forced tool call on a Pydantic schema (Anthropic or Qwen)
   │            ├─ structural repair + field-level rescue
   │            ├─ verify.py : dates checked against the source text
   │            └─ one corrective second pass when needed
   ▼
state.py ── dedup / change tracking ──► data/state.json
   │
   ▼
data/site_data.json ──► index.html + assets/ (GitHub Pages)
```

No CSS selectors are used anywhere: pages are converted to text and read by a compact LLM, so a site redesign does not break the pipeline.

## Engineering notes: making LLM extraction trustworthy

A small, cheap model is fine for this task, but its failures are quiet: the JSON is valid and the dates look plausible. Most of the work went into detecting and containing those failures, each one found on a real run.

| Problem seen in production | What the code does |
|---|---|
| **Invented dates** (a source said "22/06/27", the model returned 16/06/2027) | `verify.py` checks every date against the source text in the formats a French page uses; unconfirmed dates are dropped and the source is re-asked once, telling the model which dates do not exist. |
| **Wrong year** ("30 September every year" returned as 2027 when 30/09/2026 was two days away) | The source text wins when it gives one year; for calendars the page itself marks as recurring ("every year", "per year"), the next occurrence from today is used. Pages without such a cue are left alone. |
| **Broken structure** (double-encoded JSON, `null` in required text fields, fragmented objects) | Structural repair, then a field-level rescue: only the smallest faulty part (a step, a window, a value) is removed instead of rejecting the whole source. |
| **Same session returned three times**, or split differently from one run to the next | Identical commissions are merged; state matching uses an exact key with a fuzzy fallback on overlapping dates; stale superseded versions are hidden on the site. |
| **Pages that are not the page** (Cloudflare challenge, home page served instead of the fund page) | Detected before any LLM call (zero cost); the source is reported as failed and its known data is left untouched. |
| **Cost surprises** on a pay-as-you-go provider | A monthly budget guard estimates each call before sending it and blocks it if the cap would be exceeded. |

Two design choices worth mentioning:

- **The site derives session status from dates, not from pipeline flags.** "Opens on 01/10", "Application open", "Application closed", "Finished" are computed in the browser from the dates, so they stay correct between pipeline runs. A pipeline-side "not found on last run" flag used to be displayed as "closed", which was misleading.
- **Failing loudly beats guessing.** Ambiguous cases (several different years for the same day, a date with no source support) keep the source's data or drop the item; nothing is invented.

## Tests

Everything below runs offline and costs nothing (LLM calls are simulated). Each test reproduces a real failure.

```bash
python test_state.py       # dedup, fuzzy matching, site data rules
python test_verify.py      # date verification, year rules, label recovery
python test_retry.py       # corrective second pass and its safeguards
python test_robustesse.py  # malformed model output, empty replies, duplicates
python test_fetch.py       # blocked pages; checks all 16 real pages are accepted
python test_alerts.py      # per-date tracking
npm install && npm run test:site   # website in jsdom, incl. the generated .ics
```

`test_extraction.py` replays the 16 saved pages through the real LLM (costs a few cents).

## Getting started

Requires Python 3.10+. Node.js is only needed for the website tests.

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Choose a provider (Anthropic is the default):

```bash
export ANTHROPIC_API_KEY=...                     # PowerShell: $env:ANTHROPIC_API_KEY="..."
# or
export PROVIDER=qwen DASHSCOPE_API_KEY=...       # cheaper, pay-as-you-go
export BUDGET_LIMIT_USD=2.0                      # monthly cap enforced in code (default 2.0)
```

Run the pipeline (a full run costs a few tens of cents with a compact model):

```bash
export DRY_RUN=true  && python main.py    # plumbing check: no API call, nothing written
export DRY_RUN=false && python main.py    # real run: writes data/*.json
python rebuild_site.py                    # rebuild site_data.json from state.json, free
```

View the site locally (browsers block `fetch()` on `file://`):

```bash
python -m http.server 8000                # then open http://localhost:8000/
```

To deploy: GitHub → Settings → Pages → *Deploy from a branch* → `main` / `/ (root)`. The site only needs `index.html`, `assets/` and `data/site_data.json`.

## Project layout

```
index.html, assets/          static site (HTML, CSS, vanilla JS, no dependencies)
config.py                    the 16 sources
fetch.py                     page / PDF retrieval, blocked-page detection
extract.py                   LLM call, structural repair, rescue, second pass
verify.py                    dates checked against the source text
schema.py, prompts.py        Pydantic schema and system prompt
state.py, alerts.py          change tracking, site data
budget.py                    monthly cost guard
main.py                      full pipeline
rebuild_site.py              regenerate site data without any API call
test_*.py, test_site.js      offline tests
test_data/                   the 16 captured pages used as fixtures
data/                        state.json, site_data.json, budget.json …
```

## Known limitations

- **Three sources cannot be read automatically**: Brittany, Grand Est (region) and Auvergne-Rhône-Alpes sit behind anti-bot protection (a Cloudflare challenge, or a redirect to the home page). They are detected and reported as failures rather than silently treated as empty. I deliberately did not build a workaround for the protection. Covering them needs manual input of the calendar text, or an agreement with the agencies.
- **LLM variance**: the same page can be split differently between two runs. The dedup layer absorbs most of it; a few cards may still be reshuffled.
- **Scope**: mostly short-film funds, plus the CNC; not an exhaustive list of French film aid.
- Dates in "month year" form without a day are not handled specially.
- Data comes from official pages and can lag or be wrong: always check the source before applying (the site says so).

## Roadmap

1. Weekly GitHub Actions run with the API key in GitHub Secrets, committing the updated `data/*.json`.
2. Manual-input files for sources that block automation, with a staleness warning.
3. More regions and funds.
