# Deploy to your Databricks workspace

This guide takes you from a fresh clone to the running app in your own workspace. You type three commands; the rest runs by itself for about 20 minutes and costs well under 1 USD. Everything is synthetic data, so nothing here touches real patients. To run it on real data later, follow [Moving to real data](#moving-to-real-data).

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

- `EDIP_SYNTHETIC_DATA`: `true` for this synthetic data. It is what allows the replay clock and demo personas; before any real patient data, follow [Moving to real data](#moving-to-real-data).
- `EDIP_CLOCK_MODE`: `replay` shows the synthetic week with a clock you can move; `live` uses the current time.
- `EDIP_DEMO_PERSONAS`: `true` lets anyone switch roles (synthetic data only). `false` maps signed in users to roles with `EDIP_ROLE_MAP` (the `role_map` variable above); anyone not in the map can only read.

Every app setting, and what changes with real data: [Moving to real data](#moving-to-real-data), step 2.

## Day to day

- **Costs.** Everything scales to zero except a running app. A job stops this deployment's app and pipeline at 01:00 Pacific every night. To start it again: `databricks apps start edip-handoff` (or **Compute > Apps > edip-handoff > Start**); `databricks apps stop edip-handoff` stops it.
- **Redraft handoffs** (after changing a template or the model): delete the rows in `gold.handoff_draft`, then run the job `edip-handoff-generator-dev` (its `drafts` parameter is `model`; `workers` sets how many model calls run at once, 4 by default; lower it if the log shows HTTP 429). It skips drafts that exist and refreshes Lakebase when it finishes.
- **Live updates.** The handoff job's trigger is paused, so a deploy never starts model calls by itself. To draft new handoffs as data arrives (Zerobus replays or real feeds), see step 3 of [Moving to real data](#moving-to-real-data).
- **Change the code**: `databricks bundle deploy -t dev`, then `databricks bundle run edip_app -t dev` for app changes.
- **Lakebase only**: `databricks bundle run edip_lakebase_setup -t dev` runs the `lakebase` task of setup alone (synced tables, a full refresh, the app's read grant; about 3 minutes), without loading data. Use it for a catalog with real data, where `edip_setup` must not run, and once after an upgrade that adds a column to the drafts, when the handoff job's log asks for it (docs/data-model.md, section 3).
- In the workspace, jobs and pipelines of the `dev` target show with a `[dev <your name>]` prefix; that is normal for development targets.

## Moving to real data

Everything above runs on synthetic data. This section is the checklist for pointing a deployment at real patients. Do it only with your organization's approval, in a workspace set up for protected health information (PHI), and read [feature-status.md](feature-status.md) first.

### Demo and real data side by side

| | Demo (as deployed) | Real data |
| --- | --- | --- |
| `EDIP_SYNTHETIC_DATA` | `true`: the data is synthetic, so the two demo features below are allowed | `false`, or not set: both demo features are off, whatever the other settings say |
| Clock | Replay: one recorded week, with a clock you move back, forward or play | Live: the current time. A read only, logged past view can look back up to `EDIP_PAST_VIEW_HOURS` hours (step 4) |
| Who you are | Demo personas: anyone picks any role in the menu at the bottom left | Your own sign in. Your role comes from `EDIP_ROLE_MAP`; anyone not in it can only read |

### How the app decides

The app reads its settings once, when it starts (`app/server/config.py`):

- Unless `EDIP_SYNTHETIC_DATA` is `true`, the app treats the data as real. The clock is live whatever `EDIP_CLOCK_MODE` says, demo personas are off whatever `EDIP_DEMO_PERSONAS` says, each signed in user's role comes from `EDIP_ROLE_MAP`, and anyone not in the map is a read only viewer.
- A missing `EDIP_SYNTHETIC_DATA` counts as `false`. Forgetting it is safe: you get the real data behavior, not the demo.
- Mock mode (`EDIP_MODE=mock`, the local run on fixtures) is always synthetic. The bundle deploys the app with `EDIP_MODE=live`, which reads Lakebase.

The settings live in `databricks.yml`, under the app's `config.env`. The line to change already carries a reminder:

```yaml
          - {name: EDIP_SYNTHETIC_DATA, value: "true"}   # set to false (or remove) before any real patient data
```

A change takes effect only after a redeploy: `databricks bundle deploy -t <target>`, then `databricks bundle run edip_app -t <target>`.

### The checklist

**1. A separate catalog and real feeds.** Deploy with your own catalog (`--var catalog=<name>`) that never held the synthetic week, and land your feeds in its bronze tables as [ingestion/README.md](ingestion/README.md) explains. Do not run `edip_setup` against that catalog: its `data` task generates the synthetic week and replaces the bronze tables, and its `pipeline` task runs a full refresh. Its last task, `lakebase` (`jobs/setup_lakebase.py`), is the step that creates the Lakebase synced tables and lets the app read them, and the job `edip_lakebase_setup` runs that step on its own, with no data loading:

```bash
databricks bundle run edip_lakebase_setup -t <target> --var catalog=<name>
```

Run it once the pipeline has filled silver and the handoff job has run once (step 3; the job creates `gold.handoff_draft`). It creates the synced tables, waits for the first sync and grants the app read access (about 3 minutes); later handoff runs keep Lakebase up to date. Run it again if a synced table is recreated, or when a handoff run's log asks for it after an upgrade.

**2. App settings.** In the app's `config.env` in `databricks.yml`, except the role map, which is a bundle variable (below):

| Setting | Set it to | What it does |
| --- | --- | --- |
| `EDIP_SYNTHETIC_DATA` | `false` | The switch described above. Removing the line has the same effect. |
| `EDIP_CLOCK_MODE` | `live` | Already forced to `live` when the data is not synthetic; set it anyway so the file says what happens. |
| `EDIP_DEMO_PERSONAS` | `false` | Already forced off when the data is not synthetic; set it anyway for the same reason. |
| `EDIP_ROLE_MAP` | A JSON map of email to persona id (below), through the bundle variable `role_map` | Gives each signed in user their role. Anyone not in it can read but not change anything. |
| `EDIP_TIMEZONE` | Your facilities' time zone, an IANA name such as `America/Chicago` | The server and the client show times in it. Default `America/New_York`. A name the app does not know falls back to the default, with a warning in the app log. |
| `EDIP_STALE_BOARDING_HOURS` | Check the default, 24 | With the live clock, a patient with an admit decision and no ED departure for longer than this is left off the board and the boarding counts, so a departure your feeds missed does not keep them boarding forever. |
| `EDIP_ACTIONS_TTL` | Check the default, 2 | Seconds the app reuses the clinician actions it read from Lakebase before reading them again, so every app process sees what the others saved. |
| `EDIP_PAST_VIEW_HOURS` | Check the default, 72 | How far back the past view goes, in hours (step 4); at most 8760. `0` turns it off. |
| `EDIP_SHIFT_CHANGES` | Your ED's shift change times, local, for example `07:00,19:00` (the default) | ED nurses and physicians see a shift change bar on the board before each one, and Analytics measures handovers against them. A value the app cannot read falls back to the default, with a warning in the app log. |
| `EDIP_SHIFT_WARNING_MINUTES` | Check the default, 30 | How long before a shift change the bar shows. |
| `EDIP_STALE_OWNER_HOURS` | Check the default, 14 | An owner who has not acted on a patient, taken it or been given it for longer than this no longer counts: the patient shows as Unassigned. Set it a little over one shift. |

Persona ids for `EDIP_ROLE_MAP` (details in [app/README.md](../app/README.md#live-mode-deployed)):

| Persona id | Role |
| --- | --- |
| `ed-rn` | ED nurse: edits, attests and signs SBAR and the custom template; owns patients for the nurse role |
| `ed-md` | ED physician: edits, attests and signs I-PASS and the custom template; owns patients for the physician role |
| `hosp-md` | Admitting physician: accepts I-PASS and the custom template |
| `unit-<facility>-<unit code>`, lowercased | Receiving nurse of one unit: sees only patients bound for it, accepts SBAR and the custom template. Use the facility and unit codes as they appear in your data; in the synthetic week, `unit-sgh-micu` or `unit-lks-3s` |
| `viewer` | Read only. You do not need to list viewers: anyone not in the map gets this role |

Map every ED nurse to `ed-rn` and every ED physician to `ed-md`, one line per email. Each person is their sign in email, so many people share one persona id. The role map is also the list behind **Assign to a colleague** and **Take over**: a colleague shows there once they are in the map, by email until they first act in the app (an edit, a signature, a Take), then by the display name they last used: the app reads it back from `app.handoff_action` and `app.assignment`, so it survives a restart. `ed-rn-2` and `ed-md-2` are the second demo nurse and physician; a deployment for real use does not need them.

A worked example, with the rest of the app's `config.env` unchanged (the `EDIP_ROLE_MAP` line stays as it is: it reads the variable):

```yaml
          - {name: EDIP_SYNTHETIC_DATA, value: "false"}
          - {name: EDIP_CLOCK_MODE, value: live}
          - {name: EDIP_DEMO_PERSONAS, value: "false"}
          - {name: EDIP_ROLE_MAP, value: "${var.role_map}"}
          - {name: EDIP_TIMEZONE, value: America/Chicago}
```

The map holds staff emails, so it stays out of `databricks.yml` and git. Put it in `.databricks/bundle/<target>/variable-overrides.json` in your copy of the repo; git ignores that folder, and every `databricks bundle` command (and `scripts/deploy.sh`) reads the file for that target. The value is a string that holds the JSON map:

```json
{"role_map": "{\"ed.nurse@example.org\": \"ed-rn\", \"ed.doctor@example.org\": \"ed-md\", \"hospitalist@example.org\": \"hosp-md\", \"icu.nurse@example.org\": \"unit-sgh-micu\"}"}
```

Or export `BUNDLE_VAR_role_map` with the JSON map in the terminal you deploy from. `--var role_map=...` does not work for it: the CLI splits `--var` values at commas and refuses double quotes. After any change, run `databricks bundle deploy -t <target>` and `databricks bundle run edip_app -t <target>`. Anyone who can view the app's settings in the workspace can read the map.

Emails match without regard to case. The app checks the map when it starts and writes a warning to the app log (**Compute > Apps > <app> > Logs**) for each problem:

- Not valid JSON, or not a JSON object: everyone gets the read only viewer role.
- A persona id that does not exist, or an empty one: that person gets the viewer role.
- The same email twice with different personas (for example in different letter case): the later one is used.
- A well formed unit id the data does not list yet is kept, so a nurse of a unit with no patients keeps their role. This means a misspelled unit id is not caught: that nurse sees an empty board. Check unit ids against your data.

**3. Feeds and drafts.**

- **The pipeline.** It is triggered, not continuous (`continuous: false` in `databricks.yml`), and nothing in the bundle starts it on a schedule. With real feeds, run it on a schedule or switch it to continuous; how often it runs decides how fresh the board is ([ingestion/README.md](ingestion/README.md) compares the latency).
- **The handoff job's trigger.** The job `edip-handoff-generator-<target>` has a table update trigger on `<catalog>.silver.handoff_events`, paused so a deploy never starts model calls by itself. Unpause it under **Jobs > edip-handoff-generator-<target> > Schedules and triggers**. It then drafts only new admit decisions and changes (points already drafted are skipped), at most every 5 minutes, and each run ends by refreshing the Lakebase synced tables. That refresh is also how new patient state reaches the app: a pipeline run alone does not refresh it ([data-model.md](data-model.md#synced-tables-schema-gold-read-only-copies)). Pause the trigger again to stop model spend.
- **Model drafts.** Keep the job's `drafts` parameter at `model`, its default. `reference` loads the handoffs shipped for the synthetic week and falls back to the model when they do not match. The endpoint is the bundle variable `model` (or the job's `model` parameter); choose it with [feature-status.md](feature-status.md) and [ai/README.md](../ai/README.md#choosing-the-model).
- **Model spend.** Every trigger run that finds new points makes one model call per point. [ai/README.md](../ai/README.md#cost) has the measured cost per call and an estimate for a day of live use; scale it by your volume and your model's price. Pay per token endpoints are rate limited; the job's `workers` parameter sets how many calls run at once.
- **The nightly shutdown.** The job `edip-nightly-shutdown-<target>` stops this deployment's app and pipeline at 01:00 Pacific every night and does not start them again. For a service that runs around the clock, pause its schedule under **Jobs**.

**4. The past view.** With the live clock, a user can look at the board, a handoff and analytics as they were at an earlier time, read only, up to `EDIP_PAST_VIEW_HOURS` hours back (default 72; `0` turns it off).

- **Limits.** A time older than the limit is refused (the app answers 400 and says how far back it can look); it is not moved to the limit. A time later than now counts as now, and so does one within 2 minutes of now, because device clocks differ.
- **Read only.** Edits, attestations, sign offs and read backs always carry the server's current time, and are refused while the user looks at an earlier time, so nothing can be backdated.
- **Logged.** Before it answers, the app writes a row to the Lakebase table `app.view_log` for every look at an earlier time, on the board, a handoff or analytics: who, the page and patient, the time viewed, and when. If that row cannot be written, the view is refused. Reads at the current time write nothing.
- **What an earlier time shows.** A draft shows from when the handoff job saved it (`created_ts`), or from its chart time if that is later. The Lakebase sync and the app's reload once a minute come after `created_ts`, so an earlier time can show a draft a few minutes before the app could show it then. Arrival, admit decision and departure show at the time they happened, so a feed message that came in late shows at that time. Encounter details (status, ESI, service) show as they are now. A canceled admission shows canceled from the time of the cancel.

**5. PHI and governance.**

- Confirm every feature with your Databricks account team and work through [Before using real patient data](feature-status.md#before-using-real-patient-data) in feature-status.md.
- The app reads data as its own identity (the app's service principal), not as the signed in user, so Unity Catalog row filters and column masks do not apply per user; per unit access is enforced in app code. If your policy requires per user data access, user authorization is the option ([feature-status.md](feature-status.md#feature-table), row "Apps forwarded user headers and user authorization").
- Grant access to the catalog (every bronze table holds PHI) and the Lakebase project only to the groups that need it; grant `CAN USE` on the app only to the clinical groups who use it. [data-model.md](data-model.md#who-can-read-and-write-what) lists what the app's identity can read and write in Lakebase.
- Three Lakebase tables record who did what: `app.handoff_action` (every edit, attestation, sign off and read back, append only), `app.assignment` (who owns each ED patient: every Take, Assign, Release and Take over, append only) and `app.view_log` (every look at an earlier time: who, the page and patient, the time viewed, and when; the app refuses the view if it cannot write the row). Include both in your audit and retention plan.

**6. Check it worked.**

- The clock at the top right shows **Live** and the current time, with **View an earlier time** next to it (a clock icon on a phone; it is missing when `EDIP_PAST_VIEW_HOURS` is `0`). There is no Play button, no "Replay the reference week" panel and no "This demo replays one recorded week" note.
- **View an earlier time** opens a picker of days and hours within the limit. Pick one: a bar under the top of the page says **Viewing** and the time, **read only**; Edit, the sign off choices, Sign and Accept handoff are turned off; arrows next to the time move one hour back or forward; **Back to live** returns. The look is a new row in `app.view_log`.
- The bottom of the left menu shows your own name (or email) and your role or unit, with no menu of demo people to switch between.
- A colleague who is not in `EDIP_ROLE_MAP` sees "Viewer (read only)" under their name and cannot edit, sign or accept a handoff.
- `<app URL>/api/config` shows `"clock": "live"`, `"demo_personas": false`, `"personas": []` and `"past_view_hours"` with your limit. `<app URL>/api/me` shows your `role` and `user_email`.
- `<app URL>/api/health` shows `"status": "ok"` and your encounter and draft counts. Its `"mode": "live"` means the app reads Lakebase (`EDIP_MODE`), not the clock: it says `live` in the demo too.

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
