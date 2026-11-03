#!/usr/bin/env bash
# Remove a deployment of the reference app: the Lakebase synced tables, then everything the bundle created
# (jobs, pipeline, app, Lakebase project). Uses only the Databricks CLI.
#
#   scripts/remove.sh [target] [--var name=value ...] [--drop-catalog] [--yes]
#   scripts/remove.sh dev
#   scripts/remove.sh dev --var catalog=edip_ana --var lakebase_project=edip-ana --var app_name=edip-handoff-ana
#
# Pass the same --var flags you deployed with. --drop-catalog also deletes the catalog and all its tables
# (needs a SQL warehouse). Asks before deleting unless --yes.
set -euo pipefail
cd "$(dirname "$0")/.."
target="${1:-dev}"
[ $# -gt 0 ] && shift
drop=false; yes=false; extra=()
for arg in "$@"; do
  case "$arg" in --drop-catalog) drop=true;; --yes) yes=true;; *) extra+=("$arg");; esac
done

vars="$(databricks bundle validate -t "$target" "${extra[@]+"${extra[@]}"}" -o json)"
var() { echo "$vars" | python3 -c 'import json,sys; print(json.load(sys.stdin)["variables"][sys.argv[1]]["value"])' "$1"; }
catalog="$(var catalog)"; app="$(var app_name)"; project="$(var lakebase_project)"

echo "This deletes, for target $target: app $app, Lakebase project $project, the jobs and pipeline,"
echo "the synced tables in $catalog.gold$($drop && echo ", and the catalog $catalog with all its tables" || true)."
if ! $yes; then read -r -p "Type yes to continue: " ok; [ "$ok" = "yes" ] || { echo "Nothing deleted."; exit 1; }; fi

for t in encounter_state_pg handoff_draft_pg; do
  if databricks postgres get-synced-table "synced_tables/$catalog.gold.$t" >/dev/null 2>&1; then
    databricks postgres delete-synced-table "synced_tables/$catalog.gold.$t" >/dev/null && echo "deleted synced table $catalog.gold.$t"
  fi
done

databricks bundle destroy -t "$target" "${extra[@]+"${extra[@]}"}" --auto-approve

if $drop; then
  wh="$(databricks warehouses list -o json | python3 -c '
import json, sys
ws = json.load(sys.stdin)
ws.sort(key=lambda w: (w.get("state") != "RUNNING", not w.get("enable_serverless_compute"), w.get("name", "")))
print(ws[0]["id"] if ws else "")')"
  if [ -z "$wh" ]; then echo "No SQL warehouse: drop it in Catalog Explorer (catalog $catalog > Delete)."; exit 0; fi
  body="$(python3 -c 'import json,sys; print(json.dumps({"warehouse_id": sys.argv[1], "statement": "DROP CATALOG IF EXISTS `" + sys.argv[2] + "` CASCADE", "wait_timeout": "50s"}))' "$wh" "$catalog")"
  databricks api post /api/2.0/sql/statements --json "$body" |
    python3 -c 'import json,sys; s=json.load(sys.stdin)["status"]; print("DROP CATALOG:", s["state"], s.get("error", {}).get("message", ""))'
fi
echo "Done. Lakebase keeps a deleted project for 7 days; to deploy again right away, use another --var lakebase_project."
