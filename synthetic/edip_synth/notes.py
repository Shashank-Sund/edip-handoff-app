"""Clinical note text. Every number in a note is read from the structured data
charted before the note's service time, so notes and tables always agree.

Style follows spec 10.7: terse, abbreviation heavy, original synthetic text.
"""
import re

from .config import TZ

# Problems a clinician mentions first in an HPI (SNOMED), most important first.
PRIORITY = ["88805009", "185086009", "87433001", "46177005", "431857002", "433144002", "44054006", "49436004",
            "414545008", "399211009", "26929004", "59621000", "195967001", "55822004", "271737000"]

ABBR = {"Shortness of breath": "SOB", "Chest pain": "CP", "Abdominal pain": "abd pain",
        "Altered mental status": "AMS", "Weakness": "weakness"}


def _hm(dt):
    return dt.astimezone(TZ).strftime("%H:%M")


def lower_first(text: str) -> str:
    """'Essential hypertension' -> 'essential hypertension'; keeps acronyms such as 'NSTEMI' or 'COPD'."""
    if len(text) > 1 and text[0].isupper() and not text[1].isupper():
        return text[0].lower() + text[1:]
    return text


def history(pat, n):
    probs = sorted(pat.problems, key=lambda p: PRIORITY.index(p["snomed"]) if p["snomed"] in PRIORITY else 99)
    keep = [p["display"] for p in probs if p["snomed"] in PRIORITY][:n] or [p["display"] for p in probs][:min(n, 3)]
    return ", ".join(lower_first(k) for k in keep)


def allergy_text(pat, allergies) -> str:
    if allergies:
        return "; ".join(f"{a.allergen} ({a.reaction.lower()})" for a in allergies)
    return {"Not on File": "not on file, unable to obtain", "No Known Allergies": "NKDA"}.get(
        getattr(pat, "allergy_review", ""), "NKDA")


def hpi(scen, pat, enc) -> str:
    text = scen.hpi or enc.chief_complaint.lower()
    if scen.key == "chf":
        meds = " ".join(m["display"].lower() for m in pat.home_meds)
        text += ", ran out of furosemide 5 days ago" if "furosemide" in meds else ", recent high salt diet"
    return text


def short_med(name: str) -> str:
    return re.split(r"\s\d", name)[0].replace(" bolus", "").replace(" injection", "").replace(" tablet", "")


def vitals_line(v) -> str:
    if v is None:
        return "VS: not yet obtained."
    parts = []
    if v.temp is not None:
        parts.append(f"T {v.temp:.1f} C")
    parts += [f"HR {v.hr}", f"BP {v.sbp}/{v.dbp}", f"RR {v.rr}"]
    o2 = "RA" if not v.o2_flow else f"{v.o2_flow:g} L {'NC' if v.o2_device == 'Nasal cannula' else v.o2_device}"
    parts.append(f"SpO2 {v.spo2}% {o2}")
    if v.pain is not None:
        parts.append(f"pain {v.pain}/10")
    return "VS: " + ", ".join(parts) + "."


def results_line(results) -> str:
    """results: latest Result per lab code (already filtered to the note time)."""
    keep = ["WBC", "HGB", "PLT", "NA", "K", "CO2", "AGAP", "BUN", "CREAT", "GLU", "LACT", "TROP", "BNP", "INR",
            "VPH", "VPCO2", "BHB", "PCT", "LIPASE", "CK"]
    short = {"WBC": "WBC", "HGB": "Hgb", "PLT": "Plt", "NA": "Na", "K": "K", "CO2": "CO2", "AGAP": "AG", "BUN": "BUN",
             "CREAT": "Cr", "GLU": "Glu", "LACT": "lactate", "TROP": "hs-TnT", "BNP": "BNP", "INR": "INR",
             "VPH": "VBG pH", "VPCO2": "pCO2", "BHB": "BHB", "PCT": "PCT", "LIPASE": "lipase", "CK": "CK"}
    parts = [f"{short[k]} {results[k].value}" for k in keep if k in results]
    for k, label in (("UA_LE", "UA LE"), ("UA_NIT", "nitrite")):
        if k in results:
            parts.append(f"{label} {results[k].value.lower()}")
    return "Results: " + (", ".join(parts) if parts else "pending") + "."


def triage(enc, pat, scen, v, allergies) -> str:
    mode = {"EMS": "Arrived via EMS", "Walk-in": "Ambulatory to triage", "Private vehicle": "Arrived by private vehicle",
            "Police": "Brought in by police", "Helicopter": "Arrived by air transport"}[enc.arrival_mode]
    hx = history(pat, 4) or "none reported"
    alg = allergy_text(pat, allergies)
    lines = [f"{mode}. CC: {enc.chief_complaint.lower()}.",
             f"Hx: {hx}. Allergies: {alg}.",
             vitals_line(v)]
    if scen.key.startswith("sepsis"):
        lines.append("Sepsis screen positive. Sepsis alert called, labs and blood cultures x2 drawn.")
    if scen.key == "stroke":
        lines.append("Stroke alert called at triage. POC glucose obtained. To CT.")
    if scen.key in ("chest_pain_ro", "nstemi", "stemi_transfer", "chest_pain_d"):
        lines.append("ECG obtained within 10 min of arrival, shown to provider.")
    lines.append(f"ESI {enc.esi}.")
    return "\n".join(lines)


def provider(enc, pat, scen, v, results, meds_given, dispo_text) -> str:
    age = pat_age(pat, enc)
    hx = history(pat, 5) or "no significant PMH"
    lines = [f"HPI: {age} {pat.sex} with {hx} presents with {hpi(scen, pat, enc)}.",
             "ROS: as per HPI, otherwise negative.",
             "PE: " + vitals_line(v).replace("VS: ", ""),
             results_line(results),
             f"MDM: {scen.mdm}"]
    if meds_given:
        lines.append("ED course: " + "; ".join(f"{short_med(m.name)} {m.dose} {m.unit} {m.route} at {_hm(t)}"
                                               for m, t in meds_given[:6]) + ".")
    lines.append(f"Disposition: {dispo_text}")
    return "\n".join(lines)


def reassessment(v, scen, boarding: bool) -> str:
    lines = [f"Reassessment. {vitals_line(v).replace('VS: ', '')}",
             _status(v)]
    if boarding:
        lines.append("Awaiting inpatient bed. Admission orders active.")
    return "\n".join(lines)


def _status(v) -> str:
    if v is None:
        return "Patient in room, call light in reach."
    if v.hr > 110 or v.sbp < 90 or v.rr > 26 or v.spo2 < 90:
        return "Remains tachycardic or hypotensive, provider aware, monitoring closely."
    if (v.pain or 0) > 4:
        return "Reports ongoing pain, provider notified."
    return "Resting comfortably, call light in reach."


def hp(enc, pat, scen, v, results, code_status) -> str:
    age = pat_age(pat, enc)
    dx = scen.dx[0][2] if scen.dx else enc.chief_complaint
    plan = "\n".join(f"{i}. {p}" for i, p in enumerate(scen.plan or ("Supportive care",), 1))
    cs = code_status or "not yet discussed"
    return "\n".join([f"Assessment: {age} {pat.sex} admitted with {lower_first(dx)}.",
                      vitals_line(v), results_line(results),
                      "Plan:", plan, f"Code status: {cs}.",
                      f"Disposition: {LEVEL_TEXT.get(enc.level_of_care, 'inpatient')} level of care."])


LEVEL_TEXT = {"ICU": "ICU", "STEPDOWN": "step down", "TELE": "telemetry", "MEDSURG": "medical surgical",
              "OBS": "observation"}


def consult(enc, specialty, scen) -> str:
    return "\n".join([f"{specialty} consult requested by ED for {lower_first(scen.label)}.",
                      "Chart, imaging and labs reviewed. Patient examined.",
                      f"Recommendations: {'; '.join(scen.plan[:2]) if scen.plan else 'agree with admission'}.",
                      "Will follow."])


def progress(enc, pat, scen, v, results) -> str:
    return "\n".join([f"Hospital day 1. {scen.label}.", vitals_line(v), results_line(results),
                      "Plan: continue current management. " + (scen.plan[0] if scen.plan else "")])


def pat_age(pat, enc) -> int:
    b = pat.birth_date
    a = enc.arrival_at
    return a.year - int(b[:4]) - ((a.month, a.day) < (int(b[5:7]), int(b[8:10])))
