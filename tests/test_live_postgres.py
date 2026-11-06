"""Live mode against a real Postgres (no Databricks): the app's tables created and upgraded in place, who owns each
patient in app.assignment, and the whole app on the fixtures loaded by app/fixtures/live_local.py.

Skipped unless EDIP_TEST_PG names a Postgres the tests may create databases in, for example:

    docker run -d --name edip-pg-test -e POSTGRES_PASSWORD=edip -p 55432:5432 postgres:16
    EDIP_TEST_PG=postgresql://postgres:edip@localhost:55432/postgres .venv/bin/python -m pytest tests/test_live_postgres.py
"""
import importlib
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv("EDIP_TEST_PG")
psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.skipif(not DSN, reason="EDIP_TEST_PG is not set (a Postgres for the live store tests)")
MODS = ("config", "data", "assignment", "logic", "personas", "workflow", "main", "lakebase")
# The view_log of a deployment from before the Take over page: an inline page check without 'takeover'.
OLD_VIEW_LOG = """CREATE TABLE app.view_log (
    view_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page TEXT NOT NULL CHECK (page IN ('board', 'handoff', 'analytics')),
    csn TEXT, at TIMESTAMPTZ NOT NULL, recorded_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    by_name TEXT NOT NULL, role TEXT NOT NULL, user_email TEXT)"""


@pytest.fixture
def db(monkeypatch):
    """A new, empty database, with the PG* settings the app reads pointing at it."""
    name = f"edip_test_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {name}")
    u = urlparse(DSN)
    for k, v in {"PGHOST": u.hostname, "PGPORT": str(u.port or 5432), "PGUSER": u.username, "PGPASSWORD": u.password,
                 "PGDATABASE": name, "PGSSLMODE": "disable"}.items():
        monkeypatch.setenv(k, v)
    yield u._replace(path=f"/{name}").geturl()
    for m in MODS:
        sys.modules.pop(m, None)
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


def _lakebase(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "app/server"))
    for m in MODS:
        sys.modules.pop(m, None)
    return importlib.import_module("lakebase")


def _row(csn, kind, who, at, role="ed_nurse", by_id=None):
    return {"csn": csn, "role": role, "kind": kind, "assignee_id": who, "assignee_name": who and who.upper(),
            "from_id": None, "from_name": None, "at": at, "by_name": "RN", "by_role": role, "user_email": None,
            "by_id": by_id or who, "detail": {}}


def test_the_tables_are_created_and_an_old_view_log_is_widened_in_place(db, monkeypatch):
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA app")
        conn.execute(OLD_VIEW_LOG)
        conn.execute("INSERT INTO app.view_log (page, at, by_name, role) VALUES ('board', now(), 'RN', 'ed_nurse')")
    mod = _lakebase(monkeypatch)
    mod.LakebaseStore(ttl=0)                      # runs the DDL
    mod.LakebaseStore(ttl=0)                      # and again: every statement is safe to repeat
    with psycopg.connect(db, autocommit=True) as conn:
        checks = conn.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'app.view_log'::regclass "
                              "AND contype = 'c'").fetchall()
        assert len(checks) == 1 and "takeover" in checks[0][0]
        conn.execute("INSERT INTO app.view_log (page, at, by_name, role) VALUES ('takeover', now(), 'RN', 'ed_nurse')")
        assert conn.execute("SELECT count(*) FROM app.view_log").fetchone()[0] == 2      # the old rows stay
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO app.view_log (page, at, by_name, role) VALUES ('nowhere', now(), 'RN', 'ed_nurse')")
        cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'app' "
                                           "AND table_name = 'assignment' ORDER BY ordinal_position")]
        assert cols == ["assignment_id", "csn", "role", "kind", "assignee_id", "assignee_name", "from_id", "from_name", "at",
                        "recorded_ts", "by_name", "by_role", "user_email", "detail"]
        idx = conn.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = 'app' AND indexname = 'ix_assignment_csn'").fetchone()
        assert "(csn, role, at)" in idx[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO app.assignment (csn, role, kind, at, by_name, by_role) VALUES ('1', 'ed_nurse', 'steal', now(), 'RN', 'ed_nurse')")


def test_assignments_are_kept_in_order_and_every_process_sees_them(db, monkeypatch):
    mod = _lakebase(monkeypatch)
    import assignment
    a, b = mod.LakebaseStore(ttl=60), mod.LakebaseStore(ttl=60)
    t = datetime(2026, 10, 28, 20, tzinfo=timezone.utc)
    assert b.assignments("1") == []                               # b has read (and cached) the table
    first = a.assign(_row("1", "claim", "rn1@example.org", t))
    a.assign(_row("1", "assign", "rn2@example.org", t, by_id="rn1@example.org"))
    a.assign(_row("1", "claim", "md@example.org", t - timedelta(hours=1), role="ed_physician"))
    assert first["assignment_id"] and first["recorded_ts"]
    assert b.assignments("1") == []                               # within its ttl, b reuses what it read
    rows = b.assignments("1", fresh=True)
    assert [r["kind"] for r in rows] == ["claim", "claim", "assign"]           # by time, then the order saved
    assert rows[2]["by_id"] == "rn1@example.org" and rows[2]["detail"] == {"by_id": "rn1@example.org"}
    assert b.assignments("1", at=t - timedelta(minutes=30)) == rows[:1]
    owner = assignment.owner_at(rows, "ed_nurse", [], t, timedelta(hours=14), "rn2@example.org")
    assert owner["id"] == "rn2@example.org" and owner["you"]
    assert mod.LakebaseStore(ttl=0).assignments("2") == []


@pytest.fixture
def live_app(db, monkeypatch):
    """The app in live mode on the fixtures moved to end now (live_local.py), two ED nurses and a physician in the
    role map, as a deployment for real use runs (no demo personas, the live clock)."""
    fixtures = Path(os.getenv("EDIP_FIXTURES", ROOT / "app/fixtures/data"))
    if not (fixtures / "meta.json").exists():
        pytest.skip(f"no fixtures in {fixtures}")
    subprocess.run([sys.executable, str(ROOT / "app/fixtures/live_local.py"), "--dsn", db, "--fixtures", str(fixtures)],
                   check=True, capture_output=True)
    for k, v in {"EDIP_MODE": "live", "EDIP_SYNTHETIC_DATA": "false", "EDIP_STATIC": "/nonexistent", "EDIP_FIXTURES": str(fixtures),
                 "EDIP_ROLE_MAP": json.dumps({"rn.one@example.org": "ed-rn", "rn.two@example.org": "ed-rn", "md@example.org": "ed-md"})}.items():
        monkeypatch.setenv(k, v)
    for k in ("EDIP_DEMO_PERSONAS", "EDIP_CLOCK_MODE", "EDIP_CLOCK"):
        monkeypatch.delenv(k, raising=False)
    _lakebase(monkeypatch)
    main = importlib.import_module("main")
    from fastapi.testclient import TestClient
    return TestClient(main.app), main


def who(email, name=None):
    return {"x-forwarded-email": email} | ({"x-forwarded-preferred-username": name} if name else {})


def test_the_live_app_takes_assigns_and_hands_over_through_app_assignment(live_app, db):
    c, main = live_app
    one, two = who("rn.one@example.org", "Pat One"), who("rn.two@example.org", "Sam Two")
    c.get("/api/me", headers=two)                                  # Sam signs in once, so the list has their name
    board = c.get("/api/board", headers=one).json()
    rows = [r for r in board["rows"] if r["phase"] == "boarding"]
    assert rows and all(r["owners"] == {"ed_nurse": None, "ed_physician": None} for r in rows)   # no demo seed live
    csn = rows[0]["csn"]
    took = c.post(f"/api/handoffs/{csn}/owner", json={"kind": "claim"}, headers=one)
    assert took.status_code == 200 and took.json()["summary"]["owners"]["ed_nurse"]["id"] == "rn.one@example.org"
    # Acting on it as the other nurse is allowed, and outside the assignment.
    edit = c.post(f"/api/handoffs/{csn}/sbar_nurse/edit", json={"element": "E25", "text": "By a colleague"}, headers=two).json()
    assert next(a for a in edit["activity"] if a["by"] == "Sam Two")["off_assignment"] is True
    view = c.get("/api/takeover", params={"colleague": "rn.one@example.org"}, headers=two).json()
    assert [r["csn"] for r in view["rows"]] == [csn]
    taken = c.post("/api/takeover", json={"colleague": "rn.one@example.org", "csns": [csn]}, headers=two).json()
    assert taken["taken"] == [csn]
    assert c.get("/api/board", headers=two).json()["kpis"]["mine"] == 1
    # Looking at Take over an hour back is read only and logged as the takeover page.
    earlier = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert c.get("/api/takeover", params={"at": earlier, "colleague": "rn.one@example.org"}, headers=two).status_code == 200
    assert c.post("/api/takeover", params={"at": earlier}, json={"colleague": "rn.one@example.org", "csns": [csn]},
                  headers=two).status_code == 409
    with psycopg.connect(db) as conn:
        kinds = conn.execute("SELECT kind, assignee_id, from_id, by_name, user_email, detail FROM app.assignment ORDER BY assignment_id").fetchall()
        assert [(k[0], k[1], k[2]) for k in kinds] == [("claim", "rn.one@example.org", None),
                                                        ("handover", "rn.two@example.org", "rn.one@example.org")]
        assert kinds[1][3] == "Sam Two" and kinds[1][4] == "rn.two@example.org"
        assert conn.execute("SELECT detail->>'off_assignment' FROM app.handoff_action").fetchone()[0] == "true"
        assert conn.execute("SELECT page FROM app.view_log").fetchall() == [("takeover",)]
