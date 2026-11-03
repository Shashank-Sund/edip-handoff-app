"""Silver shaped records and chart packets built locally from a rendered world (no Spark), for tests, the
evaluation set and the app's mock mode. Mirrors the pipeline: both feeds parsed, deduplicated, encounter state
folded in event time order with nulls ignored."""
import json
from datetime import datetime, timezone
from pathlib import Path

from . import chart

KINDS = ("adt", "obs", "med", "note", "allergy", "flag")


def _ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if s else None


def records(world: Path, feeds=("fhir", "hl7")):
    """Parsed contract records from the rendered feeds, deduplicated as silver does."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "pipelines"))
    from parse_fhir import parse_fhir
    from parse_hl7 import parse_hl7
    out = {k: [] for k in KINDS}
    if "fhir" in feeds:
        for line in open(world / "fhir" / "events.jsonl", encoding="utf-8"):
            r = parse_fhir(json.loads(line))
            for k in KINDS:
                out[k] += r[k]
    if "hl7" in feeds:
        for line in open(world / "hl7" / "messages.jsonl", encoding="utf-8"):
            m = json.loads(line)
            received = datetime.fromisoformat(m["delivery_ts"].replace("Z", "+00:00")) if m.get("delivery_ts") else None
            r = parse_hl7((world / "hl7" / m["file"]).read_text(encoding="utf-8"), received)
            for k in KINDS:
                out[k] += r[k]
    keys = {"obs": ("csn", "category", "order_id", "code", "status", "observed_ts", "resulted_ts"),
            "med": ("csn", "order_id", "event", "line", "action"), "note": ("csn", "note_id", "status"),
            "allergy": ("mrn", "allergen"), "flag": ("csn", "kind", "value", "recorded_ts")}
    for k, ks in keys.items():
        seen, kept = set(), []
        for r in out[k]:
            key = tuple(r[x].replace(microsecond=0) if isinstance(r[x], datetime) else r[x] for x in ks)
            if key not in seen:
                seen.add(key)
                kept.append(r)
        out[k] = kept
    seen, adt = set(), []
    for r in out["adt"]:
        if (r["source"], r["msg_key"]) not in seen:
            seen.add((r["source"], r["msg_key"]))
            adt.append(r)
    out["adt"] = adt
    return out


STATUS = {"arrival": "in_ed", "pending_admit": "boarding", "bed_assigned": "boarding", "cancel_transfer": "boarding",
          "cancel_discharge": "in_ed", "cancel_admit": "canceled", "discharge": "discharged"}


def encounter_states(adt):
    """Fold ADT events per CSN in (effective, recorded) order, ignoring nulls, like the AUTO CDC SCD1 target."""
    states = {}
    for r in sorted((a for a in adt if a["csn"] and a["effective_ts"]), key=lambda a: (a["effective_ts"], a["recorded_ts"])):
        s = states.setdefault(r["csn"], {"csn": r["csn"]})
        for k, v in r.items():
            if v not in (None, "") and k not in ("source", "msg_key", "content_hash", "kind", "trigger"):
                s[k] = v
        if r.get("surviving_mrn"):
            s["mrn"] = r["surviving_mrn"]
        status = STATUS.get(r["kind"]) or ("inpatient" if r["kind"] == "transfer" and r.get("unit") != "ED" else None)
        if status:
            s["status"] = status
        if r["kind"] == "arrival":
            s["arrival_ts"] = r["effective_ts"]
        if r["kind"] == "pending_admit":
            s["admit_decision_ts"] = r["effective_ts"]
        if r["kind"] == "transfer" and r.get("unit") != "ED":
            s["ed_departure_ts"] = r["effective_ts"]
        if r["kind"] == "bed_unassigned":           # a release carries empty room and bed; nulls ignored would keep the old bed
            s["pending_room"], s["pending_bed"] = None, None
        s["last_event_kind"], s["last_event_ts"] = r["kind"], r["effective_ts"]
    return states


def packet(recs, state, now, clarity=None):
    """Chart packet for one encounter at time now; clarity: optional dict of Clarity rows by table."""
    csn, mrn = state["csn"], state.get("mrn")
    cl = clarity or {}
    pat = next((p for p in cl.get("PATIENT", []) if p.get("PAT_MRN_ID") == mrn), None)
    pat_id = pat.get("PAT_ID") if pat else None
    probs = [r for r in cl.get("PROBLEM_LIST", []) if r.get("PAT_ID") == pat_id]
    home = [r for r in cl.get("ORDER_MED", []) if r.get("PAT_ID") == pat_id and r.get("ORDERING_MODE_C_NAME") == "Outpatient"]
    return chart.build_packet(state, [o for o in recs["obs"] if o["csn"] == csn], [m for m in recs["med"] if m["csn"] == csn],
                              [n for n in recs["note"] if n["csn"] == csn], [a for a in recs["allergy"] if a["mrn"] == mrn],
                              [f for f in recs["flag"] if f["csn"] == csn], probs, home, pat, now=now)


def clarity_tables(world: Path, snapshot=None, tables=("PATIENT", "PROBLEM_LIST", "ORDER_MED")):
    base = world / "clarity"
    snap = snapshot or sorted(p.name for p in base.iterdir() if p.is_dir())[-1]
    return {t: [json.loads(line) for line in open(base / snap / f"{t}.jsonl", encoding="utf-8")] for t in tables}
