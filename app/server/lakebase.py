"""Live mode: the read model from Lakebase synced tables, and clinician actions in a Lakebase table.

Connection: the app's postgres resource (databricks.yml) injects PGHOST, PGDATABASE, PGUSER and PGPORT; the password
is a short lived token from the Databricks SDK for the endpoint in EDIP_PG_ENDPOINT (projects/<p>/branches/<b>/
endpoints/<e>). Tokens last an hour; a new one is generated every 45 minutes.

Reads: gold.encounter_state_pg and gold.handoff_draft_pg (synced from Unity Catalog by jobs/setup_lakebase.py and
refreshed by the handoff job; retracted_ts and prior_status once the sync has them). Writes: app.handoff_action, app.assignment
(who owns each patient: claim, assign, release, handover), app.task_event (tasks: create, done, not_needed, reopen,
reassign, due), app.seen_event (each clinician's looks at a patient, for What changed since you looked) and
app.view_log (with the live clock, every view of an earlier time), created here on first start, append only. Every statement is safe to run again on a deployed database.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import config
from data import Dataset
from workflow import StoreUnavailable, last_names

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
        page          TEXT        NOT NULL CONSTRAINT view_log_page_check CHECK (page IN ('board', 'handoff', 'analytics', 'takeover', 'tasks')),
        csn          TEXT,
        at            TIMESTAMPTZ NOT NULL,
        recorded_ts   TIMESTAMPTZ NOT NULL DEFAULT now(),
        by_name       TEXT        NOT NULL,
        role          TEXT        NOT NULL,
        user_email    TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_view_recorded ON app.view_log (recorded_ts)",
    # A view_log created before the Take over page, or before the Tasks page, has the page check without 'takeover' or
    # 'tasks' (Postgres named the inline check view_log_page_check). Widen it once; the check of pg_constraint makes
    # this a no op afterwards, and the DO block is one transaction, so a second app process starting at the same time
    # redoes it harmlessly.
    """DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'app.view_log'::regclass
                       AND conname = 'view_log_page_check' AND pg_get_constraintdef(oid) LIKE '%''tasks''%') THEN
            ALTER TABLE app.view_log DROP CONSTRAINT IF EXISTS view_log_page_check;
            ALTER TABLE app.view_log ADD CONSTRAINT view_log_page_check
                CHECK (page IN ('board', 'handoff', 'analytics', 'takeover', 'tasks'));
        END IF;
    END $$""",
    """CREATE TABLE IF NOT EXISTS app.assignment (
        assignment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        csn           TEXT        NOT NULL,
        role          TEXT        NOT NULL CHECK (role IN ('ed_nurse', 'ed_physician')),
        kind          TEXT        NOT NULL CHECK (kind IN ('claim', 'assign', 'release', 'handover')),
        assignee_id   TEXT,
        assignee_name TEXT,
        from_id       TEXT,
        from_name     TEXT,
        at            TIMESTAMPTZ NOT NULL,
        recorded_ts   TIMESTAMPTZ NOT NULL DEFAULT now(),
        by_name       TEXT        NOT NULL,
        by_role       TEXT        NOT NULL,
        user_email    TEXT,
        detail        JSONB       NOT NULL DEFAULT '{}'::jsonb
    )""",
    "CREATE INDEX IF NOT EXISTS ix_assignment_csn ON app.assignment (csn, role, at)",
    # Tasks (tasks.py): one row per event; a create takes its task_id from the sequence, every later event names it.
    "CREATE SEQUENCE IF NOT EXISTS app.task_id_seq",
    """CREATE TABLE IF NOT EXISTS app.task_event (
        event_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        task_id       BIGINT      NOT NULL,
        csn           TEXT        NOT NULL,
        kind          TEXT        NOT NULL CHECK (kind IN ('create', 'done', 'not_needed', 'reopen', 'reassign', 'due')),
        template      TEXT,
        item_key      TEXT,
        text          TEXT,
        role          TEXT        CHECK (role IN ('nurse', 'physician')),
        assignee_id   TEXT,
        assignee_name TEXT,
        due           TIMESTAMPTZ,
        reason        TEXT,
        at            TIMESTAMPTZ NOT NULL,
        recorded_ts   TIMESTAMPTZ NOT NULL DEFAULT now(),
        by_name       TEXT        NOT NULL,
        by_role       TEXT        NOT NULL,
        user_email    TEXT,
        detail        JSONB       NOT NULL DEFAULT '{}'::jsonb
    )""",
    "CREATE INDEX IF NOT EXISTS ix_task_event_csn ON app.task_event (csn, at)",
    # An action list item becomes a task once per template, also when two app processes save the same sign off.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_task_event_item ON app.task_event (csn, template, item_key) "
    "WHERE kind = 'create' AND template IS NOT NULL",
    # Looks (seen.py): one row per open of a handoff or Mark as seen; by_id is the person (persona id, or the sign in
    # email live). The last look at a time is read per person, so the index leads with them.
    """CREATE TABLE IF NOT EXISTS app.seen_event (
        seen_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        by_id         TEXT        NOT NULL,
        csn           TEXT        NOT NULL,
        at            TIMESTAMPTZ NOT NULL,
        recorded_ts   TIMESTAMPTZ NOT NULL DEFAULT now(),
        source        TEXT        NOT NULL CHECK (source IN ('handoff', 'board')),
        by_name       TEXT        NOT NULL,
        role          TEXT        NOT NULL,
        user_email    TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_seen_event_who ON app.seen_event (by_id, csn, at)",
]
# app.task_event columns written from a task event; who acted (by_id) and what a change replaced go in detail.
TASK = ("csn", "kind", "template", "item_key", "text", "role", "assignee_id", "assignee_name", "due", "reason", "at",
        "by_name", "by_role", "user_email")
TASK_DETAIL = ("by_id", "version", "due_text", "owner_text", "from_role", "from_assignee_id", "from_assignee_name",
               "from_due", "undo", "outside_role")
# app.assignment columns written from an assignment row; by_id (who acted, for the stale rule) goes in detail.
ASSIGNMENT = ("csn", "role", "kind", "assignee_id", "assignee_name", "from_id", "from_name", "at", "by_name", "by_role",
              "user_email")
DETAIL = ("element", "text", "items", "model_before", "restore", "rule", "option", "entry", "entry_note", "remove", "callback",
          "readback", "questions", "by_id", "off_assignment", "assigned_to")
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
        self._assigned: dict[str, list[dict]] | None = None
        self._assigned_read = 0.0
        self._tasks: dict[str, list[dict]] | None = None
        self._tasks_read = 0.0
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

    def _all_assigned(self, fresh: bool = False) -> dict[str, list[dict]]:
        """Every row of app.assignment by csn, reread like the actions (ttl, or fresh)."""
        with self._lock:
            if fresh or self._assigned is None or time.time() - self._assigned_read > self.ttl:
                try:
                    self._ensure()
                    from psycopg.rows import dict_row
                    with PG.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
                        rows = cur.execute("SELECT * FROM app.assignment ORDER BY at, assignment_id").fetchall()
                except Exception as exc:
                    raise StoreUnavailable(UNREACHABLE) from exc
                out: dict[str, list[dict]] = {}
                for r in rows:
                    d = r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"] or "{}")
                    out.setdefault(r["csn"], []).append({**r, "detail": d, "by_id": d.get("by_id")})
                self._assigned, self._assigned_read = out, time.time()
            return self._assigned

    def assignments(self, csn: str, at: datetime | None = None, fresh: bool = False) -> list[dict]:
        return [r for r in self._all_assigned(fresh).get(csn, []) if at is None or r["at"] <= at]

    def names(self) -> dict[str, str]:
        """The display name each signed in email last used, from the rows already read (and reread with them)."""
        return last_names(self._all(), [r for rows in self._all_assigned().values() for r in rows],
                          [r for rows in self._all_tasks().values() for r in rows])

    def assign(self, row: dict) -> dict:
        """One ownership change, a row in app.assignment."""
        from psycopg.types.json import Jsonb
        detail = {**(row.get("detail") or {}), **({"by_id": row["by_id"]} if row.get("by_id") else {})}
        try:
            self._ensure()
            with PG.connect() as conn:
                aid, recorded = conn.execute(
                    f"INSERT INTO app.assignment ({', '.join(ASSIGNMENT)}, detail) VALUES ({', '.join(['%s'] * (len(ASSIGNMENT) + 1))}) "
                    "RETURNING assignment_id, recorded_ts", [row.get(k) for k in ASSIGNMENT] + [Jsonb(detail)]).fetchone()
        except Exception as exc:
            raise StoreUnavailable(UNREACHABLE) from exc
        with self._lock:
            self._assigned = None
        return {**row, "assignment_id": aid, "recorded_ts": recorded}

    def _all_tasks(self, fresh: bool = False) -> dict[str, list[dict]]:
        """Every row of app.task_event by csn, reread like the actions (ttl, or fresh)."""
        with self._lock:
            if fresh or self._tasks is None or time.time() - self._tasks_read > self.ttl:
                try:
                    self._ensure()
                    from psycopg.rows import dict_row
                    with PG.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
                        rows = cur.execute("SELECT * FROM app.task_event ORDER BY at, event_id").fetchall()
                except Exception as exc:
                    raise StoreUnavailable(UNREACHABLE) from exc
                out: dict[str, list[dict]] = {}
                for r in rows:
                    d = r["detail"] if isinstance(r["detail"], dict) else json.loads(r["detail"] or "{}")
                    if d.get("from_due"):
                        d["from_due"] = datetime.fromisoformat(d["from_due"])
                    out.setdefault(r["csn"], []).append({**{k: v for k, v in r.items() if k != "detail"}, **d})
                self._tasks, self._tasks_read = out, time.time()
            return self._tasks

    def task_events(self, csn: str, at: datetime | None = None, fresh: bool = False) -> list[dict]:
        return [r for r in self._all_tasks(fresh).get(csn, []) if at is None or r["at"] <= at]

    def all_task_events(self, at: datetime | None = None, fresh: bool = False) -> list[dict]:
        return [r for rows in self._all_tasks(fresh).values() for r in rows if at is None or r["at"] <= at]

    def add_tasks(self, rows: list[dict]) -> list[dict]:
        """Task events, rows in app.task_event saved in one transaction. A create takes the next task_id from
        app.task_id_seq; a create from a handoff whose item is a task of that template already is skipped (the unique
        index ux_task_event_item), also when another app process saved it a moment ago. Returns the rows saved."""
        from psycopg.types.json import Jsonb
        out = []
        try:
            self._ensure()
            with PG.connect() as conn, conn.transaction():
                for row in rows:
                    detail = {k: row[k] for k in TASK_DETAIL if row.get(k) is not None}
                    if isinstance(detail.get("from_due"), datetime):
                        detail["from_due"] = detail["from_due"].isoformat()
                    task_id = "nextval('app.task_id_seq')" if row["kind"] == "create" else "%s"
                    got = conn.execute(
                        f"INSERT INTO app.task_event (task_id, {', '.join(TASK)}, detail) VALUES ({task_id}, "
                        f"{', '.join(['%s'] * (len(TASK) + 1))}) ON CONFLICT (csn, template, item_key) "
                        "WHERE kind = 'create' AND template IS NOT NULL DO NOTHING RETURNING event_id, task_id, recorded_ts",
                        ([] if row["kind"] == "create" else [row["task_id"]]) + [row.get(k) for k in TASK] + [Jsonb(detail)]
                    ).fetchone()
                    if got:
                        out.append({**row, "event_id": got[0], "task_id": got[1], "recorded_ts": got[2]})
        except Exception as exc:
            raise StoreUnavailable(UNREACHABLE) from exc
        with self._lock:
            self._tasks = None
        return out

    def look(self, row: dict) -> dict:
        """One look at a patient, a row in app.seen_event."""
        try:
            self._ensure()
            with PG.connect() as conn:
                sid, recorded = conn.execute(
                    "INSERT INTO app.seen_event (by_id, csn, at, source, by_name, role, user_email) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING seen_id, recorded_ts",
                    (row["by_id"], row["csn"], row["at"], row["source"], row["by_name"], row["role"],
                     row.get("user_email"))).fetchone()
        except Exception as exc:
            raise StoreUnavailable(UNREACHABLE) from exc
        return {**row, "seen_id": sid, "recorded_ts": recorded}

    def last_looks(self, who: str, at: datetime, csn: str | None = None, fresh: bool = False) -> dict[str, dict]:
        """{csn: the latest look by `who` with at <= `at`}, at one patient (csn) or all: read on every call, one
        row per patient (seen.last_looks in SQL), as the table grows with every open."""
        try:
            self._ensure()
            from psycopg.rows import dict_row
            with PG.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
                rows = cur.execute(
                    "SELECT DISTINCT ON (csn) * FROM app.seen_event WHERE by_id = %s AND at <= %s"
                    + (" AND csn = %s" if csn is not None else "") + " ORDER BY csn, at DESC, seen_id DESC",
                    (who, at) + ((csn,) if csn is not None else ())).fetchall()
        except Exception as exc:
            raise StoreUnavailable(UNREACHABLE) from exc
        return {r["csn"]: r for r in rows}

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
