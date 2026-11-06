"""Who owns each ED patient, in mock mode, on fixtures built from a stand in world with the mock model.

Take, Assign and Release with their refusals, owners on the board and the handoff, an action outside the assignment,
the stale owner rule, the shift change banner, Take over at a shift change, the replay clock, and the colleague list
from the role map. The rules themselves (owner at a time, stale, next shift change) are tested without the app too.
"""
import importlib
import json
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

MODS = ("config", "data", "assignment", "logic", "personas", "workflow", "main", "lakebase")


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


def own(c, csn, persona, kind, to=None, at=None):
    body = {"kind": kind} | ({"to": to} if to else {})
    return c.post(f"/api/handoffs/{csn}/owner", json=body, headers=h(persona), params={"at": at} if at else None)


def boarding(c, at=None, persona="ed-rn"):
    """Boarding patients no one owns yet: the demo seed gives a few patients owners at start up and after Reset."""
    b = c.get("/api/board", headers=h(persona), params={"at": at} if at else None).json()
    return [r for r in b["rows"] if r["phase"] == "boarding" and not any(r["owners"].values())]


# ---------------------------------------------------------------------------------------- the rules alone
def _rules():
    if str(ROOT / "app/server") not in sys.path:
        sys.path.insert(0, str(ROOT / "app/server"))
    import assignment
    return assignment


T0 = datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc)


def _row(n, kind, who=None, at=T0, role="ed_nurse"):
    return {"assignment_id": n, "csn": "1", "role": role, "kind": kind, "assignee_id": who,
            "assignee_name": who and who.upper(), "at": at}


def test_the_latest_row_up_to_the_time_decides_the_owner():
    a = _rules()
    stale = timedelta(hours=14)
    rows = [_row(1, "claim", "rn1"), _row(2, "assign", "rn2", T0 + timedelta(hours=1)),
            _row(3, "release", None, T0 + timedelta(hours=2)), _row(4, "claim", "md1", T0, role="ed_physician")]
    assert a.owner_at(rows, "ed_nurse", [], T0 - timedelta(minutes=1), stale, "rn1") is None
    first = a.owner_at(rows, "ed_nurse", [], T0, stale, "rn1")
    assert first["id"] == "rn1" and first["you"] and not first["stale"]
    assert a.owner_at(rows, "ed_nurse", [], T0 + timedelta(minutes=90), stale, "rn1")["id"] == "rn2"
    assert a.owner_at(rows, "ed_nurse", [], T0 + timedelta(hours=3), stale, "rn1") is None      # released
    assert a.owner_at(rows, "ed_physician", [], T0 + timedelta(hours=3), stale, None)["name"] == "MD1"
    # Two rows at the same time: the one saved last wins.
    same = [_row(2, "claim", "rn2"), _row(1, "claim", "rn1")]
    assert a.owner_at(same, "ed_nurse", [], T0, stale, None)["id"] == "rn2"


def test_an_owner_goes_stale_unless_they_act_on_the_patient():
    a = _rules()
    stale = timedelta(hours=14)
    rows = [_row(1, "claim", "rn1")]
    late = T0 + timedelta(hours=14, minutes=1)
    assert a.owner_at(rows, "ed_nurse", [], T0 + timedelta(hours=14), stale, None)["stale"] is False
    assert a.owner_at(rows, "ed_nurse", [], late, stale, None)["stale"] is True
    mine = [{"at": T0 + timedelta(hours=3), "by_id": "rn1"}]
    theirs = [{"at": T0 + timedelta(hours=3), "by_id": "rn2"}, {"at": T0 + timedelta(hours=3), "user_email": "RN1@example.org"}]
    o = a.owner_at(rows, "ed_nurse", mine, late, stale, None)
    assert not o["stale"] and o["last_active"].startswith("2026-10-28T15:00")
    assert a.owner_at(rows, "ed_nurse", theirs, late, stale, None)["stale"] is True
    # Live rows carry the email, which is the person: it refreshes an owner known by their email.
    by_email = [_row(1, "claim", "rn1@example.org")]
    assert not a.owner_at(by_email, "ed_nurse", theirs, late, stale, None)["stale"]
    assert not a.counts(None) and not a.counts({"stale": True}) and a.counts({"stale": False})


def test_the_next_shift_change_is_at_the_local_time_of_day_also_across_a_clock_change():
    a = _rules()
    from data import TZ
    local = lambda *x: datetime(*x, tzinfo=TZ)  # noqa: E731
    shifts = [(7, 0), (19, 0)]
    assert a.next_shift(local(2026, 10, 28, 6, 59), shifts) == local(2026, 10, 28, 7, 0)
    assert a.next_shift(local(2026, 10, 28, 7, 0), shifts) == local(2026, 10, 28, 19, 0)
    assert a.next_shift(local(2026, 10, 28, 19, 30), shifts) == local(2026, 10, 29, 7, 0)
    # The night clocks fall back (America/New_York, November 1, 2026): 07:00 is 07:00 EST, 13 hours after 19:00 EDT.
    nxt = a.next_shift(local(2026, 10, 31, 19, 0), shifts)
    assert nxt.astimezone(timezone.utc) == datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    assert a.next_shift(local(2026, 10, 28, 12, 0), [(6, 30), (18, 30)]) == local(2026, 10, 28, 18, 30)


def test_shift_and_stale_settings_fall_back_when_they_cannot_be_read(monkeypatch, caplog):
    if str(ROOT / "app/server") not in sys.path:
        sys.path.insert(0, str(ROOT / "app/server"))
    sys.modules.pop("config", None)
    import config
    caplog.set_level("WARNING", logger="edip")
    assert config._shift_changes("06:30, 18:30") == [(6, 30), (18, 30)]
    assert config._shift_changes("19:00,07:00,07:00") == [(7, 0), (19, 0)]
    for bad in ("7am", "25:00", "07:61", ","):
        assert config._shift_changes(bad) == [(7, 0), (19, 0)]
    monkeypatch.setenv("EDIP_STALE_OWNER_HOURS", "0")
    assert config._number("EDIP_STALE_OWNER_HOURS", 14, 1, 720) == 14
    monkeypatch.setenv("EDIP_STALE_OWNER_HOURS", "8")
    assert config._number("EDIP_STALE_OWNER_HOURS", 14, 1, 720) == 8
    assert any("EDIP_SHIFT_CHANGES" in r.message for r in caplog.records)


# -------------------------------------------------------------------------------------------------- the API
def test_take_assign_and_release_with_their_refusals(client):
    c, main = client
    c.post("/api/demo/reset")
    r = boarding(c)[0]
    csn = r["csn"]
    assert r["owners"] == {"ed_nurse": None, "ed_physician": None}
    took = own(c, csn, "ed-rn", "claim")
    assert took.status_code == 200
    o = took.json()["summary"]["owners"]["ed_nurse"]
    assert o["id"] == "ed-rn" and o["name"] == "Jordan Reyes, RN" and o["you"] and not o["stale"]
    assert own(c, csn, "ed-rn", "claim").json()["detail"] == "This patient is already yours."
    assert own(c, csn, "ed-rn-2", "release").json()["detail"].startswith("Only Jordan Reyes, RN can release")
    assert own(c, csn, "ed-rn", "assign", "ed-md").json()["detail"] == "Choose a colleague from the list."
    assert own(c, csn, "ed-rn", "assign", "ed-rn").json()["detail"] == "To take this patient yourself, press Take."
    given = own(c, csn, "ed-rn", "assign", "ed-rn-2").json()["summary"]["owners"]["ed_nurse"]
    assert given["id"] == "ed-rn-2" and not given["you"]
    assert own(c, csn, "ed-rn", "assign", "ed-rn-2").json()["detail"] == "Robin Asante, RN already owns this patient."
    # The second nurse sees the patient as theirs; the physician owner is separate.
    assert c.get(f"/api/handoffs/{csn}", headers=h("ed-rn-2")).json()["summary"]["owners"]["ed_nurse"]["you"]
    assert own(c, csn, "ed-md", "claim").json()["summary"]["owners"]["ed_physician"]["name"] == "Alex Chen, MD"
    assert own(c, csn, "ed-rn-2", "release").json()["summary"]["owners"]["ed_nurse"] is None
    assert own(c, csn, "ed-rn", "release").json()["detail"].startswith("No one owns this patient")
    for p in ("viewer", "hosp-md"):
        assert own(c, csn, p, "claim").json()["detail"] == "Only ED nurses and physicians take patients."
    assert own(c, csn, "ed-rn", "steal").status_code == 409
    assert c.post(f"/api/handoffs/{csn}/owner", json={}, headers=h("ed-rn")).status_code == 422
    # Every row is kept: claim, assign, claim (physician), release.
    rows = main.STORE.assignments(csn)
    assert [x["kind"] for x in rows] == ["claim", "assign", "claim", "release"]
    assert rows[1]["from_id"] == "ed-rn" and rows[1]["assignee_id"] == "ed-rn-2" and rows[1]["by_name"] == "Jordan Reyes, RN"


def test_an_arrived_patient_has_no_one_to_take(client):
    c, main = client
    c.post("/api/demo/reset")
    rows = c.get("/api/board", headers=h("ed-rn")).json()["rows"]
    arrived = next((r for r in rows if r["phase"] == "arrived"), None)
    if arrived is None:
        pytest.skip("no arrived patient at the default time")
    assert "left the ED" in own(c, arrived["csn"], "ed-rn", "claim").json()["detail"]


def test_board_counts_my_patients_and_the_unassigned_for_ed_roles_only(client):
    c, main = client
    c.post("/api/demo/reset")
    rows = boarding(c)
    own(c, rows[0]["csn"], "ed-rn", "claim")
    own(c, rows[1]["csn"], "ed-rn-2", "claim")
    b = c.get("/api/board", headers=h("ed-rn")).json()
    all_boarding = [r for r in b["rows"] if r["phase"] == "boarding"]
    mine = [r for r in all_boarding if (r["owners"]["ed_nurse"] or {}).get("you")]
    assert rows[0]["csn"] in {r["csn"] for r in mine} and rows[1]["csn"] not in {r["csn"] for r in mine}
    assert b["kpis"]["mine"] == len(mine) and b["kpis"]["unassigned"] == sum(1 for r in all_boarding if not r["owners"]["ed_nurse"])
    assert b["shift"]["mine"] == len(mine)
    md = c.get("/api/board", headers=h("ed-md")).json()
    assert md["kpis"]["mine"] == 0
    for p in ("viewer", "hosp-md"):
        other = c.get("/api/board", headers=h(p)).json()
        assert other["shift"] is None and "mine" not in other["kpis"]
        # Everyone sees the owners on the board.
        assert next(r for r in other["rows"] if r["csn"] == rows[0]["csn"])["owners"]["ed_nurse"]["name"] == "Jordan Reyes, RN"


def test_acting_on_a_colleagues_patient_is_allowed_and_recorded_as_outside_the_assignment(client):
    c, main = client
    c.post("/api/demo/reset")
    csn = boarding(c)[0]["csn"]
    url = f"/api/handoffs/{csn}"
    own(c, csn, "ed-rn", "claim")
    off = c.post(f"{url}/sbar_nurse/edit", json={"element": "E25", "text": "By a colleague"}, headers=h("ed-rn-2"))
    assert off.status_code == 200
    by = lambda case, name: next(a for a in case["activity"] if a["by"] == name)  # noqa: E731
    last = by(off.json(), "Robin Asante, RN")
    assert last["off_assignment"] is True and last["assigned_to"] == "Jordan Reyes, RN"
    on = c.post(f"{url}/sbar_nurse/edit", json={"element": "E25", "text": "By the owner"}, headers=h("ed-rn")).json()
    assert "off_assignment" not in by(on, "Jordan Reyes, RN")
    # No physician owns the patient: the physician's action is not outside an assignment.
    md = c.post(f"{url}/custom_sea58/edit", json={"element": "E25", "text": "By the physician"}, headers=h("ed-md")).json()
    assert "off_assignment" not in by(md, "Alex Chen, MD")
    acts = main.STORE.actions(csn)
    assert [a.get("off_assignment") for a in acts] == [True, None, None]
    assert [a["by_id"] for a in acts] == ["ed-rn-2", "ed-rn", "ed-md"]


def test_ownership_follows_the_replay_clock(client):
    c, main = client
    c.post("/api/demo/reset")
    default = main._current()[1]
    csn = boarding(c)[0]["csn"]
    own(c, csn, "ed-rn", "claim")
    earlier = (default - timedelta(hours=1)).isoformat()
    then = c.get(f"/api/handoffs/{csn}", params={"at": earlier}, headers=h("ed-rn")).json()
    assert then["summary"]["owners"]["ed_nurse"] is None
    # An assignment cannot go before one already recorded for the patient and role.
    assert "earlier time" in own(c, csn, "ed-rn-2", "claim", at=(default - timedelta(minutes=5)).isoformat()).json()["detail"]
    assert own(c, csn, "ed-md", "claim", at=(default - timedelta(minutes=5)).isoformat()).status_code == 200


def test_a_stale_owner_counts_as_unassigned_until_someone_takes_the_patient(client):
    c, main = client
    c.post("/api/demo/reset")
    ds = main.VIEWS.ds
    long = max((e for e in ds.enc.values() if e["admit_decision_ts"] and ds.versions.get(e["csn"])
                and not any(r["status"] == "retracted" for r in ds.versions[e["csn"]])),
               key=lambda e: (e["ed_departure_ts"] or ds.end) - ds.versions[e["csn"]][0]["at"])
    t1 = ds.versions[long["csn"]][0]["at"] + timedelta(minutes=1)
    if (long["ed_departure_ts"] or ds.end) - t1 < timedelta(hours=14, minutes=5):
        pytest.skip("no patient boards for the stale limit in this world")
    csn, at = long["csn"], lambda d: (t1 + d).isoformat()  # noqa: E731
    assert own(c, csn, "ed-rn", "claim", at=at(timedelta())).status_code == 200
    case = lambda d: c.get(f"/api/handoffs/{csn}", params={"at": at(d)}, headers=h("ed-rn")).json()  # noqa: E731
    assert not case(timedelta(hours=14))["summary"]["owners"]["ed_nurse"]["stale"]
    late = case(timedelta(hours=14, minutes=1))["summary"]["owners"]["ed_nurse"]
    assert late["stale"] and late["name"] == "Jordan Reyes, RN"
    b = c.get("/api/board", params={"at": at(timedelta(hours=14, minutes=1))}, headers=h("ed-rn")).json()
    assert b["kpis"]["mine"] == 0
    # Stale counts as Unassigned: acting on it is not outside an assignment, and a colleague takes it.
    assert own(c, csn, "ed-rn-2", "claim", at=at(timedelta(hours=14, minutes=2))).json()["summary"]["owners"]["ed_nurse"]["you"]


def test_the_shift_change_banner_shows_in_the_half_hour_before(client):
    c, main = client
    from assignment import next_shift
    from data import iso
    c.post("/api/demo/reset")
    default = main._current()[1]
    nxt = next_shift(default, [(7, 0), (19, 0)])
    board = lambda m: c.get("/api/board", params={"at": (nxt - timedelta(minutes=m)).isoformat()}, headers=h("ed-rn")).json()  # noqa: E731
    assert board(31)["shift"]["warning"] is False
    s = board(30)["shift"]
    assert s["warning"] is True and s["minutes"] == 30 and s["at"] == iso(nxt)
    assert board(0)["shift"]["warning"] is False                    # the change itself: the next one is 12 hours away
    cfg = c.get("/api/config").json()["shifts"]
    assert cfg == {"changes": ["07:00", "19:00"], "warning_minutes": 30, "stale_owner_hours": 14}


def test_take_over_lists_a_colleagues_patients_and_takes_some_or_all_in_one_go(client):
    c, main = client
    c.post("/api/demo/reset")
    rows = boarding(c)
    mine = [r["csn"] for r in rows[:3]]
    for csn in mine:
        own(c, csn, "ed-rn", "claim")
    other = rows[3]["csn"] if len(rows) > 3 else None
    view = c.get("/api/takeover", params={"colleague": "ed-rn"}, headers=h("ed-rn-2")).json()
    seeded = [r["csn"] for r in view["rows"] if r["csn"] not in mine]           # the demo seed's patients
    assert view["colleagues"] == [{"id": "ed-rn", "name": "Jordan Reyes, RN", "patients": len(mine) + len(seeded)}]
    assert set(mine) <= {r["csn"] for r in view["rows"]}
    first = next(r for r in view["rows"] if r["csn"] in mine)
    assert first["state"] == "draft" and first["owner"]["id"] == "ed-rn" and isinstance(first["to_confirm"], list)
    assert set(first["to_confirm"]) <= {"Code status", "Allergies", "Isolation"} and first["changed"] == []
    # All or none: one patient Jordan does not own refuses the whole request.
    if other:
        bad = c.post("/api/takeover", json={"colleague": "ed-rn", "csns": mine[:1] + [other]}, headers=h("ed-rn-2"))
        assert bad.status_code == 409 and "no longer owns" in bad.json()["detail"]
        assert not any(x["kind"] == "handover" for x in main.STORE.assignments(mine[0]))
    took = c.post("/api/takeover", json={"colleague": "ed-rn", "csns": mine[:2]}, headers=h("ed-rn-2")).json()
    assert took["taken"] == mine[:2] and {r["csn"] for r in took["rows"]} == {mine[2], *seeded}
    row = main.STORE.assignments(mine[0])[-1]
    assert row["kind"] == "handover" and row["from_id"] == "ed-rn" and row["assignee_id"] == "ed-rn-2"
    assert c.get("/api/board", headers=h("ed-rn-2")).json()["kpis"]["mine"] == 2
    assert c.get("/api/board", headers=h("ed-rn")).json()["kpis"]["mine"] == 1 + len(seeded)
    for persona, params in (("ed-rn-2", {"colleague": "ed-rn-2"}), ("ed-md", {"colleague": "ed-rn"})):
        assert c.get("/api/takeover", params=params, headers=h(persona)).status_code == 409
    assert c.get("/api/takeover", headers=h("viewer")).status_code == 409
    assert c.post("/api/takeover", json={"colleague": "ed-rn", "csns": []}, headers=h("ed-rn-2")).status_code == 409
    assert c.post("/api/takeover", json={"colleague": "ed-rn", "csns": "x"}, headers=h("ed-rn-2")).status_code == 422


def test_take_over_shows_signed_and_changed_handoffs_with_what_changed(client):
    c, main = client
    c.post("/api/demo/reset")
    csn = boarding(c)[0]["csn"]
    own(c, csn, "ed-rn", "claim")
    url = f"/api/handoffs/{csn}"
    d = c.get(url, headers=h("ed-rn")).json()["templates"]["sbar_nurse"]["draft"]
    for rule in d["rules"]:
        if rule["rule"] in d["completeness"]["blocking"]:
            opts = rule.get("attest_options") or []
            body, kind = ({"rule": rule["rule"], "option": opts[0]}, "attest") if opts and rule["status"] != "missing" else \
                ({"element": rule["element"], "text": "Written by the nurse"}, "edit")
            assert c.post(f"{url}/sbar_nurse/{kind}", json=body, headers=h("ed-rn")).status_code == 200
    assert c.post(f"{url}/sbar_nurse/sign", json={"callback": "x4471"}, headers=h("ed-rn")).status_code == 200
    pick = lambda: next(r for r in c.get("/api/takeover", params={"colleague": "ed-rn"}, headers=h("ed-rn-2")).json()["rows"] if r["csn"] == csn)  # noqa: E731
    row = pick()
    assert row["state"] == "signed" and row["signed"]["by"] == "Jordan Reyes, RN"
    c.post(f"{url}/sbar_nurse/edit", json={"element": "E25", "text": "Changed after signing"}, headers=h("ed-rn"))
    row = pick()
    assert row["state"] == "changed" and row["changed"]
    # The signature stays with the signer after the handover; the new owner signs again.
    c.post("/api/takeover", json={"colleague": "ed-rn", "csns": [csn]}, headers=h("ed-rn-2"))
    wf = c.get(url, headers=h("ed-rn-2")).json()["templates"]["sbar_nurse"]["workflow"]
    assert wf["state"] == "changed" and wf["signed"]["by"] == "Jordan Reyes, RN"


def test_the_demo_starts_with_owners_who_board_through_the_shift_change_and_reset_restores_them(client):
    c, main = client
    from assignment import next_shift
    c.post("/api/demo/reset")
    default = main._current()[1]
    nxt = next_shift(default, [(7, 0), (19, 0)])
    seeded = {r["csn"]: r for csn in main.VIEWS.ds.enc for r in main.STORE.assignments(csn)}
    rows = [r for csn in main.VIEWS.ds.enc for r in main.STORE.assignments(csn)]
    assert rows and all(r["kind"] == "claim" and r["detail"] == {"seed": True} for r in rows)
    assert {r["assignee_id"] for r in rows if r["role"] == "ed_nurse"} == {"ed-rn"}
    assert {r["assignee_id"] for r in rows if r["role"] == "ed_physician"} <= {"ed-md-2"}
    for r in rows:
        e = main.VIEWS.ds.enc[r["csn"]]
        assert r["at"] <= nxt - timedelta(minutes=30) and r["at"] >= default - timedelta(hours=3)
        assert e["ed_departure_ts"] is None or e["ed_departure_ts"] > nxt          # boarding through the shift change
    # In the half hour before the shift change, Jordan's patients are on the board and none is stale.
    b = c.get("/api/board", params={"at": (nxt - timedelta(minutes=29)).isoformat()}, headers=h("ed-rn")).json()
    assert b["shift"]["warning"] and b["kpis"]["mine"] == len([r for r in rows if r["role"] == "ed_nurse"])
    own(c, next(iter(seeded)), "ed-rn-2", "claim", at=(nxt - timedelta(minutes=20)).isoformat())
    c.post("/api/demo/reset")
    again = [r for csn in main.VIEWS.ds.enc for r in main.STORE.assignments(csn)]
    assert [(r["csn"], r["role"], r["at"]) for r in again] == [(r["csn"], r["role"], r["at"]) for r in rows]


def test_analytics_counts_unassigned_patients_outside_actions_handovers_and_time_to_sign(client):
    c, main = client
    from assignment import HANDOVER_BUCKETS, handover_bucket, next_shift
    from data import TZ
    c.post("/api/demo/reset")
    default = main._current()[1]
    before = c.get("/api/analytics").json()
    from data import iso
    seeded = [r["at"] for csn in main.VIEWS.ds.enc for r in main.STORE.assignments(csn, default)]
    assert before["owners"]["since"] == (iso(min(seeded)) if seeded else None)          # the first owner recorded
    start = c.get("/api/analytics", params={"at": main.VIEWS.ds.start.isoformat()}).json()
    assert start["owners"]["since"] is None                                             # none yet at the week's start
    point = before["series"][-1]
    assert point["unassigned_rn"] <= point["boarding"] and point["unassigned_md"] <= point["boarding"]
    rows = boarding(c)
    csn = rows[0]["csn"]
    own(c, csn, "ed-rn", "claim")
    after = c.get("/api/analytics").json()
    assert after["series"][-1]["unassigned_rn"] == point["unassigned_rn"] - 1
    assert after["series"][-1]["unassigned_md"] == point["unassigned_md"]
    url = f"/api/handoffs/{csn}"
    c.post(f"{url}/sbar_nurse/edit", json={"element": "E25", "text": "By a colleague"}, headers=h("ed-rn-2"))
    d = c.get(url, headers=h("ed-rn")).json()["templates"]["sbar_nurse"]["draft"]
    for rule in d["rules"]:
        if rule["rule"] in d["completeness"]["blocking"]:
            opts = rule.get("attest_options") or []
            body, kind = ({"rule": rule["rule"], "option": opts[0]}, "attest") if opts and rule["status"] != "missing" else \
                ({"element": rule["element"], "text": "Written by the nurse"}, "edit")
            c.post(f"{url}/sbar_nurse/{kind}", json=body, headers=h("ed-rn"))
    assert c.post(f"{url}/sbar_nurse/sign", json={"callback": "x4471"}, headers=h("ed-rn")).status_code == 200
    o = c.get("/api/analytics").json()["owners"]
    nurse = next(x for x in o["outside"] if x["role"] == "ed_nurse")
    assert nurse["n"] == 1 and nurse["of"] >= 3
    mine = next(r for r in o["to_sign"] if r["role"] == "ed_nurse" and r["owner"] == "Jordan Reyes, RN")
    admit = main.VIEWS.ds.enc[csn]["admit_decision_ts"]
    assert mine["n"] == 1 and mine["median_min"] == round((default - admit).total_seconds() / 60)
    assert [x["bucket"] for x in o["handovers"]] == list(HANDOVER_BUCKETS) and sum(x["n"] for x in o["handovers"]) == 0
    # The buckets around the shift change, at the facility's time of day.
    nxt = next_shift(default, [(7, 0), (19, 0)])
    for minutes, bucket in ((-45, 0), (-30, 1), (-1, 1), (0, 2), (30, 2), (31, 3), (5 * 60, 3)):
        assert handover_bucket(nxt + timedelta(minutes=minutes), [(7, 0), (19, 0)]) == HANDOVER_BUCKETS[bucket], minutes
    assert nxt.astimezone(TZ).hour in (7, 19)
    took = c.post("/api/takeover", json={"colleague": "ed-rn", "csns": [csn]}, headers=h("ed-rn-2"))
    assert took.status_code == 200
    o = c.get("/api/analytics").json()["owners"]
    assert sum(x["n"] for x in o["handovers"]) == 1


def test_colleagues_come_from_the_demo_personas_and_live_from_the_role_map(client):
    c, main = client
    names = lambda p: [(x["id"], x["you"]) for x in c.get("/api/colleagues", headers=h(p)).json()]  # noqa: E731
    assert names("ed-rn") == [("ed-rn", True), ("ed-rn-2", False)]
    assert names("ed-md-2") == [("ed-md", False), ("ed-md-2", True)]
    assert names("viewer") == [] and names("hosp-md") == []
    # Live (no demo personas): a person is their lowercased email, named as they last signed in.
    fixtures = os.environ["EDIP_FIXTURES"]
    try:
        c2, m2 = _load(EDIP_DEMO_PERSONAS="false", EDIP_ROLE_MAP=json.dumps(
            {"Rn.One@example.org": "ed-rn", "rn.two@example.org": "ed-rn", "md@example.org": "ed-md"}))
        who = lambda email, name=None: {"x-forwarded-email": email} | ({"x-forwarded-preferred-username": name} if name else {})  # noqa: E731
        me = c2.get("/api/me", headers=who("RN.one@example.org", "Pat One")).json()
        assert me["key"] == "rn.one@example.org"
        col = c2.get("/api/colleagues", headers=who("rn.one@example.org", "Pat One")).json()
        assert col == [{"id": "rn.one@example.org", "name": "Pat One", "you": True},
                       {"id": "rn.two@example.org", "name": "rn.two@example.org", "you": False}]
        csn = next(r["csn"] for r in c2.get("/api/board", headers=who("rn.one@example.org")).json()["rows"] if r["phase"] == "boarding")
        took = c2.post(f"/api/handoffs/{csn}/owner", json={"kind": "assign", "to": "rn.two@example.org"},
                       headers=who("rn.one@example.org", "Pat One")).json()
        assert took["summary"]["owners"]["ed_nurse"]["id"] == "rn.two@example.org"
        assert c2.get("/api/board", headers=who("RN.TWO@example.org")).json()["kpis"]["mine"] == 1
    finally:
        for k in ("EDIP_DEMO_PERSONAS", "EDIP_ROLE_MAP"):
            os.environ.pop(k, None)
        os.environ["EDIP_FIXTURES"] = fixtures
        for m in MODS:
            sys.modules.pop(m, None)
