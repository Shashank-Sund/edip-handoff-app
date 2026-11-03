"""Check the model's narrative against the chart packet before anyone sees it.

- the output parses as the expected JSON (tolerant of a fenced block or text around the object);
- every cited source id exists in the packet; unknown ids are removed and counted;
- every number in a statement appears in the packet values the statement cites (or anywhere in the packet,
  which is weaker and reported separately); numbers compare by value, a decimal may be a charted value rounded
  to the stated precision, and thresholds, schedules, clock times and durations are exempt; unsupported numbers
  are flagged for the clinician (a summed fluid total, for example, is not a charted value);
- severity is one of stable, watcher, unstable; confidence is clamped to 0 to 1;
- no em or en dashes.
"""
import json
import re

NUM = re.compile(r"(?<![\w.])(?<![A-Za-z]-)\d+(?:\.\d+)?")       # "COVID-19" and "IL-6" are names, not values
# Numbers that set a threshold, a schedule or an instruction are advice, not claimed facts ("if SBP under 90",
# "q2h", "give 500 mL").
THRESHOLD = re.compile(r"(?:over|under|above|below|exceeds?|less than|more than|greater than|at least|within|every|q|give|bolus|"
                       r"start|titrate to|increase to|repeat in|recheck in|goal|target|for|next|after|[<>\u2264\u2265\u2191\u2193]=?)\s*"
                       r"(\d+(?:\.\d+)?)(?:/(\d+(?:\.\d+)?))?", re.I)          # "BP over 180/105" exempts both
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


def _canon(n):
    """Compare numbers by value, so a charted 82.0 supports "82" and "3.00" supports "3.0"."""
    return f"{float(n):g}"


def _numbers(text):
    return {_canon(n) for n in NUM.findall(text or "")}


# Clock times ("19:47") and expected durations ("24 to 48 h", "48-72 h") are not charted values.
CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")
DURATION = re.compile(r"(\d+)(?:\s*(?:-|to)\s*(\d+))?\s*-?\s*(?:h|hr|hrs|hours?|days?|d)\b", re.I)


def _claimed(text):
    """Numbers stated as facts: all numbers minus thresholds, schedules, clock times and expected durations."""
    text = CLOCK.sub(" ", (text or "").translate(DASHES))       # "48\u201172 h" is a range like "48-72 h"
    exempt = {_canon(n) for m in THRESHOLD.findall(text) + DURATION.findall(text) for n in m if n}
    return _numbers(text) - exempt


def _in(n, pool):
    """n is in pool, or is a charted decimal rounded to the precision stated ("lactate 2.6" for 2.62)."""
    if n in pool:
        return True
    if "." not in n:
        return False
    dec, x = len(n.split(".")[1]), float(n)
    return any(round(float(v), dec) == x for v in pool if "." in v)


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
    all_numbers = _numbers(json.dumps({k: v for k, v in packet.items() if not k.startswith("_")}, ensure_ascii=False))
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
            s["unsupported_numbers"] = sorted(n for n in nums if not _in(n, cited) and not _in(n, all_numbers))
            s["weakly_supported_numbers"] = sorted(n for n in nums if not _in(n, cited) and _in(n, all_numbers))
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
