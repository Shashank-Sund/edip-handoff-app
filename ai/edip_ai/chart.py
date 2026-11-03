"""Chart packet: everything the handoff needs for one encounter, built from silver shaped records, with a
source id on every fact so the model can cite and the validator can check.

Input records use the pipeline contract (pipelines/edip_contract.py) plus the encounter state row and the
Clarity history rows. Output is a plain dict, JSON serializable.
"""
from datetime import datetime, timezone

ANALGESICS = ("morphine", "hydromorphone", "fentanyl", "acetaminophen", "ketorolac", "oxycodone")


def _iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if isinstance(t, datetime) else t


def _age_min(t, now):
    return None if not isinstance(t, datetime) else round((now - t).total_seconds() / 60)


def _latest(rows, key, ts):
    out = {}
    for r in rows:
        k = key(r)
        if r.get(ts) and (k not in out or r[ts] >= out[k][ts]):
            out[k] = r
    return out


def build_packet(state, obs, meds, notes, allergies, flags, problems=(), home_meds=(), patient=None, now=None):
    """state: encounter_state row; obs/meds/notes/allergies/flags: contract records for this encounter (allergies
    by MRN); problems, home_meds, patient: Clarity rows. now: the time the handoff is generated (UTC)."""
    now = now or datetime.now(timezone.utc)
    obs = [o for o in obs if not o.get("resulted_ts") or o["resulted_ts"] <= now]
    meds = [m for m in meds if not m.get("event_ts") or m["event_ts"] <= now]
    notes = [n for n in notes if not n.get("created_ts") or n["created_ts"] <= now]
    # Flags and allergies documented after the generation time are not yet known (F20 code status after
    # transfer, F22 isolation found late, F21 allergy added during the visit).
    flags = [f for f in flags if not f.get("recorded_ts") or f["recorded_ts"] <= now]
    allergies = [a for a in allergies if not a.get("recorded_ts") or a["recorded_ts"] <= now]
    sources = {}

    def src(sid, payload):
        sources[sid] = payload
        return sid

    p = {"csn": state["csn"], "as_of": _iso(now), "facility": state.get("facility")}
    birth = state.get("birth_date")
    age = None
    if birth:
        b = datetime.strptime(str(birth)[:10], "%Y-%m-%d")
        age = now.year - b.year - ((now.month, now.day) < (b.month, b.day))
    p["identity"] = {"name": state.get("patient_name"), "age": age, "sex": state.get("sex"), "mrn": state.get("mrn"),
                     "src": src("enc:identity", {k: state.get(k) for k in ("patient_name", "mrn", "birth_date", "sex")})}
    p["visit"] = {"chief_complaint": state.get("chief_complaint"), "esi": state.get("esi"),
                  "arrival_ts": _iso(state.get("arrival_ts")), "minutes_in_ed": _age_min(state.get("arrival_ts"), now),
                  "src": src("enc:visit", {"chief_complaint": state.get("chief_complaint"), "esi": state.get("esi")})}
    dest = {k: state.get(k) for k in ("status", "unit", "room", "bed", "pending_unit", "pending_room", "pending_bed",
                                      "level_of_care", "service", "admitting_id", "attending_id")}
    dest["admit_decision_ts"] = _iso(state.get("admit_decision_ts"))
    dest["minutes_boarding"] = _age_min(state.get("admit_decision_ts"), now)
    dest["src"] = src("enc:destination", dict(dest))
    p["destination"] = dest

    # Code status and isolation: latest flag of each kind (the feeds carry them; the state row is a fallback).
    latest_flag = _latest(flags, lambda f: f["kind"], "recorded_ts")
    for kind, key in (("code_status", "code_status"), ("isolation", "isolation")):
        f = latest_flag.get(kind)
        value = f["value"] if f else state.get(key)
        p[key] = {"value": value, "recorded_ts": _iso(f["recorded_ts"]) if f else None,
                  "src": src(f"flag:{kind}", {"value": value})} if value else None

    seen, allergy_rows = set(), []
    for a in sorted(allergies, key=lambda a: a.get("recorded_ts") or now):
        if a["allergen"] in seen:
            continue
        seen.add(a["allergen"])
        sid = src(f"allergy:{len(allergy_rows) + 1}", {k: a.get(k) for k in ("allergen", "reaction", "severity")})
        allergy_rows.append({"allergen": a["allergen"], "reaction": a.get("reaction"), "severity": a.get("severity"),
                             "src": sid})
    p["allergies"] = allergy_rows
    p["allergy_review"] = (patient or {}).get("ALLERGY_REVIEW_C_NAME")

    p["problems"] = [{"display": r.get("DESCRIPTION"), "src": src(f"problem:{r.get('PROBLEM_LIST_ID')}",
                                                                   {"display": r.get("DESCRIPTION")})}
                     for r in problems if r.get("DESCRIPTION")][:12]
    p["home_meds"] = [{"name": r.get("DESCRIPTION") or r.get("DISPLAY_NAME"),
                       "src": src(f"homemed:{r.get('ORDER_MED_ID')}", {"name": r.get("DESCRIPTION")})}
                      for r in home_meds][:15]

    # Vitals: rows per vital set (taken time), keyed by the vital code.
    vit = [o for o in obs if o.get("category") == "vital" and o.get("validated") is not False]
    sets = {}
    for o in vit:
        sets.setdefault(o["observed_ts"], {})[o.get("code")] = o
    times = sorted(sets)
    def vset(t):
        s = sets[t]
        row = {k: (s[k].get("numeric") if s[k].get("numeric") is not None else s[k].get("value")) for k in s}
        row["taken_ts"] = _iso(t)
        row["src"] = src(f"vital:{_iso(t)}", {k: v for k, v in row.items() if k != "src"})
        return row
    p["vitals"] = {"latest": vset(times[-1]) if times else None, "first": vset(times[0]) if times else None,
                   "count": len(times), "latest_age_min": _age_min(times[-1], now) if times else None}
    p["pain"] = ({"score": p["vitals"]["latest"].get("pain"), "src": p["vitals"]["latest"]["src"]}
                 if p["vitals"]["latest"] and p["vitals"]["latest"].get("pain") is not None else None)

    # Labs and imaging: latest status per (order, component); abnormal or critical labs and all imaging.
    res = [o for o in obs if o.get("category") in ("lab", "imaging", "ecg")]
    rank = {"I": 0, "P": 1, "F": 2, "C": 3}
    latest = {}
    for o in res:
        k = (o.get("order_id"), o.get("code") or o.get("loinc"))
        cur = latest.get(k)
        if cur is None or (rank.get(o["status"], 0), o["resulted_ts"]) >= (rank.get(cur["status"], 0), cur["resulted_ts"]):
            latest[k] = o
    labs, pending, imaging = [], [], []
    for (order, code), o in sorted(latest.items(), key=lambda kv: kv[1]["resulted_ts"]):
        sid = src(f"obs:{order}:{code}:{o['status']}", {k: o.get(k) for k in ("name", "value", "units", "flag", "status")}
                  | {"resulted_ts": _iso(o["resulted_ts"])})
        row = {"name": o.get("name"), "value": o.get("value"), "units": o.get("units"), "flag": o.get("flag"),
               "status": o["status"], "resulted_ts": _iso(o["resulted_ts"]), "comment": o.get("comment"), "src": sid}
        if o["category"] in ("imaging", "ecg"):
            imaging.append(row)
        elif o.get("flag"):
            labs.append(row)
        if o["status"] in ("P", "I"):
            pending.append({"name": o.get("name"), "status": "preliminary" if o["status"] == "P" else "in progress",
                            "owner": state.get("service") or "admitting team", "src": sid})
    # Keep every critical result; only the other abnormal rows are trimmed to the most recent ones.
    crit = [r for r in labs if r["flag"] in ("HH", "LL")]
    other = [r for r in labs if r["flag"] not in ("HH", "LL")]
    other = other[len(other) - max(0, 25 - len(crit)):]
    p["labs"] = sorted(crit + other, key=lambda r: r["resulted_ts"])
    p["imaging"], p["pending"] = imaging, pending

    # ED medications: administrations before the ED departure (or now), with active infusions.
    left = state.get("ed_departure_ts") or now
    admin = [m for m in meds if m.get("event") == "administration" and m.get("event_ts") and m["event_ts"] <= left]
    p["ed_meds"] = [{"name": m.get("name"), "dose": m.get("dose"), "unit": m.get("unit"), "route": m.get("route"),
                     "rate": m.get("rate"), "action": m.get("action"), "taken_ts": _iso(m["event_ts"]),
                     "src": src(f"med:{m.get('order_id')}:{m.get('line')}", {k: m.get(k) for k in ("name", "dose", "unit",
                                                                                                "route", "action")})}
                    for m in sorted(admin, key=lambda m: m["event_ts"])][-25:]
    p["pain_meds"] = [m for m in p["ed_meds"] if any(a in (m["name"] or "").lower() for a in ANALGESICS)][-1:]

    # Notes: the latest version of each note, newest first; long text trimmed for the prompt.
    by_note = _latest(notes, lambda n: n["note_id"], "created_ts")
    for n in [x for x in notes if x.get("status") == "Signed"]:
        by_note[n["note_id"]] = n
    p["notes"] = [{"type": n.get("note_type"), "status": n.get("status"), "signed_ts": _iso(n.get("signed_ts")),
                   "text": (n.get("text") or "")[:1800], "src": src(f"note:{n['note_id']}", {"type": n.get("note_type")})}
                  for n in sorted(by_note.values(), key=lambda n: n.get("created_ts") or now, reverse=True)][:8]
    p["consults"] = [{"type": n["type"], "status": n["status"], "src": n["src"]} for n in p["notes"] if n["type"] == "Consults"]
    patient = patient or {}
    p["social"] = {"language": patient.get("LANGUAGE_C_NAME"), "interpreter": patient.get("INTRPTR_NEEDED_YN"),
                   "src": src("patient:social", {"language": patient.get("LANGUAGE_C_NAME")})} if patient else None
    p["lines"], p["precautions"], p["diet"] = [], None, None     # not carried by the reference feeds
    p["diagnoses"] = None
    p["_sources"] = sources
    return p
