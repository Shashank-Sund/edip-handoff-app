"""Parse one bronze.redox_fhir_events row (a Redox cloud delivery: FHIR R4 Bundle plus the Redox
Meta block) into the edip_contract records. Pure standard library, stateless per message.

Kind detection for PatientAdmin uses the HL7 trigger carried in Meta.Extensions["event-code"] when
present (A04, A02, A03, A06, A11 to A13, A14, A15, A26, A40) and otherwise the content of the
Encounter: classHistory (a class period starting at this event is a class_change), planned
locations (a planned bed starting now is bed_assigned, a planned unit starting now is pending_admit,
a planned location ending now is bed_unassigned), Coverage present (registration completion),
Meta.CanceledEvent (cancels). Everything else is "update".
"""
import base64
import hashlib
import html
import json
import re
from datetime import datetime, timezone

from edip_contract import empty

SOURCE = "fhir"
CLASS = {"EMER": "E", "IMP": "I", "OBSENC": "OBS", "AMB": "OBS"}
CLASS_TEXT = {"emergency": "E", "e": "E", "ed": "E", "inpatient": "I", "i": "I", "inpt": "I", "observation": "OBS",
              "obs": "OBS", "o": "OBS"}
CODE_KIND = {"A04": "arrival", "A01": "arrival", "A02": "transfer", "A03": "discharge", "A06": "class_change",
             "A11": "cancel_admit", "A12": "cancel_transfer", "A13": "cancel_discharge", "A14": "pending_admit",
             "A15": "bed_assigned", "A26": "bed_unassigned", "A40": "merge"}
TYPE_KIND = {"patient-arrived": "arrival", "patient-registered": "arrival", "patient-transferred": "transfer",
             "patient-discharged": "discharge", "patient-merged": "merge"}
CANCELED = {"transfer": "cancel_transfer", "discharge": "cancel_discharge", "arrival": "cancel_admit"}
OBS_STATUS = {"preliminary": "P", "final": "F", "corrected": "C", "amended": "C", "registered": "I", "partial": "P"}
VITALS = {"8867-4": "hr", "8480-6": "sbp", "8462-4": "dbp", "8478-0": "map", "9279-1": "rr", "8310-5": "temp",
          "59408-5": "spo2", "72514-3": "pain", "9269-2": "gcs"}
ALLERGY_SYSTEM = {"http://www.nlm.nih.gov/research/umls/rxnorm": "RxNorm", "http://snomed.info/sct": "SNOMED"}
SEVERITY = {"mild": "Mild", "moderate": "Moderate", "severe": "Severe"}
FREQUENCY = {"Once": "ONCE", "Continuous": "CONTINUOUS"}


def to_ts(s):
    """FHIR instant or dateTime to an aware UTC datetime; None when absent or date only."""
    if not s or len(s) < 19:
        return None
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except ValueError:
        return None


def ident(resource, suffix):
    for i in resource.get("identifier", []):
        if i.get("system", "").endswith(suffix):
            return i.get("value")
    return None


def coding_code(cc, system_suffix=None):
    for c in (cc or {}).get("coding", []):
        if system_suffix is None or c.get("system", "").endswith(system_suffix):
            return c.get("code")
    return None


def num_text(x):
    return str(int(x)) if isinstance(x, (int, float)) and float(x).is_integer() else str(x)


class Bundle:
    """Resource lookup for one delivery."""

    def __init__(self, row):
        bundle = row["bundle"]
        self.bundle = json.loads(bundle) if isinstance(bundle, str) else bundle
        self.meta = self.bundle.get("Meta") or {}
        self.resources = [e["resource"] for e in self.bundle.get("entry", []) if "resource" in e]
        self.by_ref = {}
        for e in self.bundle.get("entry", []):
            if "resource" in e:
                self.by_ref[e.get("fullUrl")] = e["resource"]
                self.by_ref["urn:uuid:" + e["resource"].get("id", "")] = e["resource"]
        logs = self.meta.get("Logs") or [{}]
        self.msg_key = logs[0].get("ID") or row.get("event_id")
        self.model = (self.meta.get("DataModel") or row.get("data_model") or "").replace("FHIR.Event.", "")
        self.etype = self.meta.get("EventType") or row.get("event_type") or ""
        ext = self.meta.get("Extensions") or {}
        self.code = (ext.get("event-code") or {}).get("string") or row.get("hl7_event_code")
        self.recorded = to_ts(self.meta.get("EventDateTime") or row.get("event_datetime"))
        self.facility = self.meta.get("FacilityCode") or row.get("facility_code")
        self.patient = self.first("Patient", lambda p: p.get("active", True) is not False) or self.first("Patient")
        self.encounter = self.first("Encounter")

    def first(self, rtype, pred=lambda r: True):
        return next((r for r in self.resources if r["resourceType"] == rtype and pred(r)), None)

    def all(self, rtype):
        return [r for r in self.resources if r["resourceType"] == rtype]

    def resolve(self, ref):
        return self.by_ref.get((ref or {}).get("reference"))

    def content_hash(self):
        """Clinical content only: entries without the MessageHeader, plus the event semantics.
        Meta (log ids, attempt ids, EventDateTime) and the message id are excluded, so retries and
        content duplicates hash equal."""
        body = [r for r in self.resources if r["resourceType"] != "MessageHeader"]
        key = {"type": self.etype, "code": self.code, "canceled": self.meta.get("CanceledEvent"), "entries": body}
        return hashlib.sha256(json.dumps(key, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    # ---- common fields
    def base(self):
        p = self.patient or {}
        return {"source": SOURCE, "msg_key": self.msg_key, "facility": self.facility,
                "csn": ident(self.encounter or {}, ":VN"), "mrn": ident(p, ":MR")}

    def quarantine(self, reason):
        base = self.base()
        base.pop("mrn")
        return {**base, "reason": reason, "recorded_ts": self.recorded}

    def prov_id(self, ref):
        p = self.resolve(ref)
        return ident(p, ":PROVID") if p else None

    def location_parts(self, ref):
        """(unit, room, bed) from the Location chain: bed -> room -> department."""
        loc = self.resolve(ref)
        names = []
        while loc:
            names.append(loc)
            loc = self.resolve(loc.get("partOf"))
        kinds = {coding_code(x.get("physicalType")): x for x in names}
        bed = kinds.get("bd")
        room = kinds.get("ro") or kinds.get("area")
        if not names:
            return None, "", ""
        if not (bed or room):                      # department only (a planned unit): the unit code is its alias
            dept = names[0]
            return (dept.get("alias") or [dept.get("name")])[0], "", ""
        parts = (bed or room).get("name", "").split("-")
        if len(parts) >= 3:
            return parts[0], "-".join(parts[1:-1]), parts[-1]
        if len(parts) == 2:
            return parts[0], parts[1], ""
        return (parts[0] or None), "", ""


def parse_fhir(row: dict) -> dict:
    out = empty()
    try:
        b = Bundle(row)
    except (KeyError, TypeError, ValueError) as e:
        out["quarantine"].append({"source": SOURCE, "msg_key": row.get("event_id"), "facility": row.get("facility_code"),
                                  "csn": None, "reason": f"unparseable bundle: {e}", "recorded_ts": to_ts(row.get("event_datetime"))})
        return out
    handler = {"PatientAdmin": _patient_admin, "Result": _result, "Order": _medication,
               "MedicationAdministration": _medication, "Document": _document}.get(b.model)
    if not handler or b.etype not in EVENT_TYPES:
        out["quarantine"].append(b.quarantine(f"unsupported event {b.model}/{b.etype}"))
        return out
    handler(b, out)
    return out


EVENT_TYPES = set(TYPE_KIND) | {"patient-updated", "result-created", "document-created", "document-replaced",
                                "medication-request-created", "medication-administration-created"}


# ---------------------------------------------------------------- PatientAdmin
def _patient_admin(b, out):
    enc, pat = b.encounter, b.patient
    if pat is None or enc is None:
        out["quarantine"].append(b.quarantine("PatientAdmin event without an Encounter (empty visit segment)"))
        return
    at = b.recorded.replace(microsecond=0) if b.recorded else None
    kind = _kind(b, enc, at)
    locs = enc.get("location", [])
    active = [x for x in locs if x.get("status") == "active"]
    planned = [x for x in locs if x.get("status") == "planned" and not x.get("period", {}).get("end")]
    if not active:        # a finished visit: the location the patient left from
        ended = [x for x in locs if x.get("status") == "completed" and (x.get("period") or {}).get("end")]
        active = sorted(ended, key=lambda x: x["period"]["end"])[-1:]
    unit, room, bed = b.location_parts(active[-1]["location"]) if active else (None, "", "")
    pend = b.location_parts(planned[-1]["location"]) if planned else (None, "", "")
    if kind in ("arrival", "transfer"):
        effective = to_ts(active[-1].get("period", {}).get("start")) if active else to_ts(enc.get("period", {}).get("start"))
    elif kind == "discharge":
        effective = to_ts(enc.get("period", {}).get("end"))
    else:
        effective = None
    participants = {}
    for p in enc.get("participant", []):
        role = coding_code(p.get("type", [{}])[0]) if p.get("type") else None
        participants[role] = b.prov_id(p.get("individual"))
    priority = (enc.get("priority") or {}).get("text", "")
    esi = int(priority.split()[-1]) if priority.startswith("ESI") else None
    flags = {}
    for f in b.all("Flag"):
        flags[(f.get("category") or [{}])[0].get("text")] = f.get("code", {}).get("text")
    isolation = flags.get("Isolation")
    name = (pat.get("name") or [{}])[0]
    rec = {**b.base(), "content_hash": b.content_hash(), "kind": kind, "trigger": b.code,
           "effective_ts": effective or b.recorded, "recorded_ts": b.recorded,
           "patient_class": _patient_class(enc), "unit": unit, "room": room, "bed": bed,
           "pending_unit": pend[0], "pending_room": pend[1], "pending_bed": pend[2],
           "attending_id": participants.get("ATND"), "admitting_id": participants.get("ADM"),
           "service": (enc.get("serviceType") or {}).get("text"), "level_of_care": _level_of_care(enc),
           "esi": esi, "chief_complaint": (enc.get("reasonCode") or [{}])[0].get("text"),
           "disposition": ((enc.get("hospitalization") or {}).get("dischargeDisposition") or {}).get("text"),
           "isolation": isolation.removesuffix(" Precautions") if isolation else None,
           "code_status": flags.get("Code status"),
           "patient_name": f"{name.get('family', '')}, {' '.join(name.get('given', [])[:1])}",
           "birth_date": pat.get("birthDate"), "sex": {"female": "F", "male": "M"}.get(pat.get("gender")),
           "surviving_mrn": None, "retired_mrn": None}
    if kind == "merge":
        retired = next((p for p in b.all("Patient") if p.get("active") is False), None)
        rec["surviving_mrn"] = rec["mrn"]
        rec["retired_mrn"] = ident(retired, ":MR") if retired else None
    out["adt"].append(rec)
    for a in b.all("AllergyIntolerance"):
        code = a.get("code", {})
        if coding_code(code) == "716186003":
            continue                                   # no known allergy marker
        system = next((c.get("system") for c in code.get("coding", [])), None)
        reaction = (a.get("reaction") or [{}])[0]
        out["allergy"].append({**b.base(), "allergen": code.get("text"), "code": coding_code(code),
                               "system": ALLERGY_SYSTEM.get(system, "Local" if system else "Unknown"),
                               "reaction": (reaction.get("manifestation") or [{}])[0].get("text"),
                               "severity": SEVERITY.get(reaction.get("severity"), reaction.get("severity")),
                               "recorded_ts": to_ts(a.get("recordedDate"))})
    for f in b.all("Flag"):
        cat = (f.get("category") or [{}])[0].get("text")
        kind_ = {"Isolation": "isolation", "Code status": "code_status"}.get(cat)
        if kind_:
            value = f.get("code", {}).get("text", "")
            out["flag"].append({**b.base(), "kind": kind_,
                                "value": value.removesuffix(" Precautions") if kind_ == "isolation" else value,
                                "recorded_ts": to_ts((f.get("period") or {}).get("start")) or b.recorded})


def _kind(b, enc, at):
    canceled = b.meta.get("CanceledEvent")
    if canceled in CANCELED:
        return CANCELED[canceled]
    if b.code in CODE_KIND:
        return CODE_KIND[b.code]
    if b.etype in TYPE_KIND:
        return TYPE_KIND[b.etype]

    def starts_now(period):
        t = to_ts((period or {}).get("start"))
        return t is not None and at is not None and t.replace(microsecond=0) == at

    history = enc.get("classHistory") or []
    if len(history) > 1 and starts_now(history[-1].get("period")):
        return "class_change"
    for x in enc.get("location", []):
        if x.get("status") == "planned":
            end = to_ts((x.get("period") or {}).get("end"))
            if end is not None and end.replace(microsecond=0) == at:
                return "bed_unassigned"
            if starts_now(x.get("period")):
                return "bed_assigned" if coding_code(x.get("physicalType")) == "bd" else "pending_admit"
    if b.first("Coverage"):
        return "registration"
    return "update"


def _patient_class(enc):
    cls = enc.get("class") or {}
    code = CLASS.get(cls.get("code"))
    if code:
        return code
    text = (cls.get("display") or "").lower()
    for k, v in CLASS_TEXT.items():
        if text == k or text.startswith(k + " "):
            return v
    return None


def _level_of_care(enc):
    for x in enc.get("extension", []):
        if x.get("url", "").endswith(":level-of-care"):
            return x.get("valueString")
    return None


# ---------------------------------------------------------------- Result (labs, imaging, vitals)
def _result(b, out):
    dr = b.first("DiagnosticReport")
    if dr is None:
        out["quarantine"].append(b.quarantine("result-created without a DiagnosticReport"))
        return
    cat = coding_code((dr.get("category") or [{}])[0])
    sr = b.resolve((dr.get("basedOn") or [None])[0]) if dr.get("basedOn") else None
    order_id = ident(dr, ":EPC") or (ident(sr, ":EPC") if sr else None)
    flo = ident(dr, ":FLO")
    flo_line = int(flo.rsplit("-", 1)[1]) if flo and "-" in flo else None
    base = b.base()
    line = 0
    for ref in dr.get("result", []):
        o = b.resolve(ref)
        if o is None:
            continue
        line += 1
        if cat == "vital-signs":
            out["obs"].extend(_vital_rows(b, base, o, flo_line))
            continue
        loinc = coding_code(o.get("code"), "loinc.org")
        category = "ecg" if loinc == "34534-8" or coding_code(o.get("code"), ":COMPONENT") == "ECG" else \
            "imaging" if cat == "RAD" else "lab"
        q = o.get("valueQuantity")
        rr = (o.get("referenceRange") or [{}])[0]
        interp = coding_code((o.get("interpretation") or [{}])[0]) or ""
        out["obs"].append({**base, "category": category, "order_id": order_id,
                           "code": coding_code(o.get("code"), ":COMPONENT"), "loinc": loinc,
                           "name": (o.get("code") or {}).get("text"),
                           "value": num_text(q["value"]) if q else o.get("valueString", ""),
                           "numeric": float(q["value"]) if q else None, "units": (q or {}).get("unit", ""),
                           "ref_low": (rr.get("low") or {}).get("value"), "ref_high": (rr.get("high") or {}).get("value"),
                           "flag": "" if interp == "N" else interp, "status": OBS_STATUS.get(o.get("status"), "F"),
                           "observed_ts": to_ts(o.get("effectiveDateTime")), "resulted_ts": to_ts(o.get("issued")) or b.recorded,
                           "comment": " ".join(n.get("text", "") for n in o.get("note", [])), "validated": True, "line": line})


def _vital_rows(b, base, o, line):
    validated = o.get("status") != "preliminary" and "device" not in o
    common = {**base, "category": "vital", "order_id": None, "ref_low": None, "ref_high": None, "flag": "",
              "status": "P" if not validated else "F", "observed_ts": to_ts(o.get("effectiveDateTime")),
              "resulted_ts": to_ts(o.get("issued")) or b.recorded, "comment": "", "validated": validated, "line": line}
    rows = []
    parts = o.get("component") or []
    if coding_code(o.get("code"), "loinc.org") == "85354-9":
        parts = o.get("component") or []
    else:
        parts = [o] + [c for c in parts if c.get("valueQuantity") or c.get("valueString")]
    for c in parts:
        loinc = coding_code(c.get("code"), "loinc.org")
        q = c.get("valueQuantity")
        code = VITALS.get(loinc) or {"Oxygen delivery device": "o2_device", "Oxygen flow rate": "o2_flow"}.get(
            (c.get("code") or {}).get("text"), loinc)
        rows.append({**common, "code": code, "loinc": loinc, "name": (c.get("code") or {}).get("text"),
                     "value": num_text(q["value"]) if q else c.get("valueString", ""),
                     "numeric": float(q["value"]) if q else None, "units": (q or {}).get("unit", "")})
    return rows


# ---------------------------------------------------------------- Medications
def _medication(b, out):
    base = b.base()
    ma = b.first("MedicationAdministration")
    if ma is not None:
        mr = b.resolve(ma.get("request")) or {}
        d = ma.get("dosage") or {}
        text = d.get("text", "")
        action = "Held" if ma.get("status") == "not-done" else \
            text.split(":")[0] if ":" in text and text.split(":")[0] in ("New Bag", "Rate Change", "Stopped") else "Given"
        mar = ident(ma, ":MAR")
        dose = d.get("dose") or {}
        out["med"].append({**base, "order_id": ident(mr, ":EPC"),
                           "rxcui": coding_code(ma.get("medicationCodeableConcept")),
                           "name": (ma.get("medicationCodeableConcept") or {}).get("text"),
                           "dose": num_text(dose["value"]) if dose else "", "unit": dose.get("unit", ""),
                           "route": (d.get("route") or {}).get("text", ""), "rate": _rate(d.get("rateQuantity")),
                           "event": "administration", "action": action,
                           "line": int(mar.rsplit("-", 1)[1]) if mar and "-" in mar else None,
                           "event_ts": to_ts(ma.get("effectiveDateTime") or (ma.get("effectivePeriod") or {}).get("start")),
                           "frequency": _frequency(mr)})
        return
    mr = b.first("MedicationRequest", lambda m: "identifier" in m)
    if mr is None:
        out["quarantine"].append(b.quarantine("medication event without a MedicationRequest"))
        return
    d = (mr.get("dosageInstruction") or [{}])[0]
    dr_ = (d.get("doseAndRate") or [{}])[0]
    dose = dr_.get("doseQuantity") or {}
    out["med"].append({**base, "order_id": ident(mr, ":EPC"), "rxcui": coding_code(mr.get("medicationCodeableConcept")),
                       "name": (mr.get("medicationCodeableConcept") or {}).get("text"),
                       "dose": num_text(dose["value"]) if dose else "", "unit": dose.get("unit", ""),
                       "route": (d.get("route") or {}).get("text", ""), "rate": _rate(dr_.get("rateQuantity")),
                       "event": "order", "action": None, "line": None, "event_ts": to_ts(mr.get("authoredOn")),
                       "frequency": _frequency(mr)})


def _rate(q):
    return f"{num_text(q['value'])} {q.get('unit', '')}".strip() if q else ""


def _frequency(mr):
    d = (mr.get("dosageInstruction") or [{}])[0]
    text = ((d.get("timing") or {}).get("code") or {}).get("text", "")
    return FREQUENCY.get(text, text)


# ---------------------------------------------------------------- Documents
def _document(b, out):
    doc = b.first("DocumentReference")
    if doc is None:
        out["quarantine"].append(b.quarantine("document event without a DocumentReference"))
        return
    att = (doc.get("content") or [{}])[0].get("attachment", {})
    signed = doc.get("docStatus") == "final"
    out["note"].append({**b.base(), "note_id": (doc.get("masterIdentifier") or {}).get("value") or ident(doc, ":EPIC"),
                        "note_type": (doc.get("type") or {}).get("text"), "loinc": coding_code(doc.get("type"), "loinc.org"),
                        "status": "Signed" if signed else "In Progress",
                        "author_id": b.prov_id((doc.get("author") or [None])[0]) if doc.get("author") else None,
                        "created_ts": to_ts(att.get("creation")) or to_ts(doc.get("date")),
                        "service_ts": to_ts(((doc.get("context") or {}).get("period") or {}).get("start")),
                        "signed_ts": to_ts(doc.get("date")) if signed else None,
                        "text": decode_attachment(att)})


def decode_attachment(att):
    """Base64 attachment to the plain note text; text/html (fault F27) is unescaped back to plain text."""
    data = att.get("data")
    text = base64.b64decode(data).decode("utf-8") if data else ""
    if att.get("contentType", "").startswith("text/html"):
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"</?(html|body|p)>", "", text)
        text = html.unescape(text)
    return text

