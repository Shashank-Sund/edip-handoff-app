#!/usr/bin/env python3
"""Expected answers for the static demo's parity test: drives the real FastAPI app (mock mode) through a fixed
scenario list and writes every request with its status and response.

    .venv/bin/python app/fixtures/parity.py [--fixtures DIR] [--out app/fixtures/parity]

Without --fixtures it builds fixtures from the stand in world, the way tests/test_app.py does (no network, no
Databricks credentials; reproducible in CI). With --fixtures it uses an existing folder, for example the app's
own app/fixtures/data. The out folder gets the fixtures used (encounters.json, drafts.jsonl.gz, meta.json),
templates.json and scenarios.json; app/client/src/demo/parity.test.ts replays the same requests, in order,
through the in-browser implementation and compares every answer.

Normalized (legitimately nondeterministic): the `now` field of /api/health (the wall clock).
"""
import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
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
            body={"element": with_items["id"], "items": [{"text": "Repeat lactate", "owner": "RN", "due": "18:00"}, {"owner": "MD"}]})
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
                rec(f"physician attest {rule['rule']}", "POST", f"{url}/ipass_physician/attest", persona="ed-md",
                    body={"rule": rule["rule"], "option": opts[-1]})
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
    return rec.items


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
    c, main_mod = load_app(out)
    items = scenarios(c, main_mod)
    (out / "templates.json").write_text(json.dumps(c.get("/api/templates").json()))
    (out / "scenarios.json").write_text(json.dumps({"fixtures": "given" if a.fixtures else "stand in world",
                                                    "normalized": ["/api/health now"], "scenarios": items}))
    print(f"{len(items)} scenarios written to {out}")


if __name__ == "__main__":
    main()
