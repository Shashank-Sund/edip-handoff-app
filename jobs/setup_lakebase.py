"""Setup job, last step (serverless): Lakebase synced tables for the app, and read access for the app's identity.

    --catalog C   --project P (Lakebase project id)   --app A (app name)   [--database databricks_postgres]

Synced tables (Triggered, so each refresh copies only the changes):
  <C>.gold.encounter_state_pg   from <C>.silver.encounter_state   key csn
  <C>.gold.handoff_draft_pg     from <C>.gold.handoff_draft       key csn, template, version
Each appears in Postgres as gold.<name> in the database. An existing synced table gets a full refresh (setup fully
refreshes the sources), and one left over from another Lakebase project is recreated. The job waits for each sync.

The app's service principal gets a Postgres role when the app is deployed with its postgres resource (databricks.yml),
but no access to synced tables, which belong to the identity that created them. This job runs as that identity and
grants the app SELECT on schema gold. The app creates its own tables (schema app) when it starts.
"""
import argparse
import re
import time

SYNCED = [("encounter_state_pg", "silver.encounter_state", ["csn"]),
          ("handoff_draft_pg", "gold.handoff_draft", ["csn", "template", "version"])]


def wait_online(w, name, timeout=1800):
    """A newly created synced table: wait for its first sync to finish."""
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


def wait_update(w, pipeline_id, update_id, timeout=1800):
    t0 = time.time()
    while time.time() - t0 < timeout:
        u = w.pipelines.get_update(pipeline_id=pipeline_id, update_id=update_id).update
        state = u.state.value if u and u.state else ""
        if state in ("COMPLETED", "FAILED", "CANCELED"):
            return state
        time.sleep(15)
    raise RuntimeError(f"sync pipeline {pipeline_id} update {update_id} still running after {timeout}s")


def refresh(w, pipeline_id, label):
    """Full refresh of an existing sync (setup fully refreshed its source), after any refresh already running (the
    handoff job starts one when it finishes)."""
    for _ in range(3):
        latest = (w.pipelines.get(pipeline_id).latest_updates or [None])[0]
        if latest and latest.state and latest.state.value not in ("COMPLETED", "FAILED", "CANCELED"):
            print(f"{label}: waiting for the running refresh {latest.update_id}")
            wait_update(w, pipeline_id, latest.update_id)
        try:
            uid = w.pipelines.start_update(pipeline_id, full_refresh=True).update_id
            break
        except Exception as exc:          # another update slipped in between: wait for it and try again
            print(f"{label}: could not start a refresh yet ({type(exc).__name__}); retrying")
            time.sleep(15)
    else:
        raise RuntimeError(f"{label}: could not start a refresh")
    state = wait_update(w, pipeline_id, uid)
    if state != "COMPLETED":
        raise RuntimeError(f"{label}: refresh {uid} ended {state}")
    return state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--app", required=True)
    ap.add_argument("--branch", default="production")
    ap.add_argument("--database", default="databricks_postgres")
    ap.add_argument("--delete", action="store_true", help="delete the synced tables (before bundle destroy) and exit")
    a = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", a.catalog):     # same check as setup/load_bronze.check_catalog
        raise SystemExit(f"Catalog name {a.catalog!r} cannot be used: use only letters, digits and underscores.")
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service import postgres as pg

    w = WorkspaceClient()
    branch = f"projects/{a.project}/branches/{a.branch}"
    if a.delete:
        for name, _, _ in SYNCED:
            try:
                w.postgres.delete_synced_table(name=f"synced_tables/{a.catalog}.gold.{name}")
                print(f"deleted {a.catalog}.gold.{name}")
            except NotFound:
                print(f"{a.catalog}.gold.{name} does not exist")
        return
    # Triggered syncs read the source's change data feed. The pipeline sets it on silver.encounter_state (a table it
    # owns and a job cannot alter); the drafts table belongs to the handoff job, so set it here for older tables.
    from pyspark.sql import SparkSession
    SparkSession.builder.getOrCreate().sql(
        f"ALTER TABLE {a.catalog}.gold.handoff_draft SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
    created = set()
    for name, source, keys in SYNCED:
        full = f"{a.catalog}.gold.{name}"
        try:
            t = w.postgres.get_synced_table(name=f"synced_tables/{full}")
            if t.spec and t.spec.branch and t.spec.branch != branch:
                # Left over from an earlier deploy with another (perhaps deleted) Lakebase project.
                print(f"{full}: bound to {t.spec.branch}, recreating on {branch}")
                w.postgres.delete_synced_table(name=f"synced_tables/{full}")
                for _ in range(40):
                    try:
                        w.postgres.get_synced_table(name=f"synced_tables/{full}")
                        time.sleep(15)
                    except NotFound:
                        break
                raise NotFound("recreate")
            created.discard(full)
            print(f"{full}: exists, {refresh(w, t.status.pipeline_id, full).lower()} full refresh")
        except NotFound:
            created.add(full)
            spec = pg.SyncedTableSyncedTableSpec(
                source_table_full_name=f"{a.catalog}.{source}", branch=branch, postgres_database=a.database,
                primary_key_columns=keys, create_database_objects_if_missing=True,
                scheduling_policy=pg.SyncedTableSyncedTableSpecSyncedTableSchedulingPolicy.TRIGGERED,
                new_pipeline_spec=pg.NewPipelineSpec(storage_catalog=a.catalog, storage_schema="gold"))
            w.postgres.create_synced_table(synced_table=pg.SyncedTable(spec=spec), synced_table_id=full)
            print(f"{full}: created from {a.catalog}.{source}")
    for full in sorted(created):
        print(f"{full}: {wait_online(w, full).split('.')[-1]}")

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
