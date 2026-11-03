"""The Redox FHIR feed parses back to the truth world through edip_contract records.
Generates the stand in world into tmp_path, renders the FHIR feed, parses every event."""
import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "synthetic"))
sys.path.insert(0, str(ROOT / "pipelines"))

import edip_contract  # noqa: E402
from edip_synth import synthea  # noqa: E402
from edip_synth.config import Config  # noqa: E402
from edip_synth.output import write  # noqa: E402
from edip_synth.render import fhir  # noqa: E402
from edip_synth.render.common import load_truth, ts  # noqa: E402
from edip_synth.world import Simulator  # noqa: E402
from parse_fhir import parse_fhir  # noqa: E402

# Truth ADT kinds that the contract folds into "update".
UPDATE_KINDS = {"triage", "roomed", "provider", "allergy_update", "isolation_update", "code_status_update"}
EXTRA = {"F01", "F08"}       # renderer copies with no truth counterpart (documented exceptions)


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    cfg = Config()
    out = tmp_path_factory.mktemp("world")
    write(Simulator(cfg, synthea.stand_in(3000, cfg.seed)).run(), out)
    fhir.render(out / "truth", out)
    records = edip_contract.empty()
    rows = []
    with open(out / "fhir" / "events.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            row["bundle"] = json.dumps(row["bundle"])           # the pipeline hands the bundle over as a string
            parsed = parse_fhir(row)
            assert set(parsed) == set(edip_contract.KINDS)
            for k, v in parsed.items():
                for r in v:
                    assert set(r) == set(edip_contract.KINDS[k]), (k, set(r) ^ set(edip_contract.KINDS[k]))
                records[k].extend(v)
            rows.append(row)
    with open(out / "fhir" / "fault_manifest.jsonl", encoding="utf-8") as f:
        manifest = [json.loads(line) for line in f]
    return {"records": records, "rows": rows, "manifest": manifest, "truth": load_truth(out / "truth")}


def dedupe(recs):
    """Silver semantics: one record per Redox log id, then one per content hash, earliest event time wins."""
    seen_key, seen_hash, out = set(), set(), []
    for r in sorted(recs, key=lambda r: r["recorded_ts"]):
        if r["msg_key"] in seen_key or r["content_hash"] in seen_hash:
            continue
        seen_key.add(r["msg_key"])
        seen_hash.add(r["content_hash"])
        out.append(r)
    return out


def test_every_event_parses_to_something(parsed):
    counts = {k: len(v) for k, v in parsed["records"].items()}
    assert counts["adt"] and counts["obs"] and counts["med"] and counts["note"] and counts["allergy"] and counts["flag"]
    f04 = {m["event_id"] for m in parsed["manifest"] if m["fault_id"] == "F04"}
    f03 = {m["event_id"] for m in parsed["manifest"] if m["fault_id"] == "F03"}
    quarantined = {q["msg_key"] for q in parsed["records"]["quarantine"]}
    assert f04 <= quarantined and quarantined - f04 <= f03          # only F04 events and their content copies
    assert all("Encounter" in q["reason"] for q in parsed["records"]["quarantine"])
    for r in parsed["records"]["adt"]:
        assert r["kind"] in edip_contract.ADT_KINDS and r["patient_class"] in ("E", "I", "OBS")
        assert r["effective_ts"].tzinfo is not None and r["recorded_ts"].tzinfo is not None
        assert r["facility"] in ("MGH", "LKS") and len(r["csn"]) == 11 and len(r["mrn"]) == 8


def test_adt_reconciles_with_truth_after_dedupe(parsed):
    truth = parsed["truth"]
    extra = {m["event_id"] for m in parsed["manifest"] if m["fault_id"] in EXTRA}
    recs = [r for r in dedupe(parsed["records"]["adt"]) if r["msg_key"] not in extra]
    got = {(r["csn"], r["kind"], r["effective_ts"].replace(microsecond=0)) for r in recs}
    want = {(a["csn"], "update" if a["kind"] in UPDATE_KINDS else a["kind"], ts(a["effective_at"])) for a in truth["adt"]}
    assert got == want, (sorted(got - want)[:5], sorted(want - got)[:5])
    assert len(recs) == len(truth["adt"])
    # Content duplicates (F03) and retries (F02) were the only things removed besides the listed extras.
    raw = [r for r in parsed["records"]["adt"] if r["msg_key"] not in extra]
    f02 = sum(1 for m in parsed["manifest"] if m["fault_id"] == "F02")
    f03 = sum(1 for m in parsed["manifest"] if m["fault_id"] == "F03")
    assert len(raw) - len(recs) <= f02 + f03
    # Field level checks on the truth rows that carry them.
    by_key = {(r["csn"], r["effective_ts"].replace(microsecond=0), r["kind"]): r for r in recs}
    profile = truth["config"]["site_profile"]
    for a in truth["adt"]:
        kind = "update" if a["kind"] in UPDATE_KINDS else a["kind"]
        r = by_key[(a["csn"], ts(a["effective_at"]), kind)]
        assert r["patient_class"] == a["patient_class"]
        assert r["recorded_ts"].replace(microsecond=0) == ts(a["recorded_at"])
        assert (r["unit"], r["room"], r["bed"]) == (a["unit"], a["room"], a["bed"]), (a["csn"], a["kind"])
        if a["kind"] == "class_change":
            assert r["trigger"] == profile[a["facility"]]["class_change"]
        if a["kind"] == "pending_admit":
            assert r["pending_unit"] == a["details"]["pending_unit"] and r["level_of_care"] == a["details"]["level_of_care"]
            assert r["service"] == a["details"]["service"] and r["admitting_id"] == a["details"]["admitting"]
        if a["kind"] == "bed_assigned":
            assert (r["pending_unit"], r["pending_room"], r["pending_bed"]) == \
                (a["details"]["pending_unit"], a["details"]["pending_room"], a["details"]["pending_bed"])
        if a["kind"] == "bed_unassigned":
            assert r["pending_unit"] is None
        if a["kind"] == "triage":
            assert r["esi"] == a["details"]["esi"] and r["chief_complaint"] == a["details"]["chief_complaint"]
        if a["kind"] == "provider":
            assert r["attending_id"] == a["details"]["provider"]
        if a["kind"] == "discharge":
            assert r["disposition"] == a["details"]["disposition"]
        if a["kind"] == "isolation_update":
            assert r["isolation"] == a["details"]["isolation"]
        if a["kind"] == "code_status_update":
            assert r["code_status"] == a["details"]["code_status"]
        if a["kind"] == "merge":
            pats = {p["pat_id"]: p for p in truth["patients"]}
            assert r["surviving_mrn"] == pats[a["details"]["surviving"]]["mrn"]
            assert r["retired_mrn"] == pats[a["details"]["retired"]]["mrn"]
        if a["kind"] in ("cancel_transfer", "cancel_discharge", "cancel_admit"):
            assert r["trigger"] in ("A11", "A12", "A13")


def test_latest_location_matches_truth(parsed):
    truth = parsed["truth"]
    extra = {m["event_id"] for m in parsed["manifest"] if m["fault_id"] in EXTRA}
    latest = {}
    for r in dedupe(parsed["records"]["adt"]):
        if r["msg_key"] in extra:
            continue
        key = (r["effective_ts"], r["recorded_ts"])
        if r["csn"] not in latest or key > latest[r["csn"]][0]:
            latest[r["csn"]] = (key, r)
    final = {}
    for a in truth["adt"]:
        key = (ts(a["effective_at"]), ts(a["recorded_at"]))
        if a["csn"] not in final or key > final[a["csn"]][0]:
            final[a["csn"]] = (key, a)
    assert set(latest) == set(final)
    for csn, (_, a) in final.items():
        r = latest[csn][1]
        assert (r["unit"], r["room"], r["bed"], r["patient_class"]) == (a["unit"], a["room"], a["bed"], a["patient_class"]), csn


def test_results_vitals_meds_and_notes_match_truth(parsed):
    truth, recs = parsed["truth"], parsed["records"]
    labs = {(o["order_id"], o["code"], o["status"], o["resulted_ts"], o["numeric"]) for o in recs["obs"]
            if o["category"] in ("lab", "imaging", "ecg") and o["numeric"] is not None}
    for x in truth["results"]:
        if x["status"] in ("F", "C") and x["numeric"] is not None:
            assert (x["order_id"], x["lab"], x["status"], ts(x["resulted_at"]), float(x["numeric"])) in labs, x
    text_results = {(o["order_id"], o["code"], o["status"], o["value"]) for o in recs["obs"] if o["numeric"] is None}
    for x in truth["results"]:
        if x["numeric"] is None and x["status"] in ("F", "C"):
            assert (x["order_id"], x["lab"], x["status"], x["value"]) in text_results, x
    statuses = {o["status"] for o in recs["obs"]}
    assert {"P", "F", "C", "I"} <= statuses
    vitals = defaultdict(dict)
    for o in recs["obs"]:
        if o["category"] == "vital":
            vitals[(o["csn"], o["line"])][o["code"]] = (o["numeric"], o["validated"])
    for v in truth["vitals"]:
        got = vitals[(v["csn"], v["line"])]
        assert got["hr"] == (float(v["hr"]), v["validated"]) and got["sbp"][0] == float(v["sbp"]), v
        assert got["spo2"][0] == float(v["spo2"]) and got["map"][0] == float(v["map"])
    assert any(not o["validated"] for o in recs["obs"] if o["category"] == "vital")
    admins = {(m["order_id"], m["action"], m["line"], m["event_ts"]) for m in recs["med"] if m["event"] == "administration"}
    for m in truth["mar"]:
        assert (m["order_med_id"], m["action"], m["line"], ts(m["taken_at"])) in admins, m
    orders = {(m["order_id"], m["rxcui"], m["dose"], m["unit"], m["route"], m["rate"], m["frequency"])
              for m in recs["med"] if m["event"] == "order"}
    for mo in truth["med_orders"]:
        assert (mo["order_med_id"], mo["rxcui"], mo["dose"], mo["unit"], mo["route"], mo["rate"], mo["freq"]) in orders, mo
    notes = {(n["note_id"], n["status"]): n for n in recs["note"]}
    for n in truth["notes"]:
        assert notes[(n["note_id"], "In Progress")]["text"] == n["text"]
        if n["signed_at"]:
            s = notes[(n["note_id"], "Signed")]
            assert s["text"] == n["text"] and s["signed_ts"] == ts(n["signed_at"]) and s["author_id"] == n["author"]
            assert s["note_type"] == n["note_type"] and s["loinc"] == n["loinc"] and s["service_ts"] == ts(n["service_at"])
    html_notes = {m["event_id"] for m in parsed["manifest"] if m["fault_id"] == "F27"}
    assert html_notes and all(n["text"] for n in recs["note"] if n["msg_key"] in html_notes)


def test_allergies_and_flags(parsed):
    truth, recs = parsed["truth"], parsed["records"]
    allergens = {(a["mrn"], a["allergen"], a["code"] or "", a["system"]) for a in recs["allergy"]}
    pats = {p["pat_id"]: p for p in truth["patients"]}
    seen_pats = {a["mrn"] for a in recs["allergy"]}
    for a in truth["allergies"]:
        mrn = pats[a["pat_id"]]["mrn"]
        if mrn in seen_pats:
            assert (mrn, a["allergen"], a["code"], a["system"]) in allergens, a
    flags = {(f["csn"], f["kind"], f["value"], f["recorded_ts"]) for f in recs["flag"]}
    for c in truth["code_status"]:
        assert (c["csn"], "code_status", c["status"], ts(c["activated_at"])) in flags, c
    for i in truth["infections"]:
        if i["source"] == "this_visit":
            assert any(k[1] == "isolation" and k[2] == i["isolation"] and k[3] == ts(i["added_at"]) for k in flags), i
