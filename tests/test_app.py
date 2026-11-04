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


def test_static_files_never_leave_the_static_folder(tmp_path):
    """A request path with ../ (or %2e%2e) must not read files outside the built client."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html>index")
    (static / "logo.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("secret")
    os.environ["EDIP_STATIC"] = str(static)
    if str(ROOT / "app/server") not in sys.path:
        sys.path.insert(0, str(ROOT / "app/server"))
    for m in ("config", "main"):
        sys.modules.pop(m, None)
    try:
        main = importlib.import_module("main")
        from fastapi.testclient import TestClient
        c = TestClient(main.app)
        assert c.get("/logo.svg").text == "<svg/>"
        for path in ("/%2e%2e/secret.txt", "/..%2fsecret.txt", "/%2e%2e%2fsecret.txt", "/assets/%2e%2e/%2e%2e/secret.txt"):
            r = c.get(path)
            assert "secret" not in r.text, path
        assert c.get("/handoff/123").text.endswith("index")
        assert c.get("/api/nope").status_code == 404
        assert c.get("/api/nope").json() == {"detail": "Not Found"}       # as FastAPI and the static demo answer
    finally:
        for m in ("config", "main"):
            sys.modules.pop(m, None)


# ------------------------------------------------------------------------------- sign off follows the content
def _els(draft):
    return {e["id"]: e for s in draft["sections"] for e in s["elements"]}


def _resolve(c, csn, template="sbar_nurse", persona="ed-rn", at=None):
    """Attest or write every item that blocks sign off, as the clinician would."""
    params = {"at": at} if at else None
    d = c.get(f"/api/handoffs/{csn}", params=params, headers=h(persona)).json()["templates"][template]["draft"]
    for rule in d["rules"]:
        if rule["rule"] in d["completeness"]["blocking"]:
            opts = rule.get("attest_options") or []
            if opts and rule["status"] != "missing":
                body, kind = {"rule": rule["rule"], "option": opts[0]}, "attest"
            else:
                body, kind = {"element": rule["element"], "text": "Written by the clinician"}, "edit"
            assert c.post(f"/api/handoffs/{csn}/{template}/{kind}", params=params, json=body, headers=h(persona)).status_code == 200
    return d


def _receiver(main, row):
    dest = row["destination"]["pending_unit"]
    return next(p["id"] for p in main._current()[0] if p["unit"] == dest and p["facility"] == row["facility"])


def test_any_change_after_signing_needs_a_new_signature(client):
    c, main = client
    c.post("/api/demo/reset")
    row = [r for r in c.get("/api/board").json()["rows"] if r["phase"] == "boarding"][0]
    csn, unit = row["csn"], _receiver(main, row)
    url = f"/api/handoffs/{csn}/sbar_nurse"
    _resolve(c, csn)
    signed = c.post(f"{url}/sign", json={"callback": "x4471"}).json()["templates"]["sbar_nurse"]
    before = _els(signed["draft"])["E25"]["narrative"]["text"]
    edited = c.post(f"{url}/edit", json={"element": "E25", "text": "Call the ED if SBP under 90"}).json()["templates"]["sbar_nurse"]
    wf = edited["workflow"]
    assert wf["state"] == "changed" and wf["edited_since_signed"] and wf["signed"]["callback"] == "x4471"
    # What changed: the text the ED signed against the text now, clinician edits included.
    assert edited["changes"]["since"] == "signed"
    assert [(e["id"], e["before"], e["after"]) for e in edited["changes"]["elements"]] == [("E25", before, "Call the ED if SBP under 90")]
    r = c.post(f"{url}/ack", json={"readback": "Watcher, will recheck vitals on arrival"}, headers=h(unit))
    assert r.status_code == 409 and "sign again" in r.json()["detail"]
    board = c.get("/api/board").json()
    assert board["kpis"]["to_sign"] == sum(1 for x in board["rows"] if x["phase"] == "boarding"
                                           and x["templates"]["sbar_nurse"]["workflow"]["state"] in ("draft", "changed"))
    assert any(x["csn"] == csn for x in board["rows"] if x["templates"]["sbar_nurse"]["workflow"]["state"] == "changed")
    assert c.post(f"{url}/sign", json={"callback": "x4471"}).json()["templates"]["sbar_nurse"]["workflow"]["state"] == "signed"
    assert c.get("/api/board", headers=h(unit)).json()["kpis"]["to_acknowledge"] >= 1
    acked = c.post(f"{url}/ack", json={"readback": "Watcher, will recheck vitals on arrival"}, headers=h(unit)).json()
    assert acked["templates"]["sbar_nurse"]["workflow"]["state"] == "acknowledged"
    # An attestation after signing is a change too.
    d = acked["templates"]["sbar_nurse"]["draft"]
    rule = next((x for x in d["rules"] if x.get("attest_options") and x["status"] != "missing"), None)
    if rule:
        after = c.post(f"{url}/attest", json={"rule": rule["rule"], "option": rule["attest_options"][0]}).json()
        assert after["templates"]["sbar_nurse"]["workflow"]["state"] == "changed"


def test_empty_edits_are_refused_and_restore_brings_back_the_model_text(client):
    c, main = client
    c.post("/api/demo/reset")
    csn = [r for r in c.get("/api/board").json()["rows"] if r["phase"] == "boarding"][0]["csn"]
    url = f"/api/handoffs/{csn}/sbar_nurse"
    model = _els(c.get(f"/api/handoffs/{csn}").json()["templates"]["sbar_nurse"]["draft"])["E25"]["narrative"]["text"]
    for body in ({"element": "E25", "text": ""}, {"element": "E25", "text": "  "}, {"element": "E25"},
                 {"element": "E25", "items": []}, {"element": "E25", "items": [{"text": " "}]}):
        r = c.post(f"{url}/edit", json=body)
        assert r.status_code == 409 and r.json()["detail"] == "Write the text, or use Restore to bring back the model's text.", body
    assert c.post(f"{url}/edit", json={"element": "E07", "text": "stable"}).status_code == 409
    assert c.post(f"{url}/edit", json={"element": "E25", "restore": True}).status_code == 409
    c.post(f"{url}/edit", json={"element": "E25", "text": "  Call the ED if SBP under 90  "})
    e25 = _els(c.get(f"/api/handoffs/{csn}").json()["templates"]["sbar_nurse"]["draft"])["E25"]["narrative"]
    assert e25["text"] == "Call the ED if SBP under 90" and e25["edited"]
    restored = c.post(f"{url}/edit", json={"element": "E25", "restore": True}).json()
    e25 = _els(restored["templates"]["sbar_nurse"]["draft"])["E25"]["narrative"]
    assert e25["text"] == model and not e25.get("edited")
    assert any(a.get("restore") for a in restored["activity"])


def test_a_new_version_needs_signing_again_only_when_clinical_content_changed(client):
    import copy
    from datetime import datetime, timezone
    from logic import workflow

    c, main = client
    row = next(r for rows in main.VIEWS.ds.versions.values() for r in rows if r["template"] == "sbar_nurse")
    t = row["at"]
    sign = {"kind": "sign", "template": "sbar_nurse", "version": row["version"], "at": t, "recorded_ts": datetime.now(timezone.utc),
            "by": "RN", "role": "ed_nurse", "callback": "x4471"}

    def newer(change):
        r = copy.deepcopy(row)
        r["version"] += 1
        change(_els(r["draft"]))
        return r

    def moved(els):                            # the transfer: where the patient is, and code status charted again
        els["E04"]["data"] = {**(els["E04"].get("data") or {}), "unit": "MICU", "room": "3010"}
        if isinstance(els["E08"].get("data"), dict):
            els["E08"]["data"]["recorded_ts"] = "2026-01-01T00:00:00Z"

    def redrafted(els):
        els["E06"]["narrative"] = {**(els["E06"].get("narrative") or {}), "text": "A different working diagnosis"}

    assert workflow(newer(moved), [sign], [row])["state"] == "signed"
    assert workflow(newer(redrafted), [sign], [row])["state"] == "changed"
    # The clinician's own text carries forward: a redraft of an item they edited before signing is no change.
    edit = {**sign, "kind": "edit", "element": "E06", "text": "Sepsis, urinary source", "items": None,
            "model_before": {}, "recorded_ts": datetime(2000, 1, 1, tzinfo=timezone.utc)}
    assert workflow(newer(redrafted), [edit, sign], [row])["state"] == "signed"


def test_transferred_without_sign_off_and_the_ed_can_still_sign(client):
    c, main = client
    c.post("/api/demo/reset")
    ds, default = main.VIEWS.ds, main._current()[1]
    e = min((e for e in ds.enc.values() if e["ed_departure_ts"] and e["admit_decision_ts"] and ds.versions.get(e["csn"])
             and e["ed_departure_ts"] > default), key=lambda e: e["ed_departure_ts"])
    at = (e["ed_departure_ts"] + timedelta(minutes=5)).isoformat()
    case = c.get(f"/api/handoffs/{e['csn']}", params={"at": at}).json()
    assert case["summary"]["phase"] == "arrived"
    assert case["templates"]["sbar_nurse"]["workflow"]["state"] == "transferred_unsigned"
    a = c.get("/api/analytics", params={"at": at}).json()
    assert a["transferred_without_sign_off"] == a["admitted"] - a["signed_before_transfer"]
    unit = _receiver(main, case["summary"])
    r = c.post(f"/api/handoffs/{e['csn']}/sbar_nurse/ack", params={"at": at}, json={"readback": "Arrived, vitals rechecked, all as handed off"},
               headers=h(unit))
    assert r.status_code == 409 and "not been signed" in r.json()["detail"]
    _resolve(c, e["csn"], at=at)
    signed = c.post(f"/api/handoffs/{e['csn']}/sbar_nurse/sign", params={"at": at}, json={"callback": "x4471"})
    assert signed.status_code == 200 and signed.json()["templates"]["sbar_nurse"]["workflow"]["state"] == "signed"
    # Signed and the newest version: final again.
    assert c.post(f"/api/handoffs/{e['csn']}/sbar_nurse/edit", params={"at": at},
                  json={"element": "E25", "text": "Late"}).status_code == 409
    acked = c.post(f"/api/handoffs/{e['csn']}/sbar_nurse/ack", params={"at": at},
                   json={"readback": "Arrived, vitals rechecked, all as handed off"}, headers=h(unit))
    assert acked.json()["templates"]["sbar_nurse"]["workflow"]["state"] == "acknowledged"


def test_a_stale_vitals_recheck_expires_after_the_rule_limit(client):
    c, main = client
    c.post("/api/demo/reset")
    ds = main.VIEWS.ds
    found = None
    for csn, rows in ds.versions.items():
        for i, r in enumerate(rows):
            if r["template"] != "sbar_nurse" or r["status"] == "retracted":
                continue
            if not any(x["rule"] == "N08" and x["status"] == "stale" for x in r["draft"]["rules"]):
                continue
            nxt = next((x["at"] for x in rows[i + 1:] if x["template"] == "sbar_nurse"), None)
            stop = min(x for x in (nxt, ds.enc[csn]["ed_departure_ts"], ds.end) if x)
            if stop - r["at"] > timedelta(minutes=125):
                found = (csn, r)
                break
        if found:
            break
    assert found, "the stand in world has a stale vitals rule"
    csn, r = found
    limit = main.VIEWS.rules["sbar_nurse"]["N08"]["max_age_min"][r["severity"] or "watcher"]
    t0 = r["at"] + timedelta(minutes=1)
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/attest", params={"at": t0.isoformat()},
                  json={"rule": "N08", "option": "Rechecked at bedside, unchanged"}).status_code == 200

    def n08(minutes):
        d = c.get(f"/api/handoffs/{csn}", params={"at": (t0 + timedelta(minutes=minutes)).isoformat()}).json()
        d = d["templates"]["sbar_nurse"]["draft"]
        return next(x for x in d["rules"] if x["rule"] == "N08"), d["completeness"]["blocking"]
    rule, blocking = n08(limit - 1)
    assert rule.get("resolution") and "N08" not in blocking
    rule, blocking = n08(limit + 1)
    assert not rule.get("resolution") and "N08" in blocking and rule["attest_expired"]["limit_min"] == limit


def test_the_high_risk_branch_shows_one_contingency_rule_without_the_stable_option(client):
    import copy
    from logic import effective

    c, main = client
    row = copy.deepcopy(next(r for rows in main.VIEWS.ds.versions.values() for r in rows if r["template"] == "ipass_physician"
                             and any(x["rule"] == "P11" for x in r["draft"]["rules"])))
    for r in row["draft"]["rules"] + _els(row["draft"])["E25"]["rules"]:
        if r["rule"] == "P10":
            r.update(status="attest", attest_options=["Stable, nothing anticipated"])
        if r["rule"] == "P11":
            r.update(status="missing", attest_options=[])
    d = effective(row, [], None, main.VIEWS.rules["ipass_physician"])
    assert [r["rule"] for r in d["rules"] if r["element"] == "E25"] == ["P11"]
    assert [r["rule"] for r in _els(d)["E25"]["rules"]] == ["P11"]
    assert "P10" not in d["completeness"]["blocking"] and "P11" in d["completeness"]["blocking"]


def test_an_action_cannot_go_before_one_already_recorded(client):
    c, main = client
    c.post("/api/demo/reset")
    csn = [r for r in c.get("/api/board").json()["rows"] if r["phase"] == "boarding"][0]["csn"]
    now = main._current()[1]
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/edit", json={"element": "E25", "text": "Plan at now"}).status_code == 200
    r = c.post(f"/api/handoffs/{csn}/sbar_nurse/edit", params={"at": (now - timedelta(minutes=5)).isoformat()},
               json={"element": "E25", "text": "Plan earlier"})
    assert r.status_code == 409 and r.json()["detail"] == "You are viewing an earlier time; move the clock to now to act."
    assert c.post(f"/api/handoffs/{csn}/ipass_physician/edit", params={"at": (now - timedelta(minutes=5)).isoformat()},
                  json={"element": "E25", "text": "Other template"}, headers=h("ed-md")).status_code == 200


def test_board_counts_follow_the_role(client):
    c, main = client
    c.post("/api/demo/reset")
    for at in (None, main.VIEWS.ds.end.isoformat()):
        b = c.get("/api/board", params={"at": at} if at else None, headers=h("viewer")).json()
        assert b["kpis"]["to_sign"] is None and b["kpis"]["to_acknowledge"] is None and b["kpis"]["blocking"] is None
        assert b["kpis"]["arrived"] == sum(1 for r in b["rows"] if r["phase"] == "arrived")
    b = c.get("/api/board").json()
    todo = [r for r in b["rows"] if r["phase"] == "boarding" and r["templates"]["sbar_nurse"]["workflow"]["state"] in ("draft", "changed")]
    assert b["kpis"]["blocking"] == sum(1 for r in todo if r["templates"]["sbar_nurse"]["blocking"])


def test_read_back_callback_and_request_checks(client):
    c, main = client
    c.post("/api/demo/reset")
    row = [r for r in c.get("/api/board").json()["rows"] if r["phase"] == "boarding"][0]
    csn, unit = row["csn"], _receiver(main, row)
    url = f"/api/handoffs/{csn}/sbar_nurse"
    _resolve(c, csn)
    for cb in ("x1", "12", "5" * 41):
        r = c.post(f"{url}/sign", json={"callback": cb})
        assert r.status_code == 409 and "at least 3 digits" in r.json()["detail"], cb
    r = c.post(f"{url}/sign", json={})
    assert r.status_code == 422 and r.json()["detail"] == "The request is missing the callback number."
    r = c.post(f"{url}/sign", json={"callback": 4471})
    assert r.status_code == 422 and r.json()["detail"] == "The request has a value the app cannot use for the callback number."
    r = c.post(f"{url}/edit", json={"element": "E25", "items": [{"text": 5}]})
    assert r.status_code == 422 and "action items" in r.json()["detail"]
    r = c.post(f"{url}/edit", json={"element": "E25", "text": "a" * 2001})
    assert r.status_code == 409 and r.json()["detail"] == "Keep each text under 2,000 characters."
    assert c.post(f"{url}/sign", json={"callback": "(617) 555-0142"}).status_code == 200
    for rb in ("Watcher patient", "aaaa aaaa aaaa aaaa", "ok 12 34 56 78", "x" * 20):
        r = c.post(f"{url}/ack", json={"readback": rb}, headers=h(unit))
        assert r.status_code == 409 and r.json()["detail"].startswith("Write a short read back"), rb
    assert c.post(f"{url}/ack", json={"readback": "Watcher, recheck vitals on arrival"}, headers=h(unit)).status_code == 200


def _fresh_main(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("EDIP_STATIC", "/nonexistent")
    if str(ROOT / "app/server") not in sys.path:
        sys.path.insert(0, str(ROOT / "app/server"))
    for m in ("config", "data", "logic", "personas", "workflow", "main", "lakebase"):
        sys.modules.pop(m, None)
    main = importlib.import_module("main")
    from fastapi.testclient import TestClient
    return TestClient(main.app), main


@pytest.fixture
def fresh(monkeypatch):
    yield lambda **env: _fresh_main(monkeypatch, **env)
    for m in ("config", "data", "logic", "personas", "workflow", "main", "lakebase"):
        sys.modules.pop(m, None)


def test_role_map_matches_email_case_and_falls_back_to_viewer(client, fresh, caplog):
    import json

    caplog.set_level("WARNING", logger="edip")
    c, main = fresh(EDIP_DEMO_PERSONAS="false", EDIP_ROLE_MAP=json.dumps({
        "RN.Reyes@example.org": "ed-rn", "md@example.org": "nobody", "unit@example.org": "unit-zzz-9w"}))
    assert any("nobody" in r.message for r in caplog.records)          # checked at start up
    me = lambda email: c.get("/api/me", headers={"x-forwarded-email": email}).json()  # noqa: E731
    assert me("rn.REYES@example.org")["role"] == "ed_nurse"
    assert me("md@example.org")["role"] == "viewer"
    assert me("someone@example.org")["role"] == "viewer"
    # A mapped unit with no patients in the data keeps its persona.
    unit = me("unit@example.org")
    assert unit["id"] == "unit-zzz-9w" and unit["role"] == "unit_nurse" and unit["unit"] == "9W"


def test_role_map_problems_are_reported():
    if str(ROOT / "app/server") not in sys.path:
        sys.path.insert(0, str(ROOT / "app/server"))
    from personas import parse_role_map

    assert parse_role_map("not json")[0] == {} and parse_role_map("not json")[1]
    assert parse_role_map("[]")[1]
    m, problems = parse_role_map('{"A@example.org": "ed-md", "b@example.org": 3, "c@example.org": "made-up"}')
    assert m == {"a@example.org": "ed-md", "c@example.org": "made-up"} and len(problems) == 2


def test_time_zone_from_the_environment(client, fresh):
    c, main = fresh(EDIP_TIMEZONE="America/Chicago")
    cfg = c.get("/api/config").json()
    assert cfg["timezone"] == "America/Chicago"
    assert cfg["default_at"].endswith(("-05:00", "-06:00"))


def test_live_clock_does_not_keep_a_missed_departure_boarding(client):
    import copy
    from logic import Views
    from workflow import MemoryStore

    c, main = client
    ds = copy.copy(main.VIEWS.ds)
    e = next(e for e in ds.enc.values() if e["ed_departure_ts"] and ds.versions.get(e["csn"]))
    ds.enc = {**ds.enc, e["csn"]: {**e, "ed_departure_ts": None, "left_ts": None}}
    late = e["admit_decision_ts"] + timedelta(hours=30)
    person = main._current()[0][0]
    keep = Views(ds, MemoryStore(), main._templates())
    cap = Views(ds, MemoryStore(), main._templates(), timedelta(hours=24))
    assert any(r["csn"] == e["csn"] for r in keep.board(late, person)["rows"])
    assert not any(r["csn"] == e["csn"] for r in cap.board(late, person)["rows"])
    assert cap.board(e["admit_decision_ts"] + timedelta(hours=2), person)["rows"]


# ------------------------------------------------------------------------------- round 2: board line, real choices
def test_left_without_sign_off_counts_arrived_patients_never_signed_for_the_role(client):
    c, main = client
    c.post("/api/demo/reset")
    ds, default = main.VIEWS.ds, main._current()[1]
    e = min((e for e in ds.enc.values() if e["ed_departure_ts"] and e["admit_decision_ts"] and ds.versions.get(e["csn"])
             and e["ed_departure_ts"] > default), key=lambda e: e["ed_departure_ts"])
    at = (e["ed_departure_ts"] + timedelta(minutes=5)).isoformat()
    for persona, tpl in (("ed-rn", "sbar_nurse"), ("ed-md", "ipass_physician")):
        b = c.get("/api/board", params={"at": at}, headers=h(persona)).json()
        want = sum(1 for r in b["rows"] if r["phase"] == "arrived" and r["templates"][tpl]["workflow"]["state"] == "transferred_unsigned")
        assert b["kpis"]["left_unsigned"] == want >= 1
    case = c.get(f"/api/handoffs/{e['csn']}", params={"at": at}).json()
    for persona in ("viewer", "hosp-md", _receiver(main, case["summary"])):
        assert c.get("/api/board", params={"at": at}, headers=h(persona)).json()["kpis"]["left_unsigned"] is None
    before = c.get("/api/board", params={"at": at}).json()["kpis"]["left_unsigned"]
    _resolve(c, e["csn"], at=at)
    assert c.post(f"/api/handoffs/{e['csn']}/sbar_nurse/sign", params={"at": at}, json={"callback": "x4471"}).status_code == 200
    assert c.get("/api/board", params={"at": at}).json()["kpis"]["left_unsigned"] == before - 1
    # The physician's count follows the physician's template, which nobody signed.
    b = c.get("/api/board", params={"at": at}, headers=h("ed-md")).json()
    assert b["kpis"]["left_unsigned"] == sum(1 for r in b["rows"] if r["phase"] == "arrived"
                                             and r["templates"]["ipass_physician"]["workflow"]["state"] == "transferred_unsigned")


def _attestable(c, main, rule, template="sbar_nurse", persona="ed-rn"):
    """A boarding patient at the default time whose rule needs an attestation."""
    for r in c.get("/api/board", headers=h(persona)).json()["rows"]:
        if r["phase"] != "boarding":
            continue
        d = c.get(f"/api/handoffs/{r['csn']}", headers=h(persona)).json()["templates"][template]["draft"]
        x = next((x for x in d["rules"] if x["rule"] == rule and x["status"] == "attest"), None)
        if x:
            return r["csn"], x
    pytest.skip(f"no boarding patient needs {rule} at the default time")


def test_attestation_choices_come_from_the_current_templates(client):
    import copy
    from logic import effective

    c, main = client
    c.post("/api/demo/reset")
    for rule, element, choices in (("N07", "E10", ["No isolation needed", "Contact", "Droplet", "Airborne", "Contact plus (enteric)",
                                                   "Protective (neutropenic)", "Other"]),
                                   ("N05", "E08", ["Full code confirmed with patient or family", "DNR", "DNR/DNI", "Comfort care"]),
                                   ("N06", "E09", ["No known allergies confirmed", "Add an allergy"])):
        csn, r = _attestable(c, main, rule)
        assert r["attest_options"] == choices
        # A draft written before the choices changed (only the first one) still offers the current choices.
        row = copy.deepcopy(main.VIEWS.ds.latest_at(csn, main._current()[1])["sbar_nurse"])
        for x in row["draft"]["rules"] + _els(row["draft"])[element]["rules"]:
            if x["rule"] == rule:
                x["attest_options"] = choices[:1]
        d = effective(row, [], None, main.VIEWS.rules["sbar_nurse"])
        assert next(x for x in d["rules"] if x["rule"] == rule)["attest_options"] == choices
        assert next(x for x in _els(d)[element]["rules"] if x["rule"] == rule)["attest_options"] == choices
    _, iso_rule = _attestable(c, main, "N07")
    assert iso_rule["attest_typed"] == {"Other": {"label": "Type of isolation", "note": None}}
    _, allergy = _attestable(c, main, "N06")
    assert allergy["attest_typed"] == {"Add an allergy": {"label": "Allergen", "note": "Reaction (optional)"}}
    for t, rule in (("ipass_physician", "P04"), ("custom_sea58", "C07")):
        persona = "ed-md"
        _, r = _attestable(c, main, rule, t, persona)
        assert len(r["attest_options"]) > 1


def test_an_entered_value_shows_on_the_item_resolves_the_rule_and_needs_signing_again(client):
    c, main = client
    c.post("/api/demo/reset")
    csn, _ = _attestable(c, main, "N07")
    url = f"/api/handoffs/{csn}/sbar_nurse"
    r = c.post(f"{url}/attest", json={"rule": "N07", "option": "Other"})
    assert r.status_code == 409 and r.json()["detail"] == "Write the type of isolation."
    r = c.post(f"{url}/attest", json={"rule": "N07", "option": "Other", "entry": "   "})
    assert r.status_code == 409 and r.json()["detail"] == "Write the type of isolation."
    r = c.post(f"{url}/attest", json={"rule": "N07", "option": "Other", "entry": "a" * 2001})
    assert r.status_code == 409 and r.json()["detail"] == "Keep each text under 2,000 characters."
    r = c.post(f"{url}/attest", json={"rule": "N07", "option": "Other", "entry": 5})
    assert r.status_code == 422 and r.json()["detail"] == "The request has a value the app cannot use for the entered value."
    out = c.post(f"{url}/attest", json={"rule": "N07", "option": "Other", "entry": "  Contact and droplet  "}).json()
    d = out["templates"]["sbar_nurse"]["draft"]
    rule = next(x for x in d["rules"] if x["rule"] == "N07")
    assert rule["resolution"]["entered"] and "N07" not in d["completeness"]["blocking"]
    e10 = _els(d)["E10"]["entered"][0]
    assert e10["value"] == "Contact and droplet" and e10["option"] == "Other" and e10["by"] == "Jordan Reyes, RN"
    # A plain choice is entered as itself; the first choice confirms nothing to add and enters nothing.
    out = c.post(f"{url}/attest", json={"rule": "N07", "option": "Droplet", "entry": "ignored"}).json()
    assert _els(out["templates"]["sbar_nurse"]["draft"])["E10"]["entered"][0]["value"] == "Droplet"
    out = c.post(f"{url}/attest", json={"rule": "N07", "option": "No isolation needed"}).json()
    d = out["templates"]["sbar_nurse"]["draft"]
    assert "entered" not in _els(d)["E10"] and not next(x for x in d["rules"] if x["rule"] == "N07")["resolution"].get("entered")
    # History keeps every entered value, in the order entered.
    assert [x.get("entered") for x in out["activity"] if x["kind"] == "attest"] == ["Contact and droplet", "Droplet", None]
    # An allergy: the allergen is required, the reaction is not.
    csn2, _ = _attestable(c, main, "N06")
    url2 = f"/api/handoffs/{csn2}/sbar_nurse"
    r = c.post(f"{url2}/attest", json={"rule": "N06", "option": "Add an allergy", "entry_note": "Hives"})
    assert r.status_code == 409 and r.json()["detail"] == "Write the allergen."
    out = c.post(f"{url2}/attest", json={"rule": "N06", "option": "Add an allergy", "entry": "Penicillin", "entry_note": " Hives "}).json()
    e09 = _els(out["templates"]["sbar_nurse"]["draft"])["E09"]["entered"]
    assert [(x["value"], x["note"]) for x in e09] == [("Penicillin", "Hives")]
    assert [(x.get("entered"), x.get("note")) for x in out["activity"] if x["kind"] == "attest"] == [("Penicillin", "Hives")]
    # The receiver sees the entered value too; after signing, entering one is a change like any attestation.
    _resolve(c, csn2)
    assert c.post(f"{url2}/sign", json={"callback": "x4471"}).json()["templates"]["sbar_nurse"]["workflow"]["state"] == "signed"
    case = c.get(f"/api/handoffs/{csn2}").json()
    unit = _receiver(main, case["summary"])
    seen = c.get(f"/api/handoffs/{csn2}", headers=h(unit)).json()["templates"]["sbar_nurse"]["draft"]
    assert [x["value"] for x in _els(seen)["E09"]["entered"]] == ["Penicillin"]
    out = c.post(f"{url2}/attest", json={"rule": "N06", "option": "Add an allergy", "entry": "Latex"}).json()
    assert out["templates"]["sbar_nurse"]["workflow"]["state"] == "changed"


def test_a_chart_value_replaces_an_entered_one_which_stays_in_history(client):
    c, main = client
    c.post("/api/demo/reset")
    ds = main.VIEWS.ds
    found = None
    for csn, rows in ds.versions.items():
        e = ds.enc[csn]
        sb = [r for r in rows if r["template"] == "sbar_nurse" and r["status"] != "retracted"]
        for a, b in zip(sb, sb[1:]):
            st = lambda r: {x["rule"]: x["status"] for x in r["draft"]["rules"]}  # noqa: E731
            if st(a).get("N05") == "attest" and st(b).get("N05") == "pass" and (not e["ed_departure_ts"] or e["ed_departure_ts"] > b["at"]):
                found = (csn, a, b)
                break
        if found:
            break
    if not found:
        pytest.skip("no code status charted after an earlier draft in this world")
    csn, a, b = found
    t0 = (a["at"] + timedelta(minutes=1)).isoformat()
    out = c.post(f"/api/handoffs/{csn}/sbar_nurse/attest", params={"at": t0}, json={"rule": "N05", "option": "DNR"}).json()
    assert _els(out["templates"]["sbar_nurse"]["draft"])["E08"]["entered"][0]["value"] == "DNR"
    later = c.get(f"/api/handoffs/{csn}", params={"at": b["at"].isoformat()}).json()
    d = later["templates"]["sbar_nurse"]["draft"]
    assert "entered" not in _els(d)["E08"] and _els(d)["E08"]["data"]
    assert not next(x for x in d["rules"] if x["rule"] == "N05").get("resolution")
    assert any(x["kind"] == "attest" and x.get("entered") == "DNR" for x in later["activity"])


def test_attestation_entries_are_kept_in_the_live_store():
    if str(ROOT / "app/server") not in sys.path:
        sys.path.insert(0, str(ROOT / "app/server"))
    import lakebase

    assert {"entry", "entry_note"} <= set(lakebase.DETAIL)


# ------------------------------------------------------------------------------- review 2: patient level entries
def _patient_case(c, need=("N05", "N06", "N07"), also=("P04", "P05")):
    """A boarding patient whose chart lacks code status, allergies and isolation (the SBAR rules need attesting)."""
    for r in c.get("/api/board").json()["rows"]:
        if r["phase"] != "boarding":
            continue
        t = c.get(f"/api/handoffs/{r['csn']}").json()["templates"]
        st = {x["rule"]: x["status"] for tp in t.values() for x in tp["draft"]["rules"]}
        if all(st.get(x) == "attest" for x in need + also):
            return r
    pytest.skip("no boarding patient lacks these chart items")


def test_an_entered_code_status_belongs_to_the_patient_on_every_template(client):
    c, main = client
    c.post("/api/demo/reset")
    row = _patient_case(c, ("N05",), ("P04",))
    csn = row["csn"]
    out = c.post(f"/api/handoffs/{csn}/sbar_nurse/attest", json={"rule": "N05", "option": "DNR"}).json()
    for tid, rule in (("sbar_nurse", "N05"), ("ipass_physician", "P04")):
        d = out["templates"][tid]["draft"]
        assert [x["value"] for x in _els(d)["E08"]["entered"]] == ["DNR"], tid
        assert next(x for x in d["rules"] if x["rule"] == rule)["resolution"]["entered"] and rule not in d["completeness"]["blocking"]
    assert out["summary"]["entered"]["code_status"] == {"value": "DNR", "note": None, "by": "Jordan Reyes, RN", "template": "sbar_nurse"}
    board = c.get("/api/board").json()
    assert next(r for r in board["rows"] if r["csn"] == csn)["entered"]["code_status"]["value"] == "DNR"
    # A conflicting "none" on another template is refused, naming who entered what.
    r = c.post(f"/api/handoffs/{csn}/ipass_physician/attest", json={"rule": "P04", "option": "Full code confirmed with patient or family"},
               headers=h("ed-md"))
    assert r.status_code == 409
    assert r.json()["detail"] == "Code status was entered as DNR by Jordan Reyes, RN on the SBAR. Change it there first."
    # Another value from another template: the latest entered wins everywhere.
    out = c.post(f"/api/handoffs/{csn}/ipass_physician/attest", json={"rule": "P04", "option": "Comfort care"}, headers=h("ed-md")).json()
    assert [x["value"] for x in _els(out["templates"]["sbar_nurse"]["draft"])["E08"]["entered"]] == ["Comfort care"]
    # The template that holds the value can change it to "none"; then the others are free again.
    c.post(f"/api/handoffs/{csn}/ipass_physician/attest", json={"rule": "P04", "option": "Full code confirmed with patient or family"},
           headers=h("ed-md"))
    out = c.get(f"/api/handoffs/{csn}").json()
    assert [x["value"] for x in _els(out["templates"]["sbar_nurse"]["draft"])["E08"]["entered"]] == ["DNR"]


def test_entering_a_value_while_another_template_confirmed_none_is_refused(client):
    c, main = client
    c.post("/api/demo/reset")
    row = _patient_case(c, ("N06",), ("P05",))
    csn = row["csn"]
    md = c.post(f"/api/handoffs/{csn}/ipass_physician/attest", json={"rule": "P05", "option": "No known allergies confirmed"},
                headers=h("ed-md")).json()
    who = next(x for x in md["activity"] if x["kind"] == "attest")["by"]
    r = c.post(f"/api/handoffs/{csn}/sbar_nurse/attest", json={"rule": "N06", "option": "Add an allergy", "entry": "Latex"})
    assert r.status_code == 409
    assert r.json()["detail"] == f'{who} confirmed "No known allergies confirmed" on the I-PASS. Change it there first.'
    # The I-PASS "none" stays per template and does not resolve the SBAR rule.
    d = c.get(f"/api/handoffs/{csn}").json()["templates"]["sbar_nurse"]["draft"]
    assert "N06" in d["completeness"]["blocking"]


def test_entered_allergies_are_a_list_and_each_can_be_removed(client):
    c, main = client
    c.post("/api/demo/reset")
    row = _patient_case(c, ("N06",), ())
    csn = row["csn"]
    url = f"/api/handoffs/{csn}/sbar_nurse"
    c.post(f"{url}/attest", json={"rule": "N06", "option": "Add an allergy", "entry": "Penicillin", "entry_note": "Hives"})
    out = c.post(f"{url}/attest", json={"rule": "N06", "option": "Add an allergy", "entry": "Latex"}).json()
    assert [(x["value"], x["note"]) for x in _els(out["templates"]["sbar_nurse"]["draft"])["E09"]["entered"]] == [("Penicillin", "Hives"), ("Latex", None)]
    assert out["summary"]["entered"]["allergies"][1]["value"] == "Latex"
    r = c.post(f"{url}/attest", json={"rule": "N06", "option": "No known allergies confirmed"})
    assert r.status_code == 409 and r.json()["detail"].startswith("An allergy was entered as Latex by Jordan Reyes, RN on the SBAR.")
    r = c.post(f"{url}/attest", json={"rule": "N06", "option": "Add an allergy", "entry": "Aspirin", "remove": True})
    assert r.status_code == 409 and r.json()["detail"] == "That allergy is not on the list."
    _resolve(c, csn)
    assert c.post(f"{url}/sign", json={"callback": "x4471"}).json()["templates"]["sbar_nurse"]["workflow"]["state"] == "signed"
    out = c.post(f"{url}/attest", json={"rule": "N06", "option": "Add an allergy", "entry": " penicillin ", "remove": True}).json()
    t = out["templates"]["sbar_nurse"]
    assert [x["value"] for x in _els(t["draft"])["E09"]["entered"]] == ["Latex"]
    assert t["workflow"]["state"] == "changed"                       # a removal is a change after signing
    assert any(x.get("removed") == "Penicillin" for x in out["activity"])
    # Removing the last one leaves the rule open again.
    out = c.post(f"{url}/attest", json={"rule": "N06", "option": "Add an allergy", "entry": "Latex", "remove": True}).json()
    d = out["templates"]["sbar_nurse"]["draft"]
    assert "entered" not in _els(d)["E09"] and "N06" in d["completeness"]["blocking"]
    r = c.post(f"{url}/attest", json={"rule": "N06", "option": "Add an allergy", "entry": "Latex", "remove": "yes"})
    assert r.status_code == 422 and r.json()["detail"] == "The request has a value the app cannot use for the remove choice."


def test_an_entered_value_after_signing_shows_in_what_changed_for_the_receiver(client):
    c, main = client
    c.post("/api/demo/reset")
    row = _patient_case(c, ("N05",), ("P04",))
    csn, unit = row["csn"], _receiver(main, row)
    c.post(f"/api/handoffs/{csn}/sbar_nurse/attest", json={"rule": "N05", "option": "DNR"})
    _resolve(c, csn)
    assert c.post(f"/api/handoffs/{csn}/sbar_nurse/sign", json={"callback": "x4471"}).status_code == 200
    # Changed on the I-PASS by the physician: the nurse handoff's content changed too.
    assert c.post(f"/api/handoffs/{csn}/ipass_physician/attest", json={"rule": "P04", "option": "DNR/DNI"},
                  headers=h("ed-md")).status_code == 200
    t = c.get(f"/api/handoffs/{csn}", headers=h(unit)).json()["templates"]["sbar_nurse"]
    assert t["workflow"]["state"] == "changed" and t["workflow"]["edited_since_signed"]
    e08 = next(x for x in t["changes"]["elements"] if x["id"] == "E08")
    assert t["changes"]["since"] == "signed" and e08["after"]["entered"] == [{"value": "DNR/DNI", "note": None}]
    assert e08["before"] != e08["after"]
