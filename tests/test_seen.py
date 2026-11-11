"""What changed since you last looked, in mock mode, on fixtures built from a stand in world with the mock model.

Opening a handoff and Mark as seen record looks; the last look at a time replays the looks up to it, so looking at an
earlier time never moves it forward. The case's since is against the look before this one: newer versions (the
items that differ from the version seen), clinician actions, task events, owner changes, arrival and severity, by
anyone but the viewer. The board row counts the same changes, or says the patient was never opened. The rules
themselves (replay, after a look, newest first) are tested without the app too.
"""
import importlib
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "ai"), str(ROOT / "synthetic"), str(ROOT / "pipelines")]
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from eval import run_eval  # noqa: E402

MODS = ("config", "data", "assignment", "tasks", "seen", "logic", "personas", "workflow", "main", "lakebase")


def _load(**env):
    for k, v in env.items():
        os.environ[k] = v
    if str(ROOT / "app/server") not in sys.path:
        sys.path.insert(0, str(ROOT / "app/server"))
    for m in MODS:
        sys.modules.pop(m, None)
    main = importlib.import_module("main")
    from fastapi.testclient import TestClient
    return TestClient(main.app), main


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    world = tmp_path_factory.mktemp("world")
    run_eval.build_world(world)
    data = tmp_path_factory.mktemp("fixtures")
    subprocess.run([sys.executable, str(ROOT / "app/fixtures/build.py"), "--world", str(world), "--out", str(data)],
                   check=True, capture_output=True)
    for k in ("EDIP_MODE", "EDIP_CLOCK", "EDIP_CLOCK_MODE", "EDIP_DEMO_PERSONAS", "EDIP_SYNTHETIC_DATA", "EDIP_ROLE_MAP"):
        os.environ.pop(k, None)
    return _load(EDIP_FIXTURES=str(data), EDIP_STATIC=str(data / "no-static"))


def h(persona):
    return {"X-Demo-Persona": persona}


def _rules():
    if str(ROOT / "app/server") not in sys.path:
        sys.path.insert(0, str(ROOT / "app/server"))
    import seen
    return seen


# ---------------------------------------------------------------------------------------- the rules alone
T0 = datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def _look(n, csn="1", who="ed-rn", at=T0, rec=None):
    return {"seen_id": n, "csn": csn, "by_id": who, "at": at, "recorded_ts": rec or T0 + n * timedelta(seconds=1)}


def test_the_last_look_at_a_time_replays_the_looks_up_to_it():
    s = _rules()
    looks = [_look(1, at=T0), _look(2, at=T0 + 2 * HOUR), _look(3, at=T0 + HOUR),     # 3: an earlier time, saved later
             _look(4, at=T0 + HOUR), _look(5, csn="2", at=T0), _look(6, who="ed-md", at=T0 + 3 * HOUR)]
    assert s.last_looks(looks, "ed-rn", T0 - HOUR) == {}
    assert s.last_looks(looks, "ed-rn", T0)["1"]["seen_id"] == 1
    # At the same time viewed, the look saved last; an earlier time viewed later never moves a later mark forward.
    assert s.last_looks(looks, "ed-rn", T0 + HOUR)["1"]["seen_id"] == 4
    assert s.last_looks(looks, "ed-rn", T0 + 5 * HOUR)["1"]["seen_id"] == 2
    assert set(s.last_looks(looks, "ed-rn", T0 + 5 * HOUR)) == {"1", "2"}
    assert s.last_looks(looks, "ed-md", T0 + 5 * HOUR)["1"]["seen_id"] == 6


def test_a_change_counts_after_the_look_or_at_its_time_when_saved_after_it():
    s = _rules()
    look = _look(1, at=T0, rec=T0 + HOUR)
    row = lambda at, rec: {"at": at, "recorded_ts": rec, "by_id": "ed-md"}  # noqa: E731
    assert s.after(row(T0 + timedelta(minutes=1), T0), look)            # later time, even if saved before
    assert s.after(row(T0, T0 + 2 * HOUR), look)                        # same time, saved after: still news
    assert not s.after(row(T0, T0 + HOUR - timedelta(seconds=1)), look)
    assert not s.after(row(T0 - timedelta(minutes=1), T0 + 3 * HOUR), look)   # before the time viewed
    assert s.by_other(row(T0, T0), "ed-rn") and not s.by_other(row(T0, T0), "ed-md")
    assert not s.by_other({"by_id": None, "user_email": " RN@example.org "}, "rn@example.org")   # live rows by email
    assert [x["n"] for x in s.newest_first([(T0, {"n": 1}), (T0 + HOUR, {"n": 2}), (T0, {"n": 3})])] == [2, 1, 3]
    assert s.summary(None, None) == {"count": 0, "last_look": None, "first_time": True}


# ---------------------------------------------------------------------------------------- in the app
def _default(c):
    return datetime.fromisoformat(c.get("/api/config").json()["default_at"])


def _patient(c, main):
    """A boarding patient at the default time with no owner and no look yet (none of the demo's seeds), who leaves the
    ED later and was on the board an hour before."""
    ds, at = main.VIEWS.ds, _default(c)
    return next(r for r in c.get("/api/board", headers=h("ed-rn")).json()["rows"]
                if r["phase"] == "boarding" and not any(r["owners"].values()) and r["since"]["first_time"]
                and ds.enc[r["csn"]]["ed_departure_ts"] and main.VIEWS.first_shown(r["csn"], at - HOUR))


def _case(c, csn, persona="ed-rn", at=None):
    r = c.get(f"/api/handoffs/{csn}", headers=h(persona), params={"at": at} if at else None)
    assert r.status_code == 200, r.json()
    return r.json()


def _look_at(c, csn, persona="ed-rn", at=None, source="handoff"):
    return c.post(f"/api/handoffs/{csn}/seen", json={"source": source}, headers=h(persona), params={"at": at} if at else None)


def _row(c, csn, persona="ed-rn", at=None):
    rows = c.get("/api/board", headers=h(persona), params={"at": at} if at else None).json()["rows"]
    return next(r for r in rows if r["csn"] == csn)


def _resolve_and_sign(c, csn, template, persona, at=None):
    url, q = f"/api/handoffs/{csn}", {"at": at} if at else None
    d = c.get(url, headers=h(persona), params=q).json()["templates"][template]["draft"]
    for rule in d["rules"]:
        if rule["rule"] in d["completeness"]["blocking"]:
            opts = rule.get("attest_options") or []
            body, kind = ({"rule": rule["rule"], "option": opts[0]}, "attest") if opts and rule["status"] != "missing" else \
                ({"element": rule["element"], "text": "Written by the clinician"}, "edit")
            assert c.post(f"{url}/{template}/{kind}", json=body, headers=h(persona), params=q).status_code == 200
    r = c.post(f"{url}/{template}/sign", json={"callback": "x4471"}, headers=h(persona), params=q)
    assert r.status_code == 200, r.json()


def test_the_first_open_has_no_banner_and_the_board_says_not_opened_yet(client):
    c, main = client
    c.post("/api/demo/reset")
    p = _patient(c, main)
    assert p["since"] == {"count": 0, "last_look": None, "first_time": True}
    case = _case(c, p["csn"])
    assert case["since"] == {"at": case["since"]["at"], "count": 0, "last_look": None, "first_time": True, "items": []}
    assert case["summary"]["since"] == {"count": 0, "last_look": None, "first_time": True}
    r = _look_at(c, p["csn"], at=case["since"]["at"])
    assert r.status_code == 200 and r.json() == {"csn": p["csn"], "at": case["since"]["at"],
                                                  "since": {"count": 0, "last_look": case["since"]["at"], "first_time": False}}
    assert _row(c, p["csn"])["since"] == {"count": 0, "last_look": case["since"]["at"], "first_time": False}
    # Opening again at the same time: nothing new.
    assert _case(c, p["csn"])["since"]["items"] == []
    look = main.STORE.last_looks("ed-rn", _default(c), p["csn"])[p["csn"]]
    assert (look["source"], look["by_name"], look["role"]) == ("handoff", "Jordan Reyes, RN", "ed_nurse")


def test_changes_by_others_show_and_your_own_do_not(client):
    c, main = client
    c.post("/api/demo/reset")
    p = _patient(c, main)
    csn, url = p["csn"], f"/api/handoffs/{p['csn']}"
    for persona in ("ed-rn", "ed-md-2"):
        assert _look_at(c, csn, persona).status_code == 200
    # At the same clock time (the replay clock stands still while people act): Jordan takes the patient and adds a task;
    # Kai takes it for the physician, signs the I-PASS (its action list becomes tasks) and marks a task done.
    assert c.post(f"{url}/owner", json={"kind": "claim"}, headers=h("ed-rn")).status_code == 200
    assert c.post(f"{url}/tasks", json={"text": "Mine", "role": "nurse"}, headers=h("ed-rn")).status_code == 200
    assert c.post(f"{url}/owner", json={"kind": "claim"}, headers=h("ed-md-2")).status_code == 200
    _resolve_and_sign(c, csn, "ipass_physician", "ed-md-2")
    tid = c.get(f"{url}/tasks", headers=h("ed-rn")).json()["tasks"][0]["id"]
    assert c.post(f"/api/tasks/{tid}", json={"kind": "done"}, headers=h("ed-md-2")).status_code == 200
    rn = _case(c, csn)["since"]
    kinds = [(x["kind"], x.get("action") or x.get("owner") or x.get("task")) for x in rn["items"]]
    assert ("owner", "claim") in kinds and ("action", "sign") in kinds and ("task", "done") in kinds
    assert all(x.get("by") == "Kai Moreno, MD" for x in rn["items"])          # not Jordan's own Take or task
    claim = next(x for x in rn["items"] if x["kind"] == "owner")
    assert claim | {"at": None} == {"kind": "owner", "owner": "claim", "role": "ed_physician", "to": "Kai Moreno, MD",
                                    "from": None, "by": "Kai Moreno, MD", "at": None}
    done = next(x for x in rn["items"] if x.get("task") == "done")
    assert done["text"] == "Mine" and done["task_id"] == tid
    sign = next(x for x in rn["items"] if x.get("action") == "sign")
    assert (sign["template"], sign["element"], sign["label"]) == ("ipass_physician", None, None)
    attests = [x for x in rn["items"] if x.get("action") == "attest"]
    assert all(x["element"] and x["label"] and x["option"] for x in attests)
    # Kai sees only Jordan's changes.
    md = _case(c, csn, "ed-md-2")["since"]
    assert {(x["kind"], x.get("owner") or x.get("task")) for x in md["items"]} == {("owner", "claim"), ("task", "create")}
    assert all(x["by"] == "Jordan Reyes, RN" for x in md["items"])
    # The board row counts the same changes; newest first, same time in the order listed.
    assert _row(c, csn)["since"]["count"] == rn["count"] == len(rn["items"])
    assert _row(c, csn, "ed-md-2")["since"]["count"] == 2
    assert [datetime.fromisoformat(x["at"]) for x in rn["items"]] == sorted(
        (datetime.fromisoformat(x["at"]) for x in rn["items"]), reverse=True)


def test_a_newer_version_shows_the_items_that_differ_from_the_version_seen(client):
    c, main = client
    c.post("/api/demo/reset")
    ds = main.VIEWS.ds
    at = _default(c)
    # A patient on the board at the default time with two newer versions of the SBAR since an earlier look.
    for r in c.get("/api/board", headers=h("ed-rn")).json()["rows"]:
        v = [x for x in ds.versions[r["csn"]] if x["template"] == "sbar_nurse" and x["shown"] <= at]
        if len(v) >= 3 and r["since"]["first_time"]:
            break
    else:
        pytest.skip("no patient with three SBAR versions")
    csn = r["csn"]
    then = v[-3]["shown"] + timedelta(minutes=1)          # (the first draft can show a moment before the admit decision)
    assert then < v[-2]["shown"]
    assert _look_at(c, csn, at=then.isoformat()).status_code == 200
    since = _case(c, csn)["since"]
    item = next(x for x in since["items"] if x["kind"] == "version" and x["template"] == "sbar_nurse")
    assert (item["from"], item["to"]) == (v[-3]["version"], v[-1]["version"])
    # The items that differ from the version seen, with clinician edits applied to both (logic.diff of effective drafts).
    import logic
    acts = main.STORE.actions(csn, at)
    rules = main.VIEWS.rules["sbar_nurse"]
    want = logic.diff(logic.effective(v[-3], acts, at, rules), logic.effective(v[-1], acts, at, rules))
    assert item["elements"] == [{"id": x["id"], "label": x["label"]} for x in want] and item["elements"]
    # Pipeline changes have no author: they count for everyone, also a look at the version before only shows that step.
    assert _look_at(c, csn, at=(v[-2]["shown"] + timedelta(minutes=1)).isoformat()).status_code == 200
    again = next(x for x in _case(c, csn)["since"]["items"] if x["kind"] == "version" and x["template"] == "sbar_nurse")
    assert (again["from"], again["to"]) == (v[-2]["version"], v[-1]["version"])
    case = _case(c, csn)
    assert again["elements"] == [{"id": x["id"], "label": x["label"]} for x in case["templates"]["sbar_nurse"]["changes"]["elements"]]


def test_an_earlier_look_does_not_move_the_mark_and_mark_as_seen_records_a_look(client):
    c, main = client
    c.post("/api/demo/reset")
    p = _patient(c, main)
    csn, at = p["csn"], _default(c)
    iso = lambda d: (at + d).isoformat()  # noqa: E731
    assert _look_at(c, csn, at=iso(timedelta(0))).status_code == 200
    # A look at an earlier time is recorded (a look is not an action) but the mark at the default time stays.
    assert _look_at(c, csn, at=iso(-HOUR)).status_code == 200
    assert datetime.fromisoformat(_row(c, csn)["since"]["last_look"]) == at
    # At a time between the two, the earlier look is the last one.
    assert datetime.fromisoformat(_row(c, csn, at=iso(-HOUR / 2))["since"]["last_look"]) == at - HOUR
    # Before both: never opened.
    assert _row(c, csn, at=iso(-HOUR - timedelta(minutes=10)))["since"]["first_time"] is True
    # A look at a time earlier than an action already recorded is allowed (an action would be refused).
    assert c.post(f"/api/handoffs/{csn}/owner", json={"kind": "claim"}, headers=h("ed-md-2")).status_code == 200
    assert _look_at(c, csn, at=iso(-HOUR / 4)).status_code == 200
    assert _row(c, csn)["since"]["count"] == 1                   # Kai's Take, after the look at the default time
    # Mark as seen on the board: a look from the board, the count is gone.
    r = _look_at(c, csn, source="board")
    assert r.status_code == 200 and r.json()["since"]["count"] == 0
    assert _row(c, csn)["since"] == {"count": 0, "last_look": r.json()["at"], "first_time": False}
    looks = [x for x in main.STORE._looks if x["csn"] == csn]
    assert [x["source"] for x in looks] == ["handoff", "handoff", "handoff", "board"]


def test_arrival_and_severity_count_and_every_persona_gets_looks(client):
    c, main = client
    c.post("/api/demo/reset")
    p = _patient(c, main)
    csn = p["csn"]
    dep = main.VIEWS.ds.enc[csn]["ed_departure_ts"]
    assert _look_at(c, csn, at=(dep - timedelta(minutes=5)).isoformat()).status_code == 200
    after = (dep + timedelta(minutes=30)).isoformat()
    items = _case(c, csn, at=after)["since"]["items"]
    assert {"kind": "phase", "phase": "arrived", "at": main.iso(dep)} in items
    # Severity: any patient whose board severity changed between two of its versions.
    ds = main.VIEWS.ds
    sev = main.VIEWS._severity
    for enc in ds.enc.values():
        rows = ds.versions.get(enc["csn"], [])
        pts = sorted({r["shown"] for r in rows})
        pair = next(((a, b) for a, b in zip(pts, pts[1:]) if sev(ds.latest_at(enc["csn"], a)) != sev(ds.latest_at(enc["csn"], b))
                     and sev(ds.latest_at(enc["csn"], a)) and enc["admit_decision_ts"] and enc["admit_decision_ts"] <= a
                     and (not enc["ed_departure_ts"] or enc["ed_departure_ts"] > b)), None)
        if pair:
            break
    else:
        pytest.skip("no severity change in the stand in world")
    a, b = pair
    assert _look_at(c, enc["csn"], "ed-md", at=a.isoformat()).status_code == 200
    items = _case(c, enc["csn"], "ed-md", at=b.isoformat())["since"]["items"]
    s = next(x for x in items if x["kind"] == "severity")
    assert (s["from"], s["to"]) == (sev(ds.latest_at(enc["csn"], a)), sev(ds.latest_at(enc["csn"], b)))
    # The viewer and the receiving nurse get looks too; a patient off your board cannot be looked at.
    assert _look_at(c, csn, "viewer").status_code == 200
    assert _row(c, csn, "viewer")["since"]["first_time"] is False
    units = [x for x in c.get("/api/config").json()["personas"] if x["role"] == "unit_nurse"]
    other = next(u for u in units if (u["facility"], u["unit"]) != (p["facility"], p["destination"]["pending_unit"]))
    r = _look_at(c, csn, other["id"])
    assert (r.status_code, r.json()["detail"]) == (409, "This patient is not on your board at this time.")


def test_refusals(client):
    c, main = client
    c.post("/api/demo/reset")
    p = _patient(c, main)
    url = f"/api/handoffs/{p['csn']}/seen"
    r = _look_at(c, p["csn"], source="tasks")
    assert (r.status_code, r.json()["detail"]) == (409, "A look comes from the handoff page or the board.")
    r = c.post(url, json={}, headers=h("ed-rn"))
    assert (r.status_code, r.json()["detail"]) == (422, "The request is missing the page.")
    r = c.post(url, json={"source": 1}, headers=h("ed-rn"))
    assert (r.status_code, r.json()["detail"]) == (422, "The request has a value the app cannot use for the page.")
    assert _look_at(c, "0000000").status_code == 409
    assert _look_at(c, p["csn"], at="nope").status_code == 400
    assert c.get(url, headers=h("ed-rn")).status_code == 405


def test_the_demo_starts_with_looks_and_reset_restores_them(client):
    c, main = client
    c.post("/api/demo/reset")
    seeded = list(main.STORE._looks)
    owners = {(r["csn"], r["role"]): r for csn in main.VIEWS.ds.enc for r in main.STORE.assignments(csn)}
    assert {x["by_id"] for x in seeded} == {"ed-rn", "ed-md-2"} and all(x["source"] == "handoff" for x in seeded)
    for x in seeded:
        o = owners.get((x["csn"], x["role"]))
        assert o is None or x["at"] == o["at"] - timedelta(minutes=1)
    for persona, other in (("ed-rn", "Kai Moreno, MD"), ("ed-md-2", "Jordan Reyes, RN")):
        rows = c.get("/api/board", headers=h(persona)).json()["rows"]
        news = [r for r in rows if r["since"]["count"]]
        assert news and any(not r["since"]["first_time"] for r in rows) and any(r["since"]["first_time"] for r in rows)
        # The colleague's Take and tasks after the look are news; one's own are not.
        owned = next(r for r in news if r["owners"]["ed_nurse"])
        by = {x["by"] for x in _case(c, owned["csn"], persona)["since"]["items"] if "by" in x}
        assert by == {other}
    c.post("/api/demo/reset")
    assert [(x["csn"], x["by_id"], x["at"]) for x in main.STORE._looks] == [(x["csn"], x["by_id"], x["at"]) for x in seeded]
