#!/usr/bin/env bash
# Deploy the whole reference app to a Databricks workspace: catalog, bundle, data and app.
#
#   scripts/deploy.sh [target] [--var name=value ...]
#   scripts/deploy.sh dev
#   scripts/deploy.sh dev --var catalog=my_catalog --var app_name=my-handoff
#
# Needs the Databricks CLI (1.23 or later) logged in to the workspace (DATABRICKS_CONFIG_PROFILE or a default
# profile). Steps: 0 create the catalog if missing (SQL, so Default Storage works), 1 deploy the bundle,
# 2 run the setup job (about 2 hours: data, pipeline, model drafts, Lakebase sync), 3 build and start the app.
# Safe to re-run: every step is idempotent; step 2 regenerates the data and the drafts.
set -euo pipefail
cd "$(dirname "$0")/.."
target="${1:-dev}"
[ $# -gt 0 ] && shift
extra=("$@")

catalog="$(databricks bundle validate -t "$target" "${extra[@]+"${extra[@]}"}" -o json |
  python3 -c 'import json,sys; print(json.load(sys.stdin)["variables"]["catalog"]["value"])')"
echo "== target $target, catalog $catalog"

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

echo "== 2/3 setup job (about 2 hours)"
databricks bundle run edip_setup -t "$target" "${extra[@]+"${extra[@]}"}"

echo "== 3/3 app"
databricks bundle run edip_app -t "$target" "${extra[@]+"${extra[@]}"}"
databricks bundle summary -t "$target" "${extra[@]+"${extra[@]}"}" | grep -i -A2 "apps" || true
