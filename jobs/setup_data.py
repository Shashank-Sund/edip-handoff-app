"""Setup job, step 1 (serverless): catalog and schemas, then the synthetic world generated and loaded into bronze.

    --catalog C        catalog to use; created when missing and the job's identity may create catalogs
    --code-path P      the bundle's workspace file path (the repo)
    --population X     synthea (default: the committed Synthea population slice, the reference dataset), standin
                       (the built in population), or a path to another population file or Synthea CSV folder
    --fhir M           batch (default): load the FHIR events into bronze now; zerobus: leave the table empty for the
                       Zerobus replayer (see docs/zerobus.md)

The world is generated on the driver's local disk, rendered as HL7 v2, Redox FHIR and Clarity, then written to the
landing volume and loaded with setup/load_bronze.py. Re-running replaces bronze, so run the pipeline with a full
refresh afterwards (the setup job does).
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True)
    ap.add_argument("--code-path", required=True)
    ap.add_argument("--population", default="synthea")
    ap.add_argument("--fhir", choices=["batch", "zerobus"], default="batch")
    a = ap.parse_args()
    code = Path(a.code_path)
    sys.path[:0] = [str(code / "setup"), str(code / "synthetic")]
    from load_bronze import check_catalog, load

    cat = check_catalog(a.catalog)            # before any compute: a hyphen in the name would break the SQL
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.getOrCreate()

    def sql(statement, quiet=False):
        rows = spark.sql(statement).collect()
        if not quiet:
            print(f"  ok: {statement.strip().splitlines()[0][:100]}")
        return [list(r) for r in rows]

    print(f"catalog {cat}")
    exists = [r for r in sql("SHOW CATALOGS", quiet=True) if r[0] == cat]
    if not exists:
        try:
            sql(f"CREATE CATALOG IF NOT EXISTS {cat} COMMENT 'ED to inpatient handoff reference app (synthetic data)'")
        except Exception as exc:          # no CREATE CATALOG privilege, or the metastore needs a storage location
            raise RuntimeError(f"Catalog {cat} does not exist and could not be created ({str(exc)[:200]}). Create it "
                               f"(or ask an admin), grant yourself USE CATALOG and CREATE SCHEMA, and run setup again; "
                               f"or deploy with --var catalog=<an existing catalog>.") from None
    for schema, comment in (("bronze", "Raw synthetic feeds"), ("silver", "Parsed and reconciled feeds"),
                            ("gold", "Handoff drafts and app facing tables"), ("synthetic_truth", "Generator truth")):
        sql(f"CREATE SCHEMA IF NOT EXISTS {cat}.{schema} COMMENT '{comment}'")

    population = {"synthea": str(code / "synthetic" / "population" / "synthea_3000.json.gz"), "standin": None}.get(
        a.population, a.population)
    with tempfile.TemporaryDirectory() as tmp:
        world = Path(tmp) / "world"
        env = {**os.environ, "PYTHONPATH": str(code / "synthetic")}
        t0 = time.time()
        cmd = [sys.executable, "-m", "edip_synth", "--out", str(world)] + (["--synthea", population] if population else [])
        subprocess.run(cmd, check=True, env=env)
        for feed in ("hl7", "fhir", "clarity"):
            subprocess.run([sys.executable, "-m", f"edip_synth.render.{feed}", "--truth", str(world / "truth"),
                            "--out", str(world)], check=True, env=env)
        print(f"world generated and rendered in {time.time() - t0:.0f}s")

        def put(remote, data):
            os.makedirs(os.path.dirname(remote), exist_ok=True)
            with open(remote, "wb") as f:
                f.write(data)

        bad = load(world, cat, sql, put, fhir=a.fhir)
    if bad:
        raise RuntimeError(f"{bad} bronze tables do not match the generated world; see the row counts above")
    print("bronze loaded")
    # New data means new drafts: the handoff job skips points it already drafted, so clear drafts of earlier data.
    for table in ("handoff_draft", "handoff_latest"):
        if spark.catalog.tableExists(f"{cat}.gold.{table}"):
            sql(f"DELETE FROM {cat}.gold.{table}")


if __name__ == "__main__":
    main()
