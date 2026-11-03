"""Normalized record contract shared by the HL7 v2 and Redox FHIR parsers.

Both real time feeds are parsed into these records, so silver tables have one schema whatever the
source. Parsers are pure Python (standard library) and return plain dicts with exactly these keys;
the pipeline wraps them as UDFs. All timestamps are timezone aware UTC datetimes (or None).

A parser returns {"adt": [...], "obs": [...], "med": [...], "note": [...], "allergy": [...],
"flag": [...], "quarantine": [...]}; any list may be empty.
"""

# ADT kinds (one vocabulary for both feeds).
ADT_KINDS = ("arrival", "registration", "update", "pending_admit", "class_change", "bed_assigned", "bed_unassigned",
             "transfer", "cancel_transfer", "discharge", "cancel_discharge", "cancel_admit", "merge")

ADT = {
    "source": "fhir or hl7",
    "msg_key": "unique per delivered message: Redox Logs[0].ID, or MSH-3|MSH-4|MSH-10 for HL7",
    "content_hash": "hash of the clinical content, identical for content duplicates (F01, F03)",
    "facility": "SGH or LKS",
    "csn": "encounter id (PV1-19 / Encounter.identifier VN)",
    "mrn": "medical record number used on the message (retired MRN before a merge)",
    "kind": "one of ADT_KINDS",
    "trigger": "HL7 trigger event (A04 ...), also for FHIR via Meta extension when present",
    "effective_ts": "when it happened (EVN-6, else EVN-2; FHIR location or encounter period)",
    "recorded_ts": "when it was documented (EVN-2 / Meta.EventDateTime)",
    "patient_class": "E, I or OBS (normalized from E/I/O/OBS strings and FHIR class codes)",
    "unit": "point of care (ED, MICU, 5N, 6W, 7E, 4C, ICU, 3S, 2E), None when the message has no location",
    "room": "room or ED bay",
    "bed": "bed label",
    "pending_unit": "pending location unit (PV1-42 / planned location), None if absent",
    "pending_room": "", "pending_bed": "",
    "attending_id": "attending provider id",
    "admitting_id": "admitting provider id",
    "service": "hospital service",
    "level_of_care": "ICU, STEPDOWN, TELE, MEDSURG, OBS when known",
    "esi": "int 1 to 5 when present",
    "chief_complaint": "", "disposition": "discharge disposition text",
    "isolation": "isolation precaution text when present", "code_status": "code status text when present",
    "patient_name": "Last, First", "birth_date": "YYYY-MM-DD", "sex": "F or M",
    "surviving_mrn": "merge only", "retired_mrn": "merge only",
}

OBS = {
    "source": "", "msg_key": "", "facility": "", "csn": "", "mrn": "",
    "category": "lab, vital, imaging or ecg",
    "order_id": "placer order number (ORC-2 / ServiceRequest identifier); None for vitals",
    "code": "local or panel code of the component (truth lab code such as K, HGB) when derivable",
    "loinc": "", "name": "", "value": "text value as sent", "numeric": "float or None", "units": "",
    "ref_low": "float or None", "ref_high": "float or None", "flag": "'', H, L, HH, LL, A",
    "status": "P, F or C (I for incomplete components)",
    "observed_ts": "", "resulted_ts": "", "comment": "", "validated": "bool (False for device values, F24)",
    "line": "component line within the order, or the vital set line",
}

MED = {
    "source": "", "msg_key": "", "facility": "", "csn": "", "mrn": "",
    "order_id": "", "rxcui": "", "name": "", "dose": "", "unit": "", "route": "", "rate": "",
    "event": "order or administration",
    "action": "Given, New Bag, Rate Change, Stopped, Held (administration); None for an order",
    "line": "administration line, None for an order", "event_ts": "ordered or taken time", "frequency": "",
}

NOTE = {
    "source": "", "msg_key": "", "facility": "", "csn": "", "mrn": "",
    "note_id": "", "note_type": "", "loinc": "", "status": "In Progress or Signed", "author_id": "",
    "created_ts": "", "service_ts": "", "signed_ts": "None until signed", "text": "full text, unescaped",
}

ALLERGY = {
    "source": "", "msg_key": "", "facility": "", "csn": "", "mrn": "",
    "allergen": "", "code": "", "system": "", "reaction": "", "severity": "", "recorded_ts": "",
}

FLAG = {
    "source": "", "msg_key": "", "facility": "", "csn": "", "mrn": "",
    "kind": "isolation or code_status", "value": "", "recorded_ts": "",
}

QUARANTINE = {
    "source": "", "msg_key": "", "facility": "", "csn": "", "reason": "", "recorded_ts": "",
}

KINDS = {"adt": ADT, "obs": OBS, "med": MED, "note": NOTE, "allergy": ALLERGY, "flag": FLAG, "quarantine": QUARANTINE}


def empty() -> dict:
    return {k: [] for k in KINDS}

