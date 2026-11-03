"""Completeness rules from the template YAML, evaluated against the chart packet and the narrative.

Result per rule: status pass, missing (no data), stale (too old), attest (needs an attestation from the fixed
list), deferred (checked at sign or accept time in the app), with the evidence source ids.
"""


def _has(packet, narrative, element, el_defs):
    d = el_defs[element]
    if d["kind"] == "narrative":
        n = (narrative or {}).get(element) or {}
        return bool(n.get("text") or n.get("items")), n.get("sources", [])
    key = d.get("packet")
    if not key:
        return False, []
    v = packet.get(key)
    if element == "E03":
        v = (v or {}).get("service") or (v or {}).get("admitting_id")
    if element == "E28":
        v = (v or {}).get("pending_bed") or (v or {}).get("bed")
    if element == "E14":
        v = (v or {}).get("latest")
    if isinstance(v, dict):
        return bool({k: x for k, x in v.items() if k != "src" and x not in (None, "", [])}), [v.get("src")] if v.get("src") else []
    if isinstance(v, list):
        return bool(v), [x.get("src") for x in v if isinstance(x, dict) and x.get("src")]
    return v not in (None, ""), []


def evaluate(template, packet, narrative, el_defs):
    severity = ((narrative or {}).get("E07") or {}).get("value") or "watcher"
    out = []
    for r in template["rules"]:
        if r.get("when") and severity not in ("watcher", "unstable"):
            continue
        res = {"rule": r["id"], "element": r["element"], "level": r["level"], "label": el_defs[r["element"]]["label"]}
        if r.get("at"):
            res.update(status="deferred", evidence=[], message=f"Checked at {r['at']} in the app")
            out.append(res)
            continue
        ok, evidence = _has(packet, narrative, r["element"], el_defs)
        res["evidence"] = [e for e in evidence if e]
        if not ok:
            res["status"] = "attest" if r.get("attest") else "missing"
            res["attest_options"] = r.get("attest", [])
            res["message"] = f"{res['label']}: not documented"
        elif r["level"] == "stale":
            age = (packet.get("vitals") or {}).get("latest_age_min")
            limit = (r.get("max_age_min") or {}).get(severity, 60)
            res["status"] = "stale" if age is not None and age > limit else "pass"
            res["message"] = f"Last vitals {age} min ago (limit {limit} for {severity})" if res["status"] == "stale" else ""
        else:
            res["status"] = "pass"
            res["message"] = ""
        out.append(res)
    return out


def score(results):
    """Share of non deferred rules that pass; hard failures block sign off."""
    judged = [r for r in results if r["status"] != "deferred"]
    blocking = [r for r in judged if r["level"] in ("hard", "stale") and r["status"] != "pass"]
    return {"score": round(sum(r["status"] == "pass" for r in judged) / max(1, len(judged)), 3),
            "blocking": [r["rule"] for r in blocking]}
