"""Check the model's narrative against the chart packet before anyone sees it.

- the output parses as the expected JSON (tolerant of a fenced block or text around the object);
- every cited source id exists in the packet; unknown ids are removed and counted;
- every number in a statement appears in the packet values the statement cites (or anywhere in the packet,
  which is weaker and reported separately); unsupported numbers are flagged;
- severity is one of stable, watcher, unstable; confidence is clamped to 0 to 1;
- no em or en dashes.
"""
import json
import re

NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
# Numbers that set a threshold, a schedule or an instruction are advice, not claimed facts ("if SBP under 90",
# "q2h", "give 500 mL").
THRESHOLD = re.compile(r"(?:over|under|above|below|less than|more than|greater than|at least|within|every|q|give|bolus|"
                       r"start|titrate to|increase to|repeat in|recheck in|goal|target|[<>]=?)\s*"
                       r"(\d+(?:\.\d+)?)", re.I)
SEVERITY = {"stable", "watcher", "unstable"}
# Em and en dashes become commas and "to"; Unicode hyphens become plain hyphens (project writing rule).
DASHES = {0x2014: ", ", 0x2013: " to ", 0x2011: "-", 0x2010: "-"}


def parse(raw):
    if isinstance(raw, dict):
        return raw
    text = (raw or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


def _numbers(text):
    return set(NUM.findall(text or ""))


def _claimed(text):
    """Numbers stated as facts: all numbers minus thresholds and schedules."""
    return _numbers(text) - set(THRESHOLD.findall(text or ""))


def _values(sources, ids):
    out = set()
    for i in ids:
        out |= _numbers(json.dumps(sources.get(i, {})))
    return out


def check(raw, packet, wanted):
    issues, unknown = [], 0
    try:
        data = parse(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return {e: {"text": "", "sources": [], "confidence": 0.0} for e in wanted}, {"parse_error": str(exc)[:200]}
    sources = packet["_sources"]
    all_numbers = _numbers(json.dumps({k: v for k, v in packet.items() if k != "_sources"}, ensure_ascii=False))
    out = {}
    for e in wanted:
        d = data.get(e) or {}
        if not isinstance(d, dict):
            d = {"text": str(d)}
        statements = d.get("items") if isinstance(d.get("items"), list) else [d]
        for s in statements:
            ids = [i for i in (s.get("sources") or []) if isinstance(i, str)]
            good = [i for i in ids if i in sources]
            unknown += len(ids) - len(good)
            s["sources"] = good
            nums = _claimed(s.get("text"))
            cited = _values(sources, good)
            s["unsupported_numbers"] = sorted(n for n in nums if n not in cited and n not in all_numbers)
            s["weakly_supported_numbers"] = sorted(n for n in nums if n not in cited and n in all_numbers)
            if s.get("text"):
                s["text"] = s["text"].translate(DASHES)
        try:
            d["confidence"] = max(0.0, min(1.0, float(d.get("confidence", 0))))
        except (TypeError, ValueError):
            d["confidence"] = 0.0
        if e == "E07" and d.get("value") not in SEVERITY:
            issues.append(f"severity {d.get('value')!r} not in {sorted(SEVERITY)}")
            d["value"] = "watcher"
        out[e] = d
    flat = [s for d in out.values() for s in (d.get("items") if isinstance(d.get("items"), list) else [d])]
    report = {"unknown_sources": unknown,
              "unsupported_numbers": sum(len(s.get("unsupported_numbers", [])) for s in flat),
              "uncited_statements": sum(1 for s in flat if s.get("text") and not s.get("sources")),
              "issues": issues}
    return out, report
