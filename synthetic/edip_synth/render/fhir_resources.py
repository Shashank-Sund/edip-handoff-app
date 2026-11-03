"""FHIR R4 resource builders for the Redox renderer (spec 6.8 and 6.9).

Pure functions from truth rows to resource dicts. Every id is a uuid5 of the
truth id, so the same resource gets the same id in every bundle and every run.
Identifier systems follow spec 6.8: `urn:redox:<source>:<IDType>` for local
authorities, the HL7 sid URIs for SSN and NPI.
"""
import base64
import re
import uuid

from ..reference import VITAL_LOINC

SCT = "http://snomed.info/sct"
LOINC = "http://loinc.org"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
ICD10 = "http://hl7.org/fhir/sid/icd-10-cm"
UCUM = "http://unitsofmeasure.org"
NPI = "http://hl7.org/fhir/sid/us-npi"
SSN = "http://hl7.org/fhir/sid/us-ssn"
TERM = "http://terminology.hl7.org/CodeSystem/"
US_CORE = "http://hl7.org/fhir/us/core/StructureDefinition/"
NCIT = "http://ncicb.nci.nih.gov/xml/owl/EVS/Thesaurus.owl"
DOC_KIND = "urn:redox:document_kind"

RACE = {"white": ("2106-3", "White"), "black": ("2054-5", "Black or African American"), "asian": ("2028-9", "Asian"),
        "hawaiian": ("2076-8", "Native Hawaiian or Other Pacific Islander"),
        "native": ("1002-5", "American Indian or Alaska Native"), "other": ("2131-1", "Other Race")}
ETHNICITY = {"hispanic": ("2135-2", "Hispanic or Latino"), "nonhispanic": ("2186-5", "Not Hispanic or Latino")}
LANGUAGE = {"English": "en", "Spanish": "es", "Portuguese": "pt", "Mandarin": "zh", "Haitian Creole": "ht",
            "Vietnamese": "vi"}
MARITAL = {"M": "Married", "S": "Never Married", "D": "Divorced", "W": "Widowed"}
GENDER = {"F": "female", "M": "male"}

# Patient class: truth code -> (v3-ActCode, display variants; index 0 is canonical, the rest are fault F30)
CLASS = {"E": ("EMER", ["Emergency", "Emergency Department", "EMERGENCY", "ED"]),
         "I": ("IMP", ["Inpatient", "Inpatient Admission", "INPATIENT", "Inpt"]),
         "OBS": ("OBSENC", ["Observation", "Outpatient Observation", "OBSERVATION", "Obs"])}
ESI = {1: ("EM", "emergency"), 2: ("EM", "emergency"), 3: ("UR", "urgent"), 4: ("R", "routine"), 5: ("R", "routine")}
DISCHARGE = {"01": ("home", "Home"), "02": ("other-hcf", "Other healthcare facility"), "07": ("ama", "Left against advice")}
PARTICIPANT = {"ATND": "attender", "ADM": "admitter", "CON": "consultant"}
INTERP = {"H": "High", "L": "Low", "HH": "Critical high", "LL": "Critical low", "A": "Abnormal", "N": "Normal"}
RESULT_STATUS = {"P": "preliminary", "F": "final", "C": "corrected"}
UCUM_CODE = {"K/uL": "10*3/uL", "mmHg": "mm[Hg]"}
ROUTE = {"IV": ("C38276", "Intravenous"), "PO": ("C38288", "Oral"), "SL": ("C38300", "Sublingual"),
         "INH": ("C38216", "Respiratory (inhalation)")}
FREQ = {"Q4H": (1, 4, "h"), "Q8H": (1, 8, "h"), "Q12H": (1, 12, "h"), "Q24H": (1, 24, "h"), "BID": (2, 1, "d")}
MAR_STATUS = {"Given": "completed", "New Bag": "completed", "Rate Change": "completed", "Held": "not-done"}
REACTION_SCT = {"Hives": "247472004", "Wheal (finding)": "247472004", "Rash": "271807003",
                "Eruption of skin (disorder)": "271807003", "Nausea": "422587007", "Swelling": "65124004",
                "Anaphylaxis": "39579001", "Itching": "418290006", "Cough (finding)": "49727002",
                "Abdominal pain (finding)": "21522001"}
SEVERITY = {"Low": "mild", "Mild": "mild", "Medium": "moderate", "Moderate": "moderate", "High": "severe"}
FOOD = ("peanut", "shellfish", "egg", "nut", "fish", "milk", "soy", "wheat", "sesame")
ENVIRONMENT = ("latex", "bee", "pollen", "mold", "dust", "animal", "grass", "cat", "dog", "adhesive", "tape")
PHYSICAL = {"wa": "Ward", "ro": "Room", "bd": "Bed", "area": "Area"}


class Ids:
    """uuid5 ids under a per dataset namespace: rid("pat", pat_id) is stable across runs."""

    def __init__(self, seed):
        self.ns = uuid.uuid5(uuid.NAMESPACE_URL, f"urn:edip:fhir:{seed}")

    def rid(self, *parts) -> str:
        return str(uuid.uuid5(self.ns, "/".join(str(p) for p in parts)))

    def millis(self, *parts) -> int:
        return uuid.uuid5(self.ns, "ms/" + "/".join(str(p) for p in parts)).int % 1000


def ref(resource, display=None):
    r = {"reference": f"urn:uuid:{resource['id']}"}
    if display:
        r["display"] = display
    return r


def coding(system, code, display=None):
    c = {"system": system, "code": code}
    if display:
        c["display"] = display
    return c


def cc(system, code, display=None, text=None):
    out = {"coding": [coding(system, code, display)]} if code else {}
    if text or (display and not code):
        out["text"] = text or display
    return out


def system_for(src, id_type):
    return f"urn:redox:{src}:{id_type}"


def e164(phone):
    digits = re.sub(r"\D", "", phone or "")
    return f"+1{digits}" if len(digits) == 10 else phone


def quantity(value, unit):
    q = {"value": value}
    if unit:
        q.update({"unit": unit, "system": UCUM, "code": UCUM_CODE.get(unit, unit)})
    return q


def prov_display(p):
    suffix = {"Physician": "MD", "Resident": "MD", "Nurse Practitioner": "NP", "Physician Assistant": "PA",
              "Registered Nurse": "RN"}[p["prov_type"]]
    return f"{p['first']} {p['last']}, {suffix}"


# ---------------------------------------------------------------- administrative resources
def organization(ids, fac):
    street, rest = fac["address"].split(", ", 1)
    city, state_zip = rest.split(", ")
    state, postal = state_zip.split(" ")
    return {"resourceType": "Organization", "id": ids.rid("org", fac["code"]), "active": True,
            "identifier": [{"system": NPI, "value": fac["npi"]}, {"system": system_for(fac["code"].lower(), "FAC"),
                                                                    "value": fac["code"]}],
            "type": [cc(TERM + "organization-type", "prov", "Healthcare Provider")], "name": fac["name"],
            "address": [{"line": [street], "city": city, "state": state, "postalCode": postal, "country": "US"}]}


def practitioner(ids, p, src):
    role = {"Physician": ("MD", "Doctor of Medicine"), "Resident": ("MD", "Doctor of Medicine"),
            "Nurse Practitioner": ("NP", "Nurse Practitioner"), "Physician Assistant": ("PA", "Physician Assistant"),
            "Registered Nurse": ("RN", "Registered Nurse")}[p["prov_type"]]
    return {"resourceType": "Practitioner", "id": ids.rid("prac", p["prov_id"]), "active": True,
            "identifier": [{"system": NPI, "value": p["npi"]}, {"system": system_for(src, "PROVID"), "value": p["prov_id"]}],
            "name": [{"family": p["last"], "given": [p["first"]], "suffix": [role[0]]}],
            "qualification": [{"code": cc(TERM + "v2-0360", role[0], role[1])}]}


def location_chain(ids, dept, unit, room, bed, org):
    """Department (wa), room (ro, or area for the waiting room) and bed (bd) Locations; deepest last."""
    src = dept["facility"].lower()
    out = [{"resourceType": "Location", "id": ids.rid("loc", dept["dept_id"]), "status": "active", "mode": "instance",
            "identifier": [{"system": system_for(src, "DEP"), "value": dept["dept_id"]}], "name": dept["name"],
            "physicalType": cc(TERM + "location-physical-type", "wa", "Ward"), "managingOrganization": ref(org)}]
    if room:
        kind = "area" if room == "WR" else "ro"
        out.append({"resourceType": "Location", "id": ids.rid("loc", dept["dept_id"], room), "status": "active",
                    "mode": "instance", "identifier": [{"system": system_for(src, "ROOM"), "value": f"{unit}-{room}"}],
                    "name": f"{unit}-{room}", "physicalType": cc(TERM + "location-physical-type", kind, PHYSICAL[kind]),
                    "partOf": ref(out[0])})
    if bed:
        out.append({"resourceType": "Location", "id": ids.rid("loc", dept["dept_id"], room, bed), "status": "active",
                    "mode": "instance", "identifier": [{"system": system_for(src, "BED"), "value": f"{unit}-{room}-{bed}"}],
                    "name": f"{unit}-{room}-{bed}", "physicalType": cc(TERM + "location-physical-type", "bd", "Bed"),
                    "partOf": ref(out[-1])})
    return out


def patient(ids, p, src, org, full=True, mobile=None):
    """Sparse (quick registration) or full (US Core) Patient."""
    out = {"resourceType": "Patient", "id": ids.rid("pat", p["pat_id"]),
           "identifier": [{"type": cc(TERM + "v2-0203", "MR", "Medical record number"), "system": system_for(src, "MR"),
                           "value": p["mrn"]}, {"system": system_for(src, "EPI"), "value": p["epi"]}],
           "name": [{"use": "official", "family": p["last"], "given": [x for x in (p["first"], p["middle"]) if x]}],
           "gender": GENDER[p["sex"]], "birthDate": p["birth_date"]}
    if not full:
        return out
    race, eth = RACE[p["race"]], ETHNICITY[p["ethnicity"]]
    ext = [{"url": US_CORE + "us-core-race", "extension": [
                {"url": "ombCategory", "valueCoding": coding("urn:oid:2.16.840.1.113883.6.238", *race)},
                {"url": "text", "valueString": race[1]}]},
           {"url": US_CORE + "us-core-ethnicity", "extension": [
                {"url": "ombCategory", "valueCoding": coding("urn:oid:2.16.840.1.113883.6.238", *eth)},
                {"url": "text", "valueString": eth[1]}]},
           {"url": US_CORE + "us-core-birthsex", "valueCode": p["sex"]}]
    if p["interpreter"]:
        ext.append({"url": "http://hl7.org/fhir/StructureDefinition/patient-interpreterRequired", "valueBoolean": True})
    out["extension"] = ext
    out["identifier"].append({"system": SSN, "value": p["ssn"]})
    out["active"] = True
    telecom = [{"system": "phone", "value": e164(p["phone"]), "use": "home"}]
    if mobile:
        telecom.append({"system": "phone", "value": mobile, "use": "mobile"})
    contact = p["emergency_contact"]
    out.update({
        "telecom": telecom,
        "address": [{"use": "home", "line": [p["address"]], "city": p["city"], "state": p["state"],
                     "postalCode": p["zip"], "country": "US"}],
        "maritalStatus": cc(TERM + "v3-MaritalStatus", p["marital"], MARITAL[p["marital"]]),
        "contact": [{"relationship": [cc(TERM + "v2-0131", "C", "Emergency Contact", text=contact["relation"])],
                     "name": {"text": contact["name"]},
                     "telecom": [{"system": "phone", "value": e164(contact["phone"])}]}],
        "communication": [{"language": cc("urn:ietf:bcp:47", LANGUAGE.get(p["language"], "en"), p["language"]),
                           "preferred": True}],
        "managingOrganization": ref(org, org["name"])})
    return out


def coverage(ids, p, org):
    return {"resourceType": "Coverage", "id": ids.rid("cov", p["pat_id"]), "status": "active",
            "type": cc(TERM + "v3-ActCode", "PUBLICPOL" if p["payer"] in ("Medicare", "Medicaid") else "EHCPOL",
                       "public healthcare" if p["payer"] in ("Medicare", "Medicaid") else "extended healthcare"),
            "subscriberId": f"{p['payer'][:3].upper()}{p['mrn'][-6:]}", "beneficiary": ref({"id": ids.rid("pat", p["pat_id"])}),
            "relationship": cc(TERM + "subscriber-relationship", "self", "Self"),
            "payor": [{"display": p["payer"]}]}


# ---------------------------------------------------------------- clinical resources
def allergy(ids, a, src, pat_ref, enc_ref=None):
    system = {"RxNorm": RXNORM, "SNOMED": SCT, "Local": system_for(src, "ALLERGEN")}.get(a["system"])
    name = a["allergen"].lower()
    category = "food" if any(k in name for k in FOOD) else "environment" if any(k in name for k in ENVIRONMENT) \
        else "medication"
    out = {"resourceType": "AllergyIntolerance", "id": ids.rid("alg", a["allergy_id"]),
           "clinicalStatus": cc(TERM + "allergyintolerance-clinical", "active", "Active"),
           "verificationStatus": cc(TERM + "allergyintolerance-verification", "confirmed", "Confirmed"),
           "type": "allergy", "category": [category], "criticality": "high" if a["severity"] == "High" else "low",
           "code": cc(system, a["code"] if system else None, a["allergen"], text=a["allergen"]),
           "patient": pat_ref, "recordedDate": a["noted_at"]}
    if enc_ref:
        out["encounter"] = enc_ref
    if a["reaction"]:
        sct = REACTION_SCT.get(a["reaction"])
        out["reaction"] = [{"manifestation": [cc(SCT, sct, a["reaction"], text=a["reaction"])],
                            "severity": SEVERITY.get(a["severity"], "moderate")}]
    return out


def no_known_allergy(ids, p, pat_ref):
    return {"resourceType": "AllergyIntolerance", "id": ids.rid("alg-nka", p["pat_id"]),
            "clinicalStatus": cc(TERM + "allergyintolerance-clinical", "active", "Active"),
            "verificationStatus": cc(TERM + "allergyintolerance-verification", "confirmed", "Confirmed"),
            "code": cc(SCT, "716186003", "No known allergy"), "patient": pat_ref}


def problem(ids, p, prob, pat_ref):
    out = {"resourceType": "Condition", "id": ids.rid("prob", p["pat_id"], prob["snomed"] or prob["display"]),
           "clinicalStatus": cc(TERM + "condition-clinical", "active", "Active"),
           "category": [cc(TERM + "condition-category", "problem-list-item", "Problem List Item")],
           "code": {"coding": [], "text": prob["display"]}, "subject": pat_ref}
    if prob.get("snomed"):
        out["code"]["coding"].append(coding(SCT, prob["snomed"], prob["display"]))
    if prob.get("icd10"):
        out["code"]["coding"].append(coding(ICD10, prob["icd10"], prob["display"]))
    if prob.get("onset"):
        out["onsetDateTime"] = prob["onset"]
    return out


def encounter_dx(ids, csn, dx, pat_ref, enc_ref, recorded):
    icd10, sct, display = dx
    out = {"resourceType": "Condition", "id": ids.rid("dx", csn, icd10 or display),
           "clinicalStatus": cc(TERM + "condition-clinical", "active", "Active"),
           "category": [cc(TERM + "condition-category", "encounter-diagnosis", "Encounter Diagnosis")],
           "code": {"coding": [], "text": display}, "subject": pat_ref, "encounter": enc_ref, "recordedDate": recorded}
    if icd10:
        out["code"]["coding"].append(coding(ICD10, icd10, display))
    if sct:
        out["code"]["coding"].append(coding(SCT, sct, display))
    return out


def infection(ids, inf, pat_ref, enc_ref):
    """Isolation Flag plus the infection Condition behind it (spec 6.9)."""
    flag = {"resourceType": "Flag", "id": ids.rid("flag", inf["infection_id"]), "status": "active",
            "category": [cc(TERM + "flag-category", "clinical", "Clinical", text="Isolation")],
            "code": {"text": f"{inf['isolation']} Precautions"}, "subject": pat_ref, "period": {"start": inf["added_at"]}}
    cond = {"resourceType": "Condition", "id": ids.rid("inf", inf["infection_id"]),
            "clinicalStatus": cc(TERM + "condition-clinical", "active", "Active"),
            "category": [cc(TERM + "condition-category", "problem-list-item", "Problem List Item")],
            "code": {"text": inf["infection"]}, "subject": pat_ref, "recordedDate": inf["added_at"]}
    if enc_ref and inf["source"] == "this_visit":
        flag["encounter"] = enc_ref
        cond["encounter"] = enc_ref
    return [flag, cond]


def code_status(ids, cs, pat_ref, enc_ref, author):
    """Flag and Observation, both text coded: the LOINC code status code is not verified (spec 6.9)."""
    flag = {"resourceType": "Flag", "id": ids.rid("flag-cs", cs["ocs_id"]), "status": "active",
            "category": [cc(TERM + "flag-category", "clinical", "Clinical", text="Code status")],
            "code": {"text": cs["status"]}, "subject": pat_ref, "encounter": enc_ref,
            "period": {"start": cs["activated_at"]}, "author": author}
    obs = {"resourceType": "Observation", "id": ids.rid("obs-cs", cs["ocs_id"]), "status": "final",
           "category": [{"text": "Code status"}], "code": {"text": "Code status"}, "subject": pat_ref,
           "encounter": enc_ref, "effectiveDateTime": cs["activated_at"], "performer": [author],
           "valueCodeableConcept": {"text": cs["status"]}}
    return [flag, obs]


def home_med(ids, p, med, pat_ref):
    return {"resourceType": "MedicationRequest", "id": ids.rid("hm", p["pat_id"], med["rxcui"], med["start"]),
            "status": "active", "intent": "order",
            "category": [cc(TERM + "medicationrequest-category", "community", "Community")],
            "medicationCodeableConcept": cc(RXNORM, med["rxcui"], med["display"], text=med["display"]),
            "subject": pat_ref, "authoredOn": med["start"], "reportedBoolean": True}


def _dosage(row, action=None):
    text = " ".join(x for x in (row["dose"], row["unit"], row["route"], row.get("freq", "")) if x)
    if action and action != "Given":
        text = f"{action}: {text}"
    d = {"text": text}
    if row["route"] in ROUTE:
        d["route"] = cc(NCIT, *ROUTE[row["route"]], text=row["route"])
    rate = {}
    m = re.match(r"^([\d.]+)\s*(.*)$", row.get("rate") or "")
    if m:
        rate["rateQuantity"] = quantity(float(m.group(1)), m.group(2))
    return d, rate


def medication_request(ids, mo, src, pat_ref, enc_ref, requester):
    d, rate = _dosage(mo)
    dose = {"doseQuantity": quantity(float(mo["dose"]), mo["unit"])} if re.match(r"^[\d.]+$", mo["dose"]) else {}
    d["doseAndRate"] = [{**dose, **rate}]
    if mo["freq"] in FREQ:
        f, per, unit = FREQ[mo["freq"]]
        d["timing"] = {"repeat": {"frequency": f, "period": per, "periodUnit": unit}, "code": {"text": mo["freq"]}}
    else:
        d["timing"] = {"code": {"text": {"ONCE": "Once", "CONTINUOUS": "Continuous"}.get(mo["freq"], mo["freq"])}}
    out = {"resourceType": "MedicationRequest", "id": ids.rid("mr", mo["order_med_id"]),
           "identifier": [{"system": system_for(src, "EPC"), "value": mo["order_med_id"]}],
           "status": "active", "intent": "order",
           "category": [cc(TERM + "medicationrequest-category", "inpatient", "Inpatient")],
           "medicationCodeableConcept": cc(RXNORM, mo["rxcui"], mo["name"], text=mo["name"]),
           "subject": pat_ref, "encounter": enc_ref, "authoredOn": mo["ordered_at"], "requester": requester,
           "dosageInstruction": [d]}
    if mo["comment"]:
        out["note"] = [{"text": mo["comment"]}]
    return out


def medication_administration(ids, mar, mo, pat_ref, enc_ref, performer, request_ref):
    d, rate = _dosage(mar, mar["action"])
    if re.match(r"^[\d.]+$", mar["dose"]):
        d["dose"] = quantity(float(mar["dose"]), mar["unit"])
    d.update(rate)
    out = {"resourceType": "MedicationAdministration", "id": ids.rid("ma", mar["order_med_id"], mar["line"]),
           "status": MAR_STATUS[mar["action"]],
           "category": cc(TERM + "medication-admin-category", "inpatient", "Inpatient"),
           "medicationCodeableConcept": cc(RXNORM, mo["rxcui"], mo["name"], text=mo["name"]),
           "subject": pat_ref, "context": enc_ref, "request": request_ref,
           "performer": [{"function": cc(TERM + "med-admin-perform-function", "performer", "Performer"),
                          "actor": performer}], "dosage": d}
    if mar["action"] in ("New Bag", "Rate Change"):
        out["effectivePeriod"] = {"start": mar["taken_at"]}
    else:
        out["effectiveDateTime"] = mar["taken_at"]
    if mar["action"] == "Held":
        out["statusReason"] = [{"text": "Held"}]
    return out


def document_reference(ids, note, version, src, pat_ref, enc_ref, author, org, html=False):
    status, at = version
    signed = status == "Signed"
    text = note["text"]
    if html:
        body = "<html><body><p>" + text.replace("&", "&amp;").replace("<", "&lt;").replace("\n", "<br/>") + "</p></body></html>"
        content_type = "text/html"
    else:
        body, content_type = text, "text/plain"
    out = {"resourceType": "DocumentReference", "id": ids.rid("doc", note["note_id"]),
           "meta": {"versionId": "2" if signed else "1"},
           "masterIdentifier": {"system": system_for(src, "EPIC"), "value": note["note_id"]},
           "identifier": [{"system": system_for(src, "EPIC"), "value": note["note_id"]}],
           "status": "current", "docStatus": "final" if signed else "preliminary",
           "type": cc(LOINC, note["loinc"], note["note_type"], text=note["note_type"]),
           "category": [cc(DOC_KIND, "clinical-note", "Clinical Note")], "subject": pat_ref, "date": at,
           "author": [author], "custodian": ref(org, org["name"]), "description": note["note_type"],
           "content": [{"attachment": {"contentType": content_type, "language": "en",
                                       "data": base64.b64encode(body.encode("utf-8")).decode("ascii"),
                                       "title": note["note_type"], "creation": note["created_at"]}}],
           "context": {"encounter": [enc_ref], "period": {"start": note["service_at"]}}}
    if signed:
        out["authenticator"] = author
    return out


# ---------------------------------------------------------------- results
def service_request(ids, order, src, pat_ref, enc_ref, requester, code, done):
    cat = {"Lab": ("108252007", "Laboratory procedure"), "Imaging": ("363679005", "Imaging")}[order["order_type"]]
    pri = {"STAT": "stat", "Urgent": "urgent"}.get(order["priority"], "routine")
    out = {"resourceType": "ServiceRequest", "id": ids.rid("sr", order["order_id"]),
           "identifier": [{"type": cc(TERM + "v2-0203", "PLAC", "Placer Identifier"), "system": system_for(src, "EPC"),
                           "value": order["order_id"]}],
           "status": "completed" if done else "active", "intent": "order", "category": [cc(SCT, *cat)],
           "priority": pri, "code": code, "subject": pat_ref, "encounter": enc_ref, "authoredOn": order["ordered_at"],
           "requester": requester}
    if order["accession"]:
        out["identifier"].append({"type": cc(TERM + "v2-0203", "FILL", "Filler Identifier"),
                                  "system": system_for(src, "BEAKER"), "value": order["accession"]})
    return out


def specimen(ids, order, src, pat_ref):
    urine = order["code"] in ("LAB0030", "LAB0041")
    return {"resourceType": "Specimen", "id": ids.rid("spec", order["order_id"]),
            "identifier": [{"system": system_for(src, "BEAKER"), "value": order["accession"]}],
            "status": "available", "type": cc(SCT, "122575003" if urine else "119297000", "Urine" if urine else "Blood"),
            "subject": pat_ref, "collection": {"collectedDateTime": order["collected_at"]}}


def result_observation(ids, r, status, pat_ref, enc_ref, performer, specimen_ref=None, category="laboratory"):
    out = {"resourceType": "Observation", "id": ids.rid("obs", r["order_id"], r["line"], r["status"], r["resulted_at"]),
           "status": status, "category": [cc(TERM + "observation-category", category, category.capitalize())],
           "code": cc(LOINC, r["loinc"], r["name"], text=r["name"]), "subject": pat_ref, "encounter": enc_ref,
           "effectiveDateTime": r["observed_at"], "issued": r["resulted_at"], "performer": [performer]}
    if status == "registered":
        out["dataAbsentReason"] = cc(TERM + "data-absent-reason", "temp-unknown", "Temporarily Unknown")
    elif r["numeric"] is not None:
        out["valueQuantity"] = quantity(r["numeric"], r["units"])
    else:
        out["valueString"] = r["value"]
    flag = r["flag"] or ("N" if r["numeric"] is not None and r["ref_low"] is not None else "")
    if flag and status != "registered":
        out["interpretation"] = [cc(TERM + "v3-ObservationInterpretation", flag, INTERP[flag])]
    if r["ref_low"] is not None or r["ref_high"] is not None:
        rr = {"text": f"{r['ref_low']}-{r['ref_high']}"}
        if r["ref_low"] is not None:
            rr["low"] = quantity(r["ref_low"], r["units"])
        if r["ref_high"] is not None:
            rr["high"] = quantity(r["ref_high"], r["units"])
        out["referenceRange"] = [rr]
    if r["comment"]:
        out["note"] = [{"text": r["comment"]}]
    if specimen_ref:
        out["specimen"] = specimen_ref
    return out


def diagnostic_report(ids, order, key, status, code, pat_ref, enc_ref, performer, results, issued, effective,
                      based_on=None, specimen_ref=None, conclusion=None, category=None):
    out = {"resourceType": "DiagnosticReport", "id": ids.rid("dr", *key), "status": status,
           "category": [category or cc(TERM + "v2-0074", "RAD" if order["order_type"] == "Imaging" else "LAB",
                                       "Radiology" if order["order_type"] == "Imaging" else "Laboratory")],
           "code": code, "subject": pat_ref, "encounter": enc_ref, "effectiveDateTime": effective, "issued": issued,
           "performer": [performer], "result": [ref(o) for o in results]}
    if order.get("order_id"):
        out["identifier"] = [{"system": system_for(order["src"], "EPC"), "value": order["order_id"]}]
        if order.get("accession"):
            out["identifier"].append({"system": system_for(order["src"], "BEAKER"), "value": order["accession"]})
    if based_on:
        out["basedOn"] = [based_on]
    if specimen_ref:
        out["specimen"] = [specimen_ref]
    if conclusion:
        out["conclusion"] = conclusion
    return out


def vital_observations(ids, v, pat_ref, enc_ref, performer, unvalidated=False):
    """One Observation per measure; blood pressure is the 85354-9 panel with components (spec 6.9)."""
    status = "preliminary" if unvalidated else "final"
    base = {"resourceType": "Observation", "status": status,
            "category": [cc(TERM + "observation-category", "vital-signs", "Vital Signs")], "subject": pat_ref,
            "encounter": enc_ref, "effectiveDateTime": v["taken_at"], "issued": v["entered_at"], "performer": [performer]}
    if unvalidated:
        base["device"] = {"display": "Bedside monitor, device value not validated"}

    def one(key, value, unit, code=None, name=None):
        code, name, unit = (code, name, unit) if code else VITAL_LOINC[key]
        return {**base, "id": ids.rid("vit", v["csn"], v["line"], key), "code": cc(LOINC, code, name, text=name),
                "valueQuantity": quantity(value, unit)}

    out = [one("hr", v["hr"], None)]
    bp = {**base, "id": ids.rid("vit", v["csn"], v["line"], "bp"),
          "code": cc(LOINC, "85354-9", "Blood pressure panel with all children optional", text="Blood pressure"),
          "component": [{"code": cc(LOINC, *VITAL_LOINC["sbp"][:2]), "valueQuantity": quantity(v["sbp"], "mm[Hg]")},
                        {"code": cc(LOINC, *VITAL_LOINC["dbp"][:2]), "valueQuantity": quantity(v["dbp"], "mm[Hg]")},
                        {"code": cc(LOINC, "8478-0", "Mean blood pressure"), "valueQuantity": quantity(v["map"], "mm[Hg]")}]}
    out.append(bp)
    out.append(one("rr", v["rr"], None))
    if v["temp"] is not None:
        out.append(one("temp", v["temp"], None))
    spo2 = one("spo2", v["spo2"], None)
    spo2["component"] = [{"code": {"text": "Oxygen delivery device"}, "valueString": v["o2_device"]}]
    if v["o2_flow"] is not None:
        spo2["component"].append({"code": {"text": "Oxygen flow rate"}, "valueQuantity": quantity(v["o2_flow"], "L/min")})
    out.append(spo2)
    if v["pain"] is not None:
        out.append(one("pain", v["pain"], None))
    if v["gcs"] is not None:
        out.append(one("gcs", v["gcs"], None))
    return out
