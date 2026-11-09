# Healthcare Handoff: ED to inpatient handoff (reference Databricks App)

When the emergency department decides to admit a patient, this app drafts the handoff from the chart, as an SBAR for nurses, an I-PASS for physicians and a short Joint Commission minimum. The ED clinician reviews, edits and signs it; the receiving unit accepts it with a read back, and its action list becomes tasks for whoever has the patient. Every statement the model writes cites the chart record it came from. All data is synthetic.

**Live demo:** [d16hyap5wh4fcn.cloudfront.net](https://d16hyap5wh4fcn.cloudfront.net). It runs entirely in your browser on the reference week's real model drafts (no Databricks, nothing sent anywhere); switch roles in the menu at the bottom left, sign as the ED and accept as the unit. Changes reset on reload.

![The ED boarding board: patients waiting for a bed, severity, destination, handoff state and completeness](docs/images/board.png)

| A handoff, ready to review and sign | Analytics for the week |
| --- | --- |
| ![SBAR handoff with sign off checklist](docs/images/handoff.png) | ![ED census and boarding chart](docs/images/analytics.png) |
| ![I-PASS for the physician, dark theme](docs/images/handoff-physician-dark.png) | ![The board at phone width](docs/images/board-phone.png) |

## Deploy it

Needs the Databricks CLI 1.23 or later, logged in to your workspace, and Python 3. From a clone of this repo:

```bash
export DATABRICKS_CONFIG_PROFILE=<your profile>
scripts/deploy.sh dev --check     # 10 seconds: checks the CLI, login and model, creates nothing
scripts/deploy.sh dev             # about 20 minutes, well under 1 USD
```

One bundle deploys the Lakebase database, the pipeline, the jobs and the app, then generates a synthetic week, drafts every handoff and starts the app. A first time guide, other ways to deploy (workspace UI, AI assistant), settings and troubleshooting: [docs/setup.md](docs/setup.md). Before any real patient data, follow the checklist [Moving to real data](docs/setup.md#moving-to-real-data). AI agents: [AGENTS.md](AGENTS.md).

## Try it without Databricks

Needs Python 3.11 or later and Node.js 22 (20.19 or later also works; check with `node --version`; with nvm, `nvm use` reads `.nvmrc`). About 5 minutes.

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd synthetic && ../.venv/bin/python -m edip_synth --out out/world --synthea population/synthea_3000.json.gz && cd ..
for f in hl7 fhir clarity; do (cd synthetic && ../.venv/bin/python -m edip_synth.render.$f --truth out/world/truth --out out/world); done
.venv/bin/python app/fixtures/build.py
(cd app/client && npm ci && npm run build) && (cd app/server && ../../.venv/bin/uvicorn main:app --port 8000)
```

Open http://localhost:8000. Details: [app/README.md](app/README.md).

## How it works

```
Hospital feeds (synthetic): HL7 v2, Redox FHIR (batch or Zerobus), Clarity nightly tables
  -> bronze (Unity Catalog) -> Lakeflow pipeline: parse, reconcile across feeds, encounter state, triggers
  -> handoff job: chart packet, one model call per trigger, validator, completeness rules -> gold drafts
  -> Lakebase (synced drafts; the app's edits, attestations, sign offs, read backs, owners and tasks)
  -> app: board, handoff review and sign off, read back, tasks, analytics, a guide for clinicians
```

Every table, the Lakebase schema and ready to run queries: [docs/data-model.md](docs/data-model.md).

## Which guide to read

| If you want to | Read |
| --- | --- |
| Deploy the app to your workspace, step by step, and fix setup problems | [docs/setup.md](docs/setup.md) |
| Run it on real patient data: every setting to change, and how to check it worked | [docs/setup.md, Moving to real data](docs/setup.md#moving-to-real-data) |
| Know what each table holds, where a handoff's facts come from, and query them | [docs/data-model.md](docs/data-model.md) |
| Check which Databricks features are GA or preview, and what to confirm for HIPAA | [docs/feature-status.md](docs/feature-status.md) |
| Connect your own hospital feeds instead of the synthetic ones (start here) | [docs/ingestion/README.md](docs/ingestion/README.md) |
| Send HL7 v2 from your interface engine (Rhapsody, Cloverleaf, Mirth, Bridges) | [docs/ingestion/hl7v2.md](docs/ingestion/hl7v2.md) |
| Receive FHIR events from Redox | [docs/ingestion/fhir-redox.md](docs/ingestion/fhir-redox.md) |
| Load nightly Epic Clarity history (problems, home meds, allergies) | [docs/ingestion/clarity.md](docs/ingestion/clarity.md) |
| Try streaming the demo's FHIR events through Zerobus (optional) | [docs/zerobus.md](docs/zerobus.md) |
| Plan writing signed handoffs back to Epic (future work, not built) | [docs/ingestion/epic-writeback.md](docs/ingestion/epic-writeback.md) |
| Learn why it was built this way, and its known limits | [docs/how-it-was-built.md](docs/how-it-was-built.md) |
| Let an AI coding assistant deploy or change it | [AGENTS.md](AGENTS.md) |

| Folder | Read |
| --- | --- |
| `synthetic/` | [synthetic/README.md](synthetic/README.md): the generator, 32 logged edge cases, realism tests |
| `pipelines/` | [pipelines/README.md](pipelines/README.md): bronze to gold, deduplication, daylight saving time |
| `ai/` | [ai/README.md](ai/README.md): templates, prompt, validator, evaluation results, cost |
| `app/` | [app/README.md](app/README.md): pages, rules the server enforces, local runs |
| `docs/` | The guides above |

## Tests

`pytest -q` runs the generator, realism, renderer, parser, generation, evaluation and app API tests locally (no Databricks credentials). CI also typechecks, lints and builds the client.

## Data and licenses

Patients come from [Synthea](https://github.com/synthetichealth/synthea) (a 3,000 person population slice is committed in `synthetic/population/`); the ED visits, feeds and faults are generated on top. The hospitals (Shai General Hospital, Lakeside Community Hospital), people and identifiers are invented. Chart libraries: [TradingView Lightweight Charts](https://www.tradingview.com/lightweight-charts/) and Recharts (see `NOTICE`).

This project is licensed under the Apache License 2.0 (`LICENSE`).
