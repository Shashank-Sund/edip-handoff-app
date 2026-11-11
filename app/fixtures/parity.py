#!/usr/bin/env python3
"""Expected answers for the static demo's parity test: drives the real FastAPI app (mock mode) through a fixed
scenario list and writes every request with its status and response.

    .venv/bin/python app/fixtures/parity.py [--fixtures DIR] [--out app/fixtures/parity]

Without --fixtures it builds fixtures from the stand in world, the way tests/test_app.py does (no network, no
Databricks credentials; reproducible in CI). With --fixtures it uses an existing folder, for example the app's
own app/fixtures/data. The out folder gets the fixtures used (encounters.json, drafts.jsonl.gz, meta.json, with two
admissions marked canceled by cancel_admissions()), templates.json and scenarios.json; app/client/src/demo/parity.test.ts replays the same requests, in order,
through the in-browser implementation and compares every answer.

Normalized (legitimately nondeterministic): the `now` field of /api/health (the wall clock).
"""
import argparse
import gzip
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ("encounters.json", "drafts.jsonl.gz", "meta.json")


def build_fixtures(out: Path) -> None:
    sys.path[:0] = [str(ROOT / "ai"), str(ROOT / "synthetic"), str(ROOT / "pipelines")]
    from eval import run_eval

    with tempfile.TemporaryDirectory() as world:
        run_eval.build_world(world)
        subprocess.run([sys.executable, str(ROOT / "app/fixtures/build.py"), "--world", world, "--out", str(out)],
                       check=True, capture_output=True)


def cancel_admissions(out: Path) -> list[str]:
    """Mark three admissions canceled as the handoff job does (every version retracted, retracted_ts the cancel's
    time, prior_status the status it had; the encounter's status canceled): one while boarding, one after its transfer
    and an addendum, and one whose versions an older job had retracted (no prior_status: canceled at every time). The
    synthetic week's own canceled registrations never had a handoff, so without this no scenario shows a canceled case. Unlike the
    real job (no points after a retract), versions after the cancel stay; both sides read the same fixtures."""
    t = lambda v: datetime.fromisoformat(v.replace("Z", "+00:00"))  # noqa: E731
    encounters = json.loads((out / "encounters.json").read_text())
    with gzip.open(out / "drafts.jsonl.gz", "rt", encoding="utf-8") as f:
        drafts = [json.loads(line) for line in f]
    points = {}
    for d in drafts:
        points.setdefault(d["csn"], set()).add(t(d["as_of"]))
    picks = {}
    for e in sorted(encounters, key=lambda e: e["csn"]):
        pts, dep = sorted(points.get(e["csn"], ())), e["ed_departure_ts"] and t(e["ed_departure_ts"])
        if not dep or len(pts) < 2:
            continue
        if "boarding" not in picks and dep - pts[1] > timedelta(hours=1):
            picks["boarding"] = (e, pts[1] + timedelta(minutes=30))
        elif "arrived" not in picks and pts[-1] > dep and pts[-1] + timedelta(minutes=30) < dep + timedelta(hours=11):
            picks["arrived"] = (e, pts[-1] + timedelta(minutes=30))
        elif "older job" not in picks and dep - pts[1] > timedelta(hours=1):
            picks["older job"] = (e, pts[1] + timedelta(minutes=30))
    if len(picks) < 3:
        raise SystemExit(f"cancel_admissions found only {sorted(picks)}: it needs two admissions to cancel while "
                         "boarding and one after an addendum")
    for kind, (e, at) in picks.items():
        e["status"] = "canceled"
        for d in drafts:
            if d["csn"] == e["csn"]:
                d["prior_status"], d["status"] = None if kind == "older job" else d["status"], "retracted"
                d["retracted_ts"] = at.isoformat().replace("+00:00", "Z")
    (out / "encounters.json").write_text(json.dumps(encounters))
    with gzip.open(out / "drafts.jsonl.gz", "wt", encoding="utf-8") as f:
        for d in drafts:
            f.write(json.dumps(d, ensure_ascii=False, separators=(",", ":")) + "\n")
    return sorted(e["csn"] for e, _ in picks.values())


def load_app(fixtures: Path):
    os.environ["EDIP_FIXTURES"] = str(fixtures)
    os.environ["EDIP_STATIC"] = str(fixtures / "no-static")
    for k in ("EDIP_MODE", "EDIP_CLOCK", "EDIP_CLOCK_MODE", "EDIP_DEMO_PERSONAS", "EDIP_SYNTHETIC_DATA"):
        os.environ.pop(k, None)
    sys.path.insert(0, str(ROOT / "app/server"))
    for m in ("config", "data", "assignment", "tasks", "seen", "logic", "personas", "workflow", "main"):
        sys.modules.pop(m, None)
    main = importlib.import_module("main")
    from fastapi.testclient import TestClient
    return TestClient(main.app), main


class Recorder:
    def __init__(self, client):
        self.c, self.items = client, []

    def __call__(self, name, method, path, at=None, persona=None, body=None, params=None):
        query = ({"at": at} if at is not None else {}) | (params or {})
        headers = {"X-Demo-Persona": persona} if persona is not None else None
        r = self.c.request(method, path, params=query or None, headers=headers, json=body)
        data = r.json()
        if path == "/api/health":
            data["now"] = "<now>"
        self.items.append({"name": name, "method": method, "path": path, "at": at, "persona": persona, "body": body,
                           "params": params, "status": r.status_code, "response": data})
        return r.status_code, data


def elements(draft):
    return {e["id"]: e for s in draft["sections"] for e in s["elements"]}


def scenarios(c, main):
    rec = Recorder(c)
    ds = main.VIEWS.ds
    iso = lambda t: t.isoformat()  # noqa: E731
    default = main._current()[1]
    start, end = ds.start, ds.end
    personas = main._current()[0]
    units = [p for p in personas if p["role"] == "unit_nurse"]

    rec("health", "GET", "/api/health")
    rec("config", "GET", "/api/config")
    rec("templates", "GET", "/api/templates")
    for p in personas:
        rec(f"me {p['id']}", "GET", "/api/me", persona=p["id"])
    rec("me default persona", "GET", "/api/me")
    rec("me unknown persona", "GET", "/api/me", persona="nobody")

    # Boards: default (the busiest hour), the window edges, a few hours in between, clamped and naive times.
    times = {
        "default": None, "empty at": "", "start": iso(start), "day 2 morning": iso(start + timedelta(hours=18)),
        "mid week": iso(start + (end - start) / 2), "busiest minus 5h": iso(default - timedelta(hours=5)),
        "end": iso(end), "after end (clamped)": iso(end + timedelta(days=3)),
        "before start (clamped)": iso(start - timedelta(days=1)),
        "naive utc": (default + timedelta(minutes=37, seconds=11)).replace(tzinfo=None).isoformat(),
        "z suffix with ms": (default + timedelta(hours=2, milliseconds=250)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
    }
    _, b = rec("board ed-rn default", "GET", "/api/board", persona="ed-rn")
    busy_units = [u for u in units if any(r["destination"]["pending_unit"] == u["unit"] and r["facility"] == u["facility"]
                                          for r in b["rows"])]
    board_personas = ["ed-rn", "ed-md", "hosp-md", "viewer"] + [u["id"] for u in busy_units[:2]]
    for label, t in times.items():
        for p in board_personas:
            rec(f"board {p} {label}", "GET", "/api/board", at=t, persona=p)
    for u in units:
        rec(f"board {u['id']} default", "GET", "/api/board", persona=u["id"])
    rec("board invalid at", "GET", "/api/board", at="nope", persona="ed-rn")
    rec("board unknown persona", "GET", "/api/board", persona="nobody")
    rec("board invalid at and unknown persona", "GET", "/api/board", at="2026-13-01", persona="nobody")

    # Cases: boarding, arrived on the unit (transferred), canceled, out of scope, not yet admitted, unknown.
    rows = b["rows"]
    boarding = [r for r in rows if r["phase"] == "boarding"]
    arrived = [r for r in rows if r["phase"] == "arrived"]
    canceled = [r for r in rows if r["phase"] == "canceled"]
    picks = boarding[:4] + arrived[:2] + canceled[:1]
    for r in picks:
        for p in ("ed-rn", "ed-md", "viewer"):
            rec(f"case {r['phase']} {r['csn']} {p}", "GET", f"/api/handoffs/{r['csn']}", persona=p)
    unit = busy_units[0]
    mine = [r for r in rows if r["destination"]["pending_unit"] == unit["unit"] and r["facility"] == unit["facility"]]
    others = [r for r in rows if r not in mine]
    rec("case own unit", "GET", f"/api/handoffs/{mine[0]['csn']}", persona=unit["id"])
    if others:
        rec("case other unit (404)", "GET", f"/api/handoffs/{others[0]['csn']}", persona=unit["id"])
    future = next(e for e in ds.enc.values() if e["admit_decision_ts"] and e["admit_decision_ts"] > default
                  and ds.versions.get(e["csn"]))
    rec("case admitted later (404)", "GET", f"/api/handoffs/{future['csn']}", persona="ed-rn")
    rec("case admitted later, seen at end", "GET", f"/api/handoffs/{future['csn']}", at=iso(end), persona="ed-rn")
    rec("case unknown csn", "GET", "/api/handoffs/0000000", persona="ed-rn")
    _, b_end = rec("board ed-rn end (for cases)", "GET", "/api/board", at=iso(end - timedelta(hours=6)), persona="ed-rn")
    for r in [x for x in b_end["rows"] if x["phase"] == "arrived"][:2]:
        rec(f"case transferred {r['csn']}", "GET", f"/api/handoffs/{r['csn']}", at=iso(end - timedelta(hours=6)), persona="ed-rn")

    # Analytics at a few times; analytics ignores the persona.
    for label in ("default", "mid week", "end", "start"):
        rec(f"analytics {label}", "GET", "/api/analytics", at=times[label])
    rec("analytics unknown persona", "GET", "/api/analytics", persona="nobody")

    # The full action sequence on a boarding patient with blocking rules.
    rec("reset", "POST", "/api/demo/reset")
    target = next((r for r in boarding if r["templates"].get("sbar_nurse", {}).get("blocking")), boarding[0])
    csn = target["csn"]
    url = f"/api/handoffs/{csn}"
    recv = next(p for p in units if p["unit"] == target["destination"]["pending_unit"] and p["facility"] == target["facility"])
    _, case = rec("action case before", "GET", url, persona="ed-rn")
    d = case["templates"]["sbar_nurse"]["draft"]
    els = elements(d)
    narrative = [e for e in els.values() if e["kind"] == "narrative"]
    with_items = next((e for e in narrative if isinstance((e.get("narrative") or {}).get("items"), list)), None)
    rec("receiver signs (409)", "POST", f"{url}/sbar_nurse/sign", persona=recv["id"], body={"callback": "x1"})
    rec("ED nurse acknowledges (409)", "POST", f"{url}/sbar_nurse/ack", persona="ed-rn", body={"readback": "x" * 20})
    rec("receiver acknowledges before sign (409)", "POST", f"{url}/sbar_nurse/ack", persona=recv["id"], body={"readback": "x" * 20})
    rec("physician signs SBAR (409)", "POST", f"{url}/sbar_nurse/sign", persona="ed-md", body={"callback": "x1"})
    rec("admitting physician edits (409)", "POST", f"{url}/ipass_physician/edit", persona="hosp-md",
        body={"element": "E25", "text": "x"})
    for kind, body in (("sign", {"callback": "x1"}), ("ack", {"readback": "a" * 20}), ("edit", {"element": "E25", "text": "x"}),
                       ("attest", {"rule": "N05", "option": "x"})):
        rec(f"viewer {kind} (409)", "POST", f"{url}/sbar_nurse/{kind}", persona="viewer", body=body)
    rec("unknown template (409)", "POST", f"{url}/nope/sign", persona="ed-rn", body={"callback": "x1"})
    rec("unknown csn action (409)", "POST", "/api/handoffs/0000000/sbar_nurse/sign", persona="ed-rn", body={"callback": "x1"})
    rec("edit structured (409)", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn", body={"element": "E09", "text": "none"})
    rec("edit unknown element (409)", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn", body={"element": "E99", "text": "x"})
    rec("attest made up option (409)", "POST", f"{url}/sbar_nurse/attest", persona="ed-rn", body={"rule": "N05", "option": "made up"})
    rec("attest unknown rule (409)", "POST", f"{url}/sbar_nurse/attest", persona="ed-rn", body={"rule": "ZZ9", "option": "x"})
    rec("sign blocked (409)", "POST", f"{url}/sbar_nurse/sign", persona="ed-rn", body={"callback": "x4471"})
    rec("edit E25", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn", body={"element": "E25", "text": "If SBP under 90, call the ED"})
    rec("edit E25 again", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn",
        body={"element": "E25", "text": "  If SBP under 90 or HR over 130, call the ED  "})
    if with_items:
        rec(f"edit items {with_items['id']}", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn",
            body={"element": with_items["id"], "items": [{"text": "Repeat lactate", "owner": "RN", "due": "18:00"},
                                                         {"text": " Call family ", "owner": " ", "extra": 1}]})
        rec(f"edit items without text (422) {with_items['id']}", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn",
            body={"element": with_items["id"], "items": [{"text": "Repeat lactate"}, {"owner": "MD"}]})
    blocking = d["completeness"]["blocking"]
    for rule in d["rules"]:
        if rule["rule"] in blocking:
            opts = rule.get("attest_options") or []
            if opts and rule["status"] != "missing":
                rec(f"attest {rule['rule']}", "POST", f"{url}/sbar_nurse/attest", persona="ed-rn",
                    body={"rule": rule["rule"], "option": opts[0]})
            else:
                rec(f"edit for {rule['rule']}", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn",
                    body={"element": rule["element"], "text": "Written by the nurse"})
    rec("sign blank callback (409)", "POST", f"{url}/sbar_nurse/sign", persona="ed-rn", body={"callback": "   "})
    rec("sign", "POST", f"{url}/sbar_nurse/sign", persona="ed-rn", body={"callback": " x4471 "})
    rec("ack too short (409)", "POST", f"{url}/sbar_nurse/ack", persona=recv["id"], body={"readback": "ok"})
    rec("ack wrong unit template (409)", "POST", f"{url}/ipass_physician/ack", persona=recv["id"],
        body={"readback": "Watcher, will recheck vitals on arrival"})
    rec("ack", "POST", f"{url}/sbar_nurse/ack", persona=recv["id"],
        body={"readback": "Watcher, will recheck vitals on arrival", "questions": "  "})
    # The physician side: sign I-PASS as the ED physician, acknowledge as the admitting physician.
    _, cmd = rec("physician case", "GET", url, persona="ed-md")
    pd = cmd["templates"]["ipass_physician"]["draft"]
    for rule in pd["rules"]:
        if rule["rule"] in pd["completeness"]["blocking"]:
            opts = rule.get("attest_options") or []
            if opts and rule["status"] != "missing":
                typed = {"entry": "Penicillin", "entry_note": "Hives"} if opts[-1] in (rule.get("attest_typed") or {}) else {}
                rec(f"physician attest {rule['rule']}", "POST", f"{url}/ipass_physician/attest", persona="ed-md",
                    body={"rule": rule["rule"], "option": opts[-1]} | typed)
            else:
                rec(f"physician edit for {rule['rule']}", "POST", f"{url}/ipass_physician/edit", persona="ed-md",
                    body={"element": rule["element"], "text": "Written by the physician"})
    rec("physician sign", "POST", f"{url}/ipass_physician/sign", persona="ed-md", body={"callback": "x2210"})
    rec("admitting physician ack", "POST", f"{url}/ipass_physician/ack", persona="hosp-md",
        body={"readback": "Admit to medicine, recheck lactate", "questions": "Any family to call?"})
    # The custom template: either ED clinician signs it.
    rec("custom sign by physician", "POST", f"{url}/custom_sea58/sign", persona="ed-md", body={"callback": "x2210"})

    # Views after the actions: now, an hour earlier (none of it has happened), later versions, the end.
    for p in ("ed-rn", recv["id"], "hosp-md", "viewer"):
        rec(f"after: case {p}", "GET", url, persona=p)
        rec(f"after: board {p}", "GET", "/api/board", persona=p)
    rec("after: case an hour earlier", "GET", url, at=iso(default - timedelta(hours=1)), persona="ed-rn")
    rec("after: board an hour earlier", "GET", "/api/board", at=iso(default - timedelta(hours=1)), persona="ed-rn")
    later = [x for x in ds.rows_at(csn, end) if x["at"] > default]
    for x in later[:3]:
        rec(f"after: case at newer {x['template']} v{x['version']}", "GET", url, at=iso(x["at"]), persona="ed-rn")
        rec(f"after: case at newer {x['template']} v{x['version']} (receiver)", "GET", url, at=iso(x["at"]), persona=recv["id"])
    enc = ds.enc[csn]
    if enc["ed_departure_ts"]:
        dep = enc["ed_departure_ts"] + timedelta(minutes=5)
        rec("after departure: case", "GET", url, at=iso(dep), persona="ed-rn")
        rec("after departure: edit (final, 409 unless addendum)", "POST", f"{url}/sbar_nurse/edit", at=iso(dep), persona="ed-rn",
            body={"element": "E25", "text": "late"})
        rec("after departure: ack again", "POST", f"{url}/sbar_nurse/ack", at=iso(dep), persona=recv["id"],
            body={"readback": "Arrived, vitals rechecked, all as handed off"})
    for label in ("default", "end"):
        rec(f"after: analytics {label}", "GET", "/api/analytics", at=times[label])

    # An addendum after departure: the ED may still edit and sign it.
    add = next((r for rows in ds.versions.values() for r in rows if r["status"] == "addendum" and r["template"] == "sbar_nurse"), None)
    if add:
        t = iso(add["at"] + timedelta(minutes=1))
        acsn = add["csn"]
        rec("addendum: case", "GET", f"/api/handoffs/{acsn}", at=t, persona="ed-rn")
        rec("addendum: edit", "POST", f"/api/handoffs/{acsn}/sbar_nurse/edit", at=t, persona="ed-rn",
            body={"element": "E25", "text": "Addendum: repeat troponin pending"})
    # Acting on a patient who left the ED without an addendum.
    left = next((r for r in arrived if r["templates"].get("sbar_nurse", {}).get("status") != "addendum"), None)
    if left:
        rec("arrived: sign (final, 409)", "POST", f"/api/handoffs/{left['csn']}/sbar_nurse/sign", persona="ed-rn",
            body={"callback": "x1"})
    rec("health after actions", "GET", "/api/health")
    rec("reset again", "POST", "/api/demo/reset")
    rec("after reset: case", "GET", url, persona="ed-rn")
    rec("after reset: board", "GET", "/api/board", persona="ed-rn")
    rec("unknown api path (404)", "GET", "/api/nope")
    workflow_scenarios(rec, ds, default, boarding, units, end)
    entered_scenarios(rec, ds, default, boarding, units)
    canceled_scenarios(rec, ds)
    assignment_scenarios(rec, ds, default, boarding, units)
    task_scenarios(rec, ds, default, units)
    seen_scenarios(rec, ds, default, units)
    return rec.items


def resolve(rec, url, template, persona, at, label):
    """Attest or write every item that blocks sign off, as the clinician would."""
    _, case = rec(f"{label}: case", "GET", url, at=at, persona=persona)
    if "templates" not in case:
        return case
    d = case["templates"][template]["draft"]
    for rule in d["rules"]:
        if rule["rule"] in d["completeness"]["blocking"]:
            opts = rule.get("attest_options") or []
            if opts and rule["status"] != "missing":
                rec(f"{label}: attest {rule['rule']}", "POST", f"{url}/{template}/attest", at=at, persona=persona,
                    body={"rule": rule["rule"], "option": opts[0]})
            else:
                rec(f"{label}: edit for {rule['rule']}", "POST", f"{url}/{template}/edit", at=at, persona=persona,
                    body={"element": rule["element"], "text": "Written by the clinician"})
    return case


def workflow_scenarios(rec, ds, default, boarding, units, end):
    """Sign again after a change, Restore, read back and callback rules, validation sentences, the replay clock,
    transfer without sign off, and a stale vitals recheck that expires."""
    iso = lambda t: t.isoformat()  # noqa: E731
    rec("wf reset", "POST", "/api/demo/reset")
    p = boarding[1] if len(boarding) > 1 else boarding[0]
    url = f"/api/handoffs/{p['csn']}"
    recv = next(u for u in units if u["unit"] == p["destination"]["pending_unit"] and u["facility"] == p["facility"])
    first = resolve(rec, url, "sbar_nurse", "ed-rn", None, "wf")
    attestable = next((r for r in first["templates"]["sbar_nurse"]["draft"]["rules"]
                       if r.get("attest_options") and r["status"] == "attest"), None)
    for name, body in (("sign callback x1 (409)", {"callback": "x1"}), ("sign callback 12 (409)", {"callback": "12"}),
                       ("sign callback too long (409)", {"callback": "5" * 41}), ("sign callback missing (422)", {}),
                       ("sign callback not text (422)", {"callback": 4471}), ("sign callback null (422)", {"callback": None}),
                       ("sign body a list (422)", [1]), ("sign callback ext 12", {"callback": "ext. 12"})):
        rec(f"wf {name}", "POST", f"{url}/sbar_nurse/sign", persona="ed-rn", body=body)
    rec("wf sign", "POST", f"{url}/sbar_nurse/sign", persona="ed-rn", body={"callback": "(617) 555-0142"})
    rec("wf edit after signing", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn", body={"element": "E25", "text": "Call the ED if SBP under 90"})
    rec("wf receiver blocked after the change (409)", "POST", f"{url}/sbar_nurse/ack", persona=recv["id"],
        body={"readback": "Watcher, will recheck vitals on arrival"})
    rec("wf case changed: receiver", "GET", url, persona=recv["id"])
    rec("wf board changed: ed", "GET", "/api/board", persona="ed-rn")
    rec("wf board changed: receiver", "GET", "/api/board", persona=recv["id"])
    for name, body in (("empty text (409)", {"element": "E25", "text": ""}), ("blank text (409)", {"element": "E25", "text": "   "}),
                       ("no text (409)", {"element": "E25"}), ("empty items (409)", {"element": "E25", "items": []}),
                       ("blank item (409)", {"element": "E25", "items": [{"text": " "}]}),
                       ("too long (409)", {"element": "E25", "text": "a" * 2001}),
                       ("item text not text (422)", {"element": "E25", "items": [{"text": 5}]}),
                       ("items not a list (422)", {"element": "E25", "items": "x"}),
                       ("restore not a boolean (422)", {"element": "E25", "restore": "yes"}),
                       ("severity (409)", {"element": "E07", "text": "stable"}),
                       ("restore with no edit (409)", {"element": "E06", "restore": True}),
                       ("restore", {"element": "E25", "restore": True}),
                       ("restore again (409)", {"element": "E25", "restore": True})):
        rec(f"wf edit {name}", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn", body=body)
    rec("wf sign again", "POST", f"{url}/sbar_nurse/sign", persona="ed-rn", body={"callback": "x4471"})
    for name, body in (("two words (409)", {"readback": "Watcher patient"}), ("one letter (409)", {"readback": "aaaa aaaa aaaa aaaa"}),
                       ("few letters (409)", {"readback": "ok 12 34 56 78 90"}), ("too long (409)", {"readback": "Watcher, recheck " * 130}),
                       ("questions too long (409)", {"readback": "Watcher, will recheck vitals", "questions": "q" * 2001}),
                       ("readback not text (422)", {"readback": ["a"]}), ("missing (422)", {"questions": "x"})):
        rec(f"wf ack {name}", "POST", f"{url}/sbar_nurse/ack", persona=recv["id"], body=body)
    rec("wf ack", "POST", f"{url}/sbar_nurse/ack", persona=recv["id"], body={"readback": "Watcher, will recheck vitals on arrival"})
    if attestable:
        rec("wf attest after acknowledging", "POST", f"{url}/sbar_nurse/attest", persona="ed-rn",
            body={"rule": attestable["rule"], "option": attestable["attest_options"][0]})
    rec("wf case after attest", "GET", url, persona="ed-rn")
    rec("wf sign a third time", "POST", f"{url}/sbar_nurse/sign", persona="ed-rn", body={"callback": "x4471"})
    rec("wf board to acknowledge again", "GET", "/api/board", persona=recv["id"])
    rec("wf earlier action (409)", "POST", f"{url}/sbar_nurse/edit", at=iso(default - timedelta(minutes=5)), persona="ed-rn",
        body={"element": "E25", "text": "Earlier"})
    rec("wf earlier ack (409)", "POST", f"{url}/sbar_nurse/ack", at=iso(default - timedelta(minutes=5)), persona=recv["id"],
        body={"readback": "Watcher, will recheck vitals on arrival"})
    # Later versions of the same patient: a change of clinical content needs a new signature, location alone does not.
    for x in [r for r in ds.rows_at(p["csn"], end) if r["at"] > default and r["template"] == "sbar_nurse"][:4]:
        rec(f"wf later v{x['version']} {x['trigger']}", "GET", url, at=iso(x["at"]), persona=recv["id"])
        rec(f"wf later v{x['version']} board", "GET", "/api/board", at=iso(x["at"]), persona="ed-rn")
    enc = ds.enc[p["csn"]]
    if enc["ed_departure_ts"]:
        t = iso(enc["ed_departure_ts"] + timedelta(minutes=10))
        rec("wf after transfer: case", "GET", url, at=t, persona="ed-rn")
        rec("wf after transfer: ack", "POST", f"{url}/sbar_nurse/ack", at=t, persona=recv["id"],
            body={"readback": "Arrived, vitals rechecked, all as handed off"})
        resolve(rec, url, "sbar_nurse", "ed-rn", t, "wf after transfer")
        rec("wf after transfer: sign", "POST", f"{url}/sbar_nurse/sign", at=t, persona="ed-rn", body={"callback": "x4471"})
        rec("wf after transfer: sign again (409 when final)", "POST", f"{url}/ipass_physician/sign", at=t, persona="ed-md",
            body={"callback": "x2210"})
        rec("wf after transfer: ack", "POST", f"{url}/sbar_nurse/ack", at=t, persona=recv["id"],
            body={"readback": "Arrived, vitals rechecked, all as handed off"})

    # A patient who left the ED with no sign off at all: the ED can still sign it.
    rec("wf reset 2", "POST", "/api/demo/reset")
    left = sorted((e for e in ds.enc.values() if e["ed_departure_ts"] and e["admit_decision_ts"] and ds.versions.get(e["csn"])
                   and default < e["ed_departure_ts"] < end - timedelta(hours=1)), key=lambda e: e["ed_departure_ts"])
    if left:
        e = left[0]
        t = iso(e["ed_departure_ts"] + timedelta(minutes=5))
        u = f"/api/handoffs/{e['csn']}"
        rec("unsigned transfer: board", "GET", "/api/board", at=t, persona="ed-rn")
        rec("unsigned transfer: board physician", "GET", "/api/board", at=t, persona="ed-md")
        rec("unsigned transfer: analytics", "GET", "/api/analytics", at=t)
        _, c = rec("unsigned transfer: case", "GET", u, at=t, persona="ed-rn")
        resolve(rec, u, "sbar_nurse", "ed-rn", t, "unsigned transfer")
        rec("unsigned transfer: sign", "POST", f"{u}/sbar_nurse/sign", at=t, persona="ed-rn", body={"callback": "x4471"})
        rec("unsigned transfer: sign again (409)", "POST", f"{u}/sbar_nurse/edit", at=t, persona="ed-rn",
            body={"element": "E25", "text": "After signing"})
        if "summary" in c:
            dest = c["summary"]["destination"]
            r2 = next((x for x in units if x["unit"] == dest["pending_unit"] and x["facility"] == e["facility"]), None)
            if r2:
                rec("unsigned transfer: ack", "POST", f"{u}/sbar_nurse/ack", at=t, persona=r2["id"],
                    body={"readback": "Arrived, vitals rechecked, all as handed off"})
        rec("unsigned transfer: analytics after", "GET", "/api/analytics", at=t)

    # A stale vitals recheck counts only within the rule's limit for the patient's severity.
    rec("wf reset 3", "POST", "/api/demo/reset")
    stale = None
    for csn, rows in ds.versions.items():
        e = ds.enc[csn]
        for i, r in enumerate(rows):
            if r["template"] != "sbar_nurse" or not any(x["rule"] == "N08" and x["status"] == "stale" for x in r["draft"]["rules"]):
                continue
            nxt = next((x for x in rows[i + 1:] if x["template"] == "sbar_nurse"), None)
            stop = min([x for x in (nxt and nxt["at"], e["ed_departure_ts"]) if x] or [end])
            if stop - r["at"] > timedelta(minutes=75) and r["status"] != "retracted":
                stale = (csn, r, stop)
                break
        if stale:
            break
    if stale:
        csn, r, stop = stale
        u = f"/api/handoffs/{csn}"
        t0 = r["at"] + timedelta(minutes=1)
        rec("stale: case", "GET", u, at=iso(t0), persona="ed-rn")
        rec("stale: recheck", "POST", f"{u}/sbar_nurse/attest", at=iso(t0), persona="ed-rn",
            body={"rule": "N08", "option": "Rechecked at bedside, unchanged"})
        for m in (14, 16, 29, 31, 59, 61, 74):
            if t0 + timedelta(minutes=m) < stop:
                rec(f"stale: case {m} min after the recheck", "GET", u, at=iso(t0 + timedelta(minutes=m)), persona="ed-rn")
                rec(f"stale: board {m} min after", "GET", "/api/board", at=iso(t0 + timedelta(minutes=m)), persona="ed-rn")
        rec("stale: sign after expiry (409 when expired)", "POST", f"{u}/sbar_nurse/sign", at=iso(t0 + timedelta(minutes=74)),
            persona="ed-rn", body={"callback": "x4471"})
    # I-PASS high risk branch: a watcher or unstable patient has one contingency rule, and no "Stable" attestation.
    rec("wf reset 4", "POST", "/api/demo/reset")
    branch = None
    for csn, rows in ds.versions.items():
        e = ds.enc[csn]
        for i, r in enumerate(rows):
            st = {x["rule"]: x["status"] for x in r["draft"]["rules"]} if r["template"] == "ipass_physician" else {}
            if st.get("P10") == "attest" and "P11" in st and r["status"] != "retracted" and (
                    not e["ed_departure_ts"] or e["ed_departure_ts"] > r["at"] + timedelta(minutes=2)):
                branch = (csn, r)
                break
        if branch:
            break
    if branch:
        csn, r = branch
        u, t = f"/api/handoffs/{csn}", iso(r["at"] + timedelta(minutes=1))
        rec("branch: case", "GET", u, at=t, persona="ed-md")
        rec("branch: attest stable (409)", "POST", f"{u}/ipass_physician/attest", at=t, persona="ed-md",
            body={"rule": "P10", "option": "Stable, nothing anticipated"})
        rec("branch: write the plan", "POST", f"{u}/ipass_physician/edit", at=t, persona="ed-md",
            body={"element": "E25", "text": "Watcher: if MAP under 65, bolus and call the ICU"})
    rec("wf reset end", "POST", "/api/demo/reset")


def entered_scenarios(rec, ds, default, boarding, units):
    """Attestation choices that enter a value (Droplet, DNR, an allergy, typed isolation), their checks, what the
    receiver sees, and a chart value that replaces an entered one."""
    iso = lambda t: t.isoformat()  # noqa: E731
    rec("entered reset", "POST", "/api/demo/reset")
    done = set()
    for r in boarding:
        _, case = rec(f"entered case {r['csn']}", "GET", f"/api/handoffs/{r['csn']}", persona="ed-rn")
        d = case["templates"]["sbar_nurse"]["draft"]
        url = f"/api/handoffs/{r['csn']}/sbar_nurse"
        for rule in d["rules"]:
            rid = rule["rule"]
            if rule["status"] != "attest" or rid not in ("N05", "N06", "N07") or rid in done:
                continue
            done.add(rid)
            if rid == "N07":
                for name, body in (("Other without text (409)", {"option": "Other"}),
                                   ("Other blank (409)", {"option": "Other", "entry": "  "}),
                                   ("Other too long (409)", {"option": "Other", "entry": "a" * 2001}),
                                   ("Other not text (422)", {"option": "Other", "entry": 5}),
                                   ("Other", {"option": "Other", "entry": "  Contact and droplet  "}),
                                   ("Droplet with ignored text", {"option": "Droplet", "entry": "ignored", "entry_note": "x"}),
                                   ("none again", {"option": "No isolation needed"}),
                                   ("Airborne", {"option": "Airborne"})):
                    rec(f"entered N07 {name}", "POST", f"{url}/attest", persona="ed-rn", body={"rule": rid} | body)
            elif rid == "N06":
                for name, body in (("allergy without allergen (409)", {"option": "Add an allergy", "entry_note": "Hives"}),
                                   ("allergy note too long (409)", {"option": "Add an allergy", "entry": "Latex", "entry_note": "b" * 2001}),
                                   ("allergy note not text (422)", {"option": "Add an allergy", "entry": "Latex", "entry_note": ["x"]}),
                                   ("allergy", {"option": "Add an allergy", "entry": " Penicillin ", "entry_note": " Hives "}),
                                   ("allergy without reaction", {"option": "Add an allergy", "entry": "Latex", "entry_note": "  "})):
                    rec(f"entered N06 {name}", "POST", f"{url}/attest", persona="ed-rn", body={"rule": rid} | body)
            else:
                rec("entered N05 DNR/DNI", "POST", f"{url}/attest", persona="ed-rn", body={"rule": rid, "option": "DNR/DNI"})
            recv = next((u for u in units if u["unit"] == r["destination"]["pending_unit"] and u["facility"] == r["facility"]), None)
            rec(f"entered {rid}: case", "GET", f"/api/handoffs/{r['csn']}", persona="ed-rn")
            if recv:
                rec(f"entered {rid}: receiver case", "GET", f"/api/handoffs/{r['csn']}", persona=recv["id"])
            rec(f"entered {rid}: board", "GET", "/api/board", persona="ed-rn")
        if len(done) == 3:
            break
    # Sign with an entered value, then enter another: sign again.
    if boarding:
        u = f"/api/handoffs/{boarding[0]['csn']}"
        first = resolve(rec, u, "sbar_nurse", "ed-rn", None, "entered sign")
        rec("entered sign: sign", "POST", f"{u}/sbar_nurse/sign", persona="ed-rn", body={"callback": "x4471"})
        r = next((x for x in first["templates"]["sbar_nurse"]["draft"]["rules"] if x["status"] == "attest"
                  and len(x.get("attest_options") or []) > 1), None)
        if r:
            rec(f"entered sign: enter {r['rule']} after signing", "POST", f"{u}/sbar_nurse/attest", persona="ed-rn",
                body={"rule": r["rule"], "option": r["attest_options"][1]})
    # A chart value replaces an entered one; the entered one stays in History.
    rec("entered reset 2", "POST", "/api/demo/reset")
    flip = None
    for csn, rows in ds.versions.items():
        e = ds.enc[csn]
        sb = [x for x in rows if x["template"] == "sbar_nurse" and x["status"] != "retracted"]
        for a, b in zip(sb, sb[1:]):
            st = lambda x: {y["rule"]: y["status"] for y in x["draft"]["rules"]}  # noqa: E731
            if (st(a).get("N05") == "attest" and st(b).get("N05") == "pass" and b["at"] - a["at"] > timedelta(minutes=2)
                    and (not e["ed_departure_ts"] or e["ed_departure_ts"] > b["at"])):
                flip = (csn, a, b)
                break
        if flip:
            break
    if flip:
        csn, a, b = flip
        u, t0 = f"/api/handoffs/{csn}", iso(a["at"] + timedelta(minutes=1))
        rec("chart replaces: enter DNR", "POST", f"{u}/sbar_nurse/attest", at=t0, persona="ed-rn", body={"rule": "N05", "option": "DNR"})
        rec("chart replaces: case before", "GET", u, at=t0, persona="ed-rn")
        rec("chart replaces: case after", "GET", u, at=iso(b["at"]), persona="ed-rn")
        rec("chart replaces: board after", "GET", "/api/board", at=iso(b["at"]), persona="ed-rn")
    patient_scenarios(rec, boarding, units)
    rec("entered reset end", "POST", "/api/demo/reset")


def patient_scenarios(rec, boarding, units):
    """Code status, allergies and isolation belong to the patient: an entry shows on every template, conflicts are
    refused, allergies are a list with removals, and a change on another template after signing is a change."""
    rec("patient reset", "POST", "/api/demo/reset")
    pick = None
    for r in boarding:
        _, c = rec(f"patient case {r['csn']}", "GET", f"/api/handoffs/{r['csn']}", persona="ed-rn")
        st = {x["rule"]: x["status"] for t in c["templates"].values() for x in t["draft"]["rules"]}
        if all(st.get(x) == "attest" for x in ("N05", "N06", "P04", "P05")):
            pick = r
            break
    if not pick:
        return
    u = f"/api/handoffs/{pick['csn']}"
    recv = next((x for x in units if x["unit"] == pick["destination"]["pending_unit"] and x["facility"] == pick["facility"]), None)
    A = lambda name, t, persona, body: rec(f"patient {name}", "POST", f"{u}/{t}/attest", persona=persona, body=body)  # noqa: E731
    A("SBAR enters DNR", "sbar_nurse", "ed-rn", {"rule": "N05", "option": "DNR"})
    A("I-PASS full code refused (409)", "ipass_physician", "ed-md", {"rule": "P04", "option": "Full code confirmed with patient or family"})
    A("I-PASS comfort care", "ipass_physician", "ed-md", {"rule": "P04", "option": "Comfort care"})
    rec("patient case after two entries", "GET", u, persona="ed-rn")
    A("I-PASS NKA", "ipass_physician", "ed-md", {"rule": "P05", "option": "No known allergies confirmed"})
    A("SBAR allergy refused (409)", "sbar_nurse", "ed-rn", {"rule": "N06", "option": "Add an allergy", "entry": "Latex"})
    A("I-PASS allergy replaces its NKA", "ipass_physician", "ed-md", {"rule": "P05", "option": "Add an allergy", "entry": "Latex"})
    A("SBAR second allergy", "sbar_nurse", "ed-rn", {"rule": "N06", "option": "Add an allergy", "entry": " Penicillin ", "entry_note": "Hives"})
    A("SBAR same allergy again", "sbar_nurse", "ed-rn", {"rule": "N06", "option": "Add an allergy", "entry": "LATEX", "entry_note": "Rash"})
    A("SBAR NKA refused (409)", "sbar_nurse", "ed-rn", {"rule": "N06", "option": "No known allergies confirmed"})
    A("remove unknown (409)", "sbar_nurse", "ed-rn", {"rule": "N06", "option": "Add an allergy", "entry": "Aspirin", "remove": True})
    A("remove not a boolean (422)", "sbar_nurse", "ed-rn", {"rule": "N06", "option": "Add an allergy", "entry": "Latex", "remove": "yes"})
    A("remove on a plain choice is no bypass (409)", "sbar_nurse", "ed-rn",
      {"rule": "N06", "option": "No known allergies confirmed", "remove": True})
    resolve(rec, u, "sbar_nurse", "ed-rn", None, "patient sign")
    rec("patient sign", "POST", f"{u}/sbar_nurse/sign", persona="ed-rn", body={"callback": "x4471"})
    A("remove penicillin after signing", "sbar_nurse", "ed-rn", {"rule": "N06", "option": "Add an allergy", "entry": "penicillin", "remove": True})
    A("I-PASS DNR/DNI after the SBAR was signed", "ipass_physician", "ed-md", {"rule": "P04", "option": "DNR/DNI"})
    for p in ("ed-rn", "ed-md", "hosp-md") + ((recv["id"],) if recv else ()):
        rec(f"patient case as {p}", "GET", u, persona=p)
        rec(f"patient board as {p}", "GET", "/api/board", persona=p)
    A("remove the last", "ipass_physician", "ed-md", {"rule": "P05", "option": "Add an allergy", "entry": "Latex", "remove": True})
    rec("patient case after removing all", "GET", u, persona="ed-rn")


def canceled_scenarios(rec, ds):
    """The admissions cancel_admissions() canceled: as they were just before the cancel, at it and after it, and an
    edit before (allowed while boarding) and after (refused). The one an older job retracted is canceled throughout."""
    iso = lambda t: t.isoformat()  # noqa: E731
    rec("canceled reset", "POST", "/api/demo/reset")
    for csn in sorted(c for c, rows in ds.versions.items() if any(r["retracted_ts"] for r in rows)):
        at, u = ds.versions[csn][0]["retracted_ts"], f"/api/handoffs/{csn}"
        for label, t in (("before", at - timedelta(minutes=1)), ("at", at), ("after", at + timedelta(minutes=1))):
            for p in ("ed-rn", "viewer"):
                rec(f"canceled {csn} {label}: board {p}", "GET", "/api/board", at=iso(t), persona=p)
                rec(f"canceled {csn} {label}: case {p}", "GET", u, at=iso(t), persona=p)
            rec(f"canceled {csn} {label}: analytics", "GET", "/api/analytics", at=iso(t))
        for label, t in (("before", at - timedelta(minutes=1)), ("after (409)", at + timedelta(minutes=1))):
            rec(f"canceled {csn} edit {label}", "POST", f"{u}/sbar_nurse/edit", at=iso(t), persona="ed-rn",
                body={"element": "E25", "text": "Plan before the cancel"})
    rec("canceled reset end", "POST", "/api/demo/reset")


def assignment_scenarios(rec, ds, default, boarding, units):
    """Who owns each patient: Take, Assign and Release with every refusal, the colleague list per role, owners on the
    board and the handoff at earlier and later times, an action outside the assignment, a stale owner, the shift
    change banner around a shift change, and Take over (the list, taking some, then the rest, and its refusals)."""
    from assignment import next_shift

    iso = lambda t: t.isoformat()  # noqa: E731
    rec("owner reset", "POST", "/api/demo/reset")
    # The demo's starting owners, written at start up and again by Reset.
    warn = next_shift(default, [(7, 0), (19, 0)]) - timedelta(minutes=29)
    for p in ("ed-rn", "ed-md-2", "ed-rn-2"):
        rec(f"seed board {p}", "GET", "/api/board", persona=p)
        rec(f"seed board {p} before the shift change", "GET", "/api/board", at=iso(warn), persona=p)
    rec("seed takeover from Jordan", "GET", "/api/takeover", at=iso(warn), persona="ed-rn-2", params={"colleague": "ed-rn"})
    for p in ("ed-rn", "ed-rn-2", "ed-md", "ed-md-2", "hosp-md", "viewer", units[0]["id"]):
        rec(f"colleagues {p}", "GET", "/api/colleagues", persona=p)
    rec("colleagues unknown persona (400)", "GET", "/api/colleagues", persona="nobody")
    if len(boarding) < 3:
        return
    a, b, c = boarding[0], boarding[1], boarding[2]
    own = lambda r: f"/api/handoffs/{r['csn']}/owner"  # noqa: E731
    O = lambda name, r, persona, body, at=None: rec(f"owner {name}", "POST", own(r), at=at, persona=persona, body=body)  # noqa: E731
    O("take", a, "ed-rn", {"kind": "claim"})
    O("take again (409)", a, "ed-rn", {"kind": "claim"})
    O("release by a colleague (409)", a, "ed-rn-2", {"kind": "release"})
    O("assign to someone unknown (409)", a, "ed-rn", {"kind": "assign", "to": "nobody"})
    O("assign with no colleague (409)", a, "ed-rn", {"kind": "assign"})
    O("assign to yourself (409)", a, "ed-rn", {"kind": "assign", "to": "ed-rn"})
    O("assign to another role (409)", a, "ed-rn", {"kind": "assign", "to": "ed-md"})
    O("assign", a, "ed-rn", {"kind": "assign", "to": "ed-rn-2"})
    O("assign to the owner (409)", a, "ed-rn", {"kind": "assign", "to": "ed-rn-2"})
    O("take from a colleague", a, "ed-rn", {"kind": "claim"})
    O("physician takes", a, "ed-md", {"kind": "claim"})
    O("release", a, "ed-rn", {"kind": "release"})
    O("release when no one owns it (409)", a, "ed-rn", {"kind": "release"})
    for p in ("viewer", "hosp-md", units[0]["id"]):
        O(f"take as {p} (409)", a, p, {"kind": "claim"})
    rec("owner take unknown patient (409)", "POST", "/api/handoffs/0000000/owner", persona="ed-rn", body={"kind": "claim"})
    for name, body in (("unknown kind (409)", {"kind": "steal"}), ("kind missing (422)", {}), ("kind not text (422)", {"kind": 1}),
                       ("to not text (422)", {"kind": "assign", "to": 5}), ("body a list (422)", [1])):
        O(name, a, "ed-rn", body)
    rec("owner GET (405)", "GET", own(a), persona="ed-rn")
    O("invalid at (400)", a, "ed-rn", {"kind": "claim"}, at="nope")
    O("unknown persona (400)", a, "nobody", {"kind": "claim"})
    # Owners on the board and the handoff, for each role.
    O("nurse takes b", b, "ed-rn", {"kind": "claim"})
    O("second nurse takes c", c, "ed-rn-2", {"kind": "claim"})
    O("second physician takes b", b, "ed-md-2", {"kind": "claim"})
    recv = next((u for u in units if u["unit"] == b["destination"]["pending_unit"] and u["facility"] == b["facility"]), units[0])
    for p in ("ed-rn", "ed-rn-2", "ed-md", "ed-md-2", "hosp-md", "viewer", recv["id"]):
        rec(f"owners board {p}", "GET", "/api/board", persona=p)
    rec("owners case b", "GET", f"/api/handoffs/{b['csn']}", persona="ed-rn")
    rec("owners case b (receiver)", "GET", f"/api/handoffs/{b['csn']}", persona=recv["id"])
    # Acting on a colleague's patient is allowed and recorded as outside the assignment.
    url = f"/api/handoffs/{b['csn']}"
    rec("off assignment: edit by the second nurse", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn-2",
        body={"element": "E25", "text": "Checked by a colleague"})
    rec("on assignment: edit by the owner", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn",
        body={"element": "E25", "text": "Checked by the owner"})
    rec("off assignment: physician on an unowned template", "POST", f"{url}/custom_sea58/edit", persona="ed-md",
        body={"element": "E25", "text": "Physician note"})
    rec("off assignment: case", "GET", url, persona="ed-rn")
    rec("owners analytics after acting outside the assignment", "GET", "/api/analytics")
    # The clock: an hour earlier no one owns anything yet, and an assignment cannot go before one recorded.
    rec("owners board an hour earlier", "GET", "/api/board", at=iso(default - timedelta(hours=1)), persona="ed-rn")
    rec("owners case an hour earlier", "GET", url, at=iso(default - timedelta(hours=1)), persona="ed-rn")
    O("take earlier than one recorded (409)", b, "ed-rn-2", {"kind": "claim"}, at=iso(default - timedelta(minutes=5)))
    # A stale owner: the patient who boards longest, taken at their first draft. An edit by the owner two hours later
    # keeps them the owner past the limit from the take; with no action for the limit the patient counts as
    # Unassigned, and taking it again makes it theirs again.
    rec("owner reset 2", "POST", "/api/demo/reset")
    long = max((e for e in ds.enc.values() if e["admit_decision_ts"] and ds.versions.get(e["csn"])
                and not any(r["status"] == "retracted" for r in ds.versions[e["csn"]])),
               key=lambda e: ((e["ed_departure_ts"] or ds.end) - ds.versions[e["csn"]][0]["at"], e["csn"]))
    t1, lu = ds.versions[long["csn"]][0]["at"] + timedelta(minutes=1), f"/api/handoffs/{long['csn']}"
    rec("stale owner: take", "POST", f"{lu}/owner", at=iso(t1), persona="ed-rn", body={"kind": "claim"})
    rec("stale owner: edit by the owner", "POST", f"{lu}/sbar_nurse/edit", at=iso(t1 + timedelta(hours=2)), persona="ed-rn",
        body={"element": "E25", "text": "Rechecked by the owner"})
    for label, t in (("13h", t1 + timedelta(hours=13)), ("14h after the take", t1 + timedelta(hours=14, minutes=1)),
                     ("14h after the edit", t1 + timedelta(hours=16, minutes=1))):
        rec(f"stale owner board {label}", "GET", "/api/board", at=iso(t), persona="ed-rn")
        rec(f"stale owner case {label}", "GET", lu, at=iso(t), persona="ed-rn")
    t16 = iso(t1 + timedelta(hours=16, minutes=1))
    rec("stale owner: release when stale", "POST", f"{lu}/owner", at=t16, persona="ed-rn-2", body={"kind": "release"})
    rec("stale owner: take again", "POST", f"{lu}/owner", at=t16, persona="ed-rn", body={"kind": "claim"})
    rec("stale owner board after taking again", "GET", "/api/board", at=t16, persona="ed-rn")
    # The shift change banner: before, inside and after the warning window, for patients boarding through it.
    rec("owner reset 3", "POST", "/api/demo/reset")
    nxt = next_shift(default, [(7, 0), (19, 0)])
    t0 = nxt - timedelta(minutes=45)
    _, sb = rec("shift: board", "GET", "/api/board", at=iso(t0), persona="ed-rn")
    through = [r for r in sb["rows"] if r["phase"] == "boarding" and (ds.enc[r["csn"]]["ed_departure_ts"] or ds.end) > nxt]
    for r in through[:3]:
        O(f"shift: take {r['csn']}", r, "ed-rn", {"kind": "claim"}, at=iso(t0))
    # One handoff signed and then changed (sign again), one signed, the rest drafts.
    for i, r in enumerate(through[:2]):
        u = f"/api/handoffs/{r['csn']}"
        resolve(rec, u, "sbar_nurse", "ed-rn", iso(t0), f"shift {i}")
        rec(f"shift {i}: sign", "POST", f"{u}/sbar_nurse/sign", at=iso(t0), persona="ed-rn", body={"callback": "x4471"})
        if i == 0:
            rec(f"shift {i}: edit after signing", "POST", f"{u}/sbar_nurse/edit", at=iso(t0), persona="ed-rn",
                body={"element": "E25", "text": "Changed after signing"})
    c = through[3] if len(through) > 3 else c
    for m in (45, 31, 30, 10, 0, -1):
        for p in ("ed-rn", "ed-md", "viewer"):
            rec(f"shift banner {m} min before {p}", "GET", "/api/board", at=iso(nxt - timedelta(minutes=m)), persona=p)
    # Take over at the shift change.
    tt = iso(nxt - timedelta(minutes=10))
    T = lambda name, persona, params=None, at=tt: rec(f"takeover {name}", "GET", "/api/takeover", at=at, persona=persona, params=params)  # noqa: E731
    P = lambda name, persona, body, at=tt: rec(f"take over {name}", "POST", "/api/takeover", at=at, persona=persona, body=body)  # noqa: E731
    T("list", "ed-rn-2")
    _, view = T("from Jordan", "ed-rn-2", {"colleague": "ed-rn"})
    T("from yourself (409)", "ed-rn-2", {"colleague": "ed-rn-2"})
    T("from someone unknown (409)", "ed-rn-2", {"colleague": "nobody"})
    T("from another role (409)", "ed-md", {"colleague": "ed-rn"})
    T("physician list", "ed-md")
    for p in ("hosp-md", "viewer", units[0]["id"]):
        T(f"as {p} (409)", p)
    T("unknown persona (400)", "nobody")
    T("an hour earlier", "ed-rn-2", {"colleague": "ed-rn"}, iso(nxt - timedelta(minutes=70)))
    csns = [r["csn"] for r in view.get("rows", [])]
    for name, body in (("no patients (409)", {"colleague": "ed-rn", "csns": []}),
                       ("patients not a list (422)", {"colleague": "ed-rn", "csns": "x"}),
                       ("patient not text (422)", {"colleague": "ed-rn", "csns": [1]}),
                       ("colleague missing (422)", {"csns": csns}), ("body a list (422)", [1]),
                       ("unknown patient (409)", {"colleague": "ed-rn", "csns": ["0000000"]}),
                       ("a patient they do not own (409)", {"colleague": "ed-rn", "csns": [c["csn"]]}),
                       ("from yourself (409)", {"colleague": "ed-rn-2", "csns": csns})):
        P(name, "ed-rn-2", body)
    P("as the viewer (409)", "viewer", {"colleague": "ed-rn", "csns": csns})
    P("some", "ed-rn-2", {"colleague": "ed-rn", "csns": csns[:1] * 2})
    P("the same again (409)", "ed-rn-2", {"colleague": "ed-rn", "csns": csns[:1]})
    P("earlier than one recorded (409)", "ed-rn-2", {"colleague": "ed-rn", "csns": csns[:1]}, iso(nxt - timedelta(minutes=20)))
    P("the rest", "ed-rn-2", {"colleague": "ed-rn", "csns": csns[1:]})
    for p in ("ed-rn", "ed-rn-2"):
        rec(f"take over after: board {p}", "GET", "/api/board", at=tt, persona=p)
    if csns:
        rec("take over after: case", "GET", f"/api/handoffs/{csns[0]}", at=tt, persona="ed-rn")
    T("after: list", "ed-rn-2", {"colleague": "ed-rn"})
    # Analytics with handovers on both sides of the shift change, a signature by an owner, and after the week.
    if csns:
        P("after the change", "ed-rn", {"colleague": "ed-rn-2", "csns": csns[:1]}, iso(nxt + timedelta(minutes=40)))
    for label, t in (("before the change", tt), ("after the change", iso(nxt + timedelta(hours=1))), ("end", iso(ds.end))):
        rec(f"owners analytics {label}", "GET", "/api/analytics", at=t)
    rec("owner reset end", "POST", "/api/demo/reset")


def task_scenarios(rec, ds, default, units):
    """Tasks: the action list turned into tasks on signing (owner and due mapping, Now, a time of day today and
    tomorrow, duplicates in one list, signing again, a new item after a change, an addendum), tasks added by hand,
    every change and Undo with each refusal, who has the patient (outside the role, the viewer), the replay clock,
    overdue, the Tasks page for each role and scope with its groups, the board counts and the handoff activity."""
    iso = lambda t: t.isoformat()  # noqa: E731
    from data import TZ
    rec("tasks reset", "POST", "/api/demo/reset")
    _, b = rec("tasks board before", "GET", "/api/board", persona="ed-rn")
    # A patient the demo seed gave Jordan (ed-rn) and Kai (ed-md-2), boarding now.
    p = next(r for r in b["rows"] if r["phase"] == "boarding" and (r["owners"]["ed_nurse"] or {}).get("you")
             and r["owners"]["ed_physician"])
    csn = p["csn"]
    url, tu = f"/api/handoffs/{csn}", f"/api/handoffs/{csn}/tasks"
    recv = next(u for u in units if u["unit"] == p["destination"]["pending_unit"] and u["facility"] == p["facility"])
    other_unit = next(u for u in units if u["id"] != recv["id"])
    # The demo seed: a few tasks by the seeded owners, one in every Tasks page group at the default time.
    rec("tasks seeded", "GET", tu, persona="ed-rn")
    for persona in ("ed-rn", "ed-md-2"):
        rec(f"tasks page seeded {persona}", "GET", "/api/tasks", persona=persona)
        rec(f"tasks page seeded {persona} an hour earlier", "GET", "/api/tasks", at=iso(default - timedelta(hours=1)),
            persona=persona)
    resolve(rec, url, "sbar_nurse", "ed-rn", None, "tasks")
    hour = default.astimezone(TZ).hour
    later, earlier = f"{(hour + 1) % 24:02d}:30", f"{(hour - 1) % 24:02d}:00"
    items = [{"text": "Repeat lactate", "owner": "RN", "due": later},
             {"text": "Update the family", "owner": "Hospital Medicine team", "due": "Now"},
             {"text": "Check glucose", "owner": "Cardiology", "due": "ongoing"},
             {"text": "Review imaging", "owner": "MD/RN", "due": ""},
             {"text": "Recheck potassium", "owner": "physician", "due": earlier},
             {"text": "  repeat   LACTATE ", "owner": "nurse", "due": "08:00"},
             {"text": "Give the first antibiotic dose", "owner": "Pharmacy/Nursing", "due": "STAT"},
             {"text": "Consult orthopedics", "owner": "Orthopedics", "due": f"by {later}"},
             {"text": "Daily weights", "owner": "Observation Medicine", "due": "daily"},
             {"text": "Start the infusion", "owner": "GI service", "due": f"At {earlier}"},
             {"text": "Speech therapy swallow screen", "owner": "speech therapy", "due": "Immediately"}]
    rec("tasks write the action list", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn", body={"element": "E24", "items": items})
    rec("tasks sign", "POST", f"{url}/sbar_nurse/sign", persona="ed-rn", body={"callback": "x4471"})
    _, after = rec("tasks after signing", "GET", tu, persona="ed-rn")
    rec("tasks sign again (no new tasks)", "POST", f"{url}/sbar_nurse/sign", persona="ed-rn", body={"callback": "x4471"})
    rec("tasks add an item to the action list", "POST", f"{url}/sbar_nurse/edit", persona="ed-rn",
        body={"element": "E24", "items": items + [{"text": "Hold heparin before the procedure", "owner": "MD", "due": "now"}]})
    rec("tasks sign after the change (one new task)", "POST", f"{url}/sbar_nurse/sign", persona="ed-rn", body={"callback": "x4471"})
    # The physician's handoff turns its own action list into tasks; the same text on another template is a new task.
    resolve(rec, url, "ipass_physician", "ed-md-2", None, "tasks physician")
    rec("tasks physician action list", "POST", f"{url}/ipass_physician/edit", persona="ed-md-2",
        body={"element": "E24", "items": [{"text": "Repeat lactate", "owner": "team", "due": "Now"}]})
    rec("tasks physician sign", "POST", f"{url}/ipass_physician/sign", persona="ed-md-2", body={"callback": "x2210"})
    # Added by hand.
    add = lambda name, persona, body, at=None: rec(f"tasks add {name}", "POST", tu, at=at, persona=persona, body=body)  # noqa: E731
    add("for the nurse", "ed-rn", {"text": "Bring glasses from home", "role": "nurse"})
    add("for a colleague, due soon", "ed-rn", {"text": "Order a repeat ECG", "to": "ed-md", "due": iso(default + timedelta(minutes=45))})
    add("overdue", "ed-rn", {"text": "Send the urine sample", "role": "nurse", "due": iso(default - timedelta(hours=1))})
    add("for the receiving nurse", "ed-rn", {"text": "Set up telemetry", "to": recv["id"], "role": "nurse",
                                             "due": iso(default + timedelta(hours=5))})
    for name, persona, body in (("text missing (422)", "ed-rn", {"role": "nurse"}),
                                ("text not text (422)", "ed-rn", {"text": 5, "role": "nurse"}),
                                ("due not text (422)", "ed-rn", {"text": "x", "role": "nurse", "due": 5}),
                                ("body a list (422)", "ed-rn", [1]),
                                ("text blank (409)", "ed-rn", {"text": "   ", "role": "nurse"}),
                                ("text too long (409)", "ed-rn", {"text": "x" * 2001, "role": "nurse"}),
                                ("no role (409)", "ed-rn", {"text": "x"}),
                                ("unknown role (409)", "ed-rn", {"text": "x", "role": "pharmacist"}),
                                ("unknown colleague (409)", "ed-rn", {"text": "x", "to": "nobody"}),
                                ("colleague of another unit (409)", "ed-rn", {"text": "x", "to": other_unit["id"]}),
                                ("colleague in another role (409)", "ed-rn", {"text": "x", "to": "ed-md", "role": "nurse"}),
                                ("the viewer (409)", "viewer", {"text": "x", "role": "nurse"}),
                                ("due not a time (409)", "ed-rn", {"text": "x", "role": "nurse", "due": "tomorrow"}),
                                ("unknown persona (400)", "nobody", {"text": "x", "role": "nurse"})):
        add(name, persona, body)
    add("invalid at (400)", "ed-rn", {"text": "x", "role": "nurse"}, "nope")
    rec("tasks add unknown patient (409)", "POST", "/api/handoffs/0000000/tasks", persona="ed-rn", body={"text": "x", "role": "nurse"})
    rec("tasks get unknown patient (404)", "GET", "/api/handoffs/0000000/tasks", persona="ed-rn")
    rec("tasks get other unit (404)", "GET", tu, persona=other_unit["id"])
    rec("tasks get invalid at (400)", "GET", tu, at="nope", persona="ed-rn")
    rec("tasks get unknown persona (400)", "GET", tu, persona="nobody")
    _, now = rec("tasks after adding", "GET", tu, persona="ed-rn")
    ids = {}
    for t in now["tasks"]:
        ids.setdefault(t["text"], t["id"])          # the nurse handoff's "Repeat lactate", not the physician's
    ch = lambda name, persona, tid, body, at=None: rec(f"tasks change {name}", "POST", f"/api/tasks/{tid}", at=at,  # noqa: E731
                                                       persona=persona, body=body)
    lac, fam, glass, ecg = ids["Repeat lactate"], ids["Update the family"], ids["Bring glasses from home"], ids["Order a repeat ECG"]
    ch("done", "ed-rn", lac, {"kind": "done"})
    ch("done again (409)", "ed-rn", lac, {"kind": "done"})
    ch("not needed when done (409)", "ed-rn", lac, {"kind": "not_needed", "reason": "x"})
    ch("reassign when done (409)", "ed-rn", lac, {"kind": "reassign", "role": "physician"})
    ch("undo done", "ed-rn", lac, {"kind": "undo"})
    ch("undo again (409)", "ed-rn", lac, {"kind": "undo"})
    ch("undo with nothing to undo (409)", "ed-rn", glass, {"kind": "undo"})
    ch("not needed without a reason (409)", "ed-rn", fam, {"kind": "not_needed"})
    ch("not needed blank reason (409)", "ed-rn", fam, {"kind": "not_needed", "reason": "  "})
    ch("not needed reason too long (409)", "ed-rn", fam, {"kind": "not_needed", "reason": "x" * 201})
    ch("not needed", "ed-md-2", fam, {"kind": "not_needed", "reason": " Family already updated by the team "})
    ch("undo not needed", "ed-md-2", fam, {"kind": "undo"})
    ch("not needed again", "ed-md-2", fam, {"kind": "not_needed", "reason": "Done at the bedside"})
    ch("reassign to the physician", "ed-rn", glass, {"kind": "reassign", "role": "physician"})
    ch("reassign to the same role (409)", "ed-rn", glass, {"kind": "reassign", "role": "physician"})
    ch("reassign without a role (409)", "ed-rn", glass, {"kind": "reassign"})
    ch("reassign to a colleague", "ed-rn", glass, {"kind": "reassign", "to": "ed-rn-2"})
    ch("reassign to the same colleague (409)", "ed-rn", glass, {"kind": "reassign", "to": "ed-rn-2"})
    ch("reassign to an unknown colleague (409)", "ed-rn", glass, {"kind": "reassign", "to": "nobody"})
    ch("undo reassign", "ed-rn", glass, {"kind": "undo"})
    ch("undo after an undo (409)", "ed-rn", glass, {"kind": "undo"})
    ch("due later", "ed-rn", ecg, {"kind": "due", "due": iso(default + timedelta(hours=3))})
    ch("due the same (409)", "ed-rn", ecg, {"kind": "due", "due": iso(default + timedelta(hours=3))})
    ch("due not a time (409)", "ed-rn", ecg, {"kind": "due", "due": "soon"})
    ch("due cleared", "ed-rn", ecg, {"kind": "due"})
    ch("due cleared again (409)", "ed-rn", ecg, {"kind": "due", "due": None})
    ch("undo due", "ed-rn", ecg, {"kind": "undo"})
    for name, persona, tid, body in (("kind missing (422)", "ed-rn", lac, {}), ("kind not text (422)", "ed-rn", lac, {"kind": 1}),
                                     ("reason not text (422)", "ed-rn", lac, {"kind": "not_needed", "reason": ["x"]}),
                                     ("unknown kind (409)", "ed-rn", lac, {"kind": "delete"}),
                                     ("reopen is Undo (409)", "ed-rn", lac, {"kind": "reopen"}),
                                     ("unknown task (409)", "ed-rn", 999999, {"kind": "done"}),
                                     ("not a task id (409)", "ed-rn", "abc", {"kind": "done"}),
                                     ("by the viewer (409)", "viewer", lac, {"kind": "done"}),
                                     ("unknown persona (400)", "nobody", lac, {"kind": "done"}),
                                     ("other unit's nurse (409)", other_unit["id"], lac, {"kind": "done"})):
        ch(name, persona, tid, body)
    rec("tasks change GET (405)", "GET", f"/api/tasks/{lac}", persona="ed-rn")
    # Outside the role: the receiving nurse and the admitting physician act while the patient boards.
    ch("by the receiving nurse while boarding (outside role)", recv["id"], ids["Check glucose"], {"kind": "done"})
    ch("by the admitting physician while boarding (outside role)", "hosp-md", ids["Review imaging"],
       {"kind": "reassign", "to": "hosp-md"})
    for persona in ("ed-rn", "ed-md", recv["id"], "hosp-md", "viewer"):
        rec(f"tasks panel as {persona}", "GET", tu, persona=persona)
    # The replay clock: nothing before a task already recorded; earlier views show what was true then.
    ch("an hour earlier (409)", "ed-rn", lac, {"kind": "done"}, iso(default - timedelta(hours=1)))
    add("an hour earlier (409)", "ed-rn", {"text": "x", "role": "nurse"}, iso(default - timedelta(hours=1)))
    rec("tasks panel an hour earlier", "GET", tu, at=iso(default - timedelta(hours=1)), persona="ed-rn")
    rec("tasks case with the activity", "GET", url, persona="ed-rn")
    rec("tasks board with counts", "GET", "/api/board", persona="ed-rn")
    rec("tasks board three hours on (overdue)", "GET", "/api/board", at=iso(default + timedelta(hours=3)), persona="ed-rn")
    # The Tasks page for every role, both scopes, and later times (the groups move).
    for persona in ("ed-rn", "ed-rn-2", "ed-md", "ed-md-2", recv["id"], "hosp-md", "viewer"):
        rec(f"tasks page {persona}", "GET", "/api/tasks", persona=persona)
        rec(f"tasks page {persona} all", "GET", "/api/tasks", persona=persona, params={"scope": "all"})
    for h in (1, 3, 6, 13):
        rec(f"tasks page ed-rn {h} hours on", "GET", "/api/tasks", at=iso(default + timedelta(hours=h)), persona="ed-rn")
    rec("tasks page an hour earlier", "GET", "/api/tasks", at=iso(default - timedelta(hours=1)), persona="ed-rn")
    rec("tasks page unknown scope (400)", "GET", "/api/tasks", persona="ed-rn", params={"scope": "theirs"})
    rec("tasks page unknown persona (400)", "GET", "/api/tasks", persona="nobody")
    rec("tasks page invalid at (400)", "GET", "/api/tasks", at="nope", persona="ed-rn")
    rec("tasks page POST (405)", "POST", "/api/tasks", persona="ed-rn", body={})
    # After arrival the receiving nurse and the admitting physician have the patient; the ED is outside its role.
    dep = ds.enc[csn]["ed_departure_ts"]
    if dep:
        t = iso(dep + timedelta(minutes=30))
        rec("tasks panel after arrival (receiver)", "GET", tu, at=t, persona=recv["id"])
        rec("tasks panel after arrival (ED nurse)", "GET", tu, at=t, persona="ed-rn")
        ch("after arrival: receiver done", recv["id"], lac, {"kind": "done"}, t)
        ch("after arrival: ED nurse (outside role)", "ed-rn", ids["Recheck potassium"], {"kind": "not_needed", "reason": "Left the ED"}, t)
        ch("after arrival: admitting physician", "hosp-md", ecg, {"kind": "reassign", "role": "nurse"}, t)
        for persona in (recv["id"], "hosp-md", "ed-rn"):
            rec(f"tasks page after arrival {persona}", "GET", "/api/tasks", at=t, persona=persona)
        rec("tasks board after arrival (receiver)", "GET", "/api/board", at=t, persona=recv["id"])
    # An addendum after departure: signing it adds its action list items as tasks.
    adds = [r for rows in ds.versions.values() for r in rows if r["status"] == "addendum" and r["template"] == "sbar_nurse"]
    if adds:
        a = adds[0]
        t, au = iso(a["at"] + timedelta(minutes=1)), f"/api/handoffs/{a['csn']}"
        resolve(rec, au, "sbar_nurse", "ed-rn", t, "tasks addendum")
        rec("tasks addendum action list", "POST", f"{au}/sbar_nurse/edit", at=t, persona="ed-rn",
            body={"element": "E24", "items": [{"text": "Repeat troponin at 6 hours", "owner": "Nursing", "due": "Now"}]})
        rec("tasks addendum sign", "POST", f"{au}/sbar_nurse/sign", at=t, persona="ed-rn", body={"callback": "x4471"})
        rec("tasks addendum panel", "GET", f"{au}/tasks", at=t, persona="ed-rn")
    rec("tasks reset end", "POST", "/api/demo/reset")
    rec("tasks after reset", "GET", tu, persona="ed-rn")


def seen_scenarios(rec, ds, default, units):
    """What changed since you looked: the demo's seeded looks on the board and the case, a look from the handoff and
    from the board (Mark as seen), changes by another persona after a look at the same clock time (owner, sign off with
    the tasks it made, a task by hand, a task done, edits and attestations), own changes left out, a look at an earlier
    time (allowed, and not moving the later mark), arrival, a cancel, a severity change, the viewer, and every
    refusal."""
    iso = lambda t: t.isoformat()  # noqa: E731
    rec("seen reset", "POST", "/api/demo/reset")
    for persona in ("ed-rn", "ed-md-2", "ed-md", "viewer"):
        rec(f"seen board seeded {persona}", "GET", "/api/board", persona=persona)
    _, b = rec("seen board", "GET", "/api/board", persona="ed-rn")
    for r in [r for r in b["rows"] if not r["since"]["first_time"]]:
        rec(f"seen case seeded {r['csn']}", "GET", f"/api/handoffs/{r['csn']}", persona="ed-rn")
        rec(f"seen case seeded {r['csn']} as Kai", "GET", f"/api/handoffs/{r['csn']}", persona="ed-md-2")
    # A boarding patient no one owns or looked at yet: the first open, then the look the page records.
    p = next(r for r in b["rows"] if r["phase"] == "boarding" and r["since"]["first_time"] and not any(r["owners"].values()))
    csn = p["csn"]
    url, su = f"/api/handoffs/{csn}", f"/api/handoffs/{csn}/seen"
    _, first = rec("seen first open", "GET", url, persona="ed-rn")
    rec("seen look from the handoff", "POST", su, at=first["since"]["at"], persona="ed-rn", body={"source": "handoff"})
    rec("seen look Kai", "POST", su, persona="ed-md-2", body={"source": "handoff"})
    rec("seen open again (nothing new)", "GET", url, persona="ed-rn")
    # Others act at the same clock time; Jordan's own Take and task are left out of Jordan's list.
    rec("seen Jordan takes", "POST", f"{url}/owner", persona="ed-rn", body={"kind": "claim"})
    rec("seen Jordan adds a task", "POST", f"{url}/tasks", persona="ed-rn", body={"text": "Mine", "role": "nurse"})
    rec("seen Kai takes", "POST", f"{url}/owner", persona="ed-md-2", body={"kind": "claim"})
    resolve(rec, url, "ipass_physician", "ed-md-2", None, "seen")
    rec("seen Kai action list", "POST", f"{url}/ipass_physician/edit", persona="ed-md-2",
        body={"element": "E24", "items": [{"text": "Repeat lactate", "owner": "MD", "due": "Now"},
                                          {"text": "Call the family", "owner": "team", "due": ""}]})
    rec("seen Kai signs", "POST", f"{url}/ipass_physician/sign", persona="ed-md-2", body={"callback": "x2210"})
    rec("seen Kai adds a task", "POST", f"{url}/tasks", persona="ed-md-2", body={"text": "By hand", "role": "physician"})
    _, tl = rec("seen tasks", "GET", f"{url}/tasks", persona="ed-rn")
    mine = next(t["id"] for t in tl["tasks"] if t["text"] == "Mine")
    rec("seen Kai marks a task done", "POST", f"/api/tasks/{mine}", persona="ed-md-2", body={"kind": "done"})
    rec("seen board Jordan after", "GET", "/api/board", persona="ed-rn")
    rec("seen case Jordan after", "GET", url, persona="ed-rn")
    rec("seen case Kai after", "GET", url, persona="ed-md-2")
    rec("seen case Robin (never opened)", "GET", url, persona="ed-rn-2")
    # A look at an earlier time: allowed, and the mark at the default time stays.
    for h in (1, 0.5):
        rec(f"seen look {h} h earlier", "POST", su, at=iso(default - timedelta(hours=h)), persona="ed-rn",
            body={"source": "board"})
    rec("seen board Jordan after earlier looks", "GET", "/api/board", persona="ed-rn")
    rec("seen board Jordan 45 min earlier", "GET", "/api/board", at=iso(default - timedelta(minutes=45)), persona="ed-rn")
    # Mark as seen at the default time: nothing new.
    rec("seen mark as seen", "POST", su, persona="ed-rn", body={"source": "board"})
    rec("seen board after mark as seen", "GET", "/api/board", persona="ed-rn")
    rec("seen look viewer", "POST", su, persona="viewer", body={"source": "board"})
    rec("seen board viewer", "GET", "/api/board", persona="viewer")
    # Arrival on the unit, after a look before it.
    dep = ds.enc[csn]["ed_departure_ts"]
    if dep:
        rec("seen look before arrival", "POST", su, at=iso(dep - timedelta(minutes=5)), persona="ed-md", body={"source": "handoff"})
        rec("seen case after arrival", "GET", url, at=iso(dep + timedelta(minutes=30)), persona="ed-md")
        rec("seen board after arrival", "GET", "/api/board", at=iso(dep + timedelta(minutes=30)), persona="ed-md")
    # A cancel, after a look before it.
    for c in sorted(c for c, rows in ds.versions.items() if any(r["retracted_ts"] for r in rows))[:2]:
        t = ds.versions[c][0]["retracted_ts"]
        rec(f"seen look before cancel {c}", "POST", f"/api/handoffs/{c}/seen", at=iso(t - timedelta(minutes=5)),
            persona="viewer", body={"source": "handoff"})
        rec(f"seen case after cancel {c}", "GET", f"/api/handoffs/{c}", at=iso(t + timedelta(minutes=5)), persona="viewer")
    # A severity change between two versions: its own line, and no version lists the severity item too.
    def sev(c, t):
        return ((elements(next(iter(ds.latest_at(c, t).values()))["draft"]).get("E07") or {}).get("narrative") or {}).get("value")
    for e in sorted(ds.enc.values(), key=lambda e: e["csn"]):
        pts = sorted({r["at"] for r in ds.versions.get(e["csn"], [])})
        pair = next(((a, z) for a, z in zip(pts, pts[1:]) if e["admit_decision_ts"] and e["admit_decision_ts"] <= a
                     and (not e["ed_departure_ts"] or e["ed_departure_ts"] > z) and sev(e["csn"], a) and sev(e["csn"], z)
                     and sev(e["csn"], a) != sev(e["csn"], z)), None)
        if pair:
            a, z = pair
            rec("seen look before a severity change", "POST", f"/api/handoffs/{e['csn']}/seen",
                at=iso(a + timedelta(minutes=1)), persona="hosp-md", body={"source": "handoff"})
            rec("seen case after a severity change", "GET", f"/api/handoffs/{e['csn']}", at=iso(z + timedelta(minutes=1)),
                persona="hosp-md")
            break
    # Refusals.
    rec("seen unknown source (409)", "POST", su, persona="ed-rn", body={"source": "tasks"})
    rec("seen missing source (422)", "POST", su, persona="ed-rn", body={})
    rec("seen source not text (422)", "POST", su, persona="ed-rn", body={"source": 1})
    rec("seen no body (422)", "POST", su, persona="ed-rn")
    rec("seen unknown patient (409)", "POST", "/api/handoffs/0000000/seen", persona="ed-rn", body={"source": "board"})
    rec("seen invalid at (400)", "POST", su, at="nope", persona="ed-rn", body={"source": "board"})
    rec("seen unknown persona (400)", "POST", su, persona="nobody", body={"source": "board"})
    rec("seen GET (405)", "GET", su, persona="ed-rn")
    other = next(u for u in units if (u["facility"], u["unit"]) != (p["facility"], p["destination"]["pending_unit"]))
    rec("seen other unit (409)", "POST", su, persona=other["id"], body={"source": "board"})
    rec("seen reset end", "POST", "/api/demo/reset")
    rec("seen board after reset", "GET", "/api/board", persona="ed-rn")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", help="an existing fixtures folder; omit to build from the stand in world")
    ap.add_argument("--out", default=str(ROOT / "app/fixtures/parity"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.fixtures:
        for f in FILES:
            shutil.copyfile(Path(a.fixtures) / f, out / f)
    else:
        build_fixtures(out)
    print(f"canceled admissions: {', '.join(cancel_admissions(out))}")
    c, main_mod = load_app(out)
    items = scenarios(c, main_mod)
    (out / "templates.json").write_text(json.dumps(c.get("/api/templates").json()))
    (out / "scenarios.json").write_text(json.dumps({"fixtures": "given" if a.fixtures else "stand in world",
                                                    "normalized": ["/api/health now"], "scenarios": items}))
    print(f"{len(items)} scenarios written to {out}")


if __name__ == "__main__":
    main()
