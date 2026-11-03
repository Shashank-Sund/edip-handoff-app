"""Invariants of the generated world (subset of spec section 12). Runs on the
built in stand in population, so CI needs no Synthea output."""
import json
import re
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "synthetic"))

from edip_synth import synthea  # noqa: E402
from edip_synth.config import Config  # noqa: E402
from edip_synth.output import summary  # noqa: E402
from edip_synth.world import Simulator  # noqa: E402


END = Config().window_start + timedelta(days=Config().days)


@pytest.fixture(scope="module")
def world():
    cfg = Config()
    return Simulator(cfg, synthea.stand_in(3000, cfg.seed)).run()


def test_deterministic(world):
    cfg = Config()
    again = Simulator(cfg, synthea.stand_in(3000, cfg.seed)).run()
    assert summary(again) == summary(world)
    assert [a.event_id for a in again.adt[:200]] == [a.event_id for a in world.adt[:200]]


def test_volumes(world):
    encs = [e for e in world.encounters if e.disposition != "CANCELED"]
    assert len(encs) == 700
    assert sum(e.disposition == "ADMIT_IP" for e in encs) == 90
    assert sum(e.disposition == "ADMIT_OBS" for e in encs) == 30
    assert sum(e.disposition == "CANCELED" for e in world.encounters) == 2
    assert len({e.csn for e in world.encounters}) == len(world.encounters)


def test_everything_inside_window(world):
    cfg = Config()
    end = cfg.window_start + timedelta(days=cfg.days)
    for a in world.adt:
        assert a.recorded_at <= end
    for r in world.results:
        assert r.resulted_at <= end
    for v in world.vitals:
        assert v.entered_at <= end
    for n in world.notes:
        assert n.created_at <= end and (n.signed_at is None or n.signed_at <= end)


def test_milestone_order(world):
    order = ["arrival_at", "triage_at", "roomed_at", "provider_at", "decision_at", "bed_request_at", "bed_assigned_at",
             "departure_at"]
    for e in world.encounters:
        times = [getattr(e, f) for f in order if getattr(e, f)]
        if e.disposition in ("DISCHARGE", "AMA", "LBTC", "TRANSFER", "LWBS"):
            times = [t for f, t in zip(order, times)]
        assert times == sorted(times), (e.csn, e.disposition, [(f, getattr(e, f)) for f in order])


def test_adt_stream_is_time_ordered_and_ends_on_the_unit(world):
    by = defaultdict(list)
    for a in world.adt:
        by[a.csn].append(a)
    for e in world.encounters:
        evs = sorted(by[e.csn], key=lambda a: a.seq)
        assert [a.effective_at for a in evs] == sorted(a.effective_at for a in evs)
        late_doc = "F07" in e.flags and e.departure_at and e.departure_at > END - timedelta(hours=4)
        if e.disposition.startswith("ADMIT") and e.departure_at and not late_doc:
            tr = [a for a in evs if a.kind == "transfer"]
            assert tr, e.csn
            last = tr[-1]
            assert (last.unit, last.room, last.bed) == (e.unit, e.room, e.bed)
            assert last.patient_class in ("I", "OBS")


def test_boarding_list_state(world):
    states = {e.state_at_end for e in world.encounters}
    assert {"boarding", "in_ed", "inpatient", "discharged"} <= states
    for e in world.encounters:
        if e.state_at_end == "boarding":
            assert e.decision_at and not e.departure_at


def test_vitals_physiologic(world):
    for v in world.vitals:
        assert 30 <= v.hr <= 200 and 60 <= v.sbp <= 250 and 30 <= v.dbp <= 150 and 6 <= v.rr <= 50
        assert 60 <= v.spo2 <= 100 and (v.temp is None or 34 <= v.temp <= 42)
        assert v.dbp < v.sbp and abs(v.map - (v.sbp + 2 * v.dbp) / 3) <= 1


def test_anion_gap_consistent(world):
    by = defaultdict(dict)
    for r in world.results:
        if r.lab in ("NA", "CL", "CO2", "AGAP") and r.status == "F":
            by[(r.order_id, r.resulted_at)][r.lab] = r.numeric
    checked = 0
    for vals in by.values():
        if len(vals) == 4:
            assert abs(vals["NA"] - vals["CL"] - vals["CO2"] - vals["AGAP"]) <= 2
            checked += 1
    assert checked > 100


def test_critical_values_have_callback(world):
    crit = [r for r in world.results if r.flag in ("HH", "LL")]
    assert crit
    for r in crit:
        assert "read back" in r.comment


def test_truth_faults_present(world):
    ids = {f["fault_id"] for f in world.faults}
    assert {"F07", "F09", "F10", "F11", "F12", "F13", "F14", "F15", "F16", "F17", "F18", "F19", "F20", "F21", "F22",
            "F23", "F29", "F31"} <= ids
    for key in ("F01_a08_flood", "F05_out_of_order", "F24_unvalidated_device_values", "F28_mixed_status_panel"):
        assert world.render_plan[key]


def test_merge_links_to_surviving_patient(world):
    pats = {p.pat_id: p for p in world.patients}
    merged = [e for e in world.encounters if e.registered_pat_id]
    assert len(merged) == 2
    for e in merged:
        assert pats[e.registered_pat_id].merged_into == e.pat_id
        evs = [a for a in world.adt if a.csn == e.csn]
        assert any(a.kind == "merge" for a in evs)
        assert all(a.pat_id == e.pat_id for a in evs if a.effective_at >= e._merge_at)


def test_hygiene(world):
    for p in world.patients:
        assert p.ssn.startswith("9")
        assert re.fullmatch(r"617-555-01\d\d", p.phone)
    text = json.dumps([n.text for n in world.notes], ensure_ascii=False)
    assert "—" not in text and "–" not in text


def test_note_vitals_match_structured_data(world):
    vit = {(v.csn, v.hr, v.sbp, v.dbp) for v in world.vitals}
    checked = 0
    for n in world.notes:
        m = re.search(r"HR (\d+), BP (\d+)/(\d+)", n.text)
        if m:
            assert (n.csn, int(m[1]), int(m[2]), int(m[3])) in vit, n.note_id
            checked += 1
    assert checked > 500
