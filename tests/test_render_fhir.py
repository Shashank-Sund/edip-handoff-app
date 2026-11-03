"""Invariants of the Redox FHIR R4 event feed (spec sections 2.2, 6, 11 and 12).
Generates the stand in world into tmp_path, renders it, then checks every line."""
import filecmp
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "synthetic"))

from edip_synth import synthea  # noqa: E402
from edip_synth.config import Config  # noqa: E402
from edip_synth.output import write  # noqa: E402
from edip_synth.render import fhir  # noqa: E402
from edip_synth.render.common import load_truth  # noqa: E402
from edip_synth.world import Simulator  # noqa: E402

KNOWN_SYSTEMS = {"http://hl7.org/fhir/sid/us-ssn", "http://hl7.org/fhir/sid/us-npi", "urn:redox:document_kind"}
LOCAL = re.compile(r"^urn:redox:(mgh|lks):[A-Za-z0-9_-]+$")
INJECTED = {"F01", "F03", "F04", "F08"}      # extra PatientAdmin events with no truth counterpart
ADT_CODES = {"arrival": "A04", "transfer": "A02", "discharge": "A03", "merge": "A40", "cancel_transfer": "A12",
             "cancel_discharge": "A13", "cancel_admit": "A11"}
ADT_TYPES = {"arrival": "patient-arrived", "transfer": "patient-transferred", "discharge": "patient-discharged",
             "merge": "patient-merged"}
CLASS = {"E": "EMER", "I": "IMP", "OBS": "OBSENC"}


@pytest.fixture(scope="module")
def world_dir(tmp_path_factory):
    cfg = Config()
    out = tmp_path_factory.mktemp("world")
    write(Simulator(cfg, synthea.stand_in(3000, cfg.seed)).run(), out)
    return out


@pytest.fixture(scope="module")
def feed(world_dir):
    stats = fhir.render(world_dir / "truth", world_dir)
    with open(world_dir / "fhir" / "events.jsonl", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    with open(world_dir / "fhir" / "fault_manifest.jsonl", encoding="utf-8") as f:
        manifest = [json.loads(line) for line in f]
    return {"stats": stats, "rows": rows, "manifest": manifest, "truth": load_truth(world_dir / "truth")}


def resources(row, rtype=None):
    return [e["resource"] for e in row["bundle"]["entry"] if rtype is None or e["resource"]["resourceType"] == rtype]


def walk(x):
    """Yield every dict inside a JSON value."""
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)


def test_every_line_is_a_redox_cloud_delivery(feed):
    for r in feed["rows"]:
        b = r["bundle"]
        meta = b["Meta"]
        assert meta["DataModel"].startswith("FHIR.Event.") and meta["DataModel"] == r["data_model"]
        assert meta["EventType"] in fhir.EVENTS and meta["EventType"] == r["event_type"]
        assert meta["Logs"][0]["ID"] == r["event_id"] and meta["Logs"][0]["AttemptID"] == r["attempt_id"]
        assert meta["EventDateTime"] == r["event_datetime"] and re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$", r["event_datetime"])
        assert meta["Test"] is True and r["is_test"] is True and r["landing_path"] is None
        assert meta["Source"]["ID"] == r["source_id"] and meta["FacilityCode"] == r["facility_code"] in ("MGH", "LKS")
        assert b["resourceType"] == "Bundle" and b["type"] == "transaction" and b["timestamp"] == r["event_datetime"]
        assert b["entry"] and b["entry"][0]["resource"]["resourceType"] == "MessageHeader"
        assert b["entry"][0]["resource"]["eventUri"].startswith("https://fhir.redoxengine.com/EventDefinition/")
        for e in b["entry"]:
            assert e["fullUrl"] == "urn:uuid:" + e["resource"]["id"] and e["resource"]["resourceType"]
            assert e["request"] == {"method": "POST", "url": e["resource"]["resourceType"]}
        if r["hl7_event_code"]:
            ext = meta["Extensions"]
            assert ext["event-code"]["string"] == r["hl7_event_code"]
            assert ext["message-control-id"]["string"] == r["hl7_control_id"] and re.match(r"^[4-7]\d{7}$", r["hl7_control_id"])
        else:
            assert r["hl7_control_id"] is None and "Extensions" not in meta


def test_references_resolve_inside_the_bundle(feed):
    for r in feed["rows"]:
        ids = {e["fullUrl"] for e in r["bundle"]["entry"]}
        assert len(ids) == len(r["bundle"]["entry"])
        for d in walk(r["bundle"]["entry"]):
            if "reference" in d:
                assert d["reference"] in ids, (r["event_id"], d)
        for ref in r["bundle"]["entry"][0]["resource"]["focus"]:
            assert ref["reference"] in ids


def test_identifier_systems_follow_the_spec(feed):
    for r in feed["rows"]:
        src = r["facility_code"].lower()
        for d in walk(r["bundle"]["entry"]):
            if "system" in d and d["system"].startswith("urn:redox"):
                assert LOCAL.match(d["system"]) and f":{src}:" in d["system"] or d["system"] in KNOWN_SYSTEMS, d
        for p in resources(r, "Patient"):
            systems = {i["system"] for i in p["identifier"]}
            assert f"urn:redox:{src}:MR" in systems and f"urn:redox:{src}:EPI" in systems
            mrn = next(i["value"] for i in p["identifier"] if i["system"].endswith(":MR"))
            assert re.match(r"^\d{8}$", mrn)
        for e in resources(r, "Encounter"):
            csn = next(i["value"] for i in e["identifier"] if i["system"].endswith(":VN"))
            assert re.match(r"^\d{11}$", csn)
            assert e["class"]["system"] == "http://terminology.hl7.org/CodeSystem/v3-ActCode"
            assert e["class"]["code"] in ("EMER", "IMP", "OBSENC")
        for p in resources(r, "Practitioner"):
            assert any(i["system"] == "http://hl7.org/fhir/sid/us-npi" and re.match(r"^\d{10}$", i["value"])
                       for i in p["identifier"])
        for d in resources(r, "DocumentReference"):
            assert d["category"][0]["coding"][0]["system"] == "urn:redox:document_kind"
            assert d["type"]["coding"][0]["system"] == "http://loinc.org"
            assert d["content"][0]["attachment"]["contentType"] in ("text/plain", "text/html")


def test_all_timestamps_are_utc_with_z(feed):
    with_offset = re.compile(r'"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?[+-]\d\d:?\d\d"')
    for r in feed["rows"][::7]:
        text = json.dumps(r["bundle"])
        assert not with_offset.search(text)
        assert "\u2014" not in text and "\u2013" not in text


def test_patient_admin_reconciles_with_truth_adt(feed):
    truth = feed["truth"]
    profile = truth["config"]["site_profile"]
    injected = {m["event_id"] for m in feed["manifest"] if m["fault_id"] in INJECTED}
    seen, by_csn = set(), defaultdict(list)
    for r in feed["rows"]:
        if r["data_model"] != "FHIR.Event.PatientAdmin" or r["event_id"] in seen or r["event_id"] in injected:
            continue
        seen.add(r["event_id"])
        by_csn[csn_of(r)].append(r)
    adt = defaultdict(list)
    for a in truth["adt"]:
        adt[a["csn"]].append(a)
    assert set(by_csn) == set(adt)
    for csn, rows in adt.items():
        rows = sorted(rows, key=lambda a: a["seq"])
        evs = sorted(by_csn[csn], key=lambda r: (r["event_datetime"], r["hl7_control_id"]))
        assert len(evs) == len(rows), csn
        assert [e["event_datetime"][:19] + "Z" for e in evs] == sorted(a["recorded_at"] for a in rows)
        fac = rows[0]["facility"]
        by_time = defaultdict(list)
        for e in evs:
            by_time[e["event_datetime"][:19] + "Z"].append(e)
        for a in rows:
            cands = by_time[a["recorded_at"]]
            assert any(e["event_type"] == ADT_TYPES.get(a["kind"], "patient-updated") for e in cands), (csn, a["kind"])
            if a["kind"] in ADT_CODES:
                assert any(e["hl7_event_code"] == ADT_CODES[a["kind"]] for e in cands)
            if a["kind"] == "class_change":
                assert any(e["hl7_event_code"] == profile[fac]["class_change"] for e in cands)
            if a["kind"] in ("cancel_transfer", "cancel_discharge", "cancel_admit"):
                assert any(e["bundle"]["Meta"].get("CanceledEvent") for e in cands)
        last = max(evs, key=lambda r: r["event_datetime"])
        enc = resources(last, "Encounter")[0]
        assert enc["class"]["code"] == CLASS[rows[-1]["patient_class"]]
        truth_enc = next(e for e in truth["encounters"] if e["csn"] == csn)
        expected = {"discharged": "finished", "left": "finished", "transferred": "finished", "canceled": "cancelled"}
        if truth_enc["state_at_end"] in expected:
            assert enc["status"] == expected[truth_enc["state_at_end"]], csn
        if enc["status"] in ("arrived", "in-progress"):
            last_row = max(rows, key=lambda a: a["recorded_at"])
            active = [x for x in enc["location"] if x["status"] == "active"]
            assert len(active) == 1 and active[0]["location"]["display"] == \
                "-".join(x for x in (last_row["unit"], last_row["room"], last_row["bed"]) if x), csn


def csn_of(row):
    enc = resources(row, "Encounter")
    if enc:
        return next(i["value"] for i in enc[0]["identifier"] if i["system"].endswith(":VN"))
    return None


def test_merge_retires_the_duplicate_identity(feed):
    truth = feed["truth"]
    pats = {p["pat_id"]: p for p in truth["patients"]}
    merged = [e for e in truth["encounters"] if e["registered_pat_id"]]
    assert merged
    for enc in merged:
        dup, surv = pats[enc["registered_pat_id"]], pats[enc["pat_id"]]
        evs = [r for r in feed["rows"] if r["data_model"] == "FHIR.Event.PatientAdmin" and csn_of(r) == enc["csn"]]
        merge = [r for r in evs if r["event_type"] == "patient-merged"]
        assert len({r["event_id"] for r in merge}) == 1
        pats_in = resources(merge[0], "Patient")
        assert {p["active"] for p in pats_in if "link" in p} == {True, False}
        for r in evs:
            mrns = {i["value"] for p in resources(r, "Patient") for i in p["identifier"] if i["system"].endswith(":MR")}
            if r["event_datetime"] < merge[0]["event_datetime"]:
                assert dup["mrn"] in mrns and surv["mrn"] not in mrns
            elif r["event_type"] != "patient-merged":
                assert surv["mrn"] in mrns and dup["mrn"] not in mrns


def test_every_result_mar_and_signed_note_has_an_event(feed):
    truth = feed["truth"]
    reports, admins, docs, criticals, vitals = set(), set(), set(), set(), 0
    for r in feed["rows"]:
        for dr in resources(r, "DiagnosticReport"):
            ids = {i["value"] for i in dr.get("identifier", [])}
            for oid in ids:
                reports.add((oid, dr["issued"], dr["status"]))
            if dr["category"][0]["coding"][0]["code"] == "vital-signs":
                vitals += 1
            for o in resources(r, "Observation"):
                if any(c["code"] in ("HH", "LL") for i in o.get("interpretation", []) for c in i["coding"]) and \
                        dr["status"] != "partial":
                    criticals.add(o["id"])
        mrs = {m["id"]: m["identifier"][0]["value"] for m in resources(r, "MedicationRequest") if "identifier" in m}
        for ma in resources(r, "MedicationAdministration"):
            at = ma.get("effectiveDateTime") or ma["effectivePeriod"]["start"]
            admins.add((mrs[ma["request"]["reference"].split(":")[-1]], at, ma["status"]))
        for d in resources(r, "DocumentReference"):
            docs.add((d["masterIdentifier"]["value"], d["docStatus"]))
    status = {"P": "preliminary", "F": "final", "C": "corrected"}
    for x in truth["results"]:
        if x["status"] in ("F", "C"):
            assert (x["order_id"], x["resulted_at"], status[x["status"]]) in reports, x
    assert len(criticals) == sum(1 for x in truth["results"] if x["flag"] in ("HH", "LL"))
    for m in truth["mar"]:
        assert (m["order_med_id"], m["taken_at"], "not-done" if m["action"] == "Held" else "completed") in admins, m
    for n in truth["notes"]:
        assert (n["note_id"], "preliminary") in docs
        if n["signed_at"]:
            assert (n["note_id"], "final") in docs
    assert vitals == len(truth["vitals"])


def test_delivery_order_is_monotonic_and_faults_are_delivered(feed):
    rows, manifest = feed["rows"], feed["manifest"]
    deliveries = [r["delivery_ts"] for r in rows]
    assert deliveries == sorted(deliveries)
    ids = {r["event_id"] for r in rows}
    allowed = {"F01", "F02", "F03", "F04", "F05", "F06", "F08", "F24", "F27", "F28", "F30"}
    assert {m["fault_id"] for m in manifest} == allowed
    for m in manifest:
        assert m["event_id"] in ids and m["level"] == "render" and m["stream"] == "fhir", m
    by_id = defaultdict(list)
    for r in rows:
        by_id[r["event_id"]].append(r)
    for m in manifest:
        if m["fault_id"] == "F02":
            attempts = {r["attempt_id"] for r in by_id[m["event_id"]]}
            assert len(attempts) >= 2
    control = defaultdict(set)
    for r in rows:
        if r["hl7_control_id"]:
            control[r["hl7_control_id"]].add(r["event_id"])
    for m in manifest:
        if m["fault_id"] == "F08":
            r = by_id[m["event_id"]][0]
            assert len(control[r["hl7_control_id"]]) == 2     # the original and the replay share MSH-10
            later = [x for x in rows if csn_of(x) == m["csn"] and x["hl7_event_code"] == "A02"]
            assert later and r["delivery_ts"] > later[-1]["delivery_ts"] > r["event_datetime"]
        if m["fault_id"] == "F05":
            transfer = by_id[m["event_id"]][0]
            cls = [x for x in rows if csn_of(x) == m["csn"] and x["hl7_event_code"] in ("A06", "A08")
                   and x["event_datetime"] < transfer["event_datetime"]]
            assert any(x["delivery_ts"] > transfer["delivery_ts"] for x in cls)
        if m["fault_id"] == "F06":
            result = by_id[m["event_id"]][0]
            arrival = [x for x in rows if csn_of(x) == m["csn"] and x["event_type"] == "patient-arrived"]
            assert arrival and arrival[0]["delivery_ts"] > result["delivery_ts"]
        if m["fault_id"] == "F04":
            assert not resources(by_id[m["event_id"]][0], "Encounter")
        if m["fault_id"] == "F28":
            dr = resources(by_id[m["event_id"]][0], "DiagnosticReport")[0]
            assert dr["status"] == "partial"
        if m["fault_id"] == "F27":
            d = resources(by_id[m["event_id"]][0], "DocumentReference")[0]
            assert d["content"][0]["attachment"]["contentType"] == "text/html"
    dupes = Counter(r["event_id"] for r in rows)
    assert sum(1 for m in manifest if m["fault_id"] == "F02") == sum(v - 1 for v in dupes.values())


def test_clinical_resources_are_coded(feed):
    seen = Counter()
    for r in feed["rows"]:
        for o in resources(r, "Observation"):
            if "unit" in o.get("valueQuantity", {}):
                assert o["valueQuantity"]["system"] == "http://unitsofmeasure.org"
            if o["category"][0].get("coding"):
                assert o["code"]["coding"][0]["system"] == "http://loinc.org" or o["code"]["coding"][0]["code"] == "85354-9"
            seen[o["category"][0].get("coding", [{"code": "text"}])[0]["code"]] += 1
        for m in resources(r, "MedicationRequest") + resources(r, "MedicationAdministration"):
            assert m["medicationCodeableConcept"]["coding"][0]["system"] == "http://www.nlm.nih.gov/research/umls/rxnorm"
        for a in resources(r, "AllergyIntolerance"):
            assert a["clinicalStatus"]["coding"][0]["code"] == "active" and a["patient"]
            seen["allergy"] += 1
        for f in resources(r, "Flag"):
            assert f["status"] == "active" and f["code"]["text"]
            seen["flag:" + f["category"][0]["text"]] += 1
    assert seen["laboratory"] and seen["vital-signs"] and seen["imaging"] and seen["allergy"]
    assert seen["flag:Isolation"] and seen["flag:Code status"]


def test_deterministic_and_message_bundle_variant(world_dir, feed, tmp_path):
    again = fhir.render(world_dir / "truth", tmp_path / "again")
    assert again["events"] == feed["stats"]["events"]
    assert filecmp.cmp(world_dir / "fhir" / "events.jsonl", tmp_path / "again" / "fhir" / "events.jsonl", shallow=False)
    truth = feed["truth"]
    truth = {**truth, "adt": truth["adt"][:40], "results": [], "vitals": [], "med_orders": [], "mar": [], "notes": []}
    msg = fhir.Renderer(truth, "message").run(tmp_path / "message")
    with open(Path(msg["path"]), encoding="utf-8") as f:
        for line in f:
            b = json.loads(line)["bundle"]
            assert b["type"] == "message" and "Meta" in b and all("request" not in e for e in b["entry"])
