"""Looking at an earlier time with the live clock, against a fake Postgres.

Reads may look back up to EDIP_PAST_VIEW_HOURS, read only, and every such view is a row in app.view_log (refused
when the row cannot be written); actions always carry the server's current time. A draft version shows from when the
handoff job wrote it (created_ts). The replay clock is unchanged.
"""
import importlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "app/fixtures")]
pytest.importorskip("psycopg")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import live_local  # noqa: E402
import parity  # noqa: E402

MODS = ("config", "data", "assignment", "logic", "personas", "workflow", "main", "lakebase")
RN, MD, HOSP = "rn@example.org", "md@example.org", "hosp@example.org"
ROLES = {RN: "ed-rn", MD: "ed-md", HOSP: "hosp-md"}


def who(email):
    return {"X-Forwarded-Email": email, "X-Forwarded-Preferred-Username": email.split("@")[0].upper()}


class Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return [dict(r) for r in self.rows]


class FakeDB:
    """The synced tables (read), app.handoff_action and app.view_log (append), as the app's SQL uses them."""

    def __init__(self, encounters, drafts):
        self.encounters, self.drafts = encounters, drafts
        self.actions, self.views, self.fail_views = [], [], False
        self.old_sync = False             # a synced table from before retracted_ts and prior_status

    def connect(self):
        return FakeConn(self)


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
        db = self.db
        if sql.startswith("SELECT * FROM gold.encounter_state_pg"):
            return Result(db.encounters)
        if sql.startswith("SELECT csn, template"):
            if db.old_sync and ("retracted_ts" in sql or "prior_status" in sql):
                from psycopg.errors import UndefinedColumn
                raise UndefinedColumn('column "retracted_ts" does not exist')
            new = ("retracted_ts", "prior_status") if db.old_sync else ()
            return Result([{k: v for k, v in d.items() if k not in new} for d in db.drafts])
        if sql.startswith("SELECT * FROM app.handoff_action"):
            return Result(sorted(db.actions, key=lambda r: (r["at"], r["action_id"])))
        if sql.startswith("INSERT INTO app.handoff_action"):
            csn, template, version, kind, at, by, role, email, detail = params
            db.actions.append({"action_id": len(db.actions) + 1, "csn": csn, "template": template, "version": version,
                               "kind": kind, "at": at, "recorded_ts": datetime.now(timezone.utc), "by_name": by,
                               "role": role, "user_email": email, "detail": detail.obj})
        elif sql.startswith("INSERT INTO app.view_log"):
            if db.fail_views:
                raise OSError("connection refused")
            page, csn, at, by, role, email = params
            db.views.append({"page": page, "csn": csn, "at": at, "recorded_ts": datetime.now(timezone.utc), "by_name": by,
                             "role": role, "user_email": email})
        elif not sql.startswith(("CREATE", "ALTER")):
            raise AssertionError(f"unexpected SQL {sql[:60]}")
        return Result([])


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory):
    out = tmp_path_factory.mktemp("fixtures")
    parity.build_fixtures(out)
    return out


def _clean():
    for m in MODS:
        sys.modules.pop(m, None)


@pytest.fixture
def live(fixtures, monkeypatch):
    """start(**env) -> (client, main, db): the live app on the fixtures moved so their week ends now."""
    def start(hours="72", lag=timedelta(minutes=3), edit=None, old_sync=False):
        encounters, drafts = live_local.shifted(fixtures, datetime.now(timezone.utc).replace(microsecond=0), lag)
        if edit:
            edit(drafts)
        db = FakeDB(encounters, drafts)
        db.old_sync = old_sync
        env = {"EDIP_MODE": "live", "EDIP_SYNTHETIC_DATA": "false", "EDIP_STATIC": "/nonexistent",
               "EDIP_ROLE_MAP": json.dumps(ROLES), "EDIP_PAST_VIEW_HOURS": hours, "EDIP_ACTIONS_TTL": "0", "PGUSER": "app",
               "PGHOST": "localhost", "PGPASSWORD": "x"}
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        for k in ("EDIP_CLOCK_MODE", "EDIP_DEMO_PERSONAS", "EDIP_CLOCK"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.syspath_prepend(str(ROOT / "app/server"))
        _clean()
        lakebase = importlib.import_module("lakebase")
        monkeypatch.setattr(lakebase.PG, "connect", db.connect)
        main = importlib.import_module("main")
        from fastapi.testclient import TestClient
        return TestClient(main.app), main, db
    yield start
    _clean()


def ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def boarding(c, at=None, email=RN):
    rows = c.get("/api/board", params={"at": at} if at else None, headers=who(email)).json()["rows"]
    return next(r for r in rows if r["phase"] == "boarding")


def narrative(case, template="sbar_nurse"):
    return next(e["id"] for s in case["templates"][template]["draft"]["sections"] for e in s["elements"]
                if e["kind"] == "narrative" and e["id"] != "E07")


def test_reads_look_back_within_the_limit_and_each_view_is_logged(live):
    c, main, db = live()
    cfg = c.get("/api/config").json()
    assert cfg["clock"] == "live" and cfg["past_view_hours"] == 72
    # Now: no `at`, an `at` of now (within the slack), or a later one. None is logged.
    for at in (None, ago(seconds=30), ago(hours=-3)):
        b = c.get("/api/board", params={"at": at} if at else None, headers=who(RN))
        assert b.status_code == 200
        assert abs(datetime.fromisoformat(b.json()["at"]) - datetime.now(timezone.utc)) < timedelta(minutes=1)
    assert db.views == []
    t = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=5)
    b = c.get("/api/board", params={"at": t.isoformat()}, headers=who(RN)).json()
    assert datetime.fromisoformat(b["at"]) == t
    r = next(x for x in b["rows"] if x["phase"] == "boarding")
    assert c.get(f"/api/handoffs/{r['csn']}", params={"at": t.isoformat()}, headers=who(RN)).status_code == 200
    assert c.get("/api/analytics", params={"at": t.isoformat()}, headers=who(RN)).status_code == 200
    assert [(v["page"], v["csn"]) for v in db.views] == [("board", None), ("handoff", r["csn"]), ("analytics", None)]
    assert all(v["at"] == t and v["user_email"] == RN and v["by_name"] == "RN" and v["role"] == "ed_nurse" for v in db.views)
    # Inside the limit, just: allowed; past it: refused, in plain words, and not logged.
    assert c.get("/api/board", params={"at": ago(hours=71, minutes=50)}, headers=who(RN)).status_code == 200
    n = len(db.views)
    old = c.get("/api/board", params={"at": ago(hours=72, minutes=5)}, headers=who(RN))
    assert old.status_code == 400 and old.json()["detail"] == "You can look back up to 72 hours. Pick a later time, or go back to live."
    assert c.get(f"/api/handoffs/{r['csn']}", params={"at": ago(hours=80)}, headers=who(RN)).status_code == 400
    for odd in ("yesterday", "0001-01-01T00:00:00+05:00", "9999-12-31T23:59:59-05:00", "NaN"):
        assert c.get("/api/board", params={"at": odd}, headers=who(RN)).status_code == 400, odd
        assert c.post(f"/api/handoffs/{r['csn']}/sbar_nurse/sign", params={"at": odd}, json={"callback": "x4471"},
                      headers=who(RN)).status_code == 400, odd
    assert len(db.views) == n


@pytest.mark.parametrize("value,hours", [("inf", 8760), ("1e9", 8760), ("nan", 0), ("-5", 0), ("36", 36)])
def test_past_view_hours_out_of_range(live, value, hours):
    c, main, db = live(hours=value)
    assert c.get("/api/config").json()["past_view_hours"] == hours


def test_a_past_view_is_refused_when_the_log_cannot_be_written(live):
    c, main, db = live()
    db.fail_views = True
    r = c.get("/api/board", params={"at": ago(hours=2)}, headers=who(RN))
    assert r.status_code == 503 and r.json()["detail"].startswith("The app could not record that you are looking at an earlier time")
    csn = boarding(c)["csn"]
    assert c.get(f"/api/handoffs/{csn}", params={"at": ago(hours=1)}, headers=who(RN)).status_code == 503
    assert c.get("/api/board", headers=who(RN)).status_code == 200            # now is not logged, so it works
    assert db.views == []


def test_actions_use_the_server_time_and_are_refused_while_looking_back(live):
    c, main, db = live()
    row = boarding(c)
    csn = row["csn"]
    el = narrative(c.get(f"/api/handoffs/{csn}", headers=who(RN)).json())
    body = {"element": el, "text": "Plan reviewed at the bedside."}
    for at in (ago(hours=1), ago(minutes=10)):
        r = c.post(f"/api/handoffs/{csn}/sbar_nurse/edit", params={"at": at}, json=body, headers=who(RN))
        assert r.status_code == 409 and r.json()["detail"] == main.READ_ONLY
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/edit", params={"at": "soon"}, json=body, headers=who(RN)).status_code == 400
    assert db.actions == []
    # No `at`, an `at` within the slack, or a later one: all recorded at the server's current time.
    for at in (None, ago(seconds=40), ago(hours=-5)):
        before = datetime.now(timezone.utc)
        r = c.post(f"/api/handoffs/{csn}/sbar_nurse/edit", params={"at": at} if at else None, json=body, headers=who(RN))
        assert r.status_code == 200, r.text
        assert before <= db.actions[-1]["at"] <= datetime.now(timezone.utc)
    assert len(db.actions) == 3 and db.views == []
    # Sign and accept at now still work (I-PASS: the ED physician signs, the admitting physician accepts).
    case = c.get(f"/api/handoffs/{csn}", headers=who(MD)).json()
    for rule in case["templates"]["ipass_physician"]["draft"]["rules"]:
        if rule["rule"] in case["templates"]["ipass_physician"]["draft"]["completeness"]["blocking"]:
            opt = rule.get("attest_options") or []
            if opt:
                assert c.post(f"/api/handoffs/{csn}/ipass_physician/attest", json={"rule": rule["rule"], "option": opt[0]},
                              headers=who(MD)).status_code == 200
            else:
                e = c.post(f"/api/handoffs/{csn}/ipass_physician/edit", json={"element": rule["element"], "text": "Written by the ED."},
                           headers=who(MD))
                assert e.status_code == 200, e.text
    s = c.post(f"/api/handoffs/{csn}/ipass_physician/sign", json={"callback": "x4471"}, headers=who(MD))
    assert s.status_code == 200, s.text
    readback = {"readback": "Watcher patient, key risk is sepsis, I will recheck vitals first."}
    assert c.post(f"/api/handoffs/{csn}/ipass_physician/ack", params={"at": ago(hours=1)}, json=readback,
                  headers=who(HOSP)).status_code == 409
    a = c.post(f"/api/handoffs/{csn}/ipass_physician/ack", json=readback, headers=who(HOSP))
    assert a.status_code == 200 and a.json()["templates"]["ipass_physician"]["workflow"]["state"] == "acknowledged"
    # Looking back to before the sign off shows it unsigned again: nothing was moved.
    then = c.get(f"/api/handoffs/{csn}", params={"at": ago(minutes=30)}, headers=who(HOSP)).json()
    assert then["templates"]["ipass_physician"]["workflow"]["state"] != "acknowledged"


def test_a_version_shows_from_when_the_job_wrote_it(live):
    picked = {}

    def late(drafts):
        # A second SBAR version, 10 to 40 hours ago, that the job wrote 2 hours after its as_of.
        now = datetime.now(timezone.utc)
        for d in sorted(drafts, key=lambda d: d["as_of"]):
            if d["template"] == "sbar_nurse" and d["version"] == 2 and now - timedelta(hours=40) < d["as_of"] < now - timedelta(hours=10):
                d["created_ts"] = d["as_of"] + timedelta(hours=2)
                picked.setdefault("rows", []).append((d["csn"], d["as_of"]))
    c, main, db = live(edit=late)
    for csn, as_of in picked["rows"]:
        at = (as_of + timedelta(hours=1)).isoformat()
        r = c.get(f"/api/handoffs/{csn}", params={"at": at}, headers=who(RN))
        if r.status_code != 200 or "sbar_nurse" not in r.json()["templates"]:
            continue                          # not on the board then: try the next one
        before = r.json()["templates"]["sbar_nurse"]
        assert 2 not in [v["version"] for v in before["versions"]]            # charted, but not yet written by the job
        assert before["row"]["version"] == max(v["version"] for v in before["versions"])
        r2 = c.get(f"/api/handoffs/{csn}", params={"at": (as_of + timedelta(hours=2, minutes=1)).isoformat()}, headers=who(RN))
        if r2.status_code != 200:
            continue
        after = r2.json()["templates"]["sbar_nurse"]
        v2 = next(v for v in after["versions"] if v["version"] == 2)
        assert datetime.fromisoformat(v2["as_of"]) == as_of                   # shown with its chart time
        # The newest number shows, also when an older version was written after it.
        assert after["row"]["version"] == max(v["version"] for v in after["versions"]) >= 2
        assert [v["version"] for v in after["versions"]] == sorted(v["version"] for v in after["versions"])
        break
    else:
        pytest.fail("no patient on the board with a second SBAR version in the window")


def test_dataset_uses_created_ts_only_with_the_live_clock(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "app/server"))
    _clean()
    from data import Dataset
    t = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    rows = [{"csn": "1", "template": "sbar_nurse", "version": 1, "as_of": t, "created_ts": t + timedelta(minutes=30),
             "draft": {}, "status": "draft"}]
    meta = {"window": {"start": "2026-10-26T12:00:00Z", "days": 7}}
    replay, written = Dataset([], rows, meta, "x"), Dataset([], rows, meta, "x", written=True)
    assert replay.rows_at("1", t + timedelta(minutes=1)) and not written.rows_at("1", t + timedelta(minutes=1))
    assert written.rows_at("1", t + timedelta(minutes=30))
    # A created_ts before the as_of (or none) shows from the as_of.
    early = Dataset([], [{**rows[0], "created_ts": t - timedelta(hours=1)}, {**rows[0], "version": 2, "created_ts": None}], meta, "x", written=True)
    assert len(early.rows_at("1", t)) == 2 and not early.rows_at("1", t - timedelta(minutes=1))
    _clean()


def test_a_retracted_version_has_its_prior_status_until_the_cancel(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "app/server"))
    _clean()
    from data import Dataset, status_at
    t = datetime(2026, 10, 27, 12, tzinfo=timezone.utc)
    meta = {"window": {"start": "2026-10-26T12:00:00Z", "days": 7}}

    def row(version, minutes, prior):
        return {"csn": "1", "template": "sbar_nurse", "version": version, "as_of": t + timedelta(minutes=minutes),
                "trigger": "x", "draft": {}, "status": "retracted", "retracted_ts": "2026-10-27T17:00:00Z",
                "prior_status": prior}
    # The stored status, not one inferred from the trigger (every trigger here is "x").
    rows = [row(1, 0, "draft"), row(2, 60, "draft"), row(3, 120, "final"), row(4, 180, "addendum")]
    for written in (False, True):
        ds = Dataset([], rows, meta, "x", written=written)
        assert [status_at(r, t + timedelta(hours=4)) for r in ds.rows_at("1", t + timedelta(hours=4))] == [
            "draft", "draft", "final", "addendum"]
        assert [status_at(r, t + timedelta(hours=5)) for r in ds.rows_at("1", t + timedelta(hours=5))] == ["retracted"] * 4
        assert ds.canceled == {"1": t + timedelta(hours=5)}
    # Retracted by an older handoff job (no prior_status, with or without a time), or a sync from before the
    # columns: retracted at every time, as before. Not retracted: as written.
    for old in ({"prior_status": None}, {"prior_status": None, "retracted_ts": None}, {"retracted_ts": None}):
        legacy = Dataset([], [{**r, **old} for r in rows], meta, "x")
        assert [status_at(r, t) for r in legacy.rows_at("1", t)] == ["retracted"] and legacy.canceled == {"1": None}
    sync = Dataset([], [{k: v for k, v in r.items() if k not in ("retracted_ts", "prior_status")} for r in rows], meta, "x")
    assert [status_at(r, t) for r in sync.rows_at("1", t)] == ["retracted"]
    plain = Dataset([], [{**rows[0], "status": "draft", "retracted_ts": None, "prior_status": None}], meta, "x")
    assert status_at(plain.rows_at("1", t)[0], t) == "draft" and plain.canceled == {}
    _clean()


def cancel(drafts, picked):
    """Mark admissions canceled as the handoff job does: every version retracted, prior_status the status it had,
    retracted_ts 20 minutes after the second SBAR version's as_of (the job wrote that version 3 minutes after it), for
    those 8 to 40 hours ago."""
    now = datetime.now(timezone.utc)
    second = {d["csn"]: d["as_of"] for d in drafts if d["template"] == "sbar_nurse" and d["version"] == 2
              and now - timedelta(hours=40) < d["as_of"] < now - timedelta(hours=8)}
    for d in drafts:
        if d["csn"] in second:
            d["prior_status"], d["status"] = d["status"], "retracted"
            d["retracted_ts"] = second[d["csn"]] + timedelta(minutes=20)
    picked.update(second)


def test_a_canceled_admission_shows_canceled_from_the_cancel_on(live):
    picked = {}
    c, main, db = live(edit=lambda d: cancel(d, picked))
    for csn, as_of in sorted(picked.items()):
        canceled = as_of + timedelta(minutes=20)
        then = (canceled - timedelta(minutes=5)).isoformat()
        r = c.get(f"/api/handoffs/{csn}", params={"at": then}, headers=who(RN))
        if r.status_code != 200 or r.json()["summary"]["phase"] != "boarding":
            continue                          # not boarding then: try the next one
        before = r.json()
        assert before["templates"]["sbar_nurse"]["row"]["status"] == "draft"
        assert {v["status"] for t in before["templates"].values() for v in t["versions"]} == {"draft"}
        board = c.get("/api/board", params={"at": then}, headers=who(RN)).json()
        assert next(x for x in board["rows"] if x["csn"] == csn)["phase"] == "boarding"
        for t in (canceled, canceled + timedelta(minutes=5)):
            after = c.get(f"/api/handoffs/{csn}", params={"at": t.isoformat()}, headers=who(RN)).json()
            assert after["summary"]["phase"] == "canceled"
            assert {v["status"] for x in after["templates"].values() for v in x["versions"]} == {"retracted"}
            board = c.get("/api/board", params={"at": t.isoformat()}, headers=who(RN)).json()
            assert next(x for x in board["rows"] if x["csn"] == csn)["phase"] == "canceled"
        break
    else:
        pytest.fail("no canceled patient boarding before the cancel")


def test_a_sync_from_before_retracted_ts_still_loads(live):
    """Lakebase synced before the handoff job added the columns: the app loads, and a retracted version is retracted
    at every time, as before."""
    picked = {}
    c, main, db = live(edit=lambda d: cancel(d, picked), old_sync=True)
    assert c.get("/api/health").json()["status"] == "ok"
    for csn, as_of in sorted(picked.items()):
        r = c.get(f"/api/handoffs/{csn}", params={"at": (as_of + timedelta(minutes=15)).isoformat()}, headers=who(RN))
        if r.status_code == 200:
            assert r.json()["summary"]["phase"] == "canceled"
            break
    else:
        pytest.fail("no canceled patient on the board")


def test_past_view_turned_off(live):
    c, main, db = live(hours="0")
    assert c.get("/api/config").json()["past_view_hours"] == 0
    r = c.get("/api/board", params={"at": ago(hours=1)}, headers=who(RN))
    assert r.status_code == 400 and r.json()["detail"] == "Looking at an earlier time is turned off in this app."
    assert c.get("/api/board", params={"at": ago(seconds=20)}, headers=who(RN)).status_code == 200
    assert db.views == []


def test_replay_clock_is_unchanged(fixtures, monkeypatch):
    """Mock mode (the dev app and the static demo): any time of the week, actions at that time, nothing logged."""
    c, main = parity.load_app(fixtures)
    try:
        assert "past_view_hours" not in c.get("/api/config").json()
        start = main.VIEWS.ds.start
        at = (start + timedelta(days=2)).isoformat()
        b = c.get("/api/board", params={"at": at}).json()
        assert datetime.fromisoformat(b["at"]) == start + timedelta(days=2)
        row = next(r for r in b["rows"] if r["phase"] == "boarding")
        el = narrative(c.get(f"/api/handoffs/{row['csn']}", params={"at": at}).json())
        r = c.post(f"/api/handoffs/{row['csn']}/sbar_nurse/edit", params={"at": at}, json={"element": el, "text": "Replay edit."})
        assert r.status_code == 200
        assert main.STORE.actions(row["csn"])[-1]["at"] == start + timedelta(days=2)
        assert main.STORE.views == []
        # Before the window or after it: clamped, as before.
        assert datetime.fromisoformat(c.get("/api/board", params={"at": "2001-01-01T00:00:00Z"}).json()["at"]) == start
    finally:
        main.STORE.reset()
        _clean()
        os.environ.pop("EDIP_FIXTURES", None)
        os.environ.pop("EDIP_STATIC", None)
