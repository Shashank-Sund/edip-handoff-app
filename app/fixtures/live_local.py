#!/usr/bin/env python3
"""Live mode on a laptop: loads the mock mode fixtures into a local Postgres as the two Lakebase synced tables, with
every time moved so the week ends now. Without the move the live board is empty: the live clock is the current
time, and patients who boarded last week are long gone (or dropped as stale, EDIP_STALE_BOARDING_HOURS).

    docker run -d --name edip-pg -e POSTGRES_PASSWORD=edip -p 55432:5432 postgres:16
    .venv/bin/python app/fixtures/live_local.py --dsn postgresql://postgres:edip@localhost:55432/postgres
    cd app/server && EDIP_MODE=live EDIP_SYNTHETIC_DATA=false PGHOST=localhost PGPORT=55432 PGUSER=postgres \\
        PGPASSWORD=edip PGDATABASE=postgres PGSSLMODE=disable EDIP_ROLE_MAP='{"rn@example.org": "ed-rn"}' \\
        ../../.venv/bin/uvicorn main:app --port 8000

It writes gold.encounter_state_pg and gold.handoff_draft_pg (replaced on every run) and creates the app schema the
app uses (app.handoff_action, app.view_log); --reset empties that schema first. A draft's created_ts, when the
handoff job wrote it, is its as_of plus --lag minutes. The signed in user comes from the X-Forwarded-Email header,
which Databricks Apps sets; locally, send it yourself (a small proxy, or extra headers in Playwright). The data is
the synthetic week; nothing here talks to Databricks.
"""
import argparse
import gzip
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TS_FIELDS = ("arrival_ts", "admit_decision_ts", "ed_departure_ts", "last_event_ts")
# ISO date and time with seconds and an offset or Z, as the fixtures and the draft JSON write them.
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


def _ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)


def week_end(fixtures: Path) -> datetime:
    """The end of the fixtures' week (meta.json: start and days, at the same local hour)."""
    from zoneinfo import ZoneInfo
    w = json.loads((fixtures / "meta.json").read_text())["window"]
    start = datetime.fromisoformat(w["start"])
    tz = ZoneInfo(w.get("tz") or "America/New_York")
    local = start.astimezone(tz).replace(tzinfo=None) + timedelta(days=w.get("days", 7))
    return local.replace(tzinfo=tz).astimezone(timezone.utc)


def shifted(fixtures: Path, end: datetime, lag: timedelta = timedelta(minutes=3)) -> tuple[list[dict], list[dict]]:
    """Encounters and drafts of the fixtures, every time (the draft JSON's too) moved so the week ends at `end`.
    Each draft gets created_ts = as_of + lag."""
    delta = end - week_end(fixtures)

    def move(m: re.Match) -> str:
        return (_ts(m.group(0)) + delta).isoformat().replace("+00:00", "Z")

    encounters = json.loads((fixtures / "encounters.json").read_text())
    for e in encounters:
        for k in TS_FIELDS:
            if e.get(k):
                e[k] = _ts(e[k]) + delta
    with gzip.open(fixtures / "drafts.jsonl.gz", "rt", encoding="utf-8") as f:
        drafts = [json.loads(line) for line in f]
    for d in drafts:
        d["as_of"] = _ts(d["as_of"]) + delta
        d["created_ts"] = d["as_of"] + lag
        if d.get("retracted_ts"):
            d["retracted_ts"] = _ts(d["retracted_ts"]) + delta
        d["draft"] = ISO.sub(move, json.dumps(d["draft"]))
    return encounters, drafts


ENC_DDL = """CREATE TABLE gold.encounter_state_pg (
    csn TEXT PRIMARY KEY, facility TEXT, mrn TEXT, patient_name TEXT, birth_date TEXT, sex TEXT, status TEXT,
    patient_class TEXT, unit TEXT, room TEXT, bed TEXT, pending_unit TEXT, pending_room TEXT, pending_bed TEXT,
    service TEXT, level_of_care TEXT, esi INTEGER, chief_complaint TEXT, isolation TEXT, code_status TEXT,
    arrival_ts TIMESTAMPTZ, admit_decision_ts TIMESTAMPTZ, ed_departure_ts TIMESTAMPTZ, last_event_kind TEXT,
    last_event_ts TIMESTAMPTZ)"""
DRAFT_DDL = """CREATE TABLE gold.handoff_draft_pg (
    csn TEXT, template TEXT, version INTEGER, status TEXT, trigger TEXT, as_of TIMESTAMPTZ, created_ts TIMESTAMPTZ,
    model TEXT, severity TEXT, score DOUBLE PRECISION, blocking JSONB, draft TEXT, retracted_ts TIMESTAMPTZ,
    prior_status TEXT, PRIMARY KEY (csn, template, version))"""
ENC_COLS = ("csn", "facility", "mrn", "patient_name", "birth_date", "sex", "status", "patient_class", "unit", "room", "bed",
            "pending_unit", "pending_room", "pending_bed", "service", "level_of_care", "esi", "chief_complaint", "isolation",
            "code_status", "arrival_ts", "admit_decision_ts", "ed_departure_ts", "last_event_kind", "last_event_ts")
DRAFT_COLS = ("csn", "template", "version", "status", "trigger", "as_of", "created_ts", "model", "severity", "score",
              "blocking", "draft", "retracted_ts", "prior_status")


def load(dsn: str, encounters: list[dict], drafts: list[dict], reset: bool = False) -> None:
    import psycopg
    from psycopg.types.json import Jsonb

    sys.path.insert(0, str(ROOT / "app/server"))
    from lakebase import DDL               # the app's own tables, as the app creates them

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS gold")
        conn.execute("DROP TABLE IF EXISTS gold.encounter_state_pg, gold.handoff_draft_pg")
        conn.execute(ENC_DDL)
        conn.execute(DRAFT_DDL)
        with conn.cursor() as cur:
            cur.executemany(f"INSERT INTO gold.encounter_state_pg ({', '.join(ENC_COLS)}) VALUES ({', '.join(['%s'] * len(ENC_COLS))})",
                            [tuple(e.get(k) for k in ENC_COLS) for e in encounters])
            cur.executemany(f"INSERT INTO gold.handoff_draft_pg ({', '.join(DRAFT_COLS)}) VALUES ({', '.join(['%s'] * len(DRAFT_COLS))})",
                            [tuple(Jsonb(d[k]) if k == "blocking" else d.get(k) for k in DRAFT_COLS) for d in drafts])
        if reset:
            conn.execute("DROP SCHEMA IF EXISTS app CASCADE")
        for s in DDL:
            conn.execute(s)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dsn", required=True, help="for example postgresql://postgres:edip@localhost:55432/postgres")
    ap.add_argument("--fixtures", default=str(ROOT / "app/fixtures/data"))
    ap.add_argument("--end", help="ISO time the week ends at (default: now)")
    ap.add_argument("--lag", type=float, default=3, help="minutes from a draft's as_of to its created_ts (default 3)")
    ap.add_argument("--reset", action="store_true", help="empty the app schema (clinician actions, view log) first")
    a = ap.parse_args()
    end = _ts(a.end) if a.end else datetime.now(timezone.utc)
    encounters, drafts = shifted(Path(a.fixtures), end, timedelta(minutes=a.lag))
    load(a.dsn, encounters, drafts, a.reset)
    print(f"loaded {len(encounters)} encounters and {len(drafts)} drafts, the week ending {end.isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
