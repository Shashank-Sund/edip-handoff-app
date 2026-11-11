"""Live mode against a real Postgres (no Databricks): the app's tables created and upgraded in place, who owns each
patient in app.assignment, and the whole app on the fixtures loaded by app/fixtures/live_local.py.

Skipped unless EDIP_TEST_PG names a Postgres the tests may create databases in (CI runs a Postgres 16 service for
it, .github/workflows/ci.yml), for example:

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
MODS = ("config", "data", "assignment", "tasks", "logic", "personas", "workflow", "main", "lakebase")
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
        assert len(checks) == 1 and "takeover" in checks[0][0] and "'tasks'" in checks[0][0]
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


def test_a_deployment_from_before_tasks_gets_app_task_event_and_a_view_log_that_takes_the_tasks_page(db, monkeypatch):
    # The schema as the app before tasks left it: every table, and a view_log check with 'takeover' but not 'tasks'.
    mod = _lakebase(monkeypatch)
    old = [s.replace(", 'tasks'", "") for s in mod.DDL if "task_event" not in s and "task_id_seq" not in s]
    with psycopg.connect(db, autocommit=True) as conn:
        for s in old:
            conn.execute(s)
        assert conn.execute("SELECT to_regclass('app.task_event')").fetchone()[0] is None
        conn.execute("INSERT INTO app.view_log (page, at, by_name, role) VALUES ('takeover', now(), 'RN', 'ed_nurse')")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO app.view_log (page, at, by_name, role) VALUES ('tasks', now(), 'RN', 'ed_nurse')")
    mod.LakebaseStore(ttl=0)
    mod.LakebaseStore(ttl=0)                      # again: safe to repeat
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("INSERT INTO app.view_log (page, at, by_name, role) VALUES ('tasks', now(), 'RN', 'ed_nurse')")
        assert conn.execute("SELECT count(*) FROM app.view_log").fetchone()[0] == 2
        cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'app' "
                                           "AND table_name = 'task_event' ORDER BY ordinal_position")]
        assert cols == ["event_id", "task_id", "csn", "kind", "template", "item_key", "text", "role", "assignee_id",
                        "assignee_name", "due", "reason", "at", "recorded_ts", "by_name", "by_role", "user_email", "detail"]
        idx = dict(conn.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'app' "
                                "AND tablename = 'task_event'").fetchall())
        assert "(csn, at)" in idx["ix_task_event_csn"]
        assert "UNIQUE" in idx["ux_task_event_item"] and "(csn, template, item_key)" in idx["ux_task_event_item"]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO app.task_event (task_id, csn, kind, at, by_name, by_role) "
                         "VALUES (1, '1', 'delete', now(), 'RN', 'ed_nurse')")
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO app.task_event (task_id, csn, kind, role, at, by_name, by_role) "
                         "VALUES (1, '1', 'create', 'pharmacist', now(), 'RN', 'ed_nurse')")


def _task(csn, kind, at, task_id=None, **f):
    return {"csn": csn, "kind": kind, "at": at, "by_name": "RN", "by_role": "ed_nurse", "user_email": "rn@example.org",
            "by_id": "rn@example.org"} | ({"task_id": task_id} if task_id else {}) | f


def test_task_events_round_trip_and_an_item_becomes_a_task_once_across_processes(db, monkeypatch):
    mod = _lakebase(monkeypatch)
    import tasks
    a, b = mod.LakebaseStore(ttl=60), mod.LakebaseStore(ttl=60)
    t = datetime(2026, 10, 28, 20, tzinfo=timezone.utc)
    assert b.task_events("1") == []                               # b has read (and cached) the table
    item = dict(template="sbar_nurse", version=2, item_key="repeat lactate", text="Repeat lactate", role="nurse",
                due=t + timedelta(hours=1), due_text="16:00", owner_text="RN")
    saved = a.add_tasks([_task("1", "create", t, **item), _task("1", "create", t, template=None, item_key=None,
                                                                text="By hand", role="physician")])
    assert [r["task_id"] for r in saved] == [1, 2] and all(r["event_id"] and r["recorded_ts"] for r in saved)
    # Another process saving the same item (the same sign off) adds nothing: the unique index skips it.
    assert mod.LakebaseStore(ttl=60).add_tasks([_task("1", "create", t, **item)]) == []
    assert a.add_tasks([_task("1", "create", t, **(item | {"template": "ipass_physician"}))])[0]["task_id"] == 4
    a.add_tasks([_task("1", "due", t + timedelta(hours=1), task_id=1, due=None, from_due=t + timedelta(hours=1)),
                 _task("1", "reassign", t, task_id=2, role="nurse", assignee_id="rn2@example.org", assignee_name="Sam",
                       from_role="physician", outside_role=True),
                 _task("1", "not_needed", t + timedelta(hours=2), task_id=2, reason="Done by the unit")])
    assert b.task_events("1") == []                               # within its ttl, b reuses what it read
    rows = b.task_events("1", fresh=True)
    assert [(r["kind"], r["task_id"]) for r in rows] == [("create", 1), ("create", 2), ("create", 4), ("reassign", 2),
                                                          ("due", 1), ("not_needed", 2)]       # by time, then the order saved
    assert rows[0]["due"] == t + timedelta(hours=1) and rows[0]["version"] == 2 and rows[0]["owner_text"] == "RN"
    assert rows[4]["from_due"] == t + timedelta(hours=1) and rows[4]["due"] is None
    assert rows[3]["outside_role"] is True and rows[3]["by_id"] == "rn@example.org"
    state = tasks.replay(rows, t + timedelta(hours=3))
    assert (state[1]["due"], state[2]["status"], state[2]["assignee"]["name"]) == (None, "not_needed", "Sam")
    assert len(b.task_events("1", at=t)) == 4 and b.all_task_events() == rows
    assert mod.LakebaseStore(ttl=0).names() == {"rn@example.org": "RN"}
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT detail FROM app.task_event WHERE kind = 'due'").fetchone()[0] == {
            "by_id": "rn@example.org", "from_due": (t + timedelta(hours=1)).isoformat()}


def test_looks_are_kept_in_app_seen_event_and_the_last_one_at_a_time_is_read_per_person(db, monkeypatch):
    mod = _lakebase(monkeypatch)
    a, b = mod.LakebaseStore(ttl=60), mod.LakebaseStore(ttl=60)
    t = datetime(2026, 10, 28, 20, tzinfo=timezone.utc)
    rn, md = "rn@example.org", "md@example.org"
    look = lambda csn, who, at, source="handoff": {"csn": csn, "by_id": who, "by_name": who[:2].upper(),  # noqa: E731
                                                   "role": "ed_nurse", "user_email": who, "at": at, "source": source}
    first = a.look(look("1", rn, t))
    assert first["seen_id"] and first["recorded_ts"]
    a.look(look("1", rn, t + timedelta(hours=2)))
    a.look(look("1", rn, t + timedelta(hours=1), "board"))          # an earlier time, saved later
    a.look(look("1", rn, t + timedelta(hours=1)))                   # the same time again: the later one wins
    a.look(look("2", rn, t))
    a.look(look("1", md, t + timedelta(hours=3)))
    # Read on every call (no cache): another process sees them at once. One row per patient, as seen.last_looks.
    assert b.last_looks(rn, t - timedelta(minutes=1)) == {}
    got = b.last_looks(rn, t + timedelta(hours=1))
    assert set(got) == {"1", "2"} and got["1"]["source"] == "handoff" and got["1"]["at"] == t + timedelta(hours=1)
    assert got["1"]["seen_id"] == 4
    assert b.last_looks(rn, t + timedelta(hours=9), "1")["1"]["at"] == t + timedelta(hours=2)
    assert set(b.last_looks(rn, t + timedelta(hours=9), "2")) == {"2"}
    assert b.last_looks(md, t + timedelta(hours=9))["1"]["by_id"] == md
    with psycopg.connect(db, autocommit=True) as conn:
        cols = [r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = 'app' "
                                           "AND table_name = 'seen_event' ORDER BY ordinal_position")]
        assert cols == ["seen_id", "by_id", "csn", "at", "recorded_ts", "source", "by_name", "role", "user_email"]
        idx = conn.execute("SELECT indexdef FROM pg_indexes WHERE schemaname = 'app' AND indexname = 'ix_seen_event_who'").fetchone()
        assert "(by_id, csn, at)" in idx[0]
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("INSERT INTO app.seen_event (by_id, csn, at, source, by_name, role) "
                         "VALUES ('x', '1', now(), 'tasks', 'RN', 'ed_nurse')")


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


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    """EDIP_FIXTURES when it holds fixtures, else fixtures built from the stand in world (as in CI)."""
    given = Path(os.getenv("EDIP_FIXTURES", ROOT / "app/fixtures/data"))
    if (given / "meta.json").exists():
        return given
    sys.path[:0] = [str(ROOT / "ai"), str(ROOT / "synthetic"), str(ROOT / "pipelines")]
    from eval import run_eval
    world, out = tmp_path_factory.mktemp("world"), tmp_path_factory.mktemp("fixtures")
    run_eval.build_world(world)
    subprocess.run([sys.executable, str(ROOT / "app/fixtures/build.py"), "--world", str(world), "--out", str(out)],
                   check=True, capture_output=True)
    return out


@pytest.fixture
def live_app(db, fixtures, monkeypatch):
    """The app in live mode on the fixtures moved to end now (live_local.py), two ED nurses and a physician in the
    role map, as a deployment for real use runs (no demo personas, the live clock)."""
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


def test_colleagues_keep_the_name_they_last_used_after_a_restart(live_app, monkeypatch):
    c, main = live_app
    csn = next(r["csn"] for r in c.get("/api/board", headers=who("rn.one@example.org")).json()["rows"] if r["phase"] == "boarding")
    # Sam acts under one name, then takes the patient under a newer one; the physician never does anything.
    c.post(f"/api/handoffs/{csn}/sbar_nurse/edit", json={"element": "E25", "text": "Plan"}, headers=who("rn.two@example.org", "Sam T"))
    c.post(f"/api/handoffs/{csn}/owner", json={"kind": "claim"}, headers=who("RN.Two@example.org", "Sam Two"))
    # A new app process: nothing in memory, so the names come from app.handoff_action and app.assignment.
    _lakebase(monkeypatch)
    main2 = importlib.import_module("main")
    from fastapi.testclient import TestClient
    c2 = TestClient(main2.app)
    col = c2.get("/api/colleagues", headers={"x-forwarded-email": "rn.one@example.org"}).json()
    assert col == [{"id": "rn.one@example.org", "name": "rn.one@example.org", "you": True},
                   {"id": "rn.two@example.org", "name": "Sam Two", "you": False}]
    # Signing in again wins over the saved name.
    c2.get("/api/me", headers=who("rn.one@example.org", "Pat One"))
    assert c2.get("/api/colleagues", headers=who("rn.two@example.org")).json()[0]["name"] == "Pat One"


def _sign_live(c, csn, headers, items):
    """Resolve what blocks sign off, write the action list, sign: as the ED nurse would, at the current time."""
    url = f"/api/handoffs/{csn}"
    d = c.get(url, headers=headers).json()["templates"]["sbar_nurse"]["draft"]
    for rule in d["rules"]:
        if rule["rule"] in d["completeness"]["blocking"]:
            opts = rule.get("attest_options") or []
            body, kind = ({"rule": rule["rule"], "option": opts[0]}, "attest") if opts and rule["status"] != "missing" else \
                ({"element": rule["element"], "text": "Written by the nurse"}, "edit")
            assert c.post(f"{url}/sbar_nurse/{kind}", json=body, headers=headers).status_code == 200
    assert c.post(f"{url}/sbar_nurse/edit", json={"element": "E24", "items": items}, headers=headers).status_code == 200
    r = c.post(f"{url}/sbar_nurse/sign", json={"callback": "x4471"}, headers=headers)
    assert r.status_code == 200, r.json()


ITEMS = [{"text": "Repeat lactate", "owner": "RN", "due": "Now"}, {"text": "Call the family", "owner": "team", "due": ""}]


def test_the_live_app_keeps_tasks_in_app_task_event(live_app, db):
    c, main = live_app
    one, two, md = who("rn.one@example.org", "Pat One"), who("rn.two@example.org", "Sam Two"), who("md@example.org", "Dr Lee")
    c.get("/api/me", headers=two)                                  # Sam signs in once, so the list has their name
    csn = next(r["csn"] for r in c.get("/api/board", headers=one).json()["rows"] if r["phase"] == "boarding")
    c.post(f"/api/handoffs/{csn}/owner", json={"kind": "claim"}, headers=one)
    _sign_live(c, csn, one, ITEMS)
    _sign_live(c, csn, one, ITEMS)                                 # signing twice adds nothing
    tu = f"/api/handoffs/{csn}/tasks"
    view = c.get(tu, headers=one).json()
    assert [(t["text"], t["role"], t["created"]["by"]) for t in view["tasks"]] == [
        ("Repeat lactate", "nurse", "Pat One"), ("Call the family", "physician", "Pat One")]
    assert {p["id"] for p in view["people"]} == {"rn.one@example.org", "rn.two@example.org", "md@example.org"}
    assert next(r for r in c.get("/api/board", headers=one).json()["rows"] if r["csn"] == csn)["tasks"]["open"] == 2
    lac, fam = (t["id"] for t in view["tasks"])
    added = c.post(tu, json={"text": "Bring glasses", "to": "rn.two@example.org"}, headers=one).json()["tasks"][-1]
    assert added["assignee"] == {"id": "rn.two@example.org", "name": "Sam Two"} and added["role"] == "nurse"
    assert c.post(f"/api/tasks/{lac}", json={"kind": "done"}, headers=two).status_code == 200
    assert c.post(f"/api/tasks/{fam}", json={"kind": "not_needed", "reason": "Spoke with them"}, headers=md).status_code == 200
    assert c.post(f"/api/tasks/{lac}", json={"kind": "undo"}, headers=one).status_code == 200
    assert c.post(f"/api/tasks/{added['id']}", json={"kind": "reassign", "role": "physician"}, headers=one).status_code == 200
    # Pat owns the patient: the nurse's open task is theirs.
    page = c.get("/api/tasks", headers=one).json()
    assert [t["text"] for g in page["groups"] for t in g["tasks"]] == ["Repeat lactate"]
    # A signed in user without a role reads tasks but cannot change them.
    viewer = who("someone@example.org")
    assert c.get(tu, headers=viewer).json()["access"]["can_act"] is False
    assert c.post(f"/api/tasks/{lac}", json={"kind": "done"}, headers=viewer).status_code == 409
    # Looking at an earlier time is read only and logged: the Tasks page as 'tasks', the panel as 'handoff'.
    earlier = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert c.get("/api/tasks", params={"at": earlier}, headers=one).json()["counts"]["open"] == 0
    assert c.get(tu, params={"at": earlier}, headers=one).json()["tasks"] == []
    refused = c.post(f"/api/tasks/{lac}", params={"at": earlier}, json={"kind": "done"}, headers=one)
    assert refused.status_code == 409 and "earlier time" in refused.json()["detail"]
    assert c.post(tu, params={"at": earlier}, json={"text": "x", "role": "nurse"}, headers=one).status_code == 409
    with psycopg.connect(db) as conn:
        rows = conn.execute("SELECT kind, task_id, template, text, role, by_name, user_email, reason, detail FROM app.task_event "
                            "ORDER BY event_id").fetchall()
        assert [(r[0], r[1]) for r in rows] == [("create", 1), ("create", 2), ("create", 3), ("done", 1), ("not_needed", 2),
                                                ("reopen", 1), ("reassign", 3)]
        assert rows[0][2:7] == ("sbar_nurse", "Repeat lactate", "nurse", "Pat One", "rn.one@example.org")
        assert rows[4][7] == "Spoke with them" and rows[5][8] == {"by_id": "rn.one@example.org", "undo": True}
        assert rows[2][2] is None and rows[2][8]["by_id"] == "rn.one@example.org"
        assert conn.execute("SELECT page FROM app.view_log ORDER BY view_id").fetchall() == [("tasks",), ("handoff",)]


def test_handoffs_signed_before_tasks_existed_get_their_tasks_at_start_up(live_app, db, monkeypatch):
    c, main = live_app
    one = who("rn.one@example.org", "Pat One")
    csn = next(r["csn"] for r in c.get("/api/board", headers=one).json()["rows"] if r["phase"] == "boarding")
    _sign_live(c, csn, one, ITEMS)
    # As a deployment from before tasks: the sign off is in app.handoff_action, and there is no app.task_event.
    with psycopg.connect(db, autocommit=True) as conn:
        conn.execute("DROP TABLE app.task_event")
        signed = conn.execute("SELECT at, by_name FROM app.handoff_action WHERE kind = 'sign'").fetchone()
    for _ in range(2):                                             # two starts (or two processes): the tasks once
        _lakebase(monkeypatch)
        main2 = importlib.import_module("main")
    from fastapi.testclient import TestClient
    got = TestClient(main2.app).get(f"/api/handoffs/{csn}/tasks", headers=one).json()["tasks"]
    assert [(t["text"], t["role"], t["created"]["by"]) for t in got] == [("Repeat lactate", "nurse", signed[1]),
                                                                       ("Call the family", "physician", signed[1])]
    assert datetime.fromisoformat(got[0]["created"]["at"]) == signed[0].replace(microsecond=0)
    assert datetime.fromisoformat(got[0]["due"]) == signed[0].replace(microsecond=0)
    with psycopg.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM app.task_event").fetchone()[0] == 2
