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
    """Mark two admissions canceled as the handoff job does (every version retracted, retracted_ts the cancel's time,
    the encounter's status canceled): one while boarding, one after its transfer and any addenda. The synthetic
    week's own canceled registrations never had a handoff, so without this no scenario shows a canceled case."""
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
        elif "arrived" not in picks and pts[-1] >= dep and pts[-1] + timedelta(minutes=30) < dep + timedelta(hours=11):
            picks["arrived"] = (e, pts[-1] + timedelta(minutes=30))
    for e, at in picks.values():
        e["status"] = "canceled"
        for d in drafts:
            if d["csn"] == e["csn"]:
                d["status"], d["retracted_ts"] = "retracted", at.isoformat().replace("+00:00", "Z")
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
    for m in ("config", "data", "logic", "personas", "workflow", "main"):
        sys.modules.pop(m, None)
    main = importlib.import_module("main")
    from fastapi.testclient import TestClient
    return TestClient(main.app), main


class Recorder:
    def __init__(self, client):
        self.c, self.items = client, []

    def __call__(self, name, method, path, at=None, persona=None, body=None):
        params = {"at": at} if at is not None else None
        headers = {"X-Demo-Persona": persona} if persona is not None else None
        r = self.c.request(method, path, params=params, headers=headers, json=body)
        data = r.json()
        if path == "/api/health":
            data["now"] = "<now>"
        self.items.append({"name": name, "method": method, "path": path, "at": at, "persona": persona, "body": body,
                           "status": r.status_code, "response": data})
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
    edit before (allowed while boarding) and after (refused)."""
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
    print(f"canceled admissions: {', '.join(cancel_admissions(out)) or 'none found'}")
    c, main_mod = load_app(out)
    items = scenarios(c, main_mod)
    (out / "templates.json").write_text(json.dumps(c.get("/api/templates").json()))
    (out / "scenarios.json").write_text(json.dumps({"fixtures": "given" if a.fixtures else "stand in world",
                                                    "normalized": ["/api/health now"], "scenarios": items}))
    print(f"{len(items)} scenarios written to {out}")


if __name__ == "__main__":
    main()
