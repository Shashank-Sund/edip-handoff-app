# How it was built

A short history of this reference app: what it set out to do, the phases as they happened, the decisions that shaped it, what changed from the first plan, and its known limits. For the tables themselves, see [data-model.md](data-model.md).

## The goal

Draft emergency department (ED) to inpatient handoffs in near real time from data shaped like real hospital feeds, and let clinicians review, edit, sign and acknowledge them in a Databricks App. Every statement the model writes cites the chart record it came from. All data is synthetic. Writing back to the EHR is out of scope for version 1 ([ingestion/epic-writeback.md](ingestion/epic-writeback.md)).

## Phases, as they happened

| Phase | What was built | Where |
| --- | --- | --- |
| 0. Scaffolding | Repo, sensitive data scanner with a pre-commit hook and CI, `demo-up` and `demo-down` controller | `scripts/` |
| 1. Synthetic world | Generator core; HL7 v2, Redox FHIR and Clarity renderers; a realism test suite; two fix passes until every seed held its targets; bronze loader and Zerobus replayer | `synthetic/`, `setup/load_bronze.py`, `tests/realism/` |
| 2. Pipeline | One record contract for both real time feeds, HL7 and FHIR parsers, the Lakeflow pipeline in a bundle; then cross feed deduplication, the first Lakebase sync and a freshness probe (median 34 s landing to silver) | `pipelines/` |
| 3. Handoff generation | Templates, rules, chart packet, prompt, validator, job and evaluation; then the severity floor, facility local times in drafts, incremental writes, the nightly shutdown job, synced table refresh after each run, a DST fix and a numeric validator that compares by value; points end at transfer except ED addenda; drafts carry evidence and trends | `ai/`, `jobs/handoff_job.py` |
| 4. App | Board, handoff review, sign off and read back, analytics and templates in mock mode first; then live mode on Lakebase, the redeployable bundle and the clinician guide; a clean room deploy into an empty catalog; ingestion guides, the feature status page, client tests and accessibility | `app/`, `databricks.yml`, `docs/` |
| 5. Launch | Apache 2.0 license and NOTICE; a public static demo with a parity test against the server; newcomer setup (`deploy.sh --check`, `remove.sh`); reference drafts so setup takes about 20 minutes; review fixes so a signature means what it says | `ai/reference/`, `app/client/src/demo/`, `scripts/` |

All of this happened over two days, November 2 and 3, 2026. `git log --reverse --stat` shows each step.

## Key decisions and why

- **Synthetic world first.** Real data cannot be published, and a generator that knows the truth lets every step be scored. The world is one week at two hospitals on a committed Synthea population, deterministic for a seed, with 32 planted faults (F01 to F32) logged in `fault_manifest.jsonl` and `render_plan.json`.
- **One world, rendered three ways.** The same timeline becomes HL7 v2 files, Redox style FHIR events and Clarity nightly tables, so cross feed joins reconcile exactly and a data team sees the shapes it already has. Both real time feeds parse into one contract (`pipelines/edip_contract.py`), so silver has one schema and either feed can be dropped.
- **Deterministic rules for facts, the model only for narrative.** Allergies, code status, isolation, vitals, labs, medications, pending results and destination are rendered from data. One model call per generation point writes the narrative elements shared by the three templates, and each statement must cite source ids from the chart packet.
- **A validator before anyone sees a draft.** It removes unknown source ids and highlights numbers it cannot find in the cited sources, rather than hiding them. On the reference week, 99.8% of narrative statements cite a real source.
- **A rule floor for severity.** The model alone called patients one level less sick than the reference (agreement 0.33). A floor from structured data (critical results, ICU level, shock range vitals, ESI) lifts agreement to 0.977; the model can raise severity, never lower it.
- **Lakebase for app state.** The app needs low latency reads and transactional writes. The encounter state and every draft version reach Postgres as Triggered synced tables, and clinician actions go to one append only table, `app.handoff_action`, which is its own audit trail.
- **A bundle for redeploys.** `databricks.yml` holds the Lakebase project, pipeline, jobs and app, so a new workspace or a second copy (`cleanroom` target) deploys with one command and no hosts or ids in the repo.
- **Reference drafts for fast setup.** The synthetic week is deterministic, so the drafts the model wrote once are shipped in `ai/reference/` and loaded when the generation points and templates match exactly. Setup drops from over an hour to about 15 minutes; any mismatch falls back to the model.
- **A static demo with a parity test.** The public demo runs the server's mock mode logic ported to TypeScript in the browser. A parity test runs the same scenarios against both and fails on any difference.

## What changed from the original plan

| Planned | What was built |
| --- | --- |
| HL7 files read by Auto Loader into `bronze.hl7v2_messages` | Setup loads all HL7 messages from one JSONL file in the volume into `bronze.hl7_raw_messages`; the pipeline streams from that table |
| FHIR through Zerobus into `bronze.fhir_events` | `bronze.redox_fhir_events`, loaded in batch by default; Zerobus is an optional path (`fhir_mode=zerobus`, [zerobus.md](zerobus.md)) |
| Silver `med_admin`, `allergies`, `code_status`, `isolation`, `pending_orders` | `med_events`, `allergy_events`, `flag_events` (code status and isolation together); pending results come from result status in the chart packet. `quarantine` and `handoff_events` added |
| `ai_classify` and `ai_extract` on notes, `ai_query` for the narrative | Not used. One chat call per generation point through the Databricks SDK, model set in `ai/config.yaml` |
| Claude Sonnet when available; on demand regenerate with the strongest model | Default stayed `databricks-gpt-oss-120b`; any chat endpoint works with `--var model=`. No on demand regenerate in the app |
| Synced `boarding_list` and `handoff_latest` | Synced `encounter_state_pg` and `handoff_draft_pg` (all versions, for history and "what changed"). `gold.boarding_list` and `gold.handoff_latest` are still built but the app does not read them |
| Five native Lakebase tables (case, field edit, signoff, ack, version) | One append only `app.handoff_action` with a `kind` column; versions live in the synced drafts; workflow state is computed in the app |
| On behalf of user auth | The app reads as its service principal and records the signed in user from forwarded headers; unit level access is enforced in app code |
| A 25 item realism checklist | Realism tests grouped as temporal, distribution, clinical and hygiene, run on 5 seeds; passed on 200 generated worlds |
| Budgets and budget policies | Not in the bundle; cost control is the triggered pipeline, scale to zero Lakebase, a paused live trigger and the nightly shutdown job |
| A public mock demo | A static demo on S3 and CloudFront with the parity test |
| About 120 admitted encounters | 118 with a handoff on the reference week (702 encounters in all, including 2 canceled wrong patient registrations) |

## Known limits

- One synthetic week at two EDs. Boarding medians move by about 30% between seeds, because there are only about 120 admissions.
- The handoff job reads silver into memory once per run. A production deployment would read per encounter.
- Pay per token endpoints are rate limited per workspace (about 6 handoff calls a minute on the trial workspace); a full model backfill takes 30 to 75 minutes. Production needs provisioned throughput or a hosted endpoint with higher limits.
- The synced tables are Triggered and refreshed by setup and by the handoff job, not by pipeline runs. In live mode, encounter state reaches the app at the next handoff run.
- Workflow states such as "Transferred without sign off" are computed in the app from drafts and actions; no table stores them.
- An HL7 time without a UTC offset sent more than an hour after the repeated DST hour cannot be resolved and takes the earlier reading.
- Allergy source ids (`allergy:<n>`) are positions, not keys.
- `gold.handoff_draft` has no run id; token counts repeat on the three template rows of each call.
- No writeback to the EHR. Before any real patient data, review [feature-status.md](feature-status.md): some features (VARIANT on compliance profile workspaces, Beta bundle resources, preview system tables) need confirming with your account team.
