# Set up in your own workspace

Everything on the Databricks side is one Declarative Automation Bundle (`databricks.yml`). A deploy gives you the same app, data, pipeline, jobs and database as the reference workspace, from an empty catalog.

## What gets deployed

| Piece | What it is | Where |
| --- | --- | --- |
| Catalog and schemas | `bronze` (raw feeds), `silver` (parsed), `gold` (drafts), `synthetic_truth` (the generator's answer key) | `<catalog>` (default `edip`) |
| Synthetic data | One week, 700 ED visits, 120 admissions, two invented hospitals, rendered as HL7 v2, Redox FHIR and Clarity tables | `<catalog>.bronze.*`, volume `<catalog>.bronze.landing` |
| Pipeline `edip-pipeline-<target>` | Lakeflow declarative pipeline, serverless, triggered: bronze to silver and gold | `pipelines/` |
| Job `edip-handoff-generator-<target>` | Writes a draft handoff at every admit decision and meaningful change, with a model | `jobs/handoff_job.py`, `ai/` |
| Job `edip-setup-<target>` | Runs everything above in order, then syncs to Lakebase | `jobs/setup_*.py` |
| Lakebase project `<lakebase_project>` | Postgres (Lakebase Autoscaling): synced drafts and encounters, and the app's action log (schema `app`) | `databricks.yml` |
| App `<app_name>` | FastAPI and React; reads and writes Lakebase | `app/` |
| Job `edip-nightly-shutdown-<target>` | Stops the app and pipelines at 01:00 Pacific every night | `scripts/demo_ctl.py` |

## Requirements

- A Databricks workspace with Unity Catalog, serverless jobs and pipelines, Databricks Apps and Lakebase Autoscaling. Workspaces on the free trial have all of these.
- A chat model endpoint. The default is the pay per token `databricks-gpt-oss-120b`; any chat endpoint works (`--var model=<endpoint>`).
- Permissions: create a catalog (or use an existing one where you can create schemas), create apps, create Lakebase projects, and run jobs.
- The Databricks CLI 1.23 or later, logged in: `databricks auth login --host <workspace-url> --profile <name>`, then `export DATABRICKS_CONFIG_PROFILE=<name>`.
- A SQL warehouse, only if the catalog does not exist yet (to create it with SQL, which works with Default Storage).

## Option A: one command

```bash
git clone <this repo> && cd edip-handoff-app
scripts/deploy.sh dev                       # or: scripts/deploy.sh dev --var catalog=my_catalog
```

It takes about two hours, almost all of it the model writing about 430 drafts at two at a time. The model calls cost about 1 USD at list price; serverless compute about the same.

## Option B: step by step (CLI, or the workspace UI)

**0. Catalog.** If the catalog does not exist, run in a SQL editor or notebook:

```sql
CREATE CATALOG IF NOT EXISTS edip;
```

(To use another name, pass `--var catalog=<name>` to every command below.)

**1. Deploy the bundle.** Creates the Lakebase project, the pipeline, the jobs and the app (no data yet).

```bash
databricks bundle deploy -t dev
```

In the workspace UI: open the repo as a Git folder, open `databricks.yml`, choose **Deploy**.

**2. Run the setup job.** Generates the data, runs the pipeline with a full refresh, drafts every handoff, and creates the Lakebase synced tables and the app's read access.

```bash
databricks bundle run edip_setup -t dev
```

In the UI: **Deployments > edip_setup > Run**. If one task fails, fix the cause and use **Repair run**; finished tasks are not repeated.

**3. Start the app.** Builds the client, installs the server and starts it.

```bash
databricks bundle run edip_app -t dev
```

The command prints the app's URL; it is also under **Compute > Apps**. Open it, then read **How to use** in the app.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `catalog` | `edip` | Catalog for all tables |
| `lakebase_project` | `edip` | Lakebase project id |
| `app_name` | `edip-handoff` | App name, unique in the workspace |
| `model` | `databricks-gpt-oss-120b` | Chat endpoint for the narrative |
| `population` | `synthea` | `synthea`: the committed Synthea population (the reference dataset); `standin`: the built in population |
| `fhir_mode` | `batch` | `batch`: load FHIR events during setup; `zerobus`: stream them yourself ([zerobus.md](zerobus.md)) |

Set them per command (`--var model=databricks-claude-sonnet-5-5`) or per target in `databricks.yml`. The `cleanroom` target is an example of a second, independent copy in the same workspace.

App settings (in `databricks.yml` under the app's `config.env`): `EDIP_CLOCK_MODE` (`replay` shows the synthetic week; `live` uses the current time for live feeds), `EDIP_DEMO_PERSONAS` (`true` lets anyone switch roles; `false` maps signed in users to roles with `EDIP_ROLE_MAP`).

## Check it worked

- The setup run's last task prints the encounter and draft counts in Lakebase (702 encounters and about 1,300 drafts).
- The app's `/api/health` shows `"mode": "live"` and the same counts.
- On the Board, the clock opens on the busiest hour; open a patient and switch personas to sign and accept a handoff.

## Day to day

- **Costs.** Everything scales to zero except a running app. `python scripts/demo_ctl.py status|up|down` lists, starts or stops this project's apps, pipelines and warehouses; the nightly job runs `down` at 01:00.
- **Regenerate drafts** (for example after changing a template or the model): run `edip-handoff-generator-<target>`. It skips drafts that already exist; delete `gold.handoff_draft` rows first to redo them. It refreshes Lakebase when it finishes.
- **Change the code**: `databricks bundle deploy -t dev`, then `databricks bundle run edip_app -t dev` for app changes.

## Remove it

```bash
databricks bundle destroy -t dev
```

This deletes the jobs, pipeline, app and Lakebase project (Lakebase keeps a deleted project for 7 days). The catalog and its tables stay; drop them with `DROP CATALOG <catalog> CASCADE`.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Catalog ... does not exist` on deploy | Step 0 was skipped. Create the catalog, then deploy again. |
| `Metastore storage root URL does not exist` | Create the catalog with SQL (step 0), not the API; SQL uses Default Storage. |
| Setup task `lakebase`: role for the app does not exist | The app was not deployed with its postgres resource. Run `bundle deploy` again, then Repair run. |
| Handoff job slow, HTTP 429 in the log | Pay per token endpoints are rate limited per workspace. It retries; leave it running, or use a provisioned throughput endpoint. |
| Model endpoint not found | Your region lacks the default model. Pass `--var model=<an endpoint in Serving>`. |
| App shows an error page | **Compute > Apps > <app> > Logs**. Most often Lakebase is not synced yet: finish the setup job. |
