#!/usr/bin/env bash
# Deploy the whole reference app to a Databricks workspace: catalog, bundle, data and app.
#
#   scripts/deploy.sh [target] [--var name=value ...]
#   scripts/deploy.sh dev
#   scripts/deploy.sh dev --var catalog=my_catalog --var app_name=my-handoff
#   scripts/deploy.sh dev --check        # only the preflight checks (seconds, no compute)
#
# Needs the Databricks CLI (1.23 or later) logged in to the workspace (DATABRICKS_CONFIG_PROFILE or a default
# profile). Steps: 0 create the catalog if missing (SQL, so Default Storage works), 1 deploy the bundle,
# 2 build and start the app, 3 run the setup job (about 15 minutes: data, pipeline, reference drafts, Lakebase sync;
# with --var drafts=model the model writes every handoff instead, 30 to 75 minutes).
# Safe to re-run: every step is idempotent; step 3 regenerates the data and reloads the drafts.
set -euo pipefail
cd "$(dirname "$0")/.."
target="${1:-dev}"
[ $# -gt 0 ] && shift
check_only=false; extra=()
for arg in "$@"; do if [ "$arg" = "--check" ]; then check_only=true; else extra+=("$arg"); fi; done

# Preflight: stop in seconds, before any compute, with the fix for each problem.
fail() { echo "STOP: $*" >&2; exit 1; }
command -v databricks >/dev/null || fail "the Databricks CLI is not installed. Install it: https://docs.databricks.com/dev-tools/cli/install.html (macOS: brew tap databricks/tap && brew install databricks)"
command -v python3 >/dev/null || fail "python3 is not installed (any Python 3 works for this script)."
cli="$(databricks --version | sed -E 's/[^0-9]*([0-9]+)\.([0-9]+).*/\1 \2/')"
read -r major minor <<<"$cli"
{ [ "$major" -gt 1 ] || { [ "$major" -eq 1 ] && [ "$minor" -ge 23 ]; }; } ||
  fail "Databricks CLI $(databricks --version) is too old; 1.23 or later is needed. Upgrade it (macOS: brew upgrade databricks)."
me="$(databricks current-user me -o json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["userName"])' 2>/dev/null)" ||
  fail "the CLI is not logged in to a workspace. Run: databricks auth login --host <workspace URL> --profile <name>, then export DATABRICKS_CONFIG_PROFILE=<name>"
vars="$(databricks bundle validate -t "$target" "${extra[@]+"${extra[@]}"}" -o json)" ||
  fail "the bundle does not validate (see the message above). Check the target name and the --var flags."
var() { echo "$vars" | python3 -c 'import json,sys; print(json.load(sys.stdin)["variables"][sys.argv[1]]["value"])' "$1"; }
catalog="$(var catalog)"; model="$(var model)"; app="$(var app_name)"
[[ "$catalog" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] ||
  fail "the catalog name $catalog cannot be used: only letters, digits and underscores (for example ${catalog//[^A-Za-z0-9_]/_}). Re-run with --var catalog=<that name>."
databricks serving-endpoints get "$model" >/dev/null 2>&1 ||
  fail "the model endpoint $model is not in this workspace (Serving lists the ones you have). Re-run with --var model=<a chat endpoint from Serving>."
echo "== target $target as $me: catalog $catalog, app $app, model $model"
if $check_only; then echo "Preflight passed. Run the same command without --check to deploy."; exit 0; fi

echo "== 0/3 catalog"
if databricks catalogs get "$catalog" >/dev/null 2>&1; then
  echo "catalog $catalog exists"
else
  # Prefer a running warehouse, then a serverless one; any warehouse works, a stopped one just starts first.
  wh="$(databricks warehouses list -o json | python3 -c '
import json, sys
ws = json.load(sys.stdin)
ws.sort(key=lambda w: (w.get("state") != "RUNNING", not w.get("enable_serverless_compute"), w.get("name", "")))
print(ws[0]["id"] if ws else "")')"
  if [ -z "$wh" ]; then
    echo "No SQL warehouse to create catalog $catalog. Create the catalog in Catalog Explorer (or a warehouse) and re-run." >&2
    exit 1
  fi
  location="${EDIP_CATALOG_LOCATION:-}"     # set for workspaces without Default Storage, e.g. s3://bucket/edip
  stmt="CREATE CATALOG IF NOT EXISTS \`$catalog\`${location:+ MANAGED LOCATION '$location'} COMMENT 'ED to inpatient handoff reference app'"
  body="$(python3 -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": sys.argv[2],
    "wait_timeout": "30s", "on_wait_timeout": "CONTINUE"}))' "$wh" "$stmt")"
  resp="$(databricks api post /api/2.0/sql/statements --json "$body")"
  id="$(echo "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin)["statement_id"])')"
  for _ in $(seq 1 40); do
    state="$(echo "$resp" | python3 -c 'import json,sys; d=json.load(sys.stdin)["status"]; print(d["state"], d.get("error", {}).get("message", ""))')"
    case "$state" in PENDING*|RUNNING*) sleep 10; resp="$(databricks api get "/api/2.0/sql/statements/$id")";; *) break;; esac
  done
  echo "CREATE CATALOG: $state"
  case "$state" in
    SUCCEEDED*) ;;
    *"storage root"*|*"MANAGED LOCATION"*|*"storage location"*)
      echo "This workspace needs a storage location for new catalogs. Re-run with EDIP_CATALOG_LOCATION=<cloud path of an external location you can use>, or create the catalog in Catalog Explorer." >&2; exit 1;;
    *) echo "Could not create catalog $catalog. Ask an admin for CREATE CATALOG, or deploy with --var catalog=<existing catalog>." >&2; exit 1;;
  esac
fi

echo "== 1/3 bundle deploy"
databricks bundle deploy -t "$target" "${extra[@]+"${extra[@]}"}"

# The app starts first, so its URL is ready while the data loads; it shows "data not loaded yet" and checks again
# every 30 seconds until setup has filled Lakebase.
echo "== 2/3 app (a few minutes)"
databricks bundle run edip_app -t "$target" "${extra[@]+"${extra[@]}"}"
url="$(databricks apps get "$app" -o json | python3 -c 'import json,sys; print(json.load(sys.stdin).get("url", ""))')"
echo "App: $url (it fills in when step 3 finishes)"

drafts="$(var drafts)"
if [ "$drafts" = "model" ]; then took="30 to 75 minutes, the model writes every handoff"; else took="about 15 minutes"; fi
echo "== 3/3 setup job ($took; the Run URL below shows progress, and the job keeps running if you close this terminal)"
databricks bundle run edip_setup -t "$target" "${extra[@]+"${extra[@]}"}"
echo "Done. Open $url"
