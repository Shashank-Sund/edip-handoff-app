"""The app's API in mock mode, on fixtures built from a stand in world with the mock model.

Board, scope per persona, the sign off and read back rules, the replay clock (actions are invisible before
the time they apply to), carry forward of attestations to newer versions, analytics and templates.
"""
import importlib
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "ai"), str(ROOT / "synthetic"), str(ROOT / "pipelines")]
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from eval import run_eval  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    world = tmp_path_factory.mktemp("world")
    run_eval.build_world(world)
    data = tmp_path_factory.mktemp("fixtures")
    subprocess.run([sys.executable, str(ROOT / "app/fixtures/build.py"), "--world", str(world), "--out", str(data)],
                   check=True, capture_output=True)
    os.environ["EDIP_FIXTURES"] = str(data)
    os.environ["EDIP_STATIC"] = str(data / "no-static")
    sys.path.insert(0, str(ROOT / "app/server"))
    for m in ("config", "data", "logic", "personas", "workflow", "main"):
        sys.modules.pop(m, None)
    main = importlib.import_module("main")
    from fastapi.testclient import TestClient
    return TestClient(main.app), main


def h(persona):
    return {"X-Demo-Persona": persona}


def test_board_at_the_busiest_hour(client):
    c, main = client
    cfg = c.get("/api/config").json()
    assert cfg["mode"] == "mock" and len(cfg["personas"]) >= 4 and cfg["default_at"]
    b = c.get("/api/board").json()
    assert b["kpis"]["boarding"] >= 1 and b["rows"]
    r = b["rows"][0]
    assert r["phase"] in ("boarding", "arrived") and {"sbar_nurse", "ipass_physician"} <= set(r["templates"])
    assert all(t["workflow"]["state"] == "draft" for t in r["templates"].values())


def test_receiving_unit_sees_only_its_patients(client):
    c, main = client
    rows = c.get("/api/board", headers=h("ed-rn")).json()["rows"]
    unit = next(p for p in main._current()[0] if p["role"] == "unit_nurse"
                and any(r["destination"]["pending_unit"] == p["unit"] and r["facility"] == p["facility"] for r in rows))
    mine = c.get("/api/board", headers=h(unit["id"])).json()["rows"]
    assert mine and all(r["destination"]["pending_unit"] == unit["unit"] for r in mine)
    other = next(r for r in rows if r["destination"]["pending_unit"] != unit["unit"]) if len(rows) > len(mine) else None
    if other:
        assert c.get(f"/api/handoffs/{other['csn']}", headers=h(unit["id"])).status_code == 404


def test_sign_off_and_read_back_rules(client):
    c, main = client
    c.post("/api/demo/reset")
    row = next(r for r in c.get("/api/board").json()["rows"] if r["phase"] == "boarding")
    csn = row["csn"]
    case = c.get(f"/api/handoffs/{csn}").json()
    d = case["templates"]["sbar_nurse"]["draft"]
    unit = next(p for p in main._current()[0] if p["unit"] == row["destination"]["pending_unit"] and p["facility"] == row["facility"])
    # Receivers cannot edit or sign; ED cannot acknowledge; nothing to acknowledge before signing.
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/sign", json={"callback": "x1"}, headers=h(unit["id"])).status_code == 409
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/ack", json={"readback": "x" * 20}, headers=h("ed-rn")).status_code == 409
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/ack", json={"readback": "x" * 20}, headers=h(unit["id"])).status_code == 409
    # The physician signs I-PASS, not the nurse template.
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/sign", json={"callback": "x1"}, headers=h("ed-md")).status_code == 409
    if d["completeness"]["blocking"]:
        r = c.post(f"/api/handoffs/{csn}/sbar_nurse/sign", json={"callback": "x1"})
        assert r.status_code == 409 and "Resolve before signing" in r.json()["detail"]
    for rule in d["rules"]:
        if rule["rule"] in d["completeness"]["blocking"]:
            opts = rule.get("attest_options") or (["Rechecked at bedside, unchanged"] if rule["status"] == "stale" else [])
            if opts:
                assert c.post(f"/api/handoffs/{csn}/sbar_nurse/attest", json={"rule": rule["rule"], "option": opts[0]}).status_code == 200
            else:
                c.post(f"/api/handoffs/{csn}/sbar_nurse/edit", json={"element": rule["element"], "text": "Written by the nurse"})
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/attest", json={"rule": "N05", "option": "made up"}).status_code == 409
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/sign", json={"callback": " "}).status_code == 409
    signed = c.post(f"/api/handoffs/{csn}/sbar_nurse/sign", json={"callback": "x4471"}).json()
    wf = signed["templates"]["sbar_nurse"]["workflow"]
    assert wf["state"] == "signed" and wf["signed"]["callback"] == "x4471"
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/ack", json={"readback": "ok"}, headers=h(unit["id"])).status_code == 409
    acked = c.post(f"/api/handoffs/{csn}/sbar_nurse/ack", json={"readback": "Watcher, will recheck vitals on arrival"},
                   headers=h(unit["id"])).json()
    assert acked["templates"]["sbar_nurse"]["workflow"]["state"] == "acknowledged"
    # The replay clock: an hour earlier, none of this has happened yet.
    earlier = (main._current()[1] - timedelta(hours=1)).isoformat()
    before = c.get(f"/api/handoffs/{csn}", params={"at": earlier})
    if before.status_code == 200:
        assert before.json()["templates"]["sbar_nurse"]["workflow"]["state"] == "draft"


def test_edits_carry_forward_and_structured_data_is_read_only(client):
    c, main = client
    c.post("/api/demo/reset")
    row = next(r for r in c.get("/api/board").json()["rows"] if r["phase"] == "boarding")
    csn = row["csn"]
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/edit", json={"element": "E09", "text": "none"}).status_code == 409
    r = c.post(f"/api/handoffs/{csn}/sbar_nurse/edit", json={"element": "E25", "text": "If SBP under 90, call the ED"}).json()
    el = {e["id"]: e for s in r["templates"]["sbar_nurse"]["draft"]["sections"] for e in s["elements"]}
    assert el["E25"]["narrative"]["text"] == "If SBP under 90, call the ED" and el["E25"]["narrative"]["edited"]["by"]
    later = main.VIEWS.ds.rows_at(csn, main.VIEWS.ds.end)
    newer = [x for x in later if x["template"] == "sbar_nurse" and x["at"] > main._current()[1] and x["status"] != "addendum"]
    if newer and (main.VIEWS.ds.enc[csn]["ed_departure_ts"] is None or newer[0]["at"] < main.VIEWS.ds.enc[csn]["ed_departure_ts"]):
        nxt = c.get(f"/api/handoffs/{csn}", params={"at": newer[0]["at"].isoformat()}).json()
        el = {e["id"]: e for s in nxt["templates"]["sbar_nurse"]["draft"]["sections"] for e in s["elements"]}
        assert el["E25"]["narrative"]["text"] == "If SBP under 90, call the ED"


def test_analytics_and_templates(client):
    c, main = client
    a = c.get("/api/analytics", params={"at": main.VIEWS.ds.end.isoformat()}).json()
    transfers = sum(1 for e in main.VIEWS.ds.enc.values() if e["ed_departure_ts"] and main.VIEWS.ds.versions.get(e["csn"]))
    assert a["admitted"] == transfers and a["series"] and a["units"]
    assert sum(b["n"] for b in a["completeness_at_transfer"]["histogram"]) == transfers
    t = c.get("/api/templates").json()
    assert [x["id"] for x in t] == ["sbar_nurse", "ipass_physician", "custom_sea58"]
    assert all(set(x["elements"]) >= {e for s in x["sections"] for e in s["elements"]} for x in t)


def test_viewer_role_reads_but_cannot_act(client):
    c, main = client
    row = next(r for r in c.get("/api/board", headers=h("viewer")).json()["rows"] if r["phase"] == "boarding")
    assert c.get(f"/api/handoffs/{row['csn']}", headers=h("viewer")).status_code == 200
    for kind, body in (("sign", {"callback": "x1"}), ("ack", {"readback": "a" * 20}), ("edit", {"element": "E25", "text": "x"})):
        assert c.post(f"/api/handoffs/{row['csn']}/sbar_nurse/{kind}", json=body, headers=h("viewer")).status_code == 409
