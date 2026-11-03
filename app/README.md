# App

The clinician app: an ED boarding board, the handoff for each patient in three templates, sign off and read back, per unit access, and analytics. FastAPI serves the API and the built React client.

## Run it locally (mock mode)

Mock mode needs no Databricks connection. It replays the reference week from fixtures, with demo personas and in memory actions.

```bash
# 1. Fixtures from a rendered world (synthetic/out/world). Add --drafts <export.jsonl> to show real model
#    drafts exported with ai/eval/export_drafts.py; without it the deterministic mock model writes them.
.venv/bin/python app/fixtures/build.py

# 2. Client (Node 20.19 or later for the dev server; the production build works on any Node 20)
cd app/client && npm ci && npm run build      # writes app/server/static

# 3. Server
cd ../server && ../../.venv/bin/uvicorn main:app --port 8000
```

Open http://localhost:8000. For client development, run `npm run dev` in `app/client` (port 5180, proxies `/api` to port 8000).

Checks: `npm run typecheck`, `npm run lint`, `node scripts/screenshots.mjs <url> <dir>` (every page on desktop, phone and dark; fails on console errors or sideways scroll at 390 px) and `node scripts/flow.mjs <url>` (attest, sign, switch to the receiving nurse, acknowledge). API tests: `tests/test_app.py`.

## What it does

| Page | For | Shows |
| --- | --- | --- |
| Board | everyone | Patients boarding or arrived in the last 12 hours: severity, destination, boarding time, handoff state per template, completeness. A receiving nurse sees only patients bound for their unit. |
| Handoff | everyone | The draft in SBAR, I-PASS or the Joint Commission minimum. Structured items come from the chart; narrative items are model drafted, with numbered chips that open each cited source and highlighted numbers the validator could not find. Sign off checklist, read back, what changed, vital sign and lab trends, history. |
| Analytics | everyone | ED census and boarding by hour, boarding time by destination, what blocked sign off, completeness and severity at transfer. |
| Templates | everyone | The three templates, their elements and rules, from `ai/templates`. |

## Rules the server enforces

- Only the sending ED clinician edits, attests and signs: nurses sign SBAR, physicians sign I-PASS, either signs the custom template. Structured items cannot be edited; they come from the chart.
- Sign off is blocked until every hard and stale rule passes, is attested from its fixed list, or (for narrative items) is written by the clinician. A callback number is required.
- Only the receiving clinician acknowledges, after sign off, with a read back of at least a sentence.
- A draft that arrives after sign off or acknowledgement is flagged until it is signed or acknowledged again. Edits and attestations carry forward to newer versions; an edit is flagged when the model's text changed underneath it.
- After the patient leaves the ED the handoff is final, except for ED addenda (a late ED note or a critical result collected in the ED).
- Every action carries the time it applies to, so the replay clock shows the board as it was.

## Live mode

Live mode (`EDIP_MODE=live`) reads the same shapes from Lakebase (synced `handoff_draft` and `encounter_state`) and writes actions to native tables, as the signed in user. It is set up at deploy time; see the bundle.
