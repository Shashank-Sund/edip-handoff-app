"""Setup job, last step (serverless): Lakebase synced tables for the app, and read access for the app's identity.

    --catalog C   --project P (Lakebase project id)   --app A (app name)   [--database databricks_postgres]

Synced tables (Triggered, so each refresh copies only the changes):
  <C>.gold.encounter_state_pg   from <C>.silver.encounter_state   key csn
  <C>.gold.handoff_draft_pg     from <C>.gold.handoff_draft       key csn, template, version
Each appears in Postgres as gold.<name> in the database. An existing synced table is refreshed instead of created.

The app's service principal gets a Postgres role when the app is deployed with its postgres resource (databricks.yml),
but no access to synced tables, which belong to the identity that created them. This job runs as that identity and
grants the app SELECT on schema gold. The app creates its own tables (schema app) when it starts.
"""
import argparse
import time

SYNCED = [("encounter_state_pg", "silver.encounter_state", ["csn"]),
          ("handoff_draft_pg", "gold.handoff_draft", ["csn", "template", "version"])]


def wait_online(w, name, timeout=1800):
    t0 = time.time()
    state = ""
    while time.time() - t0 < timeout:
        s = w.postgres.get_synced_table(name=f"synced_tables/{name}").status
        state = str(getattr(s, "detailed_state", "") or "")
        if "FAIL" in state:
            raise RuntimeError(f"synced table {name} failed: {getattr(s, 'message', '')}")
        if state.endswith(("ONLINE_NO_PENDING_UPDATE", "_ONLINE")):      # ONLINE_TRIGGERED_UPDATE: a refresh is running
            return state
        time.sleep(20)
    raise RuntimeError(f"synced table {name} not online after {timeout}s (state {state})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--app", required=True)
    ap.add_argument("--branch", default="production")
    ap.add_argument("--database", default="databricks_postgres")
    a = ap.parse_args()
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service import postgres as pg

    w = WorkspaceClient()
    branch = f"projects/{a.project}/branches/{a.branch}"
    for name, source, keys in SYNCED:
        full = f"{a.catalog}.gold.{name}"
        try:
            t = w.postgres.get_synced_table(name=f"synced_tables/{full}")
            w.pipelines.start_update(t.status.pipeline_id)
            print(f"{full}: exists, refresh started")
        except NotFound:
            spec = pg.SyncedTableSyncedTableSpec(
                source_table_full_name=f"{a.catalog}.{source}", branch=branch, postgres_database=a.database,
                primary_key_columns=keys, create_database_objects_if_missing=True,
                scheduling_policy=pg.SyncedTableSyncedTableSpecSyncedTableSchedulingPolicy.TRIGGERED,
                new_pipeline_spec=pg.NewPipelineSpec(storage_catalog=a.catalog, storage_schema="gold"))
            w.postgres.create_synced_table(synced_table=pg.SyncedTable(spec=spec), synced_table_id=full)
            print(f"{full}: created from {a.catalog}.{source}")
    for name, _, _ in SYNCED:
        print(f"{a.catalog}.gold.{name}: {wait_online(w, f'{a.catalog}.gold.{name}').split('.')[-1]}")

    # Grant the app read access to the synced tables.
    import psycopg

    sp = w.apps.get(a.app).service_principal_client_id
    endpoint = next(iter(w.postgres.list_endpoints(parent=branch)))
    host = endpoint.status.hosts.host
    me = w.current_user.me()
    user = me.user_name or me.application_id      # a user, or the service principal the job runs as
    token = w.postgres.generate_database_credential(endpoint=endpoint.name).token
    with psycopg.connect(host=host, dbname=a.database, user=user, password=token, sslmode="require", autocommit=True) as conn:
        roles = [r[0] for r in conn.execute("SELECT rolname FROM pg_roles WHERE rolname = %s", (sp,))]
        if not roles:
            raise RuntimeError(f"Postgres role {sp} for app {a.app} does not exist yet. Deploy the bundle (it adds the "
                               f"app's postgres resource), then run setup again.")
        conn.execute(f'GRANT USAGE ON SCHEMA gold TO "{sp}"')
        conn.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA gold TO "{sp}"')
        n = conn.execute("SELECT count(*) FROM gold.handoff_draft_pg").fetchone()[0]
        m = conn.execute("SELECT count(*) FROM gold.encounter_state_pg").fetchone()[0]
    print(f"granted {a.app} ({sp}) read access to schema gold; {m} encounters, {n} drafts in Lakebase")


if __name__ == "__main__":
    main()
