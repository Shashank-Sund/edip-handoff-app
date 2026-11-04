"""The handoff job's retraction and its upgrade of older tables, without Spark or Databricks.

A canceled admission marks every version retracted with the cancel's time (retracted_ts) and the status it had
(prior_status), so the app can show the case as it was before the cancel; tables made before the columns get them
added (edip_lakebase_setup then brings them to Lakebase with a full refresh).
"""
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "jobs"), str(ROOT / "ai"), str(ROOT / "pipelines")]
import handoff_job as H  # noqa: E402
from edip_ai.points import select_points  # noqa: E402

T = datetime(2026, 10, 28, 14, 5, 9, 250000, tzinfo=timezone.utc)


def trig(csn, action, minutes, kind=None):
    return {"csn": csn, "action": action, "kind": kind or {"start": "pending_admit", "retract": "cancel_admit"}.get(action, "bed_assigned"),
            "effective_ts": T + timedelta(minutes=minutes), "reason": ""}


def test_retraction_carries_the_time_of_the_cancel():
    triggers = [trig("401", "start", 0), trig("402", "start", 5), trig("401", "regenerate", 90), trig("401", "retract", 120),
                trig("401", "retract", 200), trig("403", "retract", 30)]
    _, retracted = select_points(triggers, 60)
    sql = H.retract_sql("edip", triggers, retracted)
    assert sql.startswith("UPDATE edip.gold.handoff_draft SET status = 'retracted', retracted_ts = CASE csn ")
    # The status before, kept once: from a row not yet retracted, never over a stored one (Delta evaluates SET from
    # the row as it was, so status here is the old status).
    assert ", prior_status = coalesce(prior_status, CASE WHEN status <> 'retracted' THEN status END) WHERE " in sql
    # The first retract of each encounter, in UTC with its fraction of a second; 402 was not canceled.
    assert "WHEN '401' THEN TIMESTAMP '2026-10-28 16:05:09.250000'" in sql
    assert "WHEN '403' THEN TIMESTAMP '2026-10-28 14:35:09.250000'" in sql
    assert "402" not in sql and "16:05:09" in sql and "17:25:09" not in sql
    # Rows already stamped with that time are left alone, so a rerun writes nothing (and the sync copies nothing).
    assert sql.endswith("WHERE csn IN ('401','403') AND NOT (status = 'retracted' AND retracted_ts <=> CASE csn "
                        "WHEN '401' THEN TIMESTAMP '2026-10-28 16:05:09.250000' "
                        "WHEN '403' THEN TIMESTAMP '2026-10-28 14:35:09.250000' END)")


def test_nothing_to_retract():
    assert H.retract_sql("edip", [trig("401", "start", 0)], set()) is None
    # An encounter id that is not plain letters and digits never reaches the SQL.
    bad = [trig("40'1", "retract", 0)]
    assert H.retract_sql("edip", bad, {"40'1"}) is None


class FakeSpark:
    def __init__(self, columns):
        self.columns, self.ran = columns, []

    def table(self, name):
        return types.SimpleNamespace(columns=self.columns[name.split(".")[-1]])

    def sql(self, stmt):
        self.ran.append(stmt)


def test_older_tables_get_the_column_once():
    old = ["csn", "template", "version", "status", "draft"]
    spark = FakeSpark({"handoff_draft": list(old), "handoff_latest": list(old)})
    both = ["retracted_ts", "prior_status"]
    assert H.add_retraction_columns(spark, "edip") is True
    assert spark.ran == ["ALTER TABLE edip.gold.handoff_draft ADD COLUMNS (retracted_ts TIMESTAMP, prior_status STRING)",
                         "ALTER TABLE edip.gold.handoff_latest ADD COLUMNS (retracted_ts TIMESTAMP, prior_status STRING)"]
    new = FakeSpark({"handoff_draft": old + both, "handoff_latest": old + both})
    assert H.add_retraction_columns(new, "edip") is False and new.ran == []
    # Only what is missing; only the synced table counts (handoff_latest is not in Lakebase).
    half = FakeSpark({"handoff_draft": old + ["retracted_ts"], "handoff_latest": old + both})
    assert H.add_retraction_columns(half, "edip") is True
    assert half.ran == ["ALTER TABLE edip.gold.handoff_draft ADD COLUMNS (prior_status STRING)"]
    latest = FakeSpark({"handoff_draft": old + both, "handoff_latest": list(old)})
    assert H.add_retraction_columns(latest, "edip") is False and len(latest.ran) == 1


def test_reference_rows_need_no_retraction_time():
    """The reference drafts load through flush(), which writes retracted_ts and prior_status as NULL: canceled admissions have no
    generation points, so no reference row is ever retracted, and ai/reference/ needs no refresh."""
    import gzip
    import json
    rows = [json.loads(line) for line in gzip.open(ROOT / "ai" / "reference" / "drafts.jsonl.gz", "rt")]
    assert {r["status"] for r in rows} <= {"draft", "final", "addendum"}
    assert not any("retracted_ts" in r or "prior_status" in r for r in rows)
