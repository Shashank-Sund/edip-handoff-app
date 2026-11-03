"""The HL7 parser reconciles the rendered feed with the truth world: render the stand in world,
parse every file, dedupe on msg_key and content_hash, compare to truth."""
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "synthetic"))
sys.path.insert(0, str(ROOT / "pipelines"))

from edip_synth import synthea  # noqa: E402
from edip_synth.config import Config  # noqa: E402
from edip_synth.output import write  # noqa: E402
from edip_synth.render import hl7  # noqa: E402
from edip_synth.render.common import load_truth, ts  # noqa: E402
from edip_synth.world import Simulator  # noqa: E402
import edip_contract as contract  # noqa: E402
from parse_hl7 import parse_hl7, parse_ts  # noqa: E402

UPDATE_KINDS = {"triage", "roomed", "provider", "allergy_update", "isolation_update", "code_status_update"}
TZ = ZoneInfo("America/New_York")


def fold(dt):
    """LKS sends no UTC offset, so a time in the repeated 01:00 hour of 2026-11-01 is ambiguous. The parser
    resolves it against the receipt time; comparisons are exact (this helper is the identity)."""
    return dt


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    cfg = Config()
    out = tmp_path_factory.mktemp("world")
    write(Simulator(cfg, synthea.stand_in(3000, cfg.seed)).run(), out)
    hl7.render(out / "truth", out)
    index = [json.loads(l) for l in open(out / "hl7" / "messages.jsonl", encoding="utf-8")]
    parsed = []
    for r in index:
        content = (out / "hl7" / r["file"]).read_bytes().decode("utf-8")
        received = datetime.strptime(r["delivery_ts"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
        parsed.append((r, parse_hl7(content, received)))
    return {"truth": load_truth(out / "truth"), "index": index, "parsed": parsed}


def dedupe(records):
    """Keep the first record per msg_key, then the first per content hash (ADT only carries one)."""
    seen_key, seen_hash, out = set(), set(), []
    for r in records:
        h = r.get("content_hash")
        if r["msg_key"] in seen_key or (h and h in seen_hash):
            continue
        seen_key.add(r["msg_key"])
        if h:
            seen_hash.add(h)
        out.append(r)
    return out


def test_records_match_the_contract_shape(world):
    for r, out in world["parsed"]:
        assert set(out) == set(contract.KINDS)
        for kind, rows in out.items():
            for row in rows:
                assert set(row) == set(contract.KINDS[kind]), (kind, set(row) ^ set(contract.KINDS[kind]))
                assert row["source"] == "hl7"
                for k, v in row.items():
                    if k.endswith("_ts") and v is not None:
                        assert v.tzinfo == timezone.utc, (kind, k)
        n = sum(len(v) for v in out.values())
        assert n > 0, r["file"]
        if "F04" in r["faults"]:
            assert out["quarantine"] and "PV1" in out["quarantine"][0]["reason"] and not out["adt"]
        else:
            assert not out["quarantine"], (r["file"], out["quarantine"])
        if r["type"] == "ADT" and "F04" not in r["faults"]:
            adt = out["adt"][0]
            assert adt["kind"] in contract.ADT_KINDS and adt["trigger"] == r["trigger"] and adt["csn"] == r["csn"]
            assert adt["msg_key"].split("|")[2] == r["msh10"] and adt["facility"] == r["facility"]
            assert adt["patient_class"] in ("E", "I", "OBS")


def test_timestamps_normalize_to_utc():
    assert parse_ts("20261026142530-0400") == datetime(2026, 10, 26, 18, 25, 30, tzinfo=timezone.utc)
    assert parse_ts("20261026142530") == datetime(2026, 10, 26, 18, 25, 30, tzinfo=timezone.utc)
    assert parse_ts("20261101013000") == datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)      # first 01:30, EDT
    assert parse_ts("20261101013000-0500") == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    assert parse_ts("20260712") == datetime(2026, 7, 12, 4, 0, tzinfo=timezone.utc)
    assert parse_ts("") is None


def test_duplicates_collapse(world):
    by_file = {r["file"]: (r, out) for r, out in world["parsed"]}
    keys, hashes = defaultdict(set), defaultdict(set)
    for r, out in world["parsed"]:
        for a in out["adt"]:
            keys[a["msg_key"]].add(r["file"])
            hashes[a["content_hash"]].add(r["file"])
    for r, out in world["parsed"]:
        if "F02" in r["faults"] and out["adt"]:
            assert len(keys[out["adt"][0]["msg_key"]]) >= 2
        if "F03" in r["faults"] and out["adt"]:
            assert len(hashes[out["adt"][0]["content_hash"]]) >= 2
    merges = [a for _, out in world["parsed"] for a in out["adt"] if a["kind"] == "merge"]
    assert merges and all(a["retired_mrn"] and a["surviving_mrn"] and a["retired_mrn"] != a["surviving_mrn"] for a in merges)


def test_adt_events_reconcile_with_truth(world):
    truth = world["truth"]
    adt = dedupe([a for r, out in world["parsed"] for a in out["adt"] if "F01" not in r["faults"]])
    got = {(a["csn"], a["kind"], fold(a["effective_ts"])) for a in adt}
    want = {(a["csn"], "update" if a["kind"] in UPDATE_KINDS else a["kind"], fold(ts(a["effective_at"]))) for a in truth["adt"]}
    assert got == want, (sorted(got - want)[:5], sorted(want - got)[:5])
    pats = {p["pat_id"]: p for p in truth["patients"]}
    mrn_want = {(a["csn"], fold(ts(a["effective_at"])), pats[a["pat_id"]]["mrn"]) for a in truth["adt"]}
    assert {(a["csn"], fold(a["effective_ts"]), a["mrn"]) for a in adt} <= mrn_want
    # Content fields on the events that carry them.
    by_event = {(a["csn"], fold(ts(a["effective_at"]))): a for a in truth["adt"]}
    for a in adt:
        t = by_event[(a["csn"], fold(a["effective_ts"]))]
        assert a["patient_class"] == t["patient_class"], a
        assert (a["unit"] or "", a["room"] or "", a["bed"] or "") == (t["unit"], t["room"], t["bed"]), a
        if t["kind"] == "bed_assigned":
            assert (a["pending_unit"], a["pending_room"], a["pending_bed"]) == \
                (t["details"]["pending_unit"], t["details"]["pending_room"], t["details"]["pending_bed"])
        if t["kind"] == "pending_admit":
            assert a["pending_unit"] == t["details"]["pending_unit"] and a["admitting_id"]
            assert a["service"] == t["details"]["service"] and a["level_of_care"] is None
        if t["kind"] == "discharge":
            assert a["disposition"]
        if t["kind"] == "code_status_update":
            assert a["code_status"] == t["details"]["code_status"]
        if t["kind"] == "isolation_update":
            assert a["isolation"] == t["details"]["isolation"]


def test_latest_location_matches_truth(world):
    truth = world["truth"]
    last = {}
    for a in sorted(truth["adt"], key=lambda a: (a["csn"], a["seq"])):
        last[a["csn"]] = (a["unit"], a["room"], a["bed"], a["patient_class"])
    adt = dedupe([a for r, out in world["parsed"] for a in out["adt"] if not set(r["faults"]) & {"F01", "F08"}])
    latest = {}
    for i, a in enumerate(adt):
        key = (fold(a["effective_ts"]), fold(a["recorded_ts"]), i)
        if a["csn"] not in latest or key > latest[a["csn"]][0]:
            latest[a["csn"]] = (key, a)
    assert set(latest) == set(last)
    for csn, (_, a) in latest.items():
        assert (a["unit"] or "", a["room"] or "", a["bed"] or "", a["patient_class"]) == last[csn], csn
    # Stale replays (F08) carry an old effective time, so they never win.
    replays = [a for r, out in world["parsed"] for a in out["adt"] if "F08" in r["faults"]]
    assert replays
    for a in replays:
        assert a["effective_ts"] < latest[a["csn"]][1]["effective_ts"]


def test_results_reconcile_with_truth(world):
    truth = world["truth"]
    obs = [o for r, out in world["parsed"] for o in out["obs"] if "F02" not in r["faults"]]
    got = {(o["order_id"], o["code"], o["status"], o["numeric"]) for o in obs if o["category"] in ("lab",)}
    for x in truth["results"]:
        if x["status"] in ("F", "C") and x["numeric"] is not None:
            assert (x["order_id"], x["lab"], x["status"], x["numeric"]) in got, x
    text = {(o["order_id"], o["code"], o["status"], o["value"]) for o in obs if o["category"] in ("imaging", "ecg", "lab")}
    for x in truth["results"]:
        if x["numeric"] is None and x["status"] in ("F", "P"):
            assert (x["order_id"], x["lab"], x["status"], x["value"]) in text, x
    assert {o["category"] for o in obs} == {"lab", "imaging", "ecg", "vital"}
    vit = [o for o in obs if o["category"] == "vital"]
    assert vit and all(o["order_id"] is None for o in vit)
    assert {o["code"] for o in vit} >= {"hr", "rr", "temp", "spo2", "sbp", "dbp", "bp", "pain"}
    device = [o for o in vit if not o["validated"]]
    assert device and all(o["status"] == "P" for o in device)
    assert all(o["validated"] for o in vit if o["status"] == "F")
    # Vitals carry the measurement time and the charting time separately.
    vt = {(o["csn"], fold(o["observed_ts"]), o["code"], o["numeric"]) for o in vit}
    for v in truth["vitals"]:
        if v["dept_id"].startswith("10101") or not v["validated"]:
            assert (v["csn"], fold(ts(v["taken_at"])), "hr", float(v["hr"])) in vt, v
    crit = [o for o in obs if o["flag"] in ("HH", "LL")]
    assert crit and all("read back" in o["comment"] for o in crit)


def test_medications_reconcile_with_truth(world):
    truth = world["truth"]
    med = [m for r, out in world["parsed"] for m in out["med"]]
    got = {(m["order_id"], m["line"], m["action"], fold(m["event_ts"])) for m in med if m["event"] == "administration"}
    assert got == {(m["order_med_id"], m["line"], m["action"], fold(ts(m["taken_at"]))) for m in truth["mar"]}
    orders = {m["order_id"]: m for m in med if m["event"] == "order"}
    assert set(orders) == {m["order_med_id"] for m in truth["med_orders"]}
    for t in truth["med_orders"]:
        m = orders[t["order_med_id"]]
        assert (m["rxcui"], m["name"], m["dose"], m["unit"], m["route"], m["rate"], m["frequency"], fold(m["event_ts"])) == \
            (t["rxcui"], t["name"], t["dose"], t["unit"], t["route"], t["rate"], t["freq"], fold(ts(t["ordered_at"])))


def test_notes_reconcile_with_truth(world):
    truth = world["truth"]
    quirks = set(truth["render_plan"]["F27_encoding_quirks"])
    notes = [n for r, out in world["parsed"] for n in out["note"]]
    signed = {n["note_id"]: n for n in notes if n["status"] == "Signed"}
    drafts = {n["note_id"] for n in notes if n["status"] == "In Progress"}
    for t in truth["notes"]:
        assert t["note_id"] in drafts
        if t["signed_at"]:
            n = signed[t["note_id"]]
            assert fold(n["signed_ts"]) == fold(ts(t["signed_at"])) and fold(n["created_ts"]) == fold(ts(t["created_at"]))
            assert n["note_type"] == t["note_type"] and n["loinc"] == t["loinc"]
            if t["note_id"] not in quirks:
                assert n["text"] == t["text"], t["note_id"]
            else:
                assert n["text"].startswith(t["text"]) and "&" in n["text"] and "\\" in n["text"] and "|" in n["text"]
        else:
            assert t["note_id"] not in signed
    allergies = [a for r, out in world["parsed"] for a in out["allergy"]]
    assert allergies and {a["allergen"] for a in allergies} >= {"Penicillins"}
    flags = [f for r, out in world["parsed"] for f in out["flag"]]
    assert {f["kind"] for f in flags} == {"isolation", "code_status"}


def test_unknown_segments_and_garbage_do_not_crash():
    msg = ("MSH|^~\\&|EPIC|SGH|RHAPSODY|SGH|20261026101500-0400|U1|ADT^A08^ADT_A01|1|T|2.5.1\r"
           "EVN||20261026101500-0400\rPID|1||1^^^MRN^MR||Nuñez^Ana^^^^^L||19700101|F\r"
           "PV1|1|O|ED^12^A^SGH|||||||EMR|||||||||40011200001^^^EPI^CSN\rZZZ|whatever|x\rQQQ\r")
    out = parse_hl7(msg)
    a = out["adt"][0]
    assert a["patient_class"] == "OBS" and a["kind"] == "update" and a["patient_name"] == "Nuñez, Ana" and not out["quarantine"]
    assert parse_hl7("garbage")["quarantine"][0]["reason"] == "no MSH segment"
    assert parse_hl7("MSH|^~\\&|EPIC|SGH|||20261026101500||ORU^R01|9|T|2.3\rPID|1\rPV1|1\r")["quarantine"]


def test_repeated_dst_hour_resolves_against_receipt_time():
    from parse_hl7 import parse_ts
    utc = timezone.utc
    edt, est = datetime(2026, 11, 1, 5, 48, 32, tzinfo=utc), datetime(2026, 11, 1, 6, 48, 32, tzinfo=utc)
    assert parse_ts("20261101014832") == edt                                      # no reference: first reading
    assert parse_ts("20261101014832", datetime(2026, 11, 1, 6, 48, 40, tzinfo=utc)) == est   # received after EST
    assert parse_ts("20261101014832", datetime(2026, 11, 1, 5, 48, 40, tzinfo=utc)) == edt   # EST is in the future
    assert parse_ts("20261101014832", datetime(2026, 11, 1, 6, 48, 40)) == est    # naive reference is UTC
    assert parse_ts("20261101013314", datetime(2026, 11, 1, 15, 33, 44, tzinfo=utc)) == datetime(
        2026, 11, 1, 5, 33, 14, tzinfo=utc)                                       # sent hours later: ambiguous
    assert parse_ts("20261101014832-0500") == est                                 # an offset always wins
