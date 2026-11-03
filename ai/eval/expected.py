"""Expected handoff facts from the synthetic truth tables (synthetic/out/world/truth or any edip_synth output).

For an admitted encounter and an as_of time, expected() says what a correct handoff generated at that moment
must contain. "Known by as_of" follows what the record system had documented at that time, which is what the
feeds carry:

  allergies       active allergies of the patient noted by as_of (an allergy added during the visit, F21, appears
                  from its noted time)
  code_status     latest code status activated by as_of, else None (F20: none until after transfer)
  isolation       latest isolation of the patient added by as_of, else None (F22: discovered late)
  vitals          latest validated vital set entered by as_of (the taken time is the set's time; a device value or
                  a set entered later is not yet known)
  results         latest status per (order, component) resulted by as_of (a correction, F19, replaces the final);
                  critical = flag HH or LL, abnormal = any flag
  pending         preliminary: components whose latest status by as_of is P or I (what the result feeds show);
                  unresulted_orders: lab and imaging orders placed by as_of with no result yet (F18); the reference
                  feeds carry no order placement messages, so a handoff built from them cannot list these
  ed_meds         administrations taken by as_of and before the ED departure
  destination     status, unit, room, bed, pending bed and level of care folded from the truth ADT events up to
                  as_of (F10 cancel transfer, F14 conversion, F15 bed release and reassignment)
  severity        reference illness severity, see reference_severity()

Reference severity rule (documented here, used by the severity agreement metric):
  unstable  any unaddressed critical result (latest status HH or LL by as_of with no later non critical result of
            the same lab), or ICU level of care, or latest vitals showing instability (SBP under 90, SpO2 under 90,
            HR over 130, RR over 30, GCS under 13)
  watcher   otherwise, when ESI is 1 or 2, or step down level of care, or any critical result during the visit,
            or latest vitals abnormal (HR over 110 or under 50, SBP under 100, RR over 24, SpO2 under 92,
            temperature 38.3 or higher or under 36.0), or a preliminary result still pending
  stable    everything else
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ADMIT_DISPOSITIONS = ("ADMIT_IP", "ADMIT_OBS")
CRITICAL_FLAGS = ("HH", "LL")
from edip_ai.severity import ABNORMAL_VITALS, UNSTABLE_VITALS  # noqa: E402  (one definition shared with the generator)
VITAL_KEYS = ("hr", "sbp", "dbp", "map", "rr", "temp", "spo2", "o2_device", "o2_flow", "pain", "gcs")
STATUS_RANK = {"I": 0, "P": 1, "F": 2, "C": 3}


def ts(s):
    """Truth timestamps are ISO strings in UTC; also accepts datetimes and a few export formats."""
    if s in (None, ""):
        return None
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    s = str(s).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(t, datetime) else t


def _rows(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class Truth:
    """The truth tables of one world, indexed by encounter and patient."""

    def __init__(self, truth_dir):
        d = Path(truth_dir)
        if (d / "truth").is_dir():
            d = d / "truth"
        self.dir = d
        self.encounters = {e["csn"]: e for e in _rows(d / "encounters.jsonl")}
        self.patients = {p["pat_id"]: p for p in _rows(d / "patients.jsonl")}
        self.adt = _by(_rows(d / "adt.jsonl"), "csn", key=lambda a: (a["effective_at"], a["seq"]))
        self.results = _by(_rows(d / "results.jsonl"), "csn", key=lambda r: (r["resulted_at"], r["line"]))
        self.orders = _by(_rows(d / "orders.jsonl"), "csn")
        self.bed_requests = _by(_rows(d / "bed_requests.jsonl"), "csn", key=lambda b: b["requested_at"]) \
            if (d / "bed_requests.jsonl").exists() else {}
        self.mar = _by(_rows(d / "mar.jsonl"), "csn", key=lambda m: m["taken_at"])
        self.med_orders = {m["order_med_id"]: m for m in _rows(d / "med_orders.jsonl")}
        self.vitals = _by(_rows(d / "vitals.jsonl"), "csn", key=lambda v: v["taken_at"])
        self.notes = _by(_rows(d / "notes.jsonl"), "csn")
        self.allergies = _by(_rows(d / "allergies.jsonl"), "pat_id", key=lambda a: a["noted_at"])
        self.code_status = _by(_rows(d / "code_status.jsonl"), "csn", key=lambda c: c["activated_at"])
        self.infections = _by(_rows(d / "infections.jsonl"), "pat_id", key=lambda i: i["added_at"])
        faults = _rows(d / "fault_manifest.jsonl") if (d / "fault_manifest.jsonl").exists() else []
        self.faults = _by(faults, "csn")
        self.mrn_to_pat = {p["mrn"]: p["pat_id"] for p in self.patients.values()}

    def admitted(self):
        """Encounters that had an admit decision (a pending_admit event), sorted by csn."""
        return sorted(c for c, evs in self.adt.items() if any(a["kind"] == "pending_admit" for a in evs))

    def patient_of(self, csn):
        return self.patients.get(self.encounters[csn]["pat_id"])

    def drug_names(self, csn):
        """Lower case generic names of every medication the encounter's truth knows: ED orders, home medications
        and allergens (an allergen is a legitimate medication mention)."""
        names = {generic_name(self.med_orders[m["order_med_id"]]["name"]) for m in self.mar.get(csn, [])
                 if m["order_med_id"] in self.med_orders}
        names |= {generic_name(m["name"]) for m in self.med_orders.values() if m["csn"] == csn}
        pat = self.patient_of(csn)
        if pat:
            names |= {generic_name(h.get("display", "")) for h in pat.get("home_meds", [])}
            names |= {a["allergen"].lower() for a in self.allergies.get(pat["pat_id"], [])}
        return {n for n in names if n}

    def lab_names(self, csn):
        """Lower case names of every result component and order of the encounter."""
        names = {r["name"].lower() for r in self.results.get(csn, [])}
        names |= {o["name"].lower() for o in self.orders.get(csn, []) if o["order_type"] in ("Lab", "Imaging")}
        return names

    def lab_values(self, csn):
        """Lower case component name to the set of numeric values ever reported for it in the encounter."""
        out = {}
        for r in self.results.get(csn, []):
            if r.get("numeric") is not None:
                out.setdefault(r["name"].lower(), set()).add(float(r["numeric"]))
        # A short name in text can mean a related test: "WBC" may be the urine WBC, "glucose" the point of care value.
        for short, related in (("wbc", "wbc, urine"), ("glucose", "glucose, poc")):
            if related in out:
                out.setdefault(short, set()).update(out[related])
        return out

    def world_lexicon(self):
        """Drug and lab names across the whole world, for the hallucination check."""
        drugs = {generic_name(m["name"]) for m in self.med_orders.values()}
        for p in self.patients.values():
            drugs |= {generic_name(h.get("display", "")) for h in p.get("home_meds", [])}
        labs = {r["name"].lower() for rows in self.results.values() for r in rows}
        return {n for n in drugs if len(n) > 3}, {n for n in labs if len(n) > 2}


def _by(rows, field, key=None):
    out = {}
    for r in rows:
        out.setdefault(r[field], []).append(r)
    if key:
        for v in out.values():
            v.sort(key=key)
    return out


def generic_name(name):
    """'piperacillin-tazobactam 4.5 g injection' to 'piperacillin-tazobactam'; 'sodium chloride 0.9 % bolus' to
    'sodium chloride': the words before the first number."""
    words = []
    for w in (name or "").lower().replace(",", " ").split():
        if w[0].isdigit() or w in ("%",):
            break
        words.append(w)
    return " ".join(words).strip()


def latest_results(truth, csn, as_of):
    """Latest status per (order, component) resulted by as_of; a correction (C) replaces the final (F)."""
    latest = {}
    for r in truth.results.get(csn, []):
        t = ts(r["resulted_at"])
        if t > as_of:
            continue
        k = (r["order_id"], r["lab"])
        cur = latest.get(k)
        if cur is None or (STATUS_RANK.get(r["status"], 0), t) >= (STATUS_RANK.get(cur["status"], 0), ts(cur["resulted_at"])):
            latest[k] = r
    return latest


def destination(truth, csn, as_of):
    """Location state folded from the truth ADT events up to as_of."""
    enc = truth.encounters[csn]
    d = {"status": "in_ed", "unit": None, "room": None, "bed": None, "pending_unit": None, "pending_room": None,
         "pending_bed": None, "level_of_care": None, "service": None, "admit_decision_ts": None, "ed_departure_ts": None}
    for a in truth.adt.get(csn, []):
        t = ts(a["effective_at"])
        if t > as_of:
            break
        k, det = a["kind"], a.get("details") or {}
        if k in ("arrival", "triage", "roomed", "provider", "registration", "cancel_discharge"):
            d.update(status="in_ed", unit=a["unit"], room=a["room"] or None, bed=a["bed"] or None)
        elif k == "pending_admit":
            d.update(status="boarding", pending_unit=det.get("pending_unit"), level_of_care=det.get("level_of_care"),
                     service=det.get("service"), admit_decision_ts=t)
        elif k == "bed_assigned":
            d.update(status="boarding", pending_unit=det.get("pending_unit") or d["pending_unit"],
                     pending_room=det.get("pending_room"), pending_bed=det.get("pending_bed"))
        elif k == "bed_unassigned":
            d.update(pending_room=None, pending_bed=None)
        elif k == "class_change":
            d.update(service=det.get("service") or d["service"])
            if det.get("conversion"):
                d["level_of_care"] = enc.get("level_of_care") or d["level_of_care"]
        elif k == "transfer":
            d.update(status="inpatient", unit=a["unit"], room=a["room"] or None, bed=a["bed"] or None, ed_departure_ts=t)
        elif k == "cancel_transfer":
            d.update(status="boarding", unit=a["unit"], room=a["room"] or None, bed=a["bed"] or None, ed_departure_ts=None)
        elif k == "cancel_admit":
            d.update(status="canceled")
        elif k == "discharge":
            d.update(status="discharged")
    # The bed request table is the authority on the pending bed: a release with a level change places a new
    # request for another unit at the same instant (F15), which the ADT release event alone does not show.
    reqs = [b for b in truth.bed_requests.get(csn, []) if ts(b["requested_at"]) <= as_of
            and not (b.get("deleted") and b.get("deleted_at") and ts(b["deleted_at"]) <= as_of)]
    if reqs and d["status"] in ("boarding", "in_ed"):
        b = reqs[-1]
        assigned = b.get("assigned_at") and ts(b["assigned_at"]) <= as_of
        d.update(pending_unit=b["unit"], pending_room=b.get("room") if assigned else None,
                 pending_bed=b.get("bed") if assigned else None)
    return d


def latest_vitals(truth, csn, as_of):
    """Latest validated vital set entered by as_of, plus the time of the latest set taken (for the staleness age)."""
    rows = [v for v in truth.vitals.get(csn, []) if v.get("validated", True) and ts(v["entered_at"]) <= as_of]
    if not rows:
        return None
    v = max(rows, key=lambda v: v["taken_at"])
    out = {k: v.get(k) for k in VITAL_KEYS}
    out["taken_ts"] = v["taken_at"]
    out["age_min"] = round((as_of - ts(v["taken_at"])).total_seconds() / 60)
    return out


def reference_severity(exp):
    """Reference severity and the reasons, from the expected facts (rule documented in the module docstring)."""
    reasons = []
    v = exp["vitals"] or {}
    if exp["critical_unaddressed"]:
        reasons.append("unaddressed critical result " + ", ".join(f"{c['name']} {c['value']}" for c in exp["critical_unaddressed"]))
    if exp["destination"].get("level_of_care") == "ICU":
        reasons.append("ICU level of care")
    for k, bad in UNSTABLE_VITALS.items():
        if isinstance(v.get(k), (int, float)) and bad(v[k]):
            reasons.append(f"{k} {v[k]}")
    if reasons:
        return "unstable", reasons
    if exp["esi"] in (1, 2):
        reasons.append(f"ESI {exp['esi']}")
    if exp["destination"].get("level_of_care") == "STEPDOWN":
        reasons.append("step down level of care")
    if exp["critical"]:
        reasons.append("critical result during the visit")
    for k, bad in ABNORMAL_VITALS.items():
        if isinstance(v.get(k), (int, float)) and bad(v[k]):
            reasons.append(f"{k} {v[k]}")
    if exp["pending"]["preliminary"]:
        reasons.append("preliminary result pending")
    return ("watcher", reasons) if reasons else ("stable", ["no instability criteria met"])


def expected(truth, csn, as_of):
    """Expected handoff facts for one encounter at as_of (a UTC datetime)."""
    as_of = ts(as_of)
    enc = truth.encounters[csn]
    pat = truth.patient_of(csn) or {}
    dest = destination(truth, csn, as_of)
    latest = latest_results(truth, csn, as_of)
    results = [dict(r) for r in sorted(latest.values(), key=lambda r: (r["resulted_at"], r["line"]))]
    critical = [r for r in results if r["flag"] in CRITICAL_FLAGS]
    abnormal = [r for r in results if r["flag"]]
    # A critical is addressed when a later non critical result of the same lab exists by as_of.
    unaddressed = [c for c in critical if not any(r["lab"] == c["lab"] and r["flag"] not in CRITICAL_FLAGS
                                                   and r["resulted_at"] > c["resulted_at"] for r in results)]
    resulted_orders = {r["order_id"] for r in truth.results.get(csn, []) if ts(r["resulted_at"]) <= as_of}
    unresulted = [{"order_id": o["order_id"], "name": o["name"], "ordered_ts": o["ordered_at"]}
                  for o in sorted(truth.orders.get(csn, []), key=lambda o: o["ordered_at"])
                  if o["order_type"] in ("Lab", "Imaging") and ts(o["ordered_at"]) <= as_of and o["order_id"] not in resulted_orders]
    cutoff = min(as_of, ts(dest["ed_departure_ts"])) if dest["ed_departure_ts"] else as_of
    meds = []
    for m in truth.mar.get(csn, []):
        if ts(m["taken_at"]) <= cutoff:
            mo = truth.med_orders.get(m["order_med_id"], {})
            meds.append({"order_id": m["order_med_id"], "line": m["line"], "name": mo.get("name"), "dose": m["dose"],
                         "unit": m["unit"], "route": m["route"], "rate": m["rate"], "action": m["action"], "taken_ts": m["taken_at"]})
    allergies = [a for a in truth.allergies.get(pat.get("pat_id"), []) if a.get("status", "Active") == "Active"
                 and ts(a["noted_at"]) <= as_of]
    cs = [c for c in truth.code_status.get(csn, []) if ts(c["activated_at"]) <= as_of]
    inf = [i for i in truth.infections.get(pat.get("pat_id"), []) if ts(i["added_at"]) <= as_of and i.get("isolation")]
    dx = (enc.get("dx") or [[None, None, None]])[0]
    exp = {
        "csn": csn, "as_of": iso(as_of), "facility": enc["facility"], "scenario": enc["scenario"],
        "scenario_label": enc["scenario_label"], "principal_dx": {"code": dx[0], "display": dx[2] if len(dx) > 2 else None},
        "esi": enc["esi"], "chief_complaint": enc["chief_complaint"], "disposition": enc["disposition"],
        "faults": sorted({f["fault_id"] for f in truth.faults.get(csn, [])} | set(enc.get("flags") or [])),
        "allergies": sorted({a["allergen"] for a in allergies}),
        "allergy_detail": [{"allergen": a["allergen"], "reaction": a["reaction"], "severity": a["severity"],
                            "noted_ts": a["noted_at"], "added_this_visit": a.get("added_csn") == csn} for a in allergies],
        "allergy_review": pat.get("allergy_review"),
        "code_status": cs[-1]["status"] if cs else None,
        "code_status_ts": cs[-1]["activated_at"] if cs else None,
        "isolation": inf[-1]["isolation"] if inf else None,
        "isolation_ts": inf[-1]["added_at"] if inf else None,
        "vitals": latest_vitals(truth, csn, as_of),
        "results": [_result(r) for r in results],
        "critical": [_result(r) for r in critical],
        "critical_unaddressed": [_result(r) for r in unaddressed],
        "abnormal": [_result(r) for r in abnormal],
        "pending": {"preliminary": [_result(r) for r in results if r["status"] in ("P", "I")], "unresulted_orders": unresulted},
        "ed_meds": meds,
        "destination": dest,
    }
    exp["severity"], exp["severity_reasons"] = reference_severity(exp)
    return exp


def _result(r):
    return {"order_id": r["order_id"], "code": r["lab"], "loinc": r.get("loinc"), "name": r["name"], "value": r["value"],
            "numeric": r.get("numeric"), "units": r["units"], "flag": r["flag"], "status": r["status"], "resulted_ts": r["resulted_at"]}


def visible_location(dest):
    """The location a handoff should show: the inpatient bed once transferred, else the pending bed or unit."""
    if dest.get("status") == "inpatient":
        return (dest.get("unit"), dest.get("room"), dest.get("bed"))
    return (dest.get("pending_unit"), dest.get("pending_room"), dest.get("pending_bed"))


def source_index(truth, csn, as_of):
    """Source ids a draft may legitimately cite for this encounter at as_of, by the id families chart.py emits."""
    as_of = ts(as_of)
    obs = set()
    for r in truth.results.get(csn, []):
        if ts(r["resulted_at"]) <= as_of:
            obs.add((r["order_id"], r["lab"], r["status"]))
            obs.add((r["order_id"], r.get("loinc"), r["status"]))
    for o in truth.orders.get(csn, []):            # imaging and ECG report rows cite the order code
        obs.add((o["order_id"], o["code"], None))
    return {"obs": obs,
            "med": {(m["order_med_id"], str(m["line"])) for m in truth.mar.get(csn, []) if ts(m["taken_at"]) <= as_of},
            "note": {n["note_id"] for n in truth.notes.get(csn, []) if ts(n["created_at"]) <= as_of},
            "vital": {v["taken_at"] for v in truth.vitals.get(csn, []) if ts(v["entered_at"]) <= as_of},
            "as_of": as_of}
