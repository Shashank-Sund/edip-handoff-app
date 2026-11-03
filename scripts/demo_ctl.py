#!/usr/bin/env python3
"""Turn the demo on and off, and show what is running.

  demo_ctl.py status   show apps, warehouse, pipelines, Lakebase (read only)
  demo_ctl.py up       start the warehouse and the apps
  demo_ctl.py down     stop pipelines, apps, and the warehouse
  demo_ctl.py down --app NAME --pipeline-id ID     only this app and pipeline (what the nightly job runs, so it
                       never touches another user's or another target's resources)

Without --app or --pipeline-id everything is found by name prefix (EDIP_PREFIX, default "edip"), so the script
needs no IDs. Locally it uses the CLI profile in DATABRICKS_CONFIG_PROFILE.
Inside a Databricks job it uses the job's own credentials; the nightly
shutdown job runs "down".

Lakebase is not started or stopped here: the endpoint scales to zero on its
own after the idle timeout and wakes on the first connection.
"""
import os
import sys

from databricks.sdk import WorkspaceClient

PREFIX = os.environ.get("EDIP_PREFIX", "edip")
# Apps that "up" leaves alone (smoke tests, scratch apps).
SKIP_ON_UP = ("-smoke",)


def ours(name):
    if name and name.startswith("[dev "):          # bundle development mode prefixes "[dev <user>] "
        name = name.split("] ", 1)[-1]
    return bool(name) and (name == PREFIX or name.startswith(PREFIX + "-") or name.startswith(PREFIX + "_"))


SCOPE = {"app": None, "pipeline_id": None}      # set by --app and --pipeline-id


def scoped():
    return SCOPE["app"] is not None or SCOPE["pipeline_id"] is not None


def apps(w):
    if scoped():
        return [w.apps.get(SCOPE["app"])] if SCOPE["app"] else []
    return [a for a in w.apps.list() if ours(a.name)]


def warehouses(w):
    return [] if scoped() else [x for x in w.warehouses.list() if ours(x.name)]


def pipelines(w):
    if scoped():
        return [w.pipelines.get(SCOPE["pipeline_id"])] if SCOPE["pipeline_id"] else []
    return [p for p in w.pipelines.list_pipelines() if ours(p.name)]


def app_state(a):
    return a.compute_status.state.value if a.compute_status and a.compute_status.state else "UNKNOWN"


def status(w):
    for a in apps(w):
        print(f"app        {a.name:28} {app_state(a)}")
    for x in warehouses(w):
        print(f"warehouse  {x.name:28} {x.state.value if x.state else 'UNKNOWN'}")
    for p in pipelines(w):
        print(f"pipeline   {p.name:28} {p.state.value if p.state else 'UNKNOWN'}")
    try:
        for proj in w.postgres.list_projects():
            if not ours(proj.name.split("/")[-1]):
                continue
            for b in w.postgres.list_branches(parent=proj.name):
                for e in w.postgres.list_endpoints(parent=b.name):
                    state = e.status.current_state.value if e.status and e.status.current_state else "UNKNOWN"
                    label = "/".join(e.name.split("/")[1::2])  # project/branch/endpoint
                    print(f"lakebase   {label:28} {state} (scales to zero)")
    except Exception as exc:  # Lakebase API missing or not permitted: report, do not fail
        print(f"lakebase   (could not read: {type(exc).__name__})")


def up(w):
    for x in warehouses(w):
        print(f"starting warehouse {x.name}")
        w.warehouses.start(x.id)
    for a in apps(w):
        if a.name.endswith(SKIP_ON_UP):
            continue
        if app_state(a) in ("ACTIVE", "STARTING"):
            print(f"app {a.name} already {app_state(a)}")
            continue
        print(f"starting app {a.name}")
        w.apps.start(a.name)
    print("start requests sent; run 'status' in a minute or two")


def down(w):
    for p in pipelines(w):
        print(f"stopping pipeline {p.name}")
        w.pipelines.stop(p.pipeline_id)
    for a in apps(w):
        if app_state(a) in ("STOPPED", "STOPPING"):
            continue
        print(f"stopping app {a.name}")
        w.apps.stop(a.name)
    for x in warehouses(w):
        if x.state and x.state.value in ("STOPPED", "STOPPING"):
            continue
        print(f"stopping warehouse {x.name}")
        w.warehouses.stop(x.id)
    print("stop requests sent")


def main(argv):
    argv = list(argv)
    for flag, key in (("--app", "app"), ("--pipeline-id", "pipeline_id")):
        if flag in argv:
            i = argv.index(flag)
            SCOPE[key] = argv[i + 1]
            del argv[i:i + 2]
    cmd = argv[0] if argv else "status"
    if cmd not in ("status", "up", "down"):
        print(__doc__)
        return 2
    w = WorkspaceClient()
    {"status": status, "up": up, "down": down}[cmd](w)
    return 0


if __name__ == "__main__":
    rc = main(sys.argv[1:])
    if rc:                      # exit only on failure: serverless Python tasks treat any SystemExit as an error
        sys.exit(rc)
