"""Live mode without Databricks: the Lakebase action store against a fake Postgres.

Several app processes share app.handoff_action, so a store rereads it after a short time (and on every action);
Lakebase being unreachable at start up does not stop the app.
"""
import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytest.importorskip("psycopg")


class FakeDB:
    def __init__(self):
        self.rows, self.up, self.ddl = [], True, 0

    def connect(self):
        if not self.up:
            raise OSError("connection refused")
        return FakeConn(self)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return list(self.rows)


class FakeConn:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, row_factory=None):
        return self

    def execute(self, sql, params=None):
        if sql.startswith("INSERT INTO app.assignment"):
            raise AssertionError("not used here: tests/test_live_postgres.py covers app.assignment")
        if sql.startswith("SELECT * FROM app.assignment"):
            return Result([])
        if sql.startswith("INSERT"):
            csn, template, version, kind, at, by, role, email, detail = params
            self.db.rows.append({"action_id": len(self.db.rows) + 1, "csn": csn, "template": template, "version": version,
                                 "kind": kind, "at": at, "recorded_ts": datetime.now(timezone.utc), "by_name": by,
                                 "role": role, "user_email": email, "detail": detail.obj})
        elif sql.startswith("SELECT"):
            return Result(sorted(self.db.rows, key=lambda r: (r["at"], r["action_id"])))
        else:
            self.db.ddl += 1
        return Result([])


@pytest.fixture
def lakebase(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "app/server"))
    for m in ("config", "data", "assignment", "workflow", "lakebase"):
        sys.modules.pop(m, None)
    mod = importlib.import_module("lakebase")
    db = FakeDB()
    monkeypatch.setattr(mod.PG, "connect", db.connect)
    yield mod, db
    for m in ("config", "data", "assignment", "workflow", "lakebase"):
        sys.modules.pop(m, None)


def action(csn="1", at=None):
    return {"csn": csn, "template": "sbar_nurse", "version": 1, "at": at or datetime(2026, 10, 27, 15, tzinfo=timezone.utc),
            "by": "RN", "role": "ed_nurse", "user_email": None, "element": "E25", "text": "Plan"}


def test_each_process_sees_the_actions_the_others_saved(lakebase):
    mod, db = lakebase
    a, b = mod.LakebaseStore(ttl=60), mod.LakebaseStore(ttl=60)
    assert b.actions("1") == []                   # b has read (and cached) the table
    a.add("edit", action())
    assert b.actions("1", fresh=True)[0]["text"] == "Plan"     # an action always reads again
    c = mod.LakebaseStore(ttl=0)
    a.add("sign", {**action(), "callback": "x4471"})
    assert [r["kind"] for r in c.actions("1")] == ["edit", "sign"]


def test_unreachable_at_start_up_does_not_stop_the_app(lakebase):
    mod, db = lakebase
    db.up = False
    store = mod.LakebaseStore(ttl=0)             # no exception
    with pytest.raises(mod.StoreUnavailable):
        store.actions("1")
    with pytest.raises(mod.StoreUnavailable):
        store.add("edit", action())
    db.up = True
    assert store.actions("1") == [] and db.ddl > 0
    store.add("edit", action())
    assert len(store.actions("1")) == 1


def test_live_app_starts_without_lakebase(monkeypatch):
    monkeypatch.setenv("EDIP_MODE", "live")
    monkeypatch.setenv("EDIP_STATIC", "/nonexistent")
    for k in ("PGHOST", "EDIP_SYNTHETIC_DATA", "EDIP_DEMO_PERSONAS", "EDIP_CLOCK_MODE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.syspath_prepend(str(ROOT / "app/server"))
    mods = ("config", "data", "assignment", "logic", "personas", "workflow", "main", "lakebase")
    for m in mods:
        sys.modules.pop(m, None)
    try:
        main = importlib.import_module("main")
        from fastapi.testclient import TestClient
        c = TestClient(main.app)
        health = c.get("/api/health").json()
        assert health["status"] == "not ready" and "not in Lakebase yet" in health["detail"]
        cfg = c.get("/api/config").json()
        assert cfg["ready"] is False and cfg["clock"] == "live"
        assert c.get("/api/board").json()["rows"] == []
    finally:
        for m in mods:
            sys.modules.pop(m, None)
