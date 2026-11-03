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

## Live mode (deployed)

The bundle (`databricks.yml`) deploys the app with `EDIP_MODE=live` and a postgres resource bound to the Lakebase project. The app then:

- reads `gold.encounter_state_pg` and `gold.handoff_draft_pg` (synced from Unity Catalog by the setup job, refreshed by the handoff job) and reloads them at most once a minute;
- writes every clinician action to `app.handoff_action` (append only: edits, attestations, sign offs and read backs, with the persona, the role and the signed in user's email), which it creates on first start;
- authenticates to Postgres as the app's service principal with a short lived token (`EDIP_PG_ENDPOINT`); the setup job grants it read access to the synced tables.

Settings (`config.env` of the app in `databricks.yml`): `EDIP_CLOCK_MODE=replay` keeps the replay clock for the synthetic week (`live` follows the current time, for real feeds); `EDIP_DEMO_PERSONAS=true` lets viewers switch roles (`false` takes the role from `EDIP_ROLE_MAP`, a JSON map of email to persona id, and shows the signed in user).

The app is built from the repository root: `package.json` builds the client into `app/server/static`, `requirements.txt` installs the server, and the command is `uvicorn main:app --app-dir app/server`.

## Static demo

A public, backend free build of the same client. `src/demo` is a TypeScript port of the server's mock mode
(`data.py`, `logic.py`, `workflow.py`, `personas.py` and the routes of `main.py`): the browser loads the fixtures
from `/data`, decompresses `drafts.jsonl.gz` with `DecompressionStream`, and answers every API call itself, with
the same rules and messages. Actions stay in the tab and reset on reload. The default build (served by FastAPI)
does not contain this code: `__STATIC_DEMO__` is a build time constant.

```bash
# Build: the client with the in-browser API into app/client/dist/demo, then the data it loads
cd app/client && npm run build:demo && cd ../..
.venv/bin/python app/fixtures/static_demo.py          # copies app/fixtures/data and writes templates.json

# Look at it locally
cd app/client && VITE_DEMO=static npx vite preview --outDir dist/demo --port 5191
STATIC_DEMO=1 node scripts/flow.mjs http://127.0.0.1:5191         # also screenshots.mjs and a11y.mjs
```

Parity test: the port must answer exactly as the server does. `app/fixtures/parity.py` drives the FastAPI app
through a fixed list of requests (config, boards at many times and for each role, cases, analytics, a full
edit, attest, sign and read back sequence with every refused action, views before and after) and writes the
answers to `app/fixtures/parity`; the Vitest test `src/demo/parity.test.ts` replays them through the port and
compares each one. Only the wall clock `now` of `/api/health` is normalized. CI runs it on fixtures built from
the stand in world (no network or credentials).

```bash
.venv/bin/python app/fixtures/parity.py                  # stand in world; or --fixtures app/fixtures/data
cd app/client && npx vitest run src/demo
```

Deploy and remove (AWS CLI default profile, us-east-1, stack `public-demo-edip-handoff`: a private S3 bucket
behind CloudFront with origin access control, security headers and a strict CSP; see
`deploy/aws/static-demo.cfn.yaml`):

```bash
scripts/deploy_static_demo.sh       # build, scan the bundle, deploy the stack, upload, invalidate
scripts/teardown_static_demo.sh     # empty the bucket and delete the stack
```
