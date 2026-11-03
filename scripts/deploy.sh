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
  wh="$(databricks warehouses list -o json | python3 -c 'import json,sys; w=json.load(sys.stdin); print(w[0]["id"] if w else "")')"
  if [ -z "$wh" ]; then
    echo "No SQL warehouse to create catalog $catalog. Create the catalog in Catalog Explorer, or create a warehouse, and re-run." >&2
    exit 1
  fi
  body="$(python3 -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "wait_timeout": "50s",
    "statement": f"CREATE CATALOG IF NOT EXISTS `{sys.argv[2]}` COMMENT \"ED to inpatient handoff reference app\""}))' "$wh" "$catalog")"
  state="$(databricks api post /api/2.0/sql/statements --json "$body" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"]["state"], d["status"].get("error", {}).get("message", ""))')"
  echo "CREATE CATALOG: $state"
  case "$state" in SUCCEEDED*) ;; *) echo "Could not create catalog $catalog. Ask an admin, or deploy with --var catalog=<existing catalog>." >&2; exit 1;; esac
fi

echo "== 1/3 bundle deploy"
databricks bundle deploy -t "$target" "${extra[@]+"${extra[@]}"}"

echo "== 2/3 setup job (about 2 hours)"
databricks bundle run edip_setup -t "$target" "${extra[@]+"${extra[@]}"}"

echo "== 3/3 app"
databricks bundle run edip_app -t "$target" "${extra[@]+"${extra[@]}"}"
databricks bundle summary -t "$target" "${extra[@]+"${extra[@]}"}" | grep -i -A2 "apps" || true
