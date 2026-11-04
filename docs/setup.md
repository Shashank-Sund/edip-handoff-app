# Deploy to your Databricks workspace

This guide takes you from a fresh clone to the running app in your own workspace. You type three commands; the rest runs by itself for about 20 minutes and costs well under 1 USD. Everything is synthetic data, so nothing here touches real patients.

**In a hurry?** Steps 1 to 3 are all you need. The rest of the page is reference.

## 1. Check you have what you need (5 minutes)

| You need | How to check | If not |
| --- | --- | --- |
| A Databricks workspace with Unity Catalog, serverless compute, Databricks Apps and Lakebase | The left menu shows **Catalog**, **Compute > Apps** and **Lakebase** (or **Compute > Database**) | A free trial workspace has all of these |
| Permission to create a catalog, apps, Lakebase projects and jobs | Workspace admins have it; ask yours otherwise | Ask an admin, or use an existing catalog (step 3, "Your own names") |
| The Databricks CLI, version 1.23 or later | `databricks --version` | macOS: `brew tap databricks/tap && brew install databricks`. Others: [install the CLI](https://docs.databricks.com/dev-tools/cli/install.html) |
| Python 3 and git | `python3 --version`, `git --version` | Install from python.org and git-scm.com |
| A chat model in Serving | **Serving** lists `databricks-gpt-oss-120b` | Any chat endpoint works; note its name for step 3 |

## 2. Log in and get the code (2 minutes)

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com --profile edip
export DATABRICKS_CONFIG_PROFILE=edip
git clone https://github.com/Shashank-Sund/edip-handoff-app.git
cd edip-handoff-app
```

The login opens your browser; sign in and come back. `export` tells every later command which workspace to use (run it again in any new terminal).

## 3. Deploy (one command, about 20 minutes)

First a 10 second check that finds problems before anything runs or costs money:

```bash
scripts/deploy.sh dev --check
```

It ends with `Preflight passed`, or with `STOP:` and what to do. Then deploy:

```bash
scripts/deploy.sh dev
```

If your workspace does not have `databricks-gpt-oss-120b`, add `--var model=<your endpoint>` to both commands.

**What you will see:**

1. `0/3 catalog`: creates the catalog `edip` (seconds).
2. `1/3 bundle deploy`: creates the database, pipeline, jobs and app (about a minute).
3. `2/3 app`: builds and starts the app and prints its URL (a few minutes). You can open it now; it says "data not loaded yet" until the next step finishes, then fills in by itself.
4. `3/3 setup job`: prints a **Run URL** and then waits. Open the URL to watch four tasks turn green: `data` (about 7 minutes), `pipeline` (about 4), `handoffs` (a minute: it loads the handoffs that come with the repo) and `lakebase` (about 3). You can close the terminal: the job keeps running in Databricks.

**Open the URL**, then read **How to use** in the app's left menu. It walks you through your first five minutes.

### The handoffs: shipped with the repo, or written by your model

The synthetic week is the same in every deploy, so the repo ships the handoffs that `databricks-gpt-oss-120b` wrote for it (`ai/reference/`), and setup loads them instead of making about 440 model calls. Setup checks that they match your data and templates exactly, and calls the model for everything if anything differs (for example another population or edited templates), so a handoff never describes a different chart.

To see your own model write them, either deploy with `--var drafts=model` (setup then takes 30 to 75 minutes and about 0.60 USD of model calls), or later run the job **edip-handoff-generator-dev** with its `drafts` parameter set to `model` after deleting the rows in `gold.handoff_draft`. Each draft shows which model wrote it.

### Your own names (shared workspaces)

The app name, the Lakebase project and the catalog must be unique in a workspace. If someone already deployed this app in yours, or you want to use an existing catalog, pick your own and pass them to every command:

```bash
scripts/deploy.sh dev --var app_name=edip-handoff-ana --var lakebase_project=edip-ana --var catalog=edip_ana
```

## Check it worked

- The job's last task, `lakebase`, ends with `702 encounters, 1320 drafts in Lakebase` (about 1,300 drafts).
- The app opens on the **Board** with patients waiting for a bed. Open one, sign it, switch roles to the receiving unit in the menu at the bottom left, and accept it.
- `<app URL>/api/health` shows `"status": "ok"` and `"mode": "live"`.

## Other ways to deploy

### Step by step (the same as the script)

```bash
# 0. Catalog (skip if it exists). In a SQL editor or with the CLI:
#    CREATE CATALOG IF NOT EXISTS edip;
#    (Workspaces without Default Storage: add MANAGED LOCATION 's3://<bucket>/edip')
databricks bundle deploy -t dev                 # 1. resources, no data yet
databricks bundle run edip_app -t dev           # 2. start the app; prints its URL
databricks bundle run edip_setup -t dev         # 3. data, pipeline, drafts, Lakebase (about 15 minutes)
```

Add the same `--var` flags to each command if you use any. Until step 3 finishes the app shows "data not loaded yet", then fills in by itself.

### From the workspace, without a terminal

1. **Workspace > Create > Git folder**, paste `https://github.com/Shashank-Sund/edip-handoff-app`, create it.
2. Create the catalog: open a SQL editor and run `CREATE CATALOG IF NOT EXISTS edip;`.
3. In the Git folder, open `databricks.yml` and choose **Deploy** (the deployments panel on the left).
4. In the same panel, run **edip_app**. The app's URL is under **Compute > Apps**.
5. Run **edip_setup** (about 15 minutes; **Jobs** shows the progress). The app fills in when it finishes.

### With an AI assistant

Open the repo in Genie Code, Claude Code or similar and say "deploy this app". `AGENTS.md` tells the assistant the steps and what to confirm with you first.

## Settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `catalog` | `edip` | Catalog for all tables |
| `lakebase_project` | `edip` | Lakebase project id |
| `app_name` | `edip-handoff` | App name, unique in the workspace |
| `model` | `databricks-gpt-oss-120b` | Chat endpoint that writes the narrative |
| `population` | `synthea` | `synthea`: the committed Synthea population (the reference dataset); `standin`: the built in population |
| `drafts` | `reference` | `reference`: load the handoffs shipped in `ai/reference/` when they match (minutes); `model`: the model writes every handoff (30 to 75 minutes) |
| `fhir_mode` | `batch` | `batch`: load FHIR events during setup; `zerobus`: stream them yourself ([zerobus.md](zerobus.md)) |
| `role_map` | `{}` | The app's `EDIP_ROLE_MAP`: a JSON map of signed in email to persona id ([app/README.md](../app/README.md)), used when `EDIP_DEMO_PERSONAS` is `false` |

Pass them per command (`--var model=databricks-claude-sonnet-5-5`) or set them per target in `databricks.yml`. The `cleanroom` target is an example of a second, independent copy in the same workspace.

`role_map` holds staff emails, so keep it out of `databricks.yml` and git. Put it in `.databricks/bundle/<target>/variable-overrides.json`, which git ignores and every `databricks bundle` command (and `scripts/deploy.sh`) reads for that target:

```json
{"role_map": "{\"rn@example.org\": \"ed-rn\", \"md@example.org\": \"ed-md\", \"micu@example.org\": \"unit-sgh-micu\"}"}
```

Or set it for one terminal: `export BUNDLE_VAR_role_map='{"rn@example.org": "ed-rn"}'`. The CLI's `--var` flag splits its value at commas and refuses double quotes, so it cannot carry a JSON map. Run `databricks bundle deploy`, then `databricks bundle run edip_app` (or `scripts/deploy.sh`) after any change. Anyone who can view the app's settings in the workspace can read the map.

App settings live in `databricks.yml` under the app's `config.env`:

- `EDIP_SYNTHETIC_DATA`: `true` for this synthetic data. Set it to `false` before any real patient data, and read [feature-status.md](feature-status.md) first.
- `EDIP_CLOCK_MODE`: `replay` shows the synthetic week with a clock you can move; `live` uses the current time (for real feeds).
- `EDIP_DEMO_PERSONAS`: `true` lets anyone switch roles (synthetic data only). `false` maps signed in users to roles with `EDIP_ROLE_MAP` (the `role_map` variable above); anyone not in the map can only read.

## Day to day

- **Costs.** Everything scales to zero except a running app. A job stops this deployment's app and pipeline at 01:00 Pacific every night. To start it again: `databricks apps start edip-handoff` (or **Compute > Apps > edip-handoff > Start**); `databricks apps stop edip-handoff` stops it.
- **Redraft handoffs** (after changing a template or the model): delete the rows in `gold.handoff_draft`, then run the job `edip-handoff-generator-dev` (its `drafts` parameter is `model`; `workers` sets how many model calls run at once, 4 by default; lower it if the log shows HTTP 429). It skips drafts that exist and refreshes Lakebase when it finishes.
- **Live updates.** The handoff job has a trigger on `<catalog>.silver.handoff_events`, paused so a deploy never starts model calls by itself. With new data arriving (for example through Zerobus) and the pipeline running, unpause it under **Jobs > edip-handoff-generator-dev > Schedules and triggers**: it then drafts only new admit decisions and changes, at most every 5 minutes. Set `EDIP_CLOCK_MODE` to `live` too. Pause it again to stop model spend.
- **Change the code**: `databricks bundle deploy -t dev`, then `databricks bundle run edip_app -t dev` for app changes.
- **Lakebase only**: `databricks bundle run edip_lakebase_setup -t dev` runs the `lakebase` task of setup alone (synced tables, a full refresh, the app's read grant; about 3 minutes), without loading data. Use it for a catalog with real data, where `edip_setup` must not run, and once after an upgrade that adds a column to the drafts, when the handoff job's log asks for it (docs/data-model.md, section 3).
- In the workspace, jobs and pipelines of the `dev` target show with a `[dev <your name>]` prefix; that is normal for development targets.

## Remove it

```bash
scripts/remove.sh dev                  # app, database, jobs, pipeline; keeps the catalog and its tables
scripts/remove.sh dev --drop-catalog   # also deletes the catalog edip and all its tables
```

Pass the same `--var` flags you deployed with. It lists what it will delete and asks you to type `yes`. Lakebase keeps a deleted project for 7 days, so to deploy again right away use another `--var lakebase_project=...`.

## Troubleshooting

| What you see | Cause and fix |
| --- | --- |
| `STOP: ...` from `deploy.sh` | The preflight found a problem; the message says what to do. Nothing was created. |
| `Catalog ... does not exist` on deploy | The catalog step was skipped. Create it (step 0 above), then deploy again. |
| `Metastore storage root URL does not exist` | Create the catalog with SQL, not the API; SQL uses Default Storage. Without Default Storage, give a location: `EDIP_CATALOG_LOCATION=s3://... scripts/deploy.sh dev`. |
| `app already exists` or `project already exists` | Someone used the same names. Pass your own (step 3, "Your own names"). |
| Setup task `lakebase`: role for the app does not exist | The app was not deployed with its database binding. Run `databricks bundle deploy -t dev` again, then **Repair run** on the job. |
| A setup task failed | Open the Run URL, read the task's error, fix the cause and choose **Repair run**: finished tasks are not repeated. |
| Handoff task slow, HTTP 429 in its log | Pay per token endpoints are rate limited. The job retries and tolerates a few failures; the next run redoes them. For more speed use a provisioned throughput endpoint. |
| App shows "data not loaded yet" | The setup job has not finished. The app checks again every 30 seconds. |
| App shows an error page | **Compute > Apps > <app> > Logs**. |
