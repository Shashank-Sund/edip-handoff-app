"""Check every clinical code the generator emits against public terminology APIs
and write the findings to code_verification.json next to this script.

Sources (read only, no key needed):
  RxNorm    https://rxnav.nlm.nih.gov/REST/rxcui/<id>/properties.json and historystatus.json
  LOINC     https://clinicaltables.nlm.nih.gov/api/loinc_items/v3/search (existence, name)
            https://tx.fhir.org/r4/CodeSystem/$lookup (STATUS property: ACTIVE, DISCOURAGED, DEPRECATED)
  ICD-10-CM https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search
  SNOMED CT https://tx.fhir.org/r4/CodeSystem/$lookup

Run: python3 synthetic/tools/verify_codes.py [--refresh]
The JSON is committed so the realism tests (items 20 and 21) run offline.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from edip_synth import scenarios, world  # noqa: E402
from edip_synth.reference import IMAGING, LABS, VITAL_LOINC  # noqa: E402
from edip_synth.synthea import _CHRONIC_ICD10  # noqa: E402

OUT = HERE / "code_verification.json"
NOTE_LOINC = {"84249-2": "ED Triage Notes", "28568-4": "ED Provider Notes", "57053-1": "ED Notes (nursing)",
              "104530-1": "H&P (hospitalist)", "11488-4": "Consults", "28651-8": "ED to Hosp-Admission Nursing Transfer",
              "11506-3": "Progress Notes"}
_SLEEP = 0.15


def _get(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "edip-synth-code-check/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read().decode("utf-8")
            time.sleep(_SLEEP)
            return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1.5 * (i + 1))
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1.5 * (i + 1))
    return None


# ------------------------------------------------------------------ collectors
def collect():
    items = {}

    def add(system, code, expected, used_in):
        key = (system, str(code))
        if key in items:
            items[key]["used_in"] = sorted(set(items[key]["used_in"]) | {used_in})
        else:
            items[key] = {"code": str(code), "system": system, "expected": expected, "used_in": [used_in]}

    for book, label in ((scenarios.SCENARIOS, "scenarios.SCENARIOS"), (scenarios.GENERIC_ADMITS, "scenarios.GENERIC_ADMITS"),
                        (scenarios.DISCHARGE, "scenarios.DISCHARGE")):
        for s in book.values():
            for m in s.meds:
                add("RxNorm", m.rxcui, m.name, f"{label}[{s.key}].meds")
            for icd, sno, disp in s.dx:
                add("ICD-10-CM", icd, disp, f"{label}[{s.key}].dx")
                if sno:
                    add("SNOMED CT", sno, disp, f"{label}[{s.key}].dx")
            for c in s.conditions:
                add("SNOMED CT", c, "patient fit condition", f"{label}[{s.key}].conditions")
    add("RxNorm", scenarios.ENOX.rxcui, scenarios.ENOX.name, "scenarios.ENOX")
    add("RxNorm", "1665088", "cefepime 2 g injection (penicillin allergy substitute)", "world._meds")
    for name, code, system, _ in world.ALLERGENS:
        if code and system == "RxNorm":
            add("RxNorm", code, f"allergen: {name}", "world.ALLERGENS")
        if code and system == "SNOMED":
            add("SNOMED CT", code, f"allergen: {name}", "world.ALLERGENS")
    add("RxNorm", "26225", "allergen: Ondansetron", "world._pre_bed_faults F21")
    for k, cat in LABS.items():
        add("LOINC", cat[0], cat[1], f"reference.LABS[{k}]")
    for k, (disp, loinc, _, _) in IMAGING.items():
        add("LOINC", loinc, disp, f"reference.IMAGING[{k}]")
    for k, (loinc, disp, _) in VITAL_LOINC.items():
        add("LOINC", loinc, disp, f"reference.VITAL_LOINC[{k}]")
    for loinc, disp in NOTE_LOINC.items():
        add("LOINC", loinc, disp, "world._notes")
    for sno, icd in _CHRONIC_ICD10.items():
        add("ICD-10-CM", icd, "chronic problem mapping", "synthea._CHRONIC_ICD10")
        add("SNOMED CT", sno, "chronic problem (Synthea)", "synthea._CHRONIC_ICD10")
    unverified = {("RxNorm", c) for c in scenarios.UNVERIFIED["rxcui"]} | {("LOINC", c) for c in scenarios.UNVERIFIED["loinc"]} \
        | {("ICD-10-CM", c) for c in scenarios.UNVERIFIED["icd10"]}
    for key, it in items.items():
        it["in_unverified_list"] = key in unverified
    missing = [k for k in unverified if k not in items]
    for sys_, code in missing:
        items[(sys_, code)] = {"code": code, "system": sys_, "expected": "(listed in scenarios.UNVERIFIED only)",
                               "used_in": ["scenarios.UNVERIFIED"], "in_unverified_list": True}
    return items


# ------------------------------------------------------------------ lookups
def check_rxnorm(code):
    base = f"https://rxnav.nlm.nih.gov/REST/rxcui/{code}"
    props = _get(base + "/properties.json")
    hist = _get(base + "/historystatus.json")
    name = tty = None
    status = "not found"
    if props:
        p = json.loads(props).get("properties") or {}
        name, tty = p.get("name"), p.get("tty")
    if hist:
        meta = (json.loads(hist).get("rxcuiStatusHistory") or {}).get("metaData") or {}
        status = meta.get("status") or status
        if not name:
            attrs = (json.loads(hist).get("rxcuiStatusHistory") or {}).get("attributes") or {}
            name, tty = attrs.get("name"), attrs.get("tty")
    found = name is not None
    return {"status_found": status, "name_found": name, "tty": tty, "verified": found and status == "Active",
            "source_url": base + "/properties.json"}


def check_loinc(code):
    q = urllib.parse.urlencode({"terms": code, "df": "LOINC_NUM,LONG_COMMON_NAME", "sf": "LOINC_NUM"})
    url = f"https://clinicaltables.nlm.nih.gov/api/loinc_items/v3/search?{q}"
    body = _get(url)
    name = None
    if body:
        data = json.loads(body)
        for row in data[3] or []:
            if row[0] == code:
                name = row[1]
    status = "not found"
    tx = _get("https://tx.fhir.org/r4/CodeSystem/$lookup?" + urllib.parse.urlencode({"system": "http://loinc.org", "code": code}))
    if tx:
        try:
            for part in json.loads(tx).get("parameter", []):
                if part.get("name") == "property":
                    parts = {x["name"]: x for x in part.get("part", [])}
                    if parts.get("code", {}).get("valueCode") == "STATUS":
                        v = parts.get("value", {})
                        status = v.get("valueString") or v.get("valueCode") or status
                if part.get("name") == "display" and not name:
                    name = part.get("valueString")
        except (ValueError, KeyError):
            pass
    if name and status == "not found":
        status = "found (status not exposed)"
    return {"status_found": status, "name_found": name, "verified": name is not None and status in ("ACTIVE", "found (status not exposed)"),
            "source_url": url}


def check_icd10(code):
    q = urllib.parse.urlencode({"sf": "code", "terms": code, "maxList": 50})
    url = f"https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search?{q}"
    body = _get(url)
    name = None
    if body:
        data = json.loads(body)
        for row in data[3] or []:
            if row[0] == code:
                name = row[1]
    return {"status_found": "billable code" if name else "not found", "name_found": name, "verified": name is not None,
            "source_url": url}


def check_snomed(code):
    url = "https://tx.fhir.org/r4/CodeSystem/$lookup?" + urllib.parse.urlencode({"system": "http://snomed.info/sct", "code": code})
    body = _get(url)
    name, inactive = None, None
    if body:
        try:
            for part in json.loads(body).get("parameter", []):
                if part.get("name") == "display":
                    name = part.get("valueString")
                if part.get("name") == "property":
                    parts = {x["name"]: x for x in part.get("part", [])}
                    if parts.get("code", {}).get("valueCode") == "inactive":
                        inactive = parts.get("value", {}).get("valueBoolean")
        except (ValueError, KeyError):
            pass
    status = "not found" if name is None else ("inactive" if inactive else "active")
    return {"status_found": status, "name_found": name, "verified": name is not None and not inactive, "source_url": url}


CHECK = {"RxNorm": check_rxnorm, "LOINC": check_loinc, "ICD-10-CM": check_icd10, "SNOMED CT": check_snomed}


# Findings from the 2026-11-02 run that need a change in the generator (not applied here; the realism
# report lists them). Each replacement was looked up on the same public APIs.
CORRECTIONS = [
    {"system": "LOINC", "code": "36916-5", "where": "reference.IMAGING[CTPE]", "problem": "36916-5 is Kappa light chains.free in Serum, not a CT angiogram",
     "replace_with": "88322-3", "replacement_name": "CT Chest W contrast IV and CT angiogram Pulmonary arteries for pulmonary embolus W contrast IV"},
    {"system": "RxNorm", "code": "70618", "where": "world.ALLERGENS Penicillins", "problem": "ingredient concept retired (NotCurrent since 02/2020)",
     "replace_with": "7980", "replacement_name": "penicillin G (IN, Active); or RxClass MeSH D010406 'Penicillins' if a class code is wanted"},
    {"system": "ICD-10-CM", "code": "E88.81", "where": "synthea._CHRONIC_ICD10[237602007]", "problem": "E88.81 was subdivided in FY2023 and is no longer billable",
     "replace_with": "E88.810", "replacement_name": "Metabolic syndrome"},
    {"system": "SNOMED CT", "code": "227037002", "where": "world.ALLERGENS Shellfish", "problem": "concept 'Fish - dietary' is inactive",
     "replace_with": "44027008", "replacement_name": "Seafood (active); use 735029006 Shellfish if the terminology server carries it"},
    {"system": "SNOMED CT", "code": "68566005", "where": "scenarios sepsis_uti and pyelo dx (N10)", "problem": "68566005 is 'Urinary tract infectious disease', paired with N10 Acute pyelonephritis",
     "replace_with": "36689008", "replacement_name": "Acute pyelonephritis (keep 68566005 for N39.0 in uti_d)"},
    {"system": "LOINC", "code": "92142-9", "where": "reference.LABS[FLU]", "problem": "code is Influenza virus A RNA only; display says 'Influenza A and B RNA'",
     "replace_with": "92142-9 plus 92141-1", "replacement_name": "add FLUB 92141-1 Influenza virus B RNA, or rename the display to Influenza A RNA; panel code 95422-2 covers A, B and SARS-CoV-2"},
    {"system": "LOINC", "code": "37622-8", "where": "reference.IMAGING[XRHIP]", "problem": "37622-8 is 'XR Pelvis AP'; display says 'XR hip and pelvis'",
     "replace_with": "30767-8", "replacement_name": "XR Pelvis and Hip Views"},
    {"system": "LOINC", "code": "24558-9", "where": "reference.IMAGING[RUQUS]", "problem": "24558-9 is 'US Abdomen' (complete); display says right upper quadrant",
     "replace_with": "24558-9", "replacement_name": "acceptable; rename the display to 'US abdomen' or use a limited RUQ code from the site's catalog"},
    {"system": "RxNorm", "code": "10180", "where": "world.ALLERGENS Sulfa", "problem": "10180 is the ingredient sulfamethoxazole, used for the sulfonamide class",
     "replace_with": "10180", "replacement_name": "acceptable approximation; note it in the dataset metadata"},
    {"system": "SNOMED CT", "code": "6525002", "where": "synthea._CHRONIC_ICD10 key (Synthea source code)", "problem": "'Dependent drug abuse' is inactive in SNOMED CT International 2025",
     "replace_with": "6525002", "replacement_name": "Synthea emits it; keep the mapping to F19.20 and flag the source code as retired"},
]


def _words(s):
    return set(re.findall(r"[a-z]{4,}", (s or "").lower()))


def main():
    refresh = "--refresh" in sys.argv
    prior = {}
    if OUT.exists() and not refresh:
        for row in json.loads(OUT.read_text())["codes"]:
            prior[(row["system"], row["code"])] = row
    items = collect()
    rows = []
    for key in sorted(items, key=lambda k: (k[0], k[1])):
        it = dict(items[key])
        if key in prior and prior[key].get("name_found") is not None:
            it.update({k: prior[key][k] for k in ("status_found", "name_found", "verified", "source_url", "tty") if k in prior[key]})
        else:
            it.update(CHECK[key[0]](key[1]))
        exp, got = _words(it["expected"]), _words(it["name_found"])
        it["name_consistent"] = bool(exp & got) if it["name_found"] else False
        rows.append(it)
        flag = "" if it["verified"] and it["name_consistent"] else "  <-- review"
        print(f"{it['system']:10} {it['code']:12} {it['status_found']:28} {str(it['name_found'])[:70]}{flag}")
    summary = {"total": len(rows), "verified": sum(r["verified"] for r in rows),
               "not_verified": [f"{r['system']} {r['code']}" for r in rows if not r["verified"]],
               "corrections": CORRECTIONS,
               "name_review": [f"{r['system']} {r['code']}: expected '{r['expected']}', found '{r['name_found']}'"
                               for r in rows if r["verified"] and not r["name_consistent"]]}
    OUT.write_text(json.dumps({"generated_by": "synthetic/tools/verify_codes.py", "checked_on": time.strftime("%Y-%m-%d"),
                               "summary": summary, "codes": rows}, indent=1, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
