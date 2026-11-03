"""Generation points for the handoff generator: one definition shared by the job (jobs/handoff_job.py) and the
evaluation set (ai/eval/run_eval.py), so both generate at exactly the same moments.

A trigger row has csn, action (start, regenerate, finalize, retract), kind (the ADT kind, critical_result or
note_signed) and effective_ts. silver.handoff_events derives them in the pipeline; triggers() derives the same
rows locally from contract records, for tests and the evaluation set.
"""
from datetime import timedelta

# ADT kind to handoff action, as silver.handoff_events maps them (pipelines/edip_pipeline.py).
ADT_ACTION = {"pending_admit": "start", "class_change": "regenerate", "bed_assigned": "regenerate",
              "bed_unassigned": "regenerate", "cancel_transfer": "regenerate", "transfer": "finalize",
              "cancel_admit": "retract"}


def triggers(recs):
    """Trigger rows from contract records (local mirror of silver.handoff_events): ADT kinds, critical results and
    signed notes, deduplicated on (csn, kind, second); start and retract are keyed on the encounter alone."""
    rows = []
    for a in recs["adt"]:
        action = ADT_ACTION.get(a.get("kind"))
        if action and a.get("csn") and a.get("effective_ts"):
            rows.append({"csn": a["csn"], "action": action, "kind": a["kind"], "effective_ts": a["effective_ts"],
                         "reason": a["kind"]})
    for o in recs["obs"]:
        if o.get("flag") in ("HH", "LL") and o.get("csn") and o.get("resulted_ts"):
            rows.append({"csn": o["csn"], "action": "regenerate", "kind": "critical_result", "effective_ts": o["resulted_ts"],
                         "reason": f"critical {o.get('name')} {o.get('value')}", "observed_ts": o.get("observed_ts")})
    for n in recs["note"]:
        if n.get("status") == "Signed" and n.get("csn") and n.get("signed_ts"):
            rows.append({"csn": n["csn"], "action": "regenerate", "kind": "note_signed", "effective_ts": n["signed_ts"],
                         "reason": f"signed {n.get('note_type')}"})
    seen, out = set(), []
    for r in sorted(rows, key=lambda r: r["effective_ts"]):
        key = (r["csn"], r["kind"], None if r["action"] in ("start", "retract") else r["effective_ts"].replace(microsecond=0))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


ED_NOTE = "signed ED Provider Notes"


def collected_times(triggers, obs):
    """Fill observed_ts on critical result triggers from observation records (the job reads silver.handoff_events,
    which carries the result time only)."""
    seen = {(o.get("csn"), o.get("resulted_ts")): o.get("observed_ts") for o in obs if o.get("flag") in ("HH", "LL")}
    for t in triggers:
        if t["kind"] == "critical_result" and not t.get("observed_ts"):
            t["observed_ts"] = seen.get((t["csn"], t["effective_ts"]))
    return triggers


def _ed_addendum(t, transferred):
    """After the transfer, only ED information changes the ED handoff: an ED provider note signed late (F16) or a
    critical result collected before the transfer (F18). Inpatient notes and later transfers belong to the unit."""
    if t["kind"] == "note_signed":
        return t.get("reason") == ED_NOTE
    if t["kind"] == "critical_result":
        return t.get("observed_ts") is not None and t["observed_ts"] <= transferred
    return False


def select_points(triggers, backfill_interval_min=60):
    """(points, retracted): points are (csn, as_of, trigger kind, status) tuples. For each encounter with a start:
    the start, the transfer (status final), and regenerate triggers at least backfill_interval_min apart from the
    previous point (status draft). After the transfer only ED addenda regenerate (status addendum, not debounced:
    each is a change the receiving unit must see). An encounter with a retract gets no points and is returned in
    the retracted set. Triggers must be in effective_ts order."""
    by = {}
    for t in triggers:
        by.setdefault(t["csn"], []).append(t)
    points, retracted = [], set()
    gap = timedelta(minutes=backfill_interval_min)
    for csn, ts in by.items():
        if any(t["action"] == "retract" for t in ts):
            retracted.add(csn)
            continue
        if not any(t["action"] == "start" for t in ts):
            continue
        last, transferred = None, None
        for t in ts:
            if transferred is not None:
                if t["action"] == "regenerate" and _ed_addendum(t, transferred) and t["effective_ts"] > last:
                    points.append((csn, t["effective_ts"], t["kind"], "addendum"))
                    last = t["effective_ts"]
                continue
            if t["action"] == "start" and last is None or t["action"] == "finalize" or \
                    (last is not None and t["action"] == "regenerate" and t["effective_ts"] - last >= gap):
                points.append((csn, t["effective_ts"], t["kind"], "final" if t["action"] == "finalize" else "draft"))
                last = t["effective_ts"]
                if t["action"] == "finalize":
                    transferred = t["effective_ts"]
    return points, retracted
