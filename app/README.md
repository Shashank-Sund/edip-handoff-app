# App

The clinician app, Healthcare Handoff: an ED boarding board, the handoff for each patient in three templates, sign off and read back, per unit access, and analytics. FastAPI serves the API and the built React client.

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
| Board | everyone | ED nurses and physicians open on My patients (theirs, with the Unassigned at the top, each with Take) or pick Everyone; every row shows its nurse and physician owner, and a shift change bar shows in the half hour before each shift change. Patients boarding or arrived in the last 12 hours: severity, destination, boarding time, handoff state per template with the open and overdue tasks under it, completeness, with a short explanation on each column. ED staff see how many patients left without sign off and can list them. A receiving nurse sees only patients bound for their unit. |
| Handoff | everyone | The draft in SBAR, I-PASS or the Joint Commission minimum, under an owner line (nurse and physician owner; Take, Assign to a colleague and Release for ED staff, and "Assigned to" when a colleague owns the patient). Structured items come from the chart; narrative items are model drafted, with numbered chips that open each cited source and highlighted numbers the validator could not find. Sign off checklist, read back, what changed, vital sign and lab trends, history. A Tasks panel above the tabs lists the patient's tasks (the action list of each signed handoff, and tasks added by hand) with every action and Add a task, and says when acting is outside your role. |
| Tasks | everyone | Open tasks across patients, grouped Overdue, Due in the next 2 hours, Later and No time, with the recently completed folded below: My tasks (for your role on the patients you have now, ED staff the ones they own, and any given to you by name) or Everyone. Each task has Done, Not needed (with a reason), Reassign, Due time and Undo. The viewer reads only. |
| Take over | ED nurses and physicians | At a shift change: pick the colleague you relieve, see each of their patients (handoff state, code status, allergies and isolation still to confirm, what changed since signing) and take some or all in one go. |
| Analytics | everyone | ED census and boarding by hour, unassigned boarding patients, boarding time by destination, what blocked sign off, completeness and severity at transfer, actions outside an assignment, handovers around the shift change, admit decision to signature by owner. |
| Templates | everyone | The three templates, their elements and rules, from `ai/templates`. |

## Rules the server enforces

- Only the sending ED clinician edits, attests and signs: nurses sign SBAR, physicians sign I-PASS, either signs the custom template. Structured items and illness severity cannot be edited; they come from the chart and the rule floor. An edit cannot be empty; Restore brings back the model's text.
- Isolation, code status and allergies offer real choices (Droplet, DNR/DNI, Add an allergy and so on), read from the current `attest` lists in `ai/templates`, so stored drafts need no redraft. Any choice but the first enters a value: the item shows it as "Entered by <name>, not yet in the chart" until a newer version has chart data, and History keeps it. Other (isolation) and Add an allergy need typed text (`entry`, and `entry_note` for the reaction).
- Sign off is blocked until every hard and stale rule passes, is attested from its fixed list, or (for narrative items) is written by the clinician. A callback number is required: at least 3 digits, or an extension like x4471.
- A stale vitals attestation ("Rechecked at bedside, unchanged") counts only for the rule's age limit for the patient's severity (`max_age_min` in the template); after that the item is stale again. Other attestations carry forward to newer versions.
- In I-PASS, a watcher or unstable patient has one contingency rule (P11): the "Stable, nothing anticipated" attestation of P10 does not apply.
- Any edit or attestation after a signature, or a newer version whose clinical content differs from the signed one, puts the template in state `changed` ("Changed after signing"): the signature stays in History, and the receiver cannot accept until the ED signs again. Clinical content is the effective text (clinician edits included) of every item except location and bed (E04, E28), ignoring a value charted again unchanged.
- Only the receiving clinician acknowledges, after sign off, with a read back of at least a sentence (three words, fifteen letters).
- After the patient leaves the ED the handoff is final, except for ED addenda (a late ED note or a critical result collected in the ED) and a handoff the ED never signed (`transferred_unsigned`, "Transferred without sign off") or signed on an older version: the ED can still complete and sign those.
- Every action carries the time it applies to, so the replay clock shows the board as it was. An action cannot be earlier than one already recorded for that patient and template.
- Each boarding patient has at most one nurse owner and one physician owner (`assignment.py`): Take, Assign to a colleague in the same role, Release (the owner only), and Take over at a shift change (one row per patient, all or none). Only ED nurses and physicians own patients, and only while the patient boards. The owner at time T is the latest row with `at <= T`; an owner idle for `EDIP_STALE_OWNER_HOURS` no longer counts. Acting on a colleague's patient is allowed and recorded as outside the assignment (`off_assignment`). In mock mode and the static demo, Jordan Reyes and Kai Moreno start owning a few patients who board through the next shift change, and Reset brings that back.
- Tasks (`tasks.py`): signing a handoff turns each item of its action list (E24) into a task, by the signer at the time of signing; signing again or an addendum adds only items whose text (case and spacing aside) is not a task of that template yet. The owner words give the role (RN, nurse, nursing: nurse; MD, physician, team, or a medical service or specialty such as medicine, hospitalist or a word ending in -ology: physician; the first such word wins; anything else the nurse); the due words give the time ("16:00", "by 16:00" or "at 16:00": the next one at or after signing, facility time; "now", "immediate", "immediately", "stat": the signing; anything else none, the words kept as `due_text`). Anyone may add a task (text, nurse or physician, optionally a colleague and a due time). Changes: done, not needed (a reason up to 200 characters), reassign (a role or a colleague), due (a time or none), and undo (reopens a closed task, or puts back the last reassign or due time; one level). The ED nurse and physician have the patient while it boards, the destination unit's nurse and the admitting physician after arrival; anyone else but the viewer may still act, recorded as `outside_role`; the viewer reads only. Overdue: open and due before the clock. Like owners, a task at time T replays its events with `at <= T`, and a change cannot be earlier than one already recorded on that patient's tasks. Live, on start, every handoff signed before tasks existed gets its tasks (`Views.backfill_tasks`). In mock mode and the static demo, the seeded owners start with a few tasks added by hand on their patients, one in every Tasks page group for each at the default time, and Reset brings them back.
- Free text is at most 2,000 characters. A request body the API cannot use gets a 422 with one plain sentence; unknown `/api` paths get a JSON 404.

## Live mode (deployed)

The bundle (`databricks.yml`) deploys the app with `EDIP_MODE=live` and a postgres resource bound to the Lakebase project. The app then:

- reads `gold.encounter_state_pg` and `gold.handoff_draft_pg` (synced from Unity Catalog by the setup job, refreshed by the handoff job) and reloads them at most once a minute;
- writes every clinician action to `app.handoff_action` (append only: edits, attestations, sign offs and read backs, with the persona, the role and the signed in user's email), every change of who owns an ED patient to `app.assignment` (append only: Take, Assign, Release, Take over), every task and change to one to `app.task_event` (append only), and with the live clock every look at an earlier time to `app.view_log`; it creates them on first start, and turns the action list of every handoff already signed into tasks;
- authenticates to Postgres as the app's service principal with a short lived token (`EDIP_PG_ENDPOINT`); the setup job grants it read access to the synced tables.

Settings (`config.env` of the app in `databricks.yml`): `EDIP_CLOCK_MODE=replay` keeps the replay clock for the synthetic week (`live` follows the current time, for real feeds); `EDIP_DEMO_PERSONAS=true` lets viewers switch roles (`false` takes the role from `EDIP_ROLE_MAP`, a JSON map of email to persona id, and shows the signed in user; the bundle fills it from the `role_map` variable, so staff emails stay out of the repo: docs/setup.md, "Settings"); `EDIP_TIMEZONE` (default `America/New_York`) is the facilities' time zone for the server and the client; `EDIP_SHIFT_CHANGES` (default `07:00,19:00`, facility local) are the ED shift changes, `EDIP_SHIFT_WARNING_MINUTES` (default 30) how long before one the board shows the shift change bar, and `EDIP_STALE_OWNER_HOURS` (default 14) how long an owner can go without acting on a patient before it shows as Unassigned.

With `EDIP_CLOCK_MODE=live`, clinicians can look at the board, a handoff, analytics, Take over and the Tasks page as they were at an earlier time, up to `EDIP_PAST_VIEW_HOURS` back (default 72; `0` turns it off): View an earlier time next to the Live label picks a day and an hour, and the time goes in the address as `?at=`. It is read only: the page says so, edit, attest, sign, accept and the task buttons are turned off, and the 20 second refresh pauses. The server enforces it: an `at` older than the limit gets a 400 with a plain message (it is refused, not moved to the limit), a later one than now is now, and one within 2 minutes of now counts as now (device clocks differ); every action carries the server's current time and is refused (409) when it comes with an earlier `at`, so nothing can be backdated. Every read at an earlier time first writes a row to `app.view_log` (who, the page and patient, the time viewed, when); if that row cannot be written, the view is refused (503). A draft version shows from when the handoff job wrote it (`created_ts`) or its `as_of`, whichever is later, so an earlier time shows the drafts as they were then (`created_ts` is when the job saved the draft; the sync and the app's reload once a minute add a few minutes before the app could show it). The rest is as the data is now: arrival, admit decision and departure stay at the time they happened, so a feed message that came in late shows at that time, and patient details from `encounter_state_pg` (status, ESI, service) show as they are now. A canceled admission shows canceled from the time of the cancel: the handoff job marks every version `retracted` with that time in `retracted_ts` and the status it had in `prior_status`, and before the cancel the versions show with that status (versions retracted by a handoff job from before these columns have no `prior_status` and show retracted at every time). The replay clock (mock mode and the synthetic deployment) is unchanged: any time of the week, actions at that time, `as_of` only, no view log.

For a live mode run on a laptop (no Databricks), `app/fixtures/live_local.py` loads the fixtures into a local Postgres as the two synced tables, with every time moved so the week ends now; its docstring has the Docker and uvicorn commands.

With `EDIP_CLOCK_MODE=live`, a patient with an admit decision and no ED departure for more than `EDIP_STALE_BOARDING_HOURS` (default 24) is left off the board and out of the boarding counts: a departure the feeds never sent would otherwise keep them boarding forever. Clinician actions are read again from Lakebase when the copy in memory is older than `EDIP_ACTIONS_TTL` seconds (default 2) and before every action, so several app processes agree. If Lakebase cannot be reached at start up the app still starts, shows "not loaded yet" and retries.

Persona ids for `EDIP_ROLE_MAP` (emails match without regard to case; an unknown id gets the read only viewer role and a warning in the app log, and the map is checked at start up):

| Persona id | Role |
| --- | --- |
| `ed-rn` | ED nurse: edits, attests and signs SBAR and the custom template |
| `ed-md` | ED physician: edits, attests and signs I-PASS and the custom template |
| `ed-rn-2`, `ed-md-2` | The second demo ED nurse (Robin Asante) and physician (Kai Moreno). Live, map every ED nurse to `ed-rn` and every ED physician to `ed-md`: each person is their email, and the role map is the colleague list for Assign and Take over |
| `hosp-md` | Admitting physician: accepts I-PASS and the custom template |
| `unit-<facility>-<unit>` | Receiving nurse of one unit, lowercased, for example `unit-sgh-micu`: sees patients bound for that unit, accepts SBAR and the custom template. A mapped unit keeps its persona when it has no patients. |
| `viewer` | Read only |

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
