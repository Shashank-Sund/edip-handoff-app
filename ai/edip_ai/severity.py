"""Illness severity floor from structured data (I-PASS "I": stable, watcher, unstable).

The model reads the chart and may raise severity, never lower it below this floor: structured risk flags come
from rules, not from model text. Criteria (shared with the evaluation's reference in ai/eval/expected.py):

  unstable  an unaddressed critical result (latest value of that lab still critical), ICU level of care (or an ICU
            destination unit before the level is charted), or
            shock range vitals (SBP under 90, SpO2 under 90, HR over 130, RR over 30, GCS under 13)
  watcher   ESI 1 or 2, step down level of care, any critical result during the visit, abnormal latest vitals
            (HR over 110 or under 50, SBP under 100, RR over 24, SpO2 under 92, temp 38.3 or higher or under 36.0),
            or a preliminary result pending
  stable    none of the above
"""
UNSTABLE_VITALS = {"sbp": lambda v: v < 90, "spo2": lambda v: v < 90, "hr": lambda v: v > 130, "rr": lambda v: v > 30,
                   "gcs": lambda v: v < 13}
ABNORMAL_VITALS = {"hr": lambda v: v > 110 or v < 50, "sbp": lambda v: v < 100, "rr": lambda v: v > 24,
                   "spo2": lambda v: v < 92, "temp": lambda v: v >= 38.3 or v < 36.0}
RANK = {"stable": 0, "watcher": 1, "unstable": 2}


def floor(packet):
    """Returns (severity, reasons, source ids) computed from the chart packet."""
    reasons, srcs = [], []
    v = (packet.get("vitals") or {}).get("latest") or {}
    labs = packet.get("labs") or []
    critical = [x for x in labs if x.get("flag") in ("HH", "LL")]
    latest_by_name = {}
    for x in labs:
        latest_by_name[x.get("name")] = x               # labs are ordered by result time
    unaddressed = [x for x in critical if latest_by_name.get(x.get("name")) is x]
    dest = packet.get("destination") or {}
    level = dest.get("level_of_care")
    if not level and "ICU" in str(dest.get("pending_unit") or "").upper():
        # At the admit decision the feeds name the destination unit before the bed request sets the level of care;
        # a patient going to an ICU is at ICU level of care from that moment.
        level = "ICU"
    if unaddressed:
        reasons.append("unaddressed critical " + ", ".join(f"{x['name']} {x['value']}" for x in unaddressed))
        srcs += [x["src"] for x in unaddressed]
    if level == "ICU":
        reasons.append("ICU level of care")
        srcs.append(packet["destination"]["src"])
    for k, bad in UNSTABLE_VITALS.items():
        if isinstance(v.get(k), (int, float)) and bad(v[k]):
            reasons.append(f"{k} {v[k]:g}")
            srcs.append(v["src"])
    if reasons:
        return "unstable", reasons, sorted(set(srcs))
    esi = (packet.get("visit") or {}).get("esi")
    if esi in (1, 2):
        reasons.append(f"ESI {esi}")
        srcs.append(packet["visit"]["src"])
    if level == "STEPDOWN":
        reasons.append("step down level of care")
        srcs.append(packet["destination"]["src"])
    if critical:
        reasons.append("critical result during the visit")
        srcs += [x["src"] for x in critical]
    for k, bad in ABNORMAL_VITALS.items():
        if isinstance(v.get(k), (int, float)) and bad(v[k]):
            reasons.append(f"{k} {v[k]:g}")
            srcs.append(v["src"])
    if any(p.get("status") == "preliminary" for p in packet.get("pending") or []):
        reasons.append("preliminary result pending")
        srcs += [p["src"] for p in packet["pending"] if p.get("status") == "preliminary"]
    return ("watcher", reasons, sorted(set(srcs))) if reasons else ("stable", [], [])


def apply(narrative, packet):
    """Raise the model's severity to the floor when it is lower, keeping its text and citing the rule's facts."""
    sev, reasons, srcs = floor(packet)
    n = narrative.setdefault("E07", {"text": "", "sources": [], "confidence": 0.0})
    model = n.get("value") if n.get("value") in RANK else "stable"
    n["model_value"] = model
    n["floor"] = {"value": sev, "reasons": reasons}
    if RANK[sev] > RANK[model]:
        n["value"] = sev
        n["text"] = (f"{sev.capitalize()} by rule: {'; '.join(reasons)}." + (f" {n['text']}" if n.get("text") else ""))
        n["sources"] = sorted(set((n.get("sources") or []) + srcs))
    return narrative
