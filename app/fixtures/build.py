#!/usr/bin/env python3
"""Build the app's mock mode data from a rendered world.

    .venv/bin/python app/fixtures/build.py [--world synthetic/out/world] [--drafts drafts.jsonl] [--out app/fixtures/data]

With --drafts (gold.handoff_draft exported by ai/eval/export_drafts.py) the mock app shows the real model's
drafts; without it, drafts come from the deterministic mock model. Either way each draft gets the evidence
and trends the app shows next to it (edip_ai.generate.attachments), rebuilt from the world at its as_of.

Writes encounters.json (every ED encounter, final state), drafts.jsonl.gz (every draft version) and meta.json
(facilities, units, the replay window).
"""
import argparse
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "ai"), str(ROOT / "pipelines")]

from edip_ai import generate, llm, local  # noqa: E402
from edip_ai.points import select_points, triggers  # noqa: E402

STATE_FIELDS = ("csn", "facility", "mrn", "patient_name", "birth_date", "sex", "status", "patient_class", "unit", "room",
                "bed", "pending_unit", "pending_room", "pending_bed", "service", "level_of_care", "esi",
                "chief_complaint", "isolation", "code_status", "arrival_ts", "admit_decision_ts", "ed_departure_ts",
                "last_event_kind", "last_event_ts")


def _iso(v):
    return v.isoformat().replace("+00:00", "Z") if hasattr(v, "isoformat") else v


def _jsonish(v):
    return json.loads(v) if isinstance(v, str) and v[:1] in "[{" else v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--world", default=str(ROOT / "synthetic/out/world"))
    ap.add_argument("--drafts", help="exported gold.handoff_draft JSONL; omit to use the mock model")
    ap.add_argument("--out", default=str(ROOT / "app/fixtures/data"))
    a = ap.parse_args()
    world, out = Path(a.world), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    recs = local.records(world)
    try:
        clarity = local.clarity_tables(world)
    except (FileNotFoundError, IndexError):
        clarity = None
    adt = recs["adt"]
    by_csn = {}
    for r in adt:
        by_csn.setdefault(r["csn"], []).append(r)
    final = local.encounter_states(adt)
    encounters = [{k: _iso(s.get(k)) for k in STATE_FIELDS} for s in final.values()]
    (out / "encounters.json").write_text(json.dumps(sorted(encounters, key=lambda e: e["arrival_ts"] or "")))

    def packet_at(csn, as_of):
        state = local.encounter_states([r for r in by_csn.get(csn, []) if r["effective_ts"] <= as_of]).get(csn)
        return local.packet(recs, state, as_of, clarity) if state else None

    rows = []
    pts, retracted = select_points(triggers(recs), 60)
    if a.drafts:
        # Keep exported drafts that fall on a current generation point, with that point's status.
        status = {(c, t.replace(microsecond=0)): s for c, t, _, s in pts}
        seen, version = set(), {}
        for line in open(a.drafts, encoding="utf-8"):
            r = json.loads(line)
            as_of = local._ts(r["as_of"])
            st = status.get((r["csn"], as_of.replace(microsecond=0)))
            if st is None:
                continue
            seen.add((r["csn"], as_of.replace(microsecond=0)))
            p = packet_at(r["csn"], as_of)
            if p is None:
                continue
            d = _jsonish(r["draft"]) | generate.attachments(p)
            v = version[(r["csn"], r["template"])] = version.get((r["csn"], r["template"]), 0) + 1
            rows.append({"csn": r["csn"], "template": r["template"], "version": v, "status": st,
                         "trigger": r["trigger"], "as_of": _iso(as_of), "model": r.get("model"), "severity": r["severity"],
                         "score": float(r["score"]), "blocking": _jsonish(r["blocking"]) or [], "draft": d})
        missing = len(status) - len(seen)
        if missing:
            print(f"{missing} generation points have no exported draft (the export predates them); they are skipped")
    else:
        version = {}
        for csn, as_of, trigger, status in pts:
            p = packet_at(csn, as_of)
            if p is None:
                continue
            drafts, run = generate.draft(p, llm.MockLLM())
            for tid, d in drafts.items():
                v = version[(csn, tid)] = version.get((csn, tid), 0) + 1
                rows.append({"csn": csn, "template": tid, "version": v, "status": status, "trigger": trigger,
                             "as_of": _iso(as_of), "model": run["model"], "severity": d["severity"],
                             "score": d["completeness"]["score"], "blocking": d["completeness"]["blocking"], "draft": d})
    rows.sort(key=lambda r: (r["csn"], r["as_of"], r["template"], r["version"]))
    with gzip.open(out / "drafts.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")

    truth = world / "truth"
    facilities = [json.loads(x) for x in open(truth / "facilities.jsonl")]
    units = [{k: d[k] for k in ("dept_id", "facility", "code", "name", "level", "beds")}
             for d in map(json.loads, open(truth / "departments.jsonl")) if d.get("level") != "ED"]
    cfg = json.loads((truth / "config.json").read_text())
    meta = {"facilities": [{k: f[k] for k in ("code", "name")} for f in facilities], "units": units,
            "window": {"start": cfg["window_start"], "days": cfg["days"], "tz": "America/New_York"},
            "drafts_from": "model export" if a.drafts else "mock model", "models": sorted({r["model"] for r in rows})}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print(f"{len(encounters)} encounters, {len(rows)} drafts ({meta['drafts_from']}), {len(units)} units -> {out}")


if __name__ == "__main__":
    main()
