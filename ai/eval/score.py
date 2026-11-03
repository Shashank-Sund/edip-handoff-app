"""Score one handoff draft (the generate.draft output for one template) against the expected facts.

Metrics per draft:
  deterministic      each deterministic element the template shows equals the expected fact: allergies set, code
                     status, isolation, latest vitals (time and values), pending set, critical labs set (and every
                     listed lab exists in truth with that value), ED medications set, destination
  missed_critical    expected critical results, preliminary pending results, allergies, code status and isolation
                     absent from the draft (safety misses, counted per item)
  narrative          statements with at least one source that the truth supports, unknown source ids (ids that do
                     not resolve to a truth record of the encounter at as_of), unsupported numbers reported by the
                     validator, lexical contradictions (a lab value or drug dose in the text that the truth never
                     reported), and hallucinated medication and lab names (names in the text that the encounter's
                     truth does not know; the vocabulary is the world's drug and lab names plus a common ED list)
  severity           draft severity against the reference severity
  completeness       the template's completeness score and blocking rules as reported in the draft
"""
import re

from . import expected as X

# Draft element id to the expected fact it must match.
DETERMINISTIC = {"E09": "allergies", "E08": "code_status", "E10": "isolation", "E14": "vitals", "E16": "pending",
                 "E15": "critical_labs", "E18": "ed_meds", "E04": "destination"}
NARRATIVE = ("E06", "E07", "E11", "E20", "E24", "E25", "E26")
VITAL_COMPARE = ("hr", "sbp", "dbp", "rr", "spo2", "temp", "pain")
# Common ED drugs, so an invented medication is caught even when no patient in the world received it.
COMMON_DRUGS = {"warfarin", "heparin", "enoxaparin", "insulin", "vancomycin", "ceftriaxone", "piperacillin-tazobactam",
                "cefepime", "metronidazole", "azithromycin", "levofloxacin", "amoxicillin", "doxycycline", "morphine",
                "hydromorphone", "fentanyl", "ketorolac", "acetaminophen", "ibuprofen", "ondansetron", "metoclopramide",
                "lorazepam", "midazolam", "haloperidol", "diazepam", "furosemide", "metoprolol", "diltiazem",
                "amiodarone", "labetalol", "nitroglycerin", "aspirin", "clopidogrel", "ticagrelor", "atorvastatin",
                "lisinopril", "amlodipine", "hydralazine", "norepinephrine", "epinephrine", "vasopressin", "dopamine",
                "phenylephrine", "albuterol", "ipratropium", "methylprednisolone", "prednisone", "dexamethasone",
                "pantoprazole", "famotidine", "octreotide", "tranexamic", "alteplase", "tenecteplase", "naloxone",
                "flumazenil", "potassium chloride", "magnesium sulfate", "calcium gluconate", "sodium bicarbonate",
                "dextrose", "thiamine", "levetiracetam", "phenytoin", "ketamine", "propofol", "etomidate",
                "rocuronium", "succinylcholine", "oxycodone", "gabapentin", "apixaban", "rivaroxaban", "digoxin"}
NUMBER = r"(\d+(?:\.\d+)?)"
LAB_ALIASES = {"hgb": "hemoglobin", "hb": "hemoglobin", "plt": "platelets", "cr": "creatinine", "creat": "creatinine",
               "k": "potassium", "na": "sodium", "bun": "bun", "glu": "glucose", "wbc": "wbc", "trop": "troponin",
               "lactate": "lactate", "inr": "inr"}


def elements(draft):
    """Element id to the element item of the draft (across sections)."""
    out = {}
    for sec in draft.get("sections", []):
        for e in sec.get("elements", []):
            out[e["id"]] = e
    return out


def statements(draft):
    """Narrative statements of the draft: (element id, statement dict with text and sources)."""
    out = []
    for eid, item in elements(draft).items():
        if item.get("kind") != "narrative" or not item.get("narrative"):
            continue
        n = item["narrative"]
        rows = n["items"] if isinstance(n.get("items"), list) else [n]
        for s in rows:
            if isinstance(s, dict) and (s.get("text") or "").strip():
                out.append((eid, s))
    return out


def _src_parts(sid):
    return sid.split(":") if isinstance(sid, str) else []


def source_known(sid, idx, exp):
    """Does a cited source id resolve to a truth record of this encounter at as_of?"""
    p = _src_parts(sid)
    if not p:
        return False
    fam = p[0]
    if fam == "enc":
        return len(p) == 2 and p[1] in ("identity", "visit", "destination")
    if fam == "patient":
        return p[1:] == ["social"]
    if fam == "flag":
        return len(p) == 2 and ((p[1] == "code_status" and exp["code_status"]) or (p[1] == "isolation" and exp["isolation"]))
    if fam == "allergy":
        return len(p) == 2 and p[1].isdigit() and 1 <= int(p[1]) <= len(exp["allergies"])
    if fam in ("problem", "homemed"):
        return len(p) == 2 and bool(p[1])
    if fam == "vital":
        rest = ":".join(p[1:])
        return rest in idx["vital"] or X.iso(X.ts(rest)) in idx["vital"]      # ids carry local times with offsets
    if fam == "obs":
        if len(p) != 4:
            return False
        order, code, status = p[1], p[2], p[3]
        return (order, code, status) in idx["obs"] or (order, code, None) in idx["obs"] and any(
            o == order and s == status for o, _, s in idx["obs"])
    if fam == "med":
        return len(p) == 3 and (p[1], p[2]) in idx["med"]
    if fam == "note":
        return len(p) == 2 and p[1] in idx["note"]
    return False


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm(v):
    """Comparable value: numbers as a rounded float string (the FHIR feed sends 25 where HL7 sends 25.0)."""
    f = _num(v)
    return f"{round(f, 3):g}" if f is not None else ("" if v is None else str(v))


def _same(a, b):
    """Equal values, numerically when both parse as numbers."""
    fa, fb = _num(a), _num(b)
    if fa is not None and fb is not None:
        return abs(fa - fb) < 0.051
    return (a in (None, "") and b in (None, "")) or str(a) == str(b)


def compare_deterministic(eid, data, exp):
    """(ok, reason) for one deterministic element against the expected facts."""
    fact = DETERMINISTIC[eid]
    if fact == "allergies":
        got = sorted({(a.get("allergen") or "").lower() for a in (data or [])})
        want = sorted(a.lower() for a in exp["allergies"])
        return got == want, f"allergies draft {got} expected {want}"
    if fact in ("code_status", "isolation"):
        got = (data or {}).get("value") if isinstance(data, dict) else None
        want = exp[fact]
        return (got or None) == (want or None), f"{fact} draft {got!r} expected {want!r}"
    if fact == "vitals":
        want = exp["vitals"]
        got = data if isinstance(data, dict) else None
        if not want and not got:
            return True, ""
        if bool(want) != bool(got):
            return False, f"vitals draft {'present' if got else 'absent'} expected {'present' if want else 'absent'}"
        if X.ts(got.get("taken_ts")) != X.ts(want["taken_ts"]):     # same instant, whatever the offset
            return False, f"latest vitals time draft {got.get('taken_ts')} expected {want['taken_ts']}"
        bad = [k for k in VITAL_COMPARE if want.get(k) is not None and not _same(got.get(k), want.get(k))]
        return not bad, "vital values differ: " + ", ".join(f"{k} draft {got.get(k)} expected {want.get(k)}" for k in bad)
    if fact == "pending":
        got = {(p[1], p[2]) for p in (_src_parts(x.get("src")) for x in (data or [])) if len(p) == 4}
        want = {(r["order_id"], r["code"]) for r in exp["pending"]["preliminary"]}
        # A component the feed shows as incomplete is fine when the truth results it after as_of.
        later = {(r["order_id"], r["code"]) for r in exp["results"]}
        extra = {g for g in got - want if g in later}
        ok = want <= got and not extra
        return ok, f"pending draft {sorted(got)} expected {sorted(want)}"
    if fact == "critical_labs":
        rows = [(x, _src_parts(x.get("src"))) for x in (data or [])]
        got_crit = {(p[1], p[2], _norm(x.get("value"))) for x, p in rows if len(p) == 4 and x.get("flag") in X.CRITICAL_FLAGS}
        want_crit = {(r["order_id"], r["code"], _norm(r["value"])) for r in exp["critical"]}
        truth_abn = {(r["order_id"], r["code"], _norm(r["value"]), r["status"]) for r in exp["abnormal"]}
        truth_abn_loinc = {(r["order_id"], r["loinc"], _norm(r["value"]), r["status"]) for r in exp["abnormal"]}
        wrong = [(p[1], p[2], x.get("value")) for x, p in rows if len(p) == 4
                 and (p[1], p[2], _norm(x.get("value")), p[3]) not in truth_abn | truth_abn_loinc]
        ok = got_crit == want_crit and not wrong
        why = []
        if got_crit != want_crit:
            why.append(f"critical draft {sorted(got_crit)} expected {sorted(want_crit)}")
        if wrong:
            why.append(f"labs not in truth {wrong}")
        return ok, "; ".join(why)
    if fact == "ed_meds":
        got = {(p[1], p[2], x.get("action"), str(_num(x.get("dose")) if _num(x.get("dose")) is not None else x.get("dose")), X.iso(X.ts(x.get("taken_ts"))))
               for x, p in ((x, _src_parts(x.get("src"))) for x in (data or [])) if len(p) == 3}
        want = {(m["order_id"], str(m["line"]), m["action"], str(_num(m["dose"]) if _num(m["dose"]) is not None else m["dose"]), X.iso(X.ts(m["taken_ts"])))
                for m in exp["ed_meds"]}
        return got == want, f"ED meds draft minus expected {sorted(got - want)}, expected minus draft {sorted(want - got)}"
    if fact == "destination":
        d = data or {}
        want = X.visible_location(exp["destination"])
        if (d.get("unit") and d.get("unit") != "ED") or exp["destination"]["status"] == "inpatient":
            got = (d.get("unit"), d.get("room"), d.get("bed"))
        else:
            got = (d.get("pending_unit"), d.get("pending_room"), d.get("pending_bed"))
        loc_ok = tuple(x or None for x in got) == tuple(x or None for x in want)
        lvl_ok = not d.get("level_of_care") or not exp["destination"].get("level_of_care") or \
            d["level_of_care"] == exp["destination"]["level_of_care"]
        return loc_ok and lvl_ok, f"destination draft {got} level {d.get('level_of_care')} expected {want} level {exp['destination'].get('level_of_care')}"
    return True, ""


def missed_critical(els, exp):
    """Expected safety items absent from the draft, counted per item (only for elements the template shows)."""
    out = {}
    if "E15" in els:
        got = {(p[1], p[2]) for p in (_src_parts(x.get("src")) for x in (els["E15"].get("data") or [])) if len(p) == 4}
        out["critical_results"] = sum(1 for r in exp["critical"] if (r["order_id"], r["code"]) not in got)
    if "E16" in els:
        got = {(p[1], p[2]) for p in (_src_parts(x.get("src")) for x in (els["E16"].get("data") or [])) if len(p) == 4}
        out["pending_results"] = sum(1 for r in exp["pending"]["preliminary"] if (r["order_id"], r["code"]) not in got)
    if "E09" in els:
        got = {(a.get("allergen") or "").lower() for a in (els["E09"].get("data") or [])}
        out["allergies"] = sum(1 for a in exp["allergies"] if a.lower() not in got)
    if "E08" in els:
        d = els["E08"].get("data") or {}
        out["code_status"] = int(bool(exp["code_status"]) and not d.get("value"))
    if "E10" in els:
        d = els["E10"].get("data") or {}
        out["isolation"] = int(bool(exp["isolation"]) and not d.get("value"))
    out["total"] = sum(out.values())
    return out


def _find_names(text, names):
    low = text.lower()
    return sorted({n for n in names if re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", low)})


def _values_after(text, name):
    pat = re.compile(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])\s*(?:of|is|was|at|to|now|:|=)?\s*" + NUMBER, re.I)
    return [float(m.group(1)) for m in pat.finditer(text)]


def _mask_strengths(text, drugs):
    """Blank out product strengths such as 'sodium chloride 0.9 %' or 'pantoprazole 40 mg/mL' so a lab scan never
    reads a strength as a lab value."""
    out = text
    for d in sorted(drugs, key=len, reverse=True):
        out = re.sub(r"(?<![a-z0-9])" + re.escape(d) + r"(?![a-z0-9])\s*" + NUMBER
                     + r"\s*(?:%|mg|g|mcg|units?|mL|mEq)(?:\s*/\s*(?:mL|kg|h|hr|min))?", " ", out, flags=re.I)
    return out


def _doses_after(text, drug):
    """Doses written after a drug name, converted to mg when written in g (1 g of acetaminophen is 1000 mg)."""
    pat = re.compile(r"(?<![a-z0-9])" + re.escape(drug) + r"(?![a-z0-9])\s*" + NUMBER + r"\s*(mg|g|mcg|units?|mL|mEq)\b", re.I)
    return [float(m.group(1)) * (1000 if m.group(2).lower() == "g" else 1) for m in pat.finditer(text)]


# Statements of fact; action lists and contingencies (E24, E25) name future drugs and threshold values, which are
# advice, so they are not checked for unknown names or contradicting values.
FACTUAL = ("E06", "E07", "E11", "E20")      # E26 disposition is a plan
CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")


def narrative_metrics(draft, exp, truth, lexicon, validation=None):
    rows = statements(draft)
    idx = X.source_index(truth, exp["csn"], exp["as_of"])
    supported, unknown, unsupported_numbers = 0, 0, 0
    text_all = []
    for eid, s in rows:
        ids = [i for i in (s.get("sources") or []) if isinstance(i, str)]
        good = [i for i in ids if source_known(i, idx, exp)]
        unknown += len(ids) - len(good)
        supported += bool(good)
        unsupported_numbers += len(s.get("unsupported_numbers") or [])
        if eid in FACTUAL:
            text_all.append(s.get("text") or "")
    text = CLOCK.sub(" ", " ".join(text_all))
    drug_lex, lab_lex = lexicon
    known_drugs, known_labs = truth.drug_names(exp["csn"]), truth.lab_names(exp["csn"])
    # A drug a note written by as_of plans ("Prednisone 40 mg daily x5 days") is charted, not invented.
    notes = " ".join(n["text"] for n in truth.notes.get(exp["csn"], []) if X.ts(n["created_at"]) <= idx["as_of"])
    known_drugs = known_drugs | set(_find_names(notes, drug_lex | COMMON_DRUGS))
    mentioned_drugs = _find_names(text, drug_lex | COMMON_DRUGS)
    mentioned_labs = _find_names(text, lab_lex)
    # A mention covered by a longer known name (sodium inside sodium chloride) is not a hallucination.
    halluc_drugs = [d for d in mentioned_drugs if d not in known_drugs and not any(d in k and d != k for k in known_drugs)]
    halluc_labs = [n for n in mentioned_labs if n not in known_labs and not any(n in k and n != k for k in known_labs)]
    contradictions = []
    values = truth.lab_values(exp["csn"])
    # Lab values are read from the text with drug product strengths masked out ("sodium chloride 0.9 %",
    # "pantoprazole 40 mg/mL"), so a strength is never read as a chloride or a lab value.
    masked = _mask_strengths(text, known_drugs | drug_lex | COMMON_DRUGS)
    for name in set(mentioned_labs) | {LAB_ALIASES[a] for a in _find_names(masked, set(LAB_ALIASES)) if LAB_ALIASES[a] in values}:
        if name not in values:
            continue
        aliases = [name] + [a for a, n in LAB_ALIASES.items() if n == name]
        for alias in aliases:
            for v in _values_after(masked, alias):
                if not any(abs(v - t) < 0.051 for t in values[name]):
                    contradictions.append(f"{name} {v:g} (truth {sorted(values[name])})")
    # Drug doses: allowed are the doses given, their total, and the strengths in the product name.
    doses, strengths = {}, {}
    for m in exp["ed_meds"]:
        g = X.generic_name(m.get("name") or "")
        if g and _num(m.get("dose")) is not None:
            doses.setdefault(g, set()).add(_num(m["dose"]))
            strengths.setdefault(g, set()).update(float(x) for x in re.findall(NUMBER, m.get("name") or ""))
    for drug, ds in doses.items():
        allowed = ds | strengths.get(drug, set()) | ({sum(ds)} if len(ds) > 1 else set())
        allowed |= {a * 1000 for a in allowed}            # truth doses recorded in g
        for v in _doses_after(text, drug):
            if not any(abs(v - a) < 0.051 for a in allowed):
                contradictions.append(f"{drug} {v:g} (truth {sorted(ds)})")
    report = validation or {}
    return {"statements": len(rows), "supported": supported,
            "support_share": round(supported / len(rows), 3) if rows else None,
            "unknown_sources": unknown + int(report.get("unknown_sources") or 0),
            "unsupported_numbers": max(unsupported_numbers, int(report.get("unsupported_numbers") or 0)),
            "contradictions": contradictions, "hallucinated_meds": halluc_drugs, "hallucinated_labs": halluc_labs}


def score_draft(draft, exp, truth, lexicon=None, validation=None):
    """Full score of one draft against the expected facts. lexicon: truth.world_lexicon() (computed once per run)."""
    lexicon = lexicon or truth.world_lexicon()
    els = elements(draft)
    det, reasons = {}, []
    for eid, fact in DETERMINISTIC.items():
        if eid not in els:
            continue
        ok, why = compare_deterministic(eid, els[eid].get("data"), exp)
        det[fact] = ok
        if not ok:
            reasons.append(f"{eid} {why}")
    missed = missed_critical(els, exp)
    narrative = narrative_metrics(draft, exp, truth, lexicon, validation)
    sev = draft.get("severity")
    completeness = draft.get("completeness") or {}
    for k, v in (("hallucinated_meds", narrative["hallucinated_meds"]), ("hallucinated_labs", narrative["hallucinated_labs"]),
                 ("contradictions", narrative["contradictions"])):
        if v:
            reasons.append(f"{k} {v}")
    if narrative["unknown_sources"]:
        reasons.append(f"unknown sources {narrative['unknown_sources']}")
    if missed["total"]:
        reasons.append("missed critical items " + ", ".join(f"{k} {v}" for k, v in missed.items() if k != "total" and v))
    return {"csn": exp["csn"], "template": draft.get("template"), "as_of": exp["as_of"], "scenario": exp["scenario_label"],
            "faults": exp["faults"], "deterministic": det,
            "deterministic_accuracy": round(sum(det.values()) / len(det), 3) if det else None,
            "missed_critical": missed, "narrative": narrative,
            "severity": {"draft": sev, "reference": exp["severity"], "agree": sev == exp["severity"],
                         "reference_reasons": exp["severity_reasons"]},
            "completeness": {"score": completeness.get("score"), "blocking": completeness.get("blocking") or []},
            "reasons": reasons}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def aggregate(results):
    """Per template and overall means, element level accuracy, and the worst cases with reasons."""
    def agg(rows):
        facts = sorted({f for r in rows for f in r["deterministic"]})
        return {"drafts": len(rows), "encounters": len({r["csn"] for r in rows}),
                "deterministic_accuracy": _mean([r["deterministic_accuracy"] for r in rows]),
                "element_accuracy": {f: _mean([float(r["deterministic"][f]) for r in rows if f in r["deterministic"]]) for f in facts},
                "missed_critical_total": sum(r["missed_critical"]["total"] for r in rows),
                "missed_critical_by_kind": {k: sum(r["missed_critical"].get(k, 0) for r in rows)
                                            for k in ("critical_results", "pending_results", "allergies", "code_status", "isolation")},
                "narrative_support_share": _mean([r["narrative"]["support_share"] for r in rows]),
                "unknown_sources": sum(r["narrative"]["unknown_sources"] for r in rows),
                "unsupported_numbers": sum(r["narrative"]["unsupported_numbers"] for r in rows),
                "contradictions": sum(len(r["narrative"]["contradictions"]) for r in rows),
                "hallucinated_meds": sum(len(r["narrative"]["hallucinated_meds"]) for r in rows),
                "hallucinated_labs": sum(len(r["narrative"]["hallucinated_labs"]) for r in rows),
                "severity_agreement": _mean([float(r["severity"]["agree"]) for r in rows if r["severity"]["draft"]]),
                "severity_confusion": _confusion(rows),
                "completeness_mean": _mean([r["completeness"]["score"] for r in rows]),
                "blocking_share": _mean([float(bool(r["completeness"]["blocking"])) for r in rows]),
                "blocking_rules": _count(x for r in rows for x in r["completeness"]["blocking"])}
    by = {}
    for r in results:
        by.setdefault(r["template"], []).append(r)
    worst = sorted(results, key=lambda r: ((r["deterministic_accuracy"] if r["deterministic_accuracy"] is not None else 1.0),
                                           -r["missed_critical"]["total"],
                                           -(len(r["narrative"]["hallucinated_meds"]) + len(r["narrative"]["hallucinated_labs"])
                                             + len(r["narrative"]["contradictions"]) + r["narrative"]["unknown_sources"]),
                                           -(0 if r["severity"]["agree"] else 1)))
    worst = [{"csn": r["csn"], "template": r["template"], "as_of": r["as_of"], "faults": r["faults"],
              "deterministic_accuracy": r["deterministic_accuracy"], "missed_critical": r["missed_critical"]["total"],
              "severity": r["severity"], "reasons": r["reasons"]} for r in worst if r["reasons"] or not r["severity"]["agree"]][:15]
    return {"overall": agg(results) if results else {}, "per_template": {t: agg(rs) for t, rs in sorted(by.items())}, "worst": worst}


def _count(xs):
    out = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def _confusion(rows):
    out = {}
    for r in rows:
        k = f"{r['severity']['reference']} as {r['severity']['draft']}"
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))
