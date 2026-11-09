"""Tasks, in mock mode, on fixtures built from a stand in world with the mock model.

The action list of a signed handoff becomes tasks (owner and due mapping, a sign again or an addendum adds only new
items), tasks added by hand, every change and Undo with their refusals, who has the patient (outside the role, the
viewer), the replay clock, the Tasks page and the board counts. The rules themselves (owner words, due times, replay,
Undo, groups) are tested without the app too.
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

MODS = ("config", "data", "assignment", "tasks", "logic", "personas", "workflow", "main", "lakebase")


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
    import tasks
    return tasks


# ---------------------------------------------------------------------------------------- the rules alone
def test_an_owner_names_the_nurse_or_the_physician_and_anything_unclear_is_the_nurse():
    t = _rules()
    for owner in ("RN", "rn", "Nurse", "nursing", "Nursing staff", "Floor nursing staff", "ICU nursing", "Nursing/MD"):
        assert t.role_of(owner) == "nurse", owner
    for owner in ("MD", "physician", "Team", "ICU team", "Hospital Medicine team", "admitting team", "MD/RN", "Pharmacy/Team"):
        assert t.role_of(owner) == "physician", owner
    # Unclear (no RN, nurse, MD, physician or team word): the nurse. "Hospital Medicine" and "Cardiology" included.
    for owner in (None, "", "Cardiology", "Hospital Medicine", "Respiratory Therapy", "Pharmacy", "RNA lab", "mdx"):
        assert t.role_of(owner) == "nurse", owner


def test_a_due_time_of_day_is_the_next_one_at_or_after_signing_now_is_the_signing_and_anything_else_none():
    t = _rules()
    from data import TZ
    local = lambda *x: datetime(*x, tzinfo=TZ)  # noqa: E731
    signed = local(2026, 10, 28, 15, 20)
    assert t.due_of("16:00", signed) == local(2026, 10, 28, 16, 0)
    assert t.due_of(" 9:05 ", signed) == local(2026, 10, 29, 9, 5)                 # earlier in the day: tomorrow
    assert t.due_of("15:20", signed) == signed                                     # at signing counts
    assert t.due_of("15:19", signed) == local(2026, 10, 29, 15, 19)
    assert t.due_of("00:00", signed) == local(2026, 10, 29, 0, 0)
    for now in ("Now", "now", " NOW "):
        assert t.due_of(now, signed) == signed
    for none in (None, "", "  ", "ongoing", "q4h", "immediately", "by 16:00", "24:00", "16:60", "1600", "16:00 today"):
        assert t.due_of(none, signed) is None, none
    # Wall clock across the clocks falling back (America/New_York, November 1, 2026): 07:00 is 07:00 EST.
    assert t.due_of("07:00", local(2026, 10, 31, 22, 0)).astimezone(timezone.utc) == \
        datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    assert t.key("  Repeat   LACTATE ") == t.key("repeat lactate") == "repeat lactate"


T0 = datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc)


def _ev(n, kind, tid=1, at=T0, **f):
    return {"event_id": n, "task_id": tid, "csn": "1", "kind": kind, "at": at, "by_name": "RN", "by_role": "ed_nurse"} | f


def test_a_task_replays_its_events_up_to_the_time_and_undo_reverts_the_last_change():
    t = _rules()
    hour = timedelta(hours=1)
    events = [_ev(1, "create", text="Repeat lactate", role="nurse", due=T0 + hour, template="sbar_nurse"),
              _ev(2, "reassign", at=T0 + hour, role="physician", assignee_id="md", assignee_name="MD", from_role="nurse"),
              _ev(4, "done", at=T0 + 2 * hour),
              _ev(3, "due", at=T0 + hour, due=T0 + 3 * hour, from_due=T0 + hour),
              _ev(5, "done", tid=9, at=T0)]                         # a task never created: ignored
    assert t.replay(events, T0 - hour) == {}
    first = t.replay(events, T0)[1]
    assert first["status"] == "open" and first["source"] == "handoff" and t.undo_of(first) is None
    mid = t.replay(events, T0 + hour)[1]                           # same time: the order saved (event_id) decides
    assert (mid["role"], mid["assignee"], mid["due"]) == ("physician", {"id": "md", "name": "MD"}, T0 + 3 * hour)
    assert t.undo_of(mid) == "due"
    assert t.overdue(mid, T0 + 3 * hour + timedelta(minutes=1)) and not t.overdue(mid, T0 + 3 * hour)
    end = t.replay(events)[1]
    assert end["status"] == "done" and end["closed"]["at"] == T0 + 2 * hour and not t.overdue(end, T0 + 9 * hour)
    assert t.undo_of(end) == "done" and len(end["history"]) == 4
    assert t.undo_of(t.replay(events + [_ev(6, "reopen", at=T0 + 2 * hour, undo=True)])[1]) is None
    # The Tasks page groups.
    task = lambda due: {"due": due}  # noqa: E731
    assert [t.group(task(d), T0) for d in (None, T0 - hour, T0, T0 + 2 * hour, T0 + 2 * hour + timedelta(seconds=1))] == \
        ["none", "overdue", "soon", "soon", "later"]


# ---------------------------------------------------------------------------------------- in the app
def _patient(c):
    """A boarding patient the demo seed gave Jordan (ed-rn) and Kai (ed-md-2), its unit's nurse persona and another."""
    b = c.get("/api/board", headers=h("ed-rn")).json()
    p = next(r for r in b["rows"] if r["phase"] == "boarding" and (r["owners"]["ed_nurse"] or {}).get("you")
             and r["owners"]["ed_physician"])
    cfg = c.get("/api/config").json()
    units = [x for x in cfg["personas"] if x["role"] == "unit_nurse"]
    recv = next(u for u in units if u["unit"] == p["destination"]["pending_unit"] and u["facility"] == p["facility"])
    return p, recv, next(u for u in units if u["id"] != recv["id"])


def _sign(c, csn, template="sbar_nurse", persona="ed-rn", items=None, at=None):
    """Resolve what blocks sign off, write the action list (items), sign; the sign off response."""
    url, q = f"/api/handoffs/{csn}", {"at": at} if at else None
    d = c.get(url, headers=h(persona), params=q).json()["templates"][template]["draft"]
    for rule in d["rules"]:
        if rule["rule"] in d["completeness"]["blocking"]:
            opts = rule.get("attest_options") or []
            body, kind = ({"rule": rule["rule"], "option": opts[0]}, "attest") if opts and rule["status"] != "missing" else \
                ({"element": rule["element"], "text": "Written by the clinician"}, "edit")
            assert c.post(f"{url}/{template}/{kind}", json=body, headers=h(persona), params=q).status_code == 200
    if items is not None:
        r = c.post(f"{url}/{template}/edit", json={"element": "E24", "items": items}, headers=h(persona), params=q)
        assert r.status_code == 200, r.json()
    r = c.post(f"{url}/{template}/sign", json={"callback": "x4471"}, headers=h(persona), params=q)
    assert r.status_code == 200, r.json()
    return r.json()


def _tasks(c, csn, persona="ed-rn", at=None):
    r = c.get(f"/api/handoffs/{csn}/tasks", headers=h(persona), params={"at": at} if at else None)
    assert r.status_code == 200, r.json()
    return r.json()


def _change(c, tid, persona, body, at=None):
    return c.post(f"/api/tasks/{tid}", json=body, headers=h(persona), params={"at": at} if at else None)


ITEMS = [{"text": "Repeat lactate", "owner": "RN", "due": "Now"},
         {"text": "Call the family", "owner": "Hospital Medicine team", "due": ""},
         {"text": "Check glucose", "owner": "Cardiology", "due": "ongoing"},
         {"text": "  repeat  LACTATE", "owner": "MD", "due": "08:00"}]


def test_signing_turns_the_action_list_into_tasks_once_and_an_addendum_adds_only_new_items(client):
    c, main = client
    c.post("/api/demo/reset")
    p, _, _ = _patient(c)
    csn = p["csn"]
    signed = _sign(c, csn, items=ITEMS)
    at = datetime.fromisoformat(c.get("/api/config").json()["default_at"])
    got = _tasks(c, csn)["tasks"]
    # One task per item, the duplicate in the same list (case and spacing aside) once.
    assert [(t["text"], t["role"], t["due_text"], t["source"], t["template"]) for t in got] == [
        ("Repeat lactate", "nurse", "Now", "handoff", "sbar_nurse"),
        ("Call the family", "physician", None, "handoff", "sbar_nurse"),
        ("Check glucose", "nurse", "ongoing", "handoff", "sbar_nurse")]
    assert datetime.fromisoformat(got[0]["due"]) == at and got[1]["due"] is None and got[2]["due"] is None
    assert got[0]["created"] == {"by": "Jordan Reyes, RN", "at": signed["templates"]["sbar_nurse"]["workflow"]["signed"]["at"]}
    assert got[1]["owner_text"] == "Hospital Medicine team" and got[0]["version"] == p["templates"]["sbar_nurse"]["version"]
    # Signing again adds nothing; a new item after a change (sign again) is added, the existing tasks stay.
    _sign(c, csn)
    assert len(_tasks(c, csn)["tasks"]) == 3
    _sign(c, csn, items=ITEMS + [{"text": "Hold heparin", "owner": "MD", "due": "16:00"}])
    after = _tasks(c, csn)["tasks"]
    assert [t["text"] for t in after] == ["Repeat lactate", "Call the family", "Check glucose", "Hold heparin"]
    assert after[:3] == got
    # Another template's action list makes its own tasks, also with the same text.
    _sign(c, csn, "ipass_physician", "ed-md-2", [{"text": "Repeat lactate", "owner": "team", "due": "now"}])
    last = _tasks(c, csn)["tasks"][-1]
    assert (last["text"], last["template"], last["role"], last["created"]["by"]) == ("Repeat lactate", "ipass_physician",
                                                                                       "physician", "Kai Moreno, MD")
    creates = [e for e in main.STORE.task_events(csn) if e["kind"] == "create"]
    assert len(creates) == 5 and {e["by_id"] for e in creates} == {"ed-rn", "ed-md-2"}


def test_an_addendum_after_departure_adds_its_new_items(client):
    c, main = client
    c.post("/api/demo/reset")
    add = next(r for rows in main.VIEWS.ds.versions.values() for r in rows if r["status"] == "addendum"
               and r["template"] == "sbar_nurse")
    t = (add["at"] + timedelta(minutes=1)).isoformat()
    _sign(c, add["csn"], items=[{"text": "Repeat troponin", "owner": "RN", "due": "Now"}], at=t)
    view = _tasks(c, add["csn"], at=t)
    assert view["phase"] == "arrived" and [x["text"] for x in view["tasks"]] == ["Repeat troponin"]
    assert view["access"]["in_role"] is False                  # the ED nurse after arrival
    assert datetime.fromisoformat(view["tasks"][0]["due"]) == datetime.fromisoformat(t)


def test_a_task_added_by_hand_with_its_refusals(client):
    c, main = client
    c.post("/api/demo/reset")
    p, recv, other = _patient(c)
    csn, tu = p["csn"], f"/api/handoffs/{p['csn']}/tasks"
    view = _tasks(c, csn)
    people = {x["id"]: x["role"] for x in view["people"]}
    assert people["ed-rn"] == "nurse" and people["ed-md"] == "physician" and people[recv["id"]] == "nurse"
    assert people["hosp-md"] == "physician" and other["id"] not in people and "viewer" not in people
    due = (datetime.fromisoformat(c.get("/api/config").json()["default_at"]) + timedelta(minutes=30)).isoformat()
    r = c.post(tu, json={"text": " Order a repeat ECG ", "to": "ed-md", "due": due}, headers=h("ed-rn")).json()
    t = r["tasks"][-1]
    assert (t["text"], t["role"], t["assignee"], t["source"], t["template"]) == (
        "Order a repeat ECG", "physician", {"id": "ed-md", "name": "Alex Chen, MD"}, "added", None)
    assert datetime.fromisoformat(t["due"]) == datetime.fromisoformat(due) and r["counts"] == {"open": 1, "overdue": 0}
    for body, status, detail in (
            ({"role": "nurse"}, 422, "The request is missing the text."),
            ({"text": "x", "role": "nurse", "due": 1}, 422, "The request has a value the app cannot use for the due time."),
            ({"text": " ", "role": "nurse"}, 409, "Write what needs to be done."),
            ({"text": "x" * 2001, "role": "nurse"}, 409, "Keep each text under 2,000 characters."),
            ({"text": "x"}, 409, "Choose nurse or physician."),
            ({"text": "x", "role": "pharmacist"}, 409, "Choose nurse or physician."),
            ({"text": "x", "to": "nobody"}, 409, "Choose a colleague from the list."),
            ({"text": "x", "to": other["id"]}, 409, "Choose a colleague from the list."),
            ({"text": "x", "to": "ed-md", "role": "nurse"}, 409, "Choose a colleague in that role, or only the role."),
            ({"text": "x", "role": "nurse", "due": "after lunch"}, 409, "The due time must be a date and time.")):
        r = c.post(tu, json=body, headers=h("ed-rn"))
        assert (r.status_code, r.json()["detail"]) == (status, detail), body
    assert c.post("/api/handoffs/0000000/tasks", json={"text": "x", "role": "nurse"}, headers=h("ed-rn")).status_code == 409
    assert c.get("/api/handoffs/0000000/tasks", headers=h("ed-rn")).status_code == 404
    assert c.get(tu, headers=h(other["id"])).status_code == 404
    assert len(main.STORE.task_events(csn)) == 1


def test_every_change_and_undo_with_their_refusals_and_who_and_when(client):
    c, main = client
    c.post("/api/demo/reset")
    p, recv, _ = _patient(c)
    csn = p["csn"]
    _sign(c, csn, items=ITEMS[:3])
    ids = {t["text"]: t["id"] for t in _tasks(c, csn)["tasks"]}
    lac, fam, glu = ids["Repeat lactate"], ids["Call the family"], ids["Check glucose"]

    def ok(tid, persona, body):
        r = _change(c, tid, persona, body)
        assert r.status_code == 200, r.json()
        return next(t for t in r.json()["tasks"] if t["id"] == tid)

    def no(tid, persona, body, status, detail):
        r = _change(c, tid, persona, body)
        assert (r.status_code, r.json()["detail"]) == (status, detail), body

    done = ok(lac, "ed-rn", {"kind": "done"})
    assert done["status"] == "done" and done["closed"]["by"] == "Jordan Reyes, RN" and done["undo"] == "done"
    closed = "This task is closed. Press Undo to open it again first."
    for body in ({"kind": "done"}, {"kind": "not_needed", "reason": "x"}, {"kind": "reassign", "role": "physician"},
                 {"kind": "due", "due": None}):
        no(lac, "ed-rn", body, 409, closed)
    back = ok(lac, "ed-rn-2", {"kind": "undo"})
    assert back["status"] == "open" and back["closed"] is None and back["undo"] is None
    assert back["history"][-1] | {"at": None} == {"kind": "reopen", "by": "Robin Asante, RN", "by_role": "ed_nurse", "at": None,
                                                  "undo": True}
    no(lac, "ed-rn", {"kind": "undo"}, 409, "There is nothing to undo on this task.")
    no(fam, "ed-rn", {"kind": "undo"}, 409, "There is nothing to undo on this task.")
    no(fam, "ed-md-2", {"kind": "not_needed"}, 409, "Write a short reason the task is not needed.")
    no(fam, "ed-md-2", {"kind": "not_needed", "reason": "x" * 201}, 409, "Keep the reason under 200 characters.")
    nn = ok(fam, "ed-md-2", {"kind": "not_needed", "reason": "  Already spoke with them  "})
    assert (nn["status"], nn["reason"], nn["history"][-1]["reason"]) == ("not_needed", "Already spoke with them",
                                                                         "Already spoke with them")
    assert ok(fam, "ed-md-2", {"kind": "undo"})["reason"] is None
    # Reassign to a role, then a colleague; Undo puts the colleague back to the role.
    moved = ok(glu, "ed-rn", {"kind": "reassign", "role": "physician"})
    assert moved["role"] == "physician" and moved["assignee"] is None
    assert moved["history"][-1] | {"at": None} == {"kind": "reassign", "by": "Jordan Reyes, RN", "by_role": "ed_nurse",
                                                   "at": None, "role": "physician", "to": None, "from_role": "nurse", "from": None}
    no(glu, "ed-rn", {"kind": "reassign", "role": "physician"}, 409, "The task is with the physician already.")
    no(glu, "ed-rn", {"kind": "reassign"}, 409, "Choose nurse or physician.")
    given = ok(glu, "ed-rn", {"kind": "reassign", "to": recv["id"]})
    assert given["role"] == "nurse" and given["assignee"]["id"] == recv["id"]
    no(glu, "ed-rn", {"kind": "reassign", "to": recv["id"]}, 409, f"The task is with {recv['name']} already.")
    undone = ok(glu, "ed-rn", {"kind": "undo"})
    assert (undone["role"], undone["assignee"], undone["undo"]) == ("physician", None, None)
    # A due time, the same again, none, Undo.
    at = datetime.fromisoformat(c.get("/api/config").json()["default_at"])
    later = (at + timedelta(hours=2)).isoformat()
    assert datetime.fromisoformat(ok(glu, "ed-rn", {"kind": "due", "due": later})["due"]) == datetime.fromisoformat(later)
    no(glu, "ed-rn", {"kind": "due", "due": later}, 409, "The task has that due time already.")
    no(glu, "ed-rn", {"kind": "due", "due": "later"}, 409, "The due time must be a date and time.")
    assert ok(glu, "ed-rn", {"kind": "due"})["due"] is None
    assert datetime.fromisoformat(ok(glu, "ed-rn", {"kind": "undo"})["due"]) == datetime.fromisoformat(later)
    for body, status, detail in (({}, 422, "The request is missing the task change."),
                                 ({"kind": "reopen"}, 409, "Choose Done, Not needed, Reassign, Change due time or Undo."),
                                 ({"kind": "delete"}, 409, "Choose Done, Not needed, Reassign, Change due time or Undo.")):
        no(lac, "ed-rn", body, status, detail)
    for tid in (999999, "abc", "-1"):
        r = _change(c, tid, "ed-rn", {"kind": "done"})
        assert (r.status_code, r.json()["detail"]) == (409, "There is no such task.")
    assert c.get(f"/api/tasks/{lac}", headers=h("ed-rn")).status_code == 405
    # Every change is a row with who and when; the handoff activity lists them with the task's text.
    kinds = [e["kind"] for e in main.STORE.task_events(csn)]
    assert kinds.count("create") == 3 and kinds.count("reopen") == 2 and kinds.count("reassign") == 3
    activity = [a for a in c.get(f"/api/handoffs/{csn}", headers=h("ed-rn")).json()["activity"] if a["kind"] == "task"]
    assert len(activity) == len(kinds) and {a["task"] for a in activity} == set(kinds)
    assert next(a for a in activity if a["task"] == "done")["detail"] == "Repeat lactate"


def test_who_has_the_patient_acts_anyone_else_is_outside_the_role_and_the_viewer_reads(client):
    c, main = client
    c.post("/api/demo/reset")
    p, recv, _ = _patient(c)
    csn = p["csn"]
    _sign(c, csn, items=ITEMS[:3])
    ids = [t["id"] for t in _tasks(c, csn)["tasks"]]
    # While boarding: the ED nurse and physician have the patient.
    for persona in ("ed-rn", "ed-md"):
        assert _tasks(c, csn, persona)["access"] == {"can_act": True, "in_role": True, "note": None}
    for persona in (recv["id"], "hosp-md"):
        a = _tasks(c, csn, persona)["access"]
        assert a["can_act"] and not a["in_role"] and a["note"].startswith("The ED nurse and physician have this patient")
    viewer = _tasks(c, csn, "viewer")
    assert viewer["access"] == {"can_act": False, "in_role": False,
                                "note": "The viewer role can read tasks but not change them."}
    assert len(viewer["tasks"]) == 3
    r = _change(c, ids[0], "viewer", {"kind": "done"})
    assert (r.status_code, r.json()["detail"]) == (409, "The viewer role can read tasks but not change them.")
    assert c.post(f"/api/handoffs/{csn}/tasks", json={"text": "x", "role": "nurse"}, headers=h("viewer")).status_code == 409
    # Acting outside the role is allowed and recorded.
    assert _change(c, ids[0], recv["id"], {"kind": "done"}).status_code == 200
    assert _change(c, ids[1], "ed-md", {"kind": "done"}).status_code == 200
    rows = [e for e in main.STORE.task_events(csn) if e["kind"] == "done"]
    assert [(e["by_role"], e.get("outside_role")) for e in rows] == [("unit_nurse", True), ("ed_physician", None)]
    step = _tasks(c, csn)["tasks"][0]["history"][-1]
    assert step["outside_role"] is True and step["by_role"] == "unit_nurse"
    act = [a for a in c.get(f"/api/handoffs/{csn}", headers=h("ed-rn")).json()["activity"] if a.get("task") == "done"]
    assert [a.get("outside_role") for a in act] == [True, None]
    # After arrival: the receiving nurse and the admitting physician; the ED is outside its role.
    dep = main.VIEWS.ds.enc[csn]["ed_departure_ts"]
    assert dep is not None
    t = (dep + timedelta(minutes=30)).isoformat()
    assert _tasks(c, csn, recv["id"], t)["access"]["in_role"] and _tasks(c, csn, "hosp-md", t)["access"]["in_role"]
    ed = _tasks(c, csn, "ed-rn", t)["access"]
    assert not ed["in_role"] and ed["note"].startswith("The receiving nurse and the admitting physician")
    assert _change(c, ids[2], "ed-rn", {"kind": "done"}, t).status_code == 200
    assert _change(c, ids[1], "hosp-md", {"kind": "undo"}, t).status_code == 200
    last = main.STORE.task_events(csn)[-2:]
    assert [(e["by_role"], bool(e.get("outside_role"))) for e in last] == [("ed_nurse", True), ("admitting_physician", False)]


def test_tasks_follow_the_replay_clock(client):
    c, main = client
    c.post("/api/demo/reset")
    p, _, _ = _patient(c)
    csn = p["csn"]
    at = datetime.fromisoformat(c.get("/api/config").json()["default_at"])
    _sign(c, csn, items=[{"text": "Repeat lactate", "owner": "RN", "due": "Now"}])
    tid = _tasks(c, csn)["tasks"][0]["id"]
    later = (at + timedelta(hours=1)).isoformat()
    assert _change(c, tid, "ed-rn", {"kind": "done"}, later).status_code == 200
    # Earlier views show what was true then: no task before signing, open before it was done.
    assert _tasks(c, csn, at=(at - timedelta(minutes=1)).isoformat())["tasks"] == []
    assert _tasks(c, csn, at=at.isoformat())["tasks"][0]["status"] == "open"
    assert _tasks(c, csn, at=later)["tasks"][0]["status"] == "done"
    # Nothing can be recorded before a task event already recorded for the patient.
    earlier = "You are viewing an earlier time; move the clock to now to act."
    r = _change(c, tid, "ed-rn", {"kind": "undo"}, at.isoformat())
    assert (r.status_code, r.json()["detail"]) == (409, earlier)
    r = c.post(f"/api/handoffs/{csn}/tasks", json={"text": "x", "role": "nurse"}, headers=h("ed-rn"), params={"at": at.isoformat()})
    assert (r.status_code, r.json()["detail"]) == (409, earlier)
    assert _change(c, tid, "ed-rn", {"kind": "undo"}, later).status_code == 200


def test_overdue_on_the_board_row_and_the_tasks_page_groups(client):
    c, main = client
    c.post("/api/demo/reset")
    p, recv, _ = _patient(c)
    csn, tu = p["csn"], f"/api/handoffs/{p['csn']}/tasks"
    at = datetime.fromisoformat(c.get("/api/config").json()["default_at"])
    iso = lambda d: (at + d).isoformat()  # noqa: E731
    _sign(c, csn, items=[{"text": "Now item", "owner": "RN", "due": "Now"}, {"text": "For the physician", "owner": "MD", "due": ""}])
    for text, due in (("Overdue", iso(-timedelta(minutes=10))), ("Soon", iso(timedelta(minutes=90))),
                      ("Later", iso(timedelta(hours=4))), ("No time", None)):
        assert c.post(tu, json={"text": text, "role": "nurse", "due": due}, headers=h("ed-rn")).status_code == 200
    c.post(tu, json={"text": "For Robin", "to": "ed-rn-2"}, headers=h("ed-rn"))
    c.post(tu, json={"text": "For the unit", "to": recv["id"]}, headers=h("ed-rn"))
    row = next(r for r in c.get("/api/board", headers=h("ed-rn")).json()["rows"] if r["csn"] == csn)
    assert row["tasks"] == {"open": 8, "overdue": 1}
    row = next(r for r in c.get("/api/board", headers=h("ed-rn"), params={"at": iso(timedelta(hours=2))}).json()["rows"]
               if r["csn"] == csn)
    assert row["tasks"] == {"open": 8, "overdue": 3}                # Now item and Soon are past due too
    texts = lambda page: {g["id"]: [t["text"] for t in g["tasks"]] for g in page["groups"]}  # noqa: E731
    mine = c.get("/api/tasks", headers=h("ed-rn")).json()
    assert texts(mine) == {"overdue": ["Overdue"], "soon": ["Now item", "Soon"], "later": ["Later"], "none": ["No time"]}
    assert mine["counts"] == {"open": 5, "overdue": 1} and mine["role"] == "nurse" and mine["done"] == []
    first = mine["groups"][0]["tasks"][0]
    assert first["csn"] == csn and first["patient"] == p["patient"] and first["in_role"] is True and first["overdue"]
    # Robin is a nurse who does not own the patient: only the task given to them. Kai owns it for the physician.
    assert texts(c.get("/api/tasks", headers=h("ed-rn-2")).json())["none"] == ["For Robin"]
    assert texts(c.get("/api/tasks", headers=h("ed-md-2")).json())["none"] == ["For the physician"]
    assert c.get("/api/tasks", headers=h("ed-md")).json()["counts"]["open"] == 0          # no owner of this patient
    assert texts(c.get("/api/tasks", headers=h(recv["id"])).json())["none"] == ["For the unit"]
    assert c.get("/api/tasks", headers=h("viewer")).json()["counts"]["open"] == 0
    assert c.get("/api/tasks", headers=h("viewer"), params={"scope": "all"}).json()["counts"]["open"] == 8
    assert c.get("/api/tasks", headers=h("ed-rn"), params={"scope": "x"}).status_code == 400
    # Done goes to the recently completed list, for 12 hours.
    tid = mine["groups"][0]["tasks"][0]["id"]
    assert _change(c, tid, "ed-rn", {"kind": "done"}).status_code == 200
    page = c.get("/api/tasks", headers=h("ed-rn")).json()
    assert [t["text"] for t in page["done"]] == ["Overdue"] and page["counts"] == {"open": 4, "overdue": 0}
    # After arrival the receiving nurse has the nurse's tasks; the ED nurse has none.
    dep = main.VIEWS.ds.enc[csn]["ed_departure_ts"]
    t = (dep + timedelta(minutes=30)).isoformat()
    unit = c.get("/api/tasks", headers=h(recv["id"]), params={"at": t}).json()
    assert sorted(x["text"] for g in unit["groups"] for x in g["tasks"]) == ["For the unit", "Later", "No time", "Now item", "Soon"]
    assert c.get("/api/tasks", headers=h("ed-rn"), params={"at": t}).json()["counts"]["open"] == 0
    hosp = c.get("/api/tasks", headers=h("hosp-md"), params={"at": t}).json()
    assert [x["text"] for g in hosp["groups"] for x in g["tasks"]] == ["For the physician"]


def test_reset_clears_tasks_and_a_post_to_the_page_is_refused(client):
    c, main = client
    c.post("/api/demo/reset")
    p, _, _ = _patient(c)
    _sign(c, p["csn"], items=ITEMS[:1])
    assert main.STORE.all_task_events()
    c.post("/api/demo/reset")
    assert main.STORE.all_task_events() == [] and _tasks(c, p["csn"])["tasks"] == []
    assert c.post("/api/tasks", headers=h("ed-rn"), json={}).status_code == 405
