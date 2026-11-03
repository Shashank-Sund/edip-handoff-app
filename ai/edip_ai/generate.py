"""Generate handoff drafts for one encounter: packet, one model call, validation, rules, per template drafts."""
import time

from . import chart, prompts, rules, severity, templates, validate


def _deterministic(element, packet, el_defs):
    key = el_defs[element].get("packet")
    v = packet.get(key) if key else None
    if element == "E14":
        v = (v or {}).get("latest")
    if element in ("E03", "E04", "E28") and v:
        fields = {"E03": ("service", "admitting_id"), "E04": ("unit", "room", "bed", "pending_unit", "pending_room",
                                                              "pending_bed", "level_of_care"),
                  "E28": ("pending_unit", "pending_room", "pending_bed", "status")}[element]
        v = {k: v.get(k) for k in fields} | {"src": v.get("src")}
    if element == "E30" and packet.get("pain_meds"):
        v = {"score": (v or {}).get("score"), "last_analgesic": packet["pain_meds"][-1], "src": (v or {}).get("src")}
    return v


def attachments(packet):
    """What the app shows next to a draft: the time it was built for, the source records it may cite, and trends."""
    return {"as_of": packet.get("as_of"), "evidence": chart.evidence(packet), "trend": packet.get("_trend")}


def draft(packet, llm, template_ids=None):
    """Returns (drafts by template id, run record). The run record carries validation and usage."""
    el_defs = templates.elements()
    wanted = templates.narrative_elements()
    t0 = time.time()
    messages = prompts.build(packet)
    raw, usage = llm.complete(messages, packet)
    narrative, report = validate.check(raw, packet, wanted)
    if "parse_error" in report:          # usually a truncated answer: retry once with a larger output budget
        raw, usage2 = llm.complete(messages, packet, retry=True)
        narrative, report = validate.check(raw, packet, wanted)
        report["retried"] = True
        usage = {k: (usage.get(k) or 0) + (usage2.get(k) or 0) for k in ("prompt_tokens", "completion_tokens")}
    severity.apply(narrative, packet)        # the model may raise severity, never lower it below the rule floor
    drafts = {}
    context = attachments(packet)
    for tid, t in templates.templates().items():
        if template_ids and tid not in template_ids:
            continue
        results = rules.evaluate(t, packet, narrative, el_defs)
        sections = []
        for sec in t["sections"]:
            items = []
            for e in sec["elements"]:
                d = el_defs[e]
                item = {"id": e, "label": d["label"], "kind": d["kind"]}
                if d["kind"] == "narrative":
                    item["narrative"] = narrative.get(e)
                elif d["kind"] == "deterministic":
                    item["data"] = _deterministic(e, packet, el_defs)
                item["rules"] = [r for r in results if r["element"] == e]
                items.append(item)
            sections.append({"id": sec["id"], "label": sec["label"], "elements": items})
        drafts[tid] = {"template": tid, "name": t["name"], "audience": t["audience"], "sections": sections,
                       "rules": results, "completeness": rules.score(results),
                       "severity": (narrative.get("E07") or {}).get("value")} | context
    run = {"model": llm.name, "usage": usage, "validation": report, "seconds": round(time.time() - t0, 2),
           "prompt_chars": sum(len(m["content"]) for m in messages)}
    return drafts, run
