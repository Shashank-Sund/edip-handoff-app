# Instructions for AI agents (Genie Code, Claude Code and others)

You are deploying or changing the ED to inpatient handoff reference app. Everything on Databricks is one bundle (`databricks.yml`); the human guide is `docs/setup.md`.

## Deploy this app ("deploy this app")

1. Confirm with the user: the target (`dev` unless they say otherwise), the catalog name (default `edip`), and that spending about 2 USD on serverless compute and model calls is fine. Ask whether the default model `databricks-gpt-oss-120b` exists in Serving; if not, ask which chat endpoint to use (`--var model=<name>`).
2. If the catalog does not exist, create it with SQL (works with Default Storage): `CREATE CATALOG IF NOT EXISTS <catalog>`. Do not use the catalogs REST API for this.
3. `databricks bundle deploy -t <target> [--var catalog=<catalog>] [--var model=<endpoint>]`
4. `databricks bundle run edip_setup -t <target> [same --var flags]`. About 2 hours. Tasks: `data`, `pipeline`, `handoffs`, `lakebase`. If a task fails, read its error, fix the cause, and repair the run rather than starting over.
5. Only after step 4 succeeds: `databricks bundle run edip_app -t <target> [same --var flags]` and give the user the app URL.
6. Verify: the `lakebase` task output reports about 702 encounters and about 1,300 drafts; the app's `/api/health` returns `"status": "ok"`, `"mode": "live"` and the same counts.

From a terminal, `scripts/deploy.sh <target> [--var ...]` does steps 2 to 5.

Without a terminal (workspace UI): open the repo as a Git folder, open `databricks.yml`, choose Deploy, then in the Deployments panel run `edip_setup`, then `edip_app`. If another deployment of this app already exists in the workspace, ask the user for their own `app_name`, `lakebase_project` and `catalog` values first.

## Rules

- Never put secrets, tokens, hosts or workspace ids in the repo or the bundle. The Zerobus path keeps its secret in `~/.config/edip/zerobus.env` (see `docs/zerobus.md`); it is optional.
- All data is synthetic. Do not connect real patient data without the user's explicit request and their organization's approval; if they do, set `EDIP_SYNTHETIC_DATA=false` and review `docs/feature-status.md` with them first.
- Do not run `databricks bundle destroy` or drop catalogs unless the user asks.
- Writing style for docs and UI text: plain words, no em or en dashes.

## Change the app

- Local first: `app/README.md` (mock mode, no Databricks needed). Tests: `pytest -q` at the repo root; client checks: `npm run typecheck && npm run lint` in `app/client`.
- Server: `app/server` (FastAPI). Client: `app/client` (React, Vite, Tailwind). Guide text for clinicians: `app/client/src/components/guide/content.tsx`; keep it in step with any UI change.
- Templates: `ai/templates/*.yaml`. Generation logic: `ai/edip_ai/`. Pipeline: `pipelines/`. Synthetic data: `synthetic/`.
- After changing code, `databricks bundle deploy -t <target>`; for app changes also `databricks bundle run edip_app -t <target>`.

## Repo map

| Path | Contents |
| --- | --- |
| `databricks.yml` | Bundle: Lakebase project, app, pipeline, jobs, variables, targets |
| `jobs/` | `setup_data.py`, `setup_lakebase.py`, `handoff_job.py` |
| `pipelines/` | Bronze to silver and gold (Lakeflow declarative pipeline), HL7 and FHIR parsers |
| `ai/` | Chart packet, prompt, validator, rules, templates, evaluation |
| `app/` | Server, client, mock fixtures builder |
| `synthetic/` | Generator, renderers, committed Synthea population, Zerobus replayer |
| `setup/load_bronze.py` | Bronze loader (used by the setup job, or locally with a warehouse) |
| `scripts/` | `deploy.sh`, `demo_ctl.py` (status, up, down), `setup_zerobus.py` |
| `docs/` | `setup.md`, `zerobus.md` |
