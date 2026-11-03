"""Clarity renderer invariants: keys, foreign keys, snapshot cutoffs (F32) and
reconciliation of the final snapshot against the truth. Runs on a small world
built from the stand in population."""
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "synthetic"))

from edip_synth import synthea  # noqa: E402
from edip_synth.config import Config  # noqa: E402
from edip_synth.output import write  # noqa: E402
from edip_synth.render import clarity  # noqa: E402
from edip_synth.render.common import load_truth, ts  # noqa: E402
from edip_synth.world import Simulator  # noqa: E402

TZ = ZoneInfo("America/New_York")
UTC = timezone.utc
FK = [("PAT_ENC_CSN_ID", "PAT_ENC", "PAT_ENC_CSN_ID"), ("PATIENT_CSN", "PAT_ENC", "PAT_ENC_CSN_ID"),
      ("ALLERGY_PAT_CSN", "PAT_ENC", "PAT_ENC_CSN_ID"), ("PAT_ID", "PATIENT", "PAT_ID"), ("PATIENT_ID", "PATIENT", "PAT_ID"),
      ("DEPARTMENT_ID", "CLARITY_DEP", "DEPARTMENT_ID"), ("UNIT_ID", "CLARITY_DEP", "DEPARTMENT_ID"),
      ("EVENT_DEPT_ID", "CLARITY_DEP", "DEPARTMENT_ID"), ("DEPT_EVENT_DEP_ID", "CLARITY_DEP", "DEPARTMENT_ID"),
      ("MAR_ADMIN_DEPT_ID", "CLARITY_DEP", "DEPARTMENT_ID"), ("TRANSFER_DEST_DEPT_ID", "CLARITY_DEP", "DEPARTMENT_ID"),
      ("DISCH_DEPT_ID", "CLARITY_DEP", "DEPARTMENT_ID"), ("ROOM_ID", "CLARITY_ROM", "ROOM_ID"), ("BED_ID", "CLARITY_BED", "BED_ID"),
      ("PROV_ID", "CLARITY_SER", "PROV_ID"), ("VISIT_PROV_ID", "CLARITY_SER", "PROV_ID"), ("AUTHRZING_PROV_ID", "CLARITY_SER", "PROV_ID"),
      ("ORD_PROV_ID", "CLARITY_SER", "PROV_ID"), ("MED_PRESC_PROV_ID", "CLARITY_SER", "PROV_ID"), ("TR_TEAM_ID", "CLARITY_SER", "PROV_ID"),
      ("ATTENDING_PROV_ID", "CLARITY_SER", "PROV_ID"), ("BILL_ATTEND_PROV_ID", "CLARITY_SER", "PROV_ID"),
      ("AUTH_LNKED_PROV_ID", "CLARITY_SER", "PROV_ID"), ("EVENT_PROV_ID", "CLARITY_SER", "PROV_ID"),
      ("PEND_ATND_PROV_ID", "CLARITY_SER", "PROV_ID"), ("COMPONENT_ID", "CLARITY_COMPONENT", "COMPONENT_ID"),
      ("MEDICATION_ID", "CLARITY_MEDICATION", "MEDICATION_ID"), ("DX_ID", "CLARITY_EDG", "DX_ID"), ("PROC_ID", "CLARITY_EAP", "PROC_ID"),
      ("ORDER_PROC_ID", "ORDER_PROC", "ORDER_PROC_ID"), ("ORDER_MED_ID", "ORDER_MED", "ORDER_MED_ID"), ("NOTE_ID", "HNO_INFO", "NOTE_ID"),
      ("HSP_ACCOUNT_ID", "HSP_ACCOUNT", "HSP_ACCOUNT_ID"), ("FSD_ID", "IP_FLWSHT_REC", "FSD_ID"), ("FLO_MEAS_ID", "IP_FLO_GP_DATA", "FLO_MEAS_ID"),
      ("ADT_EVENT_ID", "CLARITY_ADT", "EVENT_ID"), ("LINKED_EVENT_ID", "CLARITY_ADT", "EVENT_ID"), ("EMER_ADM_EVENT_ID", "CLARITY_ADT", "EVENT_ID"),
      ("INP_ADM_EVENT_ID", "CLARITY_ADT", "EVENT_ID"), ("OP_ADM_EVENT_ID", "CLARITY_ADT", "EVENT_ID"),
      ("XFER_EVENT_ID", "CLARITY_ADT", "EVENT_ID"), ("CANC_EVENT_ID", "CLARITY_ADT", "EVENT_ID"), ("ORIGINAL_EVENT_ID", "CLARITY_ADT", "EVENT_ID"),
      ("ORDER_ID", "ORDER_PROC", "ORDER_PROC_ID"), ("ADT_ORDER_ID", "ORDER_PROC", "ORDER_PROC_ID")]


@pytest.fixture(scope="module")
def world_dir(tmp_path_factory):
    cfg = replace(Config(), encounters=300)
    w = Simulator(cfg, synthea.stand_in(1500, cfg.seed)).run()
    d = tmp_path_factory.mktemp("world")
    write(w, d)
    return d


@pytest.fixture(scope="module")
def rendered(world_dir):
    summary = clarity.render(world_dir / "truth", world_dir)
    schema = json.loads((world_dir / "clarity" / "schema.json").read_text())
    snaps = {}
    for name in summary["snapshots"]:
        tables = {}
        for t in schema["tables"]:
            with open(world_dir / "clarity" / name / f"{t}.jsonl", encoding="utf-8") as f:
                tables[t] = [json.loads(line) for line in f]
        snaps[name] = tables
    return {"summary": summary, "schema": schema, "snaps": snaps, "truth": load_truth(world_dir / "truth")}


def midnight_utc(name):
    y, m, d = (int(x) for x in name.split("-"))
    return datetime(y, m, d, tzinfo=TZ).astimezone(UTC)


def test_snapshots_cover_the_window_but_not_day_seven(rendered):
    names = rendered["summary"]["snapshots"]
    cfg = rendered["truth"]["config"]
    start = datetime.fromisoformat(cfg["window_start"]).astimezone(TZ)
    end = start + timedelta(days=cfg["days"])
    first = datetime(start.year, start.month, start.day, tzinfo=TZ)
    want = [m.strftime("%Y-%m-%d") for m in (first + timedelta(days=i) for i in range(cfg["days"] + 1)) if m <= end]
    assert names == want and len(names) >= 7
    truth = rendered["truth"]
    last = rendered["snaps"][names[-1]]
    cut = midnight_utc(names[-1])
    day7 = {e["csn"] for e in truth["encounters"] if ts(e["arrival_at"]) >= cut}
    assert day7, "the small world should still have day seven arrivals"
    assert not day7 & {str(r["PAT_ENC_CSN_ID"]) for r in last["PAT_ENC"]}
    assert rendered["summary"]["bytes"] > 0


def test_rows_match_schema(rendered):
    schema = rendered["schema"]["tables"]
    for name, tables in rendered["snaps"].items():
        for t, rows in tables.items():
            cols = {c["name"] for c in schema[t]["columns"]}
            for r in rows:
                assert set(r) <= cols, (name, t, set(r) - cols)
                assert all(v is not None for v in r.values())


def test_primary_keys_unique(rendered):
    schema = rendered["schema"]["tables"]
    for name, tables in rendered["snaps"].items():
        for t, rows in tables.items():
            pk = schema[t]["primary_key"]
            keys = [tuple(r.get(k) for k in pk) for r in rows]
            assert len(keys) == len(set(keys)), (name, t, [k for k, n in Counter(keys).items() if n > 1][:3])
            assert all(None not in k for k in keys), (name, t)


def test_foreign_keys_resolve(rendered):
    schema = rendered["schema"]["tables"]
    for name, tables in rendered["snaps"].items():
        index = defaultdict(set)
        for col, target, tcol in FK:
            index[(target, tcol)] |= {r.get(tcol) for r in tables[target]}
        for t, rows in tables.items():
            cols = {c["name"] for c in schema[t]["columns"]}
            for col, target, tcol in FK:
                if (t == target and col == tcol) or col not in cols:
                    continue
                missing = {r[col] for r in rows if col in r} - index[(target, tcol)]
                assert not missing, (name, t, col, sorted(missing)[:5])


def test_nothing_dated_after_midnight(rendered):
    schema = rendered["schema"]["tables"]
    future_ok = set(rendered["schema"]["future_ok_columns"])
    for name, tables in rendered["snaps"].items():
        cut = midnight_utc(name)
        limit = {"America/New_York": cut.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S"), "UTC": cut.strftime("%Y-%m-%d %H:%M:%S")}
        for t, rows in tables.items():
            for c in schema[t]["columns"]:
                if c["type"] != "TIMESTAMP" or c["name"] in future_ok:
                    continue
                late = [r[c["name"]] for r in rows if c["name"] in r and r[c["name"]] > limit[c["tz"]]]
                assert not late, (name, t, c["name"], late[:3])


def test_adt_reconciles_with_truth(rendered):
    truth = rendered["truth"]
    truth_ids = {int(a["event_id"]) for a in truth["adt"]}
    for name, tables in rendered["snaps"].items():
        cut = midnight_utc(name)
        expected = defaultdict(set)
        for a in truth["adt"]:
            if ts(a["recorded_at"]) < cut:
                expected[a["csn"]].add(int(a["event_id"]))
        got = defaultdict(set)
        for r in tables["CLARITY_ADT"]:
            if r["EVENT_ID"] in truth_ids:
                got[str(r["PAT_ENC_CSN_ID"])].add(r["EVENT_ID"])
            else:
                assert r["EVENT_TYPE_C_NAME"] == "Transfer Out" and r["XFER_EVENT_ID"] == r["EVENT_ID"] - clarity.OUT_OFFSET
        assert got == expected, name
    # The final snapshot: EFFECTIVE_TIME earlier than EVENT_TIME exactly where the truth backdated (F07).
    last = rendered["snaps"][rendered["summary"]["snapshots"][-1]]
    cut = midnight_utc(rendered["summary"]["snapshots"][-1])
    backdated = {int(a["event_id"]) for a in truth["adt"] if a["recorded_at"] != a["effective_at"] and ts(a["recorded_at"]) < cut}
    seen = {r["EVENT_ID"] for r in last["CLARITY_ADT"] if r["EVENT_ID"] in truth_ids and r["EFFECTIVE_TIME"] != r["EVENT_TIME"]}
    assert seen == backdated
    for r in last["CLARITY_ADT"]:
        assert r["EFFECTIVE_TIME"] <= r["EVENT_TIME"]
    # Canceled originals and their cancel rows are both excluded by the spec's current state pattern.
    cancels = [r for r in last["CLARITY_ADT"] if r["EVENT_SUBTYPE_C_NAME"] == "Canceled"]
    assert cancels
    ids = {r["EVENT_ID"] for r in last["CLARITY_ADT"]}
    for r in cancels:
        assert r["ORIGINAL_EVENT_ID"] in ids
    # Every live Transfer In is paired with a Transfer Out (cancel rows stand alone).
    by_id = {r["EVENT_ID"]: r for r in last["CLARITY_ADT"]}
    for r in last["CLARITY_ADT"]:
        if r["EVENT_TYPE_C_NAME"] == "Transfer In" and r["EVENT_SUBTYPE_C_NAME"] != "Canceled":
            assert by_id[r["XFER_EVENT_ID"]]["EVENT_TYPE_C_NAME"] == "Transfer Out"


def test_results_mar_notes_reconcile(rendered):
    truth = rendered["truth"]
    name = rendered["summary"]["snapshots"][-1]
    last, cut = rendered["snaps"][name], midnight_utc(name)
    # Results: the latest value per (order, line) resulted before the cutoff, on orders placed before it.
    ordered = {o["order_id"] for o in truth["orders"] if ts(o["ordered_at"]) < cut}
    latest = {}
    for r in sorted(truth["results"], key=lambda r: (r["resulted_at"], "PFC".index(r["status"]))):
        if ts(r["resulted_at"]) < cut and r["order_id"] in ordered:
            latest[(int(r["order_id"]), r["line"])] = r
    got = {(r["ORDER_PROC_ID"], r["LINE"]): r for r in last["ORDER_RESULTS"]}
    assert set(got) == set(latest)
    for k, r in latest.items():
        assert got[k]["ORD_VALUE"] == r["value"]
        assert got[k]["RESULT_STATUS_C_NAME"] == {"P": "Preliminary", "F": "Final", "C": "Corrected"}[r["status"]]
    assert any(r["RESULT_STATUS_C_NAME"] == "Corrected" for r in last["ORDER_RESULTS"])
    # MAR: every administration taken before the cutoff, by (order, line).
    exp = {(int(m["order_med_id"]), m["line"]) for m in truth["mar"] if ts(m["taken_at"]) < cut}
    assert {(r["ORDER_MED_ID"], r["LINE"]) for r in last["MAR_ADMIN_INFO"]} == exp
    # Notes: created before the cutoff; signed only when the signature happened before it; text reassembles.
    notes = {n["note_id"]: n for n in truth["notes"] if ts(n["created_at"]) < cut}
    info = {r["NOTE_ID"]: r for r in last["HNO_INFO"]}
    assert set(info) == set(notes)
    status = {r["NOTE_ID"]: r["NOTE_STATUS_C_NAME"] for r in last["NOTE_ENC_INFO"]}
    for nid, n in notes.items():
        signed = n["signed_at"] is not None and ts(n["signed_at"]) < cut
        assert info[nid]["UNSIGNED_YN"] == ("N" if signed else "Y")
        assert status[nid] == ("Signed" if signed else "Incomplete")
    assert any(s == "Incomplete" for s in status.values())
    lines = defaultdict(dict)
    for r in last["HNO_NOTE_TEXT"]:
        lines[r["NOTE_ID"]][r["LINE"]] = r["NOTE_TEXT"]
    for nid, n in notes.items():
        assert "\n".join(lines[nid][i] for i in sorted(lines[nid])) == n["text"]


def test_encounter_state_and_faults(rendered):
    truth = rendered["truth"]
    name = rendered["summary"]["snapshots"][-1]
    last, cut = rendered["snaps"][name], midnight_utc(name)
    hsp = {str(r["PAT_ENC_CSN_ID"]): r for r in last["PAT_ENC_HSP"]}
    enc = {str(r["PAT_ENC_CSN_ID"]): r for r in last["PAT_ENC"]}
    pats = {p["pat_id"]: p for p in truth["patients"]}
    for e in truth["encounters"]:
        if ts(e["arrival_at"]) >= cut:
            continue
        r = hsp[e["csn"]]
        if e["disposition"] == "CANCELED":
            assert enc[e["csn"]]["APPT_STATUS_C_NAME"] == "Canceled"
            assert r["ADMIT_CONF_STAT_C_NAME"] == "Canceled"
        if e["disposition"].startswith("ADMIT") and e["class_change_at"] and ts(e["class_change_at"]) < cut:
            key = "OP_ADM_EVENT_ID" if e["disposition"] == "ADMIT_OBS" else "INP_ADM_EVENT_ID"
            assert key in r, e["csn"]
            assert r["ED_DISPOSITION_C_NAME"] in ("Admit", "Observation")
        if e["disposition"] == "LWBS" and e["departure_at"] and ts(e["departure_at"]) < cut:
            assert r["ED_DISPOSITION_C_NAME"] == "LWBS"
            assert enc[e["csn"]]["APPT_STATUS_C_NAME"] == "Left without seen"
        # Merged duplicates (F12) point everything at the surviving patient once the merge is documented.
        if e["registered_pat_id"]:
            dup = pats[e["registered_pat_id"]]
            merged = ts(dup["merged_at"]) < cut
            assert r["PAT_ID"] == (e["pat_id"] if merged else e["registered_pat_id"])
            prow = next(p for p in last["PATIENT"] if p["PAT_ID"] == e["registered_pat_id"])
            assert ("MERGED_INTO_PAT_ID" in prow) == merged
    # Day by day: a snapshot never shows an ED departure that was documented after its midnight (F07, F32).
    for name, tables in rendered["snaps"].items():
        cut = midnight_utc(name)
        documented = {a["csn"] for a in truth["adt"] if a["kind"] in ("transfer", "discharge") and ts(a["recorded_at"]) < cut}
        for r in tables["PAT_ENC_HSP"]:
            if "ED_DEPARTURE_TIME" in r:
                assert str(r["PAT_ENC_CSN_ID"]) in documented, (name, r["PAT_ENC_CSN_ID"])
    # History: every problem and home med of a patient with an encounter is on the batch side.
    assert len(last["PROBLEM_LIST"]) == sum(len(p["problems"]) for p in truth["patients"])
    home = [r for r in last["ORDER_MED"] if r["ORDERING_MODE_C_NAME"] == "Outpatient"]
    assert len(home) == sum(len(p["home_meds"]) for p in truth["patients"])
    assert all("PAT_ENC_CSN_ID" not in r and r["ORDER_CLASS_C_NAME"] == "Historical Med" for r in home)


def test_deterministic(world_dir, rendered):
    out = world_dir / "again"
    clarity.render(world_dir / "truth", out)
    name = rendered["summary"]["snapshots"][-1]
    for t in ("CLARITY_ADT", "ORDER_RESULTS", "PAT_ENC_HSP", "HNO_NOTE_TEXT"):
        a = (world_dir / "clarity" / name / f"{t}.jsonl").read_bytes()
        b = (out / "clarity" / name / f"{t}.jsonl").read_bytes()
        assert a == b, t
