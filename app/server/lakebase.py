"""Live mode: the read model from Lakebase synced tables, and clinician actions in a Lakebase table.

Connection: the app's postgres resource (databricks.yml) injects PGHOST, PGDATABASE, PGUSER and PGPORT; the password
is a short lived token from the Databricks SDK for the endpoint in EDIP_PG_ENDPOINT (projects/<p>/branches/<b>/
endpoints/<e>). Tokens last an hour; a new one is generated every 45 minutes.

Reads: gold.encounter_state_pg and gold.handoff_draft_pg (synced from Unity Catalog by jobs/setup_lakebase.py and
refreshed by the handoff job; retracted_ts and prior_status once the sync has them). Writes: app.handoff_action and app.view_log (with
the live clock, every view of an earlier time), created here on first start, append only.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import config
from data import Dataset
from workflow import StoreUnavailable

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
    """CREATE TABLE IF NOT EXISTS app.view_log (
        view_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        page          TEXT        NOT NULL CHECK (page IN ('board', 'handoff', 'analytics')),
        csn           TEXT,
        at            TIMESTAMPTZ NOT NULL,
        recorded_ts   TIMESTAMPTZ NOT NULL DEFAULT now(),
        by_name       TEXT        NOT NULL,
        role          TEXT        NOT NULL,
        user_email    TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_view_recorded ON app.view_log (recorded_ts)",
]
DETAIL = ("element", "text", "items", "model_before", "restore", "rule", "option", "entry", "entry_note", "remove", "callback",
          "readback", "questions")
UNREACHABLE = "The handoff database cannot be reached right now. Wait a minute and try again."
UNLOGGED = ("The app could not record that you are looking at an earlier time, so it cannot show it. Go back to live, "
            "or wait a minute and try again.")


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


DRAFT_SQL = ("SELECT csn, template, version, status, trigger, as_of, created_ts, model, severity, score, blocking, draft{} "
             "FROM gold.handoff_draft_pg")


def load_dataset() -> Dataset:
    from psycopg.errors import UndefinedColumn
    from psycopg.rows import dict_row
    with PG.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
        encounters = cur.execute("SELECT * FROM gold.encounter_state_pg").fetchall()
        try:
            drafts = cur.execute(DRAFT_SQL.format(", retracted_ts, prior_status")).fetchall()
        except UndefinedColumn:
            # Synced before the handoff job added retracted_ts and prior_status (a deployment from before them): a
            # retracted version is then retracted at every time, as before. edip_lakebase_setup (a full refresh)
            # brings the columns.
            drafts = cur.execute(DRAFT_SQL.format("")).fetchall()
    for d in drafts:
        if isinstance(d["blocking"], str):
            b = d["blocking"]
            d["blocking"] = json.loads(b) if b.startswith("[") else [x for x in b.strip("{}").split(",") if x]
    return Dataset(encounters, drafts, meta_from(encounters, drafts), "lakebase", written=config.CLOCK_MODE == "live")


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
    """Same interface as workflow.MemoryStore; every action is a row in app.handoff_action.

    Several app processes (and app instances) write the same table, so the rows read are reused for at most
    `ttl` seconds (EDIP_ACTIONS_TTL) and every action is checked against a fresh read. Lakebase being unreachable
    at start up does not stop the app: the table is created on the first use that reaches it."""
    kind = "lakebase"

    def __init__(self, ttl: float | None = None):
        self._lock = threading.Lock()
        self._rows: list[dict] | None = None
        self._read = 0.0
        self.ttl = config.ACTIONS_TTL if ttl is None else ttl
        self._ready = False
        try:
            self._ensure()
        except Exception as exc:          # Lakebase unreachable or not set up yet: retried on use
            print(f"Lakebase actions table not ready ({type(exc).__name__}); retrying on use. {str(exc)[:200]}")

    def _ensure(self):
        if not self._ready:
            with PG.connect() as conn:
                for s in DDL:
                    conn.execute(s)
            self._ready = True

    def _all(self, fresh: bool = False) -> list[dict]:
        with self._lock:
            if fresh or self._rows is None or time.time() - self._read > self.ttl:
                try:
                    self._ensure()
                    from psycopg.rows import dict_row
                    with PG.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
                        rows = cur.execute("SELECT * FROM app.handoff_action ORDER BY at, action_id").fetchall()
                except Exception as exc:
                    raise StoreUnavailable(UNREACHABLE) from exc
                self._rows, self._read = [self._row(r) for r in rows], time.time()
            return list(self._rows)

    @staticmethod
    def _row(r: dict) -> dict:
        d = r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"] or "{}")
        return {"csn": r["csn"], "template": r["template"], "version": r["version"], "kind": r["kind"], "at": r["at"],
                "recorded_ts": r["recorded_ts"], "by": r["by_name"], "role": r["role"], "user_email": r["user_email"], **d}

    def add(self, kind: str, row: dict) -> dict:
        from psycopg.types.json import Jsonb
        detail = {k: row[k] for k in DETAIL if row.get(k) is not None}
        try:
            self._ensure()
            with PG.connect() as conn:
                conn.execute("INSERT INTO app.handoff_action (csn, template, version, kind, at, by_name, role, user_email, "
                             "detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                             (row["csn"], row["template"], row["version"], kind, row["at"], row["by"], row["role"],
                              row.get("user_email"), Jsonb(detail)))
        except Exception as exc:
            raise StoreUnavailable(UNREACHABLE) from exc
        with self._lock:
            self._rows = None
        return row

    def log_view(self, row: dict) -> None:
        """A view of an earlier time (live clock): a row in app.view_log, or StoreUnavailable, which refuses the view."""
        try:
            self._ensure()
            with PG.connect() as conn:
                conn.execute("INSERT INTO app.view_log (page, csn, at, by_name, role, user_email) VALUES (%s, %s, %s, %s, %s, %s)",
                             (row["page"], row.get("csn"), row["at"], row["by"], row["role"], row.get("user_email")))
        except Exception as exc:
            raise StoreUnavailable(UNLOGGED) from exc

    def actions(self, csn: str, at: datetime | None = None, fresh: bool = False) -> list[dict]:
        return [r for r in self._all(fresh) if r["csn"] == csn and (at is None or r["at"] <= at)]

    def all_actions(self, at: datetime | None = None) -> list[dict]:
        return [r for r in self._all() if at is None or r["at"] <= at]

    def reset(self):
        raise PermissionError("actions are a permanent record in live mode")


class Reloading:
    """Holds the current Dataset and rebuilds it from Lakebase at most once a minute, on demand."""

    def __init__(self, every=timedelta(minutes=1)):
        self.every = every
        self._lock = threading.Lock()
        self._ds, self._t = self._load(), time.time()

    @staticmethod
    def _load() -> Dataset:
        try:
            return load_dataset()
        except Exception as exc:          # synced tables missing (setup not finished) or Lakebase unreachable
            ds = Dataset([], [], {"window": {}}, "lakebase")
            ds.error = (f"The handoff data is not in Lakebase yet ({type(exc).__name__}). Run the setup job "
                        f"(edip_setup) to the end; the app retries every 30 seconds.")
            print(ds.error, str(exc)[:200])
            return ds

    def get(self) -> Dataset:
        wait = 30 if getattr(self._ds, "error", None) else self.every.total_seconds()
        if time.time() - self._t > wait and self._lock.acquire(blocking=False):
            try:
                fresh = self._load()
                if not getattr(fresh, "error", None) or getattr(self._ds, "error", None):
                    self._ds = fresh          # keep serving the last good data when a reload fails
                self._t = time.time()
            finally:
                self._lock.release()
        return self._ds
