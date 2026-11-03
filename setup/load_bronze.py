#!/usr/bin/env python3
"""Load a generated world into Unity Catalog bronze tables.

  python setup/load_bronze.py --world synthetic/out/world [--catalog edip] [--warehouse ID] [--snapshot latest]

Creates <catalog>.bronze (raw feeds) and <catalog>.synthetic_truth (the generator's truth, for evaluation
only), a landing volume, and:
  bronze.clarity_<table>     one table per Clarity table, typed from clarity/schema.json (one nightly snapshot)
  bronze.hl7_raw_messages    one row per HL7 v2 message, Funke landing columns plus promoted MSH fields
  bronze.redox_fhir_events   empty, created for the Zerobus replayer (synthetic/replay/zerobus_push.py)
  synthetic_truth.<entity>   truth JSONL, schema inferred

Uses the Databricks SDK with the CLI profile in DATABRICKS_CONFIG_PROFILE. Needs a SQL warehouse.
"""
import argparse
import hashlib
import io
import json
import sys
import time
from pathlib import Path

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

FHIR_DDL = """CREATE TABLE IF NOT EXISTS {cat}.bronze.redox_fhir_events (
  event_id STRING NOT NULL, attempt_id STRING, data_model STRING, event_type STRING, event_datetime TIMESTAMP,
  source_id STRING, source_name STRING, facility_code STRING, is_test BOOLEAN, hl7_event_code STRING,
  hl7_control_id STRING, bundle VARIANT NOT NULL, ingest_ts TIMESTAMP, landing_path STRING, delivery_ts TIMESTAMP
) COMMENT 'Redox FHIR events (synthetic), written by Zerobus'"""


class Sql:
    def __init__(self, w, warehouse):
        self.w, self.warehouse = w, warehouse

    def __call__(self, statement, quiet=False):
        r = self.w.statement_execution.execute_statement(statement=statement, warehouse_id=self.warehouse,
                                                         wait_timeout="50s")
        while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
            time.sleep(2)
            r = self.w.statement_execution.get_statement(r.statement_id)
        if r.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(f"{r.status.state}: {r.status.error.message if r.status.error else ''}\n{statement[:300]}")
        rows = r.result.data_array if r.result and r.result.data_array else []
        if not quiet:
            print(f"  ok: {statement.splitlines()[0][:100]}")
        return rows


def upload(w, local: Path, remote: str):
    with open(local, "rb") as f:
        w.files.upload(remote, f, overwrite=True)


def hl7_bundle(world: Path) -> bytes:
    """One JSON line per HL7 file: path plus message text (CR separated), in delivery order."""
    out = io.BytesIO()
    for line in open(world / "hl7" / "messages.jsonl", encoding="utf-8"):
        m = json.loads(line)
        content = (world / "hl7" / m["file"]).read_bytes()
        row = {"path": m["file"], "content": content.decode("utf-8"), "md5Hash": hashlib.md5(content).hexdigest(),
               "delivery_ts": m["delivery_ts"], "msh_control_id": m["msh10"], "msh_message_type": m["type"],
               "msh_trigger": m["trigger"], "msh_timestamp": m["msh7"], "msh_sending_facility": m["facility"]}
        out.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
    return out.getvalue()


def spark_type(t):
    return "STRING" if t.startswith("STRING") else t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", type=Path, required=True)
    ap.add_argument("--catalog", default="edip")
    ap.add_argument("--warehouse", required=True)
    ap.add_argument("--snapshot", default="latest")
    a = ap.parse_args()
    w = WorkspaceClient()
    sql = Sql(w, a.warehouse)
    cat, vol = a.catalog, f"/Volumes/{a.catalog}/bronze/landing"

    print("schemas and volume")
    sql(f"CREATE SCHEMA IF NOT EXISTS {cat}.bronze COMMENT 'Raw synthetic feeds: Clarity, HL7 v2, Redox FHIR'")
    sql(f"CREATE SCHEMA IF NOT EXISTS {cat}.synthetic_truth COMMENT 'Generator truth, for evaluation only'")
    sql(f"CREATE VOLUME IF NOT EXISTS {cat}.bronze.landing COMMENT 'Landing files for the synthetic feeds'")

    schema = json.loads((a.world / "clarity" / "schema.json").read_text())
    snap = sorted(schema["snapshots"])[-1] if a.snapshot == "latest" else a.snapshot
    print(f"upload: Clarity snapshot {snap}, HL7 bundle, truth")
    t0 = time.time()
    for f in sorted((a.world / "clarity" / snap).glob("*.jsonl")):
        upload(w, f, f"{vol}/clarity/{snap}/{f.name}")
    bundle = hl7_bundle(a.world)
    w.files.upload(f"{vol}/hl7/hl7_messages.jsonl", io.BytesIO(bundle), overwrite=True)
    for f in sorted((a.world / "truth").glob("*.json*")):
        upload(w, f, f"{vol}/truth/{f.name}")
    print(f"  uploaded in {time.time() - t0:.0f}s (HL7 bundle {len(bundle) / 1e6:.0f} MB)")

    print("Clarity tables")
    for name, t in sorted(schema["tables"].items()):
        cols = t["columns"]
        read_schema = ", ".join(f"`{c['name']}` STRING" for c in cols)
        select = ", ".join(f"CAST(`{c['name']}` AS {spark_type(c['type'])}) AS `{c['name']}`" for c in cols)
        src = f"{vol}/clarity/{snap}/{name}.jsonl"
        sql(f"CREATE OR REPLACE TABLE {cat}.bronze.clarity_{name.lower()} "
            f"COMMENT 'Clarity style {name}, nightly snapshot {snap} (synthetic)' AS "
            f"SELECT {select} FROM read_files('{src}', format => 'json', schema => '{read_schema}')", quiet=True)
    print(f"  {len(schema['tables'])} tables")

    print("HL7 and FHIR tables")
    sql(f"""CREATE OR REPLACE TABLE {cat}.bronze.hl7_raw_messages COMMENT 'Raw HL7 v2 messages (synthetic), one per file' AS
        SELECT path, content, md5Hash, current_timestamp() AS inserted, uuid() AS messageId,
               msh_control_id, msh_message_type, msh_trigger, msh_timestamp, msh_sending_facility,
               CAST(delivery_ts AS TIMESTAMP) AS engine_received_ts
        FROM read_files('{vol}/hl7/hl7_messages.jsonl', format => 'json')""")
    sql(FHIR_DDL.format(cat=cat))

    print("truth tables")
    for f in sorted((a.world / "truth").glob("*.jsonl")):
        sql(f"CREATE OR REPLACE TABLE {cat}.synthetic_truth.{f.stem} AS "
            f"SELECT * FROM read_files('{vol}/truth/{f.name}', format => 'json')", quiet=True)
    print("  done")

    print("row counts")
    checks = {"bronze.hl7_raw_messages": sum(1 for _ in open(a.world / "hl7" / "messages.jsonl")),
              "bronze.clarity_pat_enc": sum(1 for _ in open(a.world / "clarity" / snap / "PAT_ENC.jsonl")),
              "bronze.clarity_clarity_adt": sum(1 for _ in open(a.world / "clarity" / snap / "CLARITY_ADT.jsonl")),
              "synthetic_truth.encounters": sum(1 for _ in open(a.world / "truth" / "encounters.jsonl"))}
    bad = 0
    for table, want in checks.items():
        got = int(sql(f"SELECT COUNT(*) FROM {cat}.{table}", quiet=True)[0][0])
        print(f"  {table}: {got} (local {want}){'' if got == want else '  MISMATCH'}")
        bad += got != want
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
