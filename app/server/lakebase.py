"""Live mode: the read model from Lakebase synced tables, and clinician actions in a Lakebase table.

Connection: the app's postgres resource (databricks.yml) injects PGHOST, PGDATABASE, PGUSER and PGPORT; the password
is a short lived token from the Databricks SDK for the endpoint in EDIP_PG_ENDPOINT (projects/<p>/branches/<b>/
endpoints/<e>). Tokens last an hour; a new one is generated every 45 minutes.

Reads: gold.encounter_state_pg and gold.handoff_draft_pg (synced from Unity Catalog by jobs/setup_lakebase.py and
refreshed by the handoff job). Writes: app.handoff_action, created here on first start, append only.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from data import Dataset

DDL = [
    "CREATE SCHEMA IF NOT EXISTS app",
    """CREATE TABLE IF NOT EXISTS app.handoff_action (
        action_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        csn           TEXT        NOT NULL,
        template      TEXT        NOT NULL,
        version       INTEGER     NOT NULL,
        kind          TEXT        NOT NULL CHECK (kind IN ('edit', 'attest', 'sign', 'ack')),
        at            TIMESTAMPTZ NOT NULL,
        recorded_ts   TIMESTAMPTZ NOT NULL DEFAULT now(),
        by_name       TEXT        NOT NULL,
        role          TEXT        NOT NULL,
        user_email    TEXT,
        detail        JSONB       NOT NULL DEFAULT '{}'::jsonb
    )""",
    "CREATE INDEX IF NOT EXISTS ix_action_csn ON app.handoff_action (csn, at)",
]
DETAIL = ("element", "text", "items", "model_before", "rule", "option", "callback", "readback", "questions")


class Pg:
    def __init__(self):
        self._token, self._exp = None, 0.0
        self._lock = threading.Lock()

    def _password(self):
        if os.getenv("PGPASSWORD"):
            return os.environ["PGPASSWORD"]
        with self._lock:
            if self._token and time.time() < self._exp:
                return self._token
            from databricks.sdk import WorkspaceClient
            w = WorkspaceClient()
            self._token = w.postgres.generate_database_credential(endpoint=os.environ["EDIP_PG_ENDPOINT"]).token
            self._exp = time.time() + 45 * 60
            return self._token

    def connect(self):
        import psycopg
        return psycopg.connect(host=os.environ["PGHOST"], port=int(os.getenv("PGPORT", "5432")),
                               dbname=os.getenv("PGDATABASE", "databricks_postgres"), user=os.environ["PGUSER"],
                               password=self._password(), sslmode=os.getenv("PGSSLMODE", "require"), autocommit=True)


PG = Pg()


def load_dataset() -> Dataset:
    from psycopg.rows import dict_row
    with PG.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        encounters = cur.execute("SELECT * FROM gold.encounter_state_pg").fetchall()
        drafts = cur.execute("SELECT csn, template, version, status, trigger, as_of, model, severity, score, blocking, "
                             "draft FROM gold.handoff_draft_pg").fetchall()
    for d in drafts:
        if isinstance(d["blocking"], str):
            d["blocking"] = json.loads(d["blocking"]) if d["blocking"].startswith("[") else d["blocking"].strip("{}").split(",")
    return Dataset(encounters, drafts, meta_from(encounters, drafts), "lakebase")


def meta_from(encounters, drafts) -> dict:
    """The replay window and the receiving units, from the data itself (fixtures carry them in meta.json)."""
    arrivals = [e["arrival_ts"] for e in encounters if e.get("arrival_ts")]
    start = min(arrivals).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) if arrivals else None
    last = max([e["last_event_ts"] for e in encounters if e.get("last_event_ts")] or [datetime.now(timezone.utc)])
    days = max(1, round((last - start).total_seconds() / 86400)) if start else 7
    units = {}
    for d in drafts:
        draft = json.loads(d["draft"]) if isinstance(d["draft"], str) else d["draft"]
        e04 = next((e for s in draft.get("sections", []) for e in s["elements"] if e["id"] == "E04"), None)
        data = (e04 or {}).get("data") or {}
        if data.get("pending_unit"):
            fac = next((e["facility"] for e in encounters if e["csn"] == d["csn"]), None)
            units[(fac, data["pending_unit"])] = data.get("level_of_care") or units.get((fac, data["pending_unit"]))
    return {"window": {"start": start.isoformat() if start else None, "days": days},
            "units": [{"dept_id": f"{f}-{u}", "facility": f, "code": u, "name": f"{f} {u}", "level": lvl or "", "beds": 0}
                      for (f, u), lvl in sorted(units.items(), key=lambda x: (x[0][0] or "", x[0][1]))],
            "facilities": [{"code": f, "name": f} for f in sorted({e["facility"] for e in encounters if e.get("facility")})],
            "drafts_from": "Lakebase", "models": sorted({d["model"] for d in drafts if d.get("model")})}


class LakebaseStore:
    """Same interface as workflow.MemoryStore; every action is a row in app.handoff_action."""
    kind = "lakebase"

    def __init__(self):
        self._lock = threading.Lock()
        self._rows: list[dict] | None = None
        with PG.connect() as conn:
            for s in DDL:
                conn.execute(s)

    def _all(self) -> list[dict]:
        with self._lock:
            if self._rows is None:
                from psycopg.rows import dict_row
                with PG.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
                    rows = cur.execute("SELECT * FROM app.handoff_action ORDER BY at, action_id").fetchall()
                self._rows = [self._row(r) for r in rows]
            return list(self._rows)

    @staticmethod
    def _row(r: dict) -> dict:
        d = r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"] or "{}")
        return {"csn": r["csn"], "template": r["template"], "version": r["version"], "kind": r["kind"], "at": r["at"],
                "recorded_ts": r["recorded_ts"], "by": r["by_name"], "role": r["role"], "user_email": r["user_email"], **d}

    def add(self, kind: str, row: dict) -> dict:
        from psycopg.types.json import Jsonb
        detail = {k: row[k] for k in DETAIL if row.get(k) is not None}
        with PG.connect() as conn:
            conn.execute("INSERT INTO app.handoff_action (csn, template, version, kind, at, by_name, role, user_email, detail) "
                         "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                         (row["csn"], row["template"], row["version"], kind, row["at"], row["by"], row["role"],
                          row.get("user_email"), Jsonb(detail)))
        with self._lock:
            self._rows = None
        return row

    def actions(self, csn: str, at: datetime | None = None) -> list[dict]:
        return [r for r in self._all() if r["csn"] == csn and (at is None or r["at"] <= at)]

    def all_actions(self, at: datetime | None = None) -> list[dict]:
        return [r for r in self._all() if at is None or r["at"] <= at]

    def reset(self):
        raise PermissionError("actions are a permanent record in live mode")


class Reloading:
    """Holds the current Dataset and rebuilds it from Lakebase at most once a minute, on demand."""

    def __init__(self, every=timedelta(minutes=1)):
        self.every, self._ds, self._t = every, load_dataset(), time.time()
        self._lock = threading.Lock()

    def get(self) -> Dataset:
        if time.time() - self._t > self.every.total_seconds() and self._lock.acquire(blocking=False):
            try:
                self._ds, self._t = load_dataset(), time.time()
            except Exception as exc:          # keep serving the last good data
                print(f"reload failed: {type(exc).__name__}: {str(exc)[:200]}")
                self._t = time.time()
            finally:
                self._lock.release()
        return self._ds
