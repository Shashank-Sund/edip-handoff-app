"""Checklist items 6 to 10: milestone order at the ADT level, the arrival curve,
timing medians, recorded versus effective times, and vitals cadence.
(tests/test_world.py already checks the encounter level milestone fields.)"""
from collections import Counter, defaultdict
from datetime import timedelta

import pytest

from realism_helpers import gap, local, median, minutes, pct, quantile, spearman, within_pct

from edip_synth.config import Config

CFG = Config()


# ---------------------------------------------------------------- 6. milestone order in the ADT stream
def _first_times(view, e):
    evs = view.adt(e.csn)
    first = {}
    for a in evs:
        first.setdefault(a.kind, a.effective_at)
    last_transfer = [a for a in evs if a.kind == "transfer"]
    if last_transfer:
        first["transfer"] = last_transfer[-1].effective_at
    return first, last_transfer


def test_adt_events_follow_the_canonical_order(view):
    """arrival <= triage <= roomed <= provider <= bed request <= bed assigned <= transfer, with the
    last transfer at the departure time. F05, F07 and F08 only disturb rendered streams, so the
    truth level effective times must always hold. The arrival anchor is the tracking board's
    "Patient Arrived in ED" time; the ADT arrival event itself is judged in the next test."""
    chain = ["arrival", "triage", "roomed", "provider", "pending_admit", "bed_assigned", "transfer"]
    for e in view.encounters:
        first, last_transfer = _first_times(view, e)
        if not first:
            assert e.arrival_at > view.end - timedelta(minutes=3), e.csn   # arrived as the window closed
            continue
        arrived = [x for x in view.ed_events(e.csn) if x.name == "Patient Arrived in ED"]
        assert arrived and arrived[0].event_at == e.arrival_at, e.csn
        first["arrival"] = arrived[0].event_at
        times = [first[k] for k in chain if k in first]
        assert times == sorted(times), (e.csn, [(k, first[k]) for k in chain if k in first])
        late_doc = "F07" in e.flags and e.departure_at and e.departure_at > view.end - timedelta(hours=4)
        if e.disposition.startswith("ADMIT") and e.departure_at and not late_doc:
            assert last_transfer and last_transfer[-1].effective_at == e.departure_at, e.csn
            dep = [x for x in view.ed_events(e.csn) if x.name == "Patient Departed from ED"]
            assert dep and dep[0].event_at == e.departure_at, e.csn
        if e.decision_at and e.class_change_at:
            assert e.decision_at <= e.class_change_at <= (e.departure_at or view.end), e.csn


@gap("world._adt gives the ADT arrival event an effective time 0.3 to 3 min after arrival while an ESI 1 or EMS "
     "triage completes 1 to 5 min after arrival, so in about 6 percent of encounters the arrival event is effective "
     "after triage; keep effective_at at the arrival time and put the registration delay into recorded_at")
def test_adt_arrival_event_is_effective_at_arrival(view):
    for e in view.encounters:
        first, _ = _first_times(view, e)
        assert first["arrival"] <= e.arrival_at + timedelta(seconds=30), (e.csn, first["arrival"], e.arrival_at)
        if "triage" in first:
            assert first["arrival"] <= first["triage"], (e.csn, first["arrival"], first["triage"])


def test_ed_tracking_events_are_in_line_order_and_linked(view):
    """Each tracking row links to an existing ADT row, except the departure of an F07 (late documented)
    transfer within 4 h of the window end, whose ADT row has not been recorded yet."""
    adt_ids = {a.event_id for a in view.w.adt}
    for e in view.encounters:
        evs = view.ed_events(e.csn)
        assert [x.line for x in evs] == list(range(1, len(evs) + 1)), e.csn
        assert [x.event_at for x in evs] == sorted(x.event_at for x in evs), e.csn
        late_doc = "F07" in e.flags and e.departure_at and e.departure_at > view.end - timedelta(hours=4)
        for x in evs:
            if x.adt_event_id and not (late_doc and x.name == "Patient Departed from ED"):
                assert x.adt_event_id in adt_ids, (e.csn, x.name)


@gap("world._adt places 'Triage Started' 1 to 4 min before triage completion and 'Ready for Transport' 5 to 30 min after "
     "bed assignment without clamping, so 17 percent of visits start triage before arrival and 15 percent of admits are "
     "'ready for transport' after they left; clamp to [arrival + 0.5 min, triage] and [bed assigned, departure - 1 min]")
def test_tracking_board_events_are_internally_ordered(view):
    for e in view.encounters:
        at = {x.name: x.event_at for x in view.ed_events(e.csn)}
        if "Triage Started" in at:
            assert at["Triage Started"] >= e.arrival_at, (e.csn, at["Triage Started"], e.arrival_at)
        if "Ready for Transport" in at and "Patient Departed from ED" in at:
            assert at["Ready for Transport"] < at["Patient Departed from ED"], e.csn


# ---------------------------------------------------------------- 7. arrival curve
def test_arrival_hour_curve_matches_the_spec(view):
    hours = Counter(local(e.arrival_at).hour for e in view.encounters)
    observed = [hours[h] for h in range(24)]
    assert spearman(observed, list(CFG.hourly)) > 0.9, observed
    days = Counter(local(e.arrival_at).weekday() for e in view.encounters)
    n = len(view.encounters)
    assert 15 <= pct(days[0], n) <= 17, pct(days[0], n)
    assert pct(days[5], n) < 14 and pct(days[6], n) < 14, (pct(days[5], n), pct(days[6], n))


# ---------------------------------------------------------------- 8. timing distributions
def _intervals(view):
    enc, adm = view.encounters, view.admits
    return {
        "door_to_provider": [minutes(e.arrival_at, e.provider_at) for e in enc if e.provider_at],
        "arrival_to_decision": [minutes(e.arrival_at, e.decision_at) for e in adm if e.decision_at],
        "boarding": [minutes(e.decision_at, e.departure_at) for e in adm if e.decision_at and e.departure_at],
        "discharged_los": [minutes(e.arrival_at, e.departure_at) for e in enc if e.disposition == "DISCHARGE" and e.departure_at],
        "bed_request_to_assigned": [minutes(e.bed_request_at, e.bed_assigned_at) for e in adm if e.bed_request_at and e.bed_assigned_at],
        "bed_assigned_to_departure": [minutes(e.bed_assigned_at, e.departure_at) for e in adm if e.bed_assigned_at and e.departure_at],
    }


# Spec section 3 targets (median, p90) in minutes.
TARGETS = {"door_to_provider": (20, 75), "arrival_to_decision": (200, 360), "boarding": (150, 480), "discharged_los": (160, 330),
           "bed_request_to_assigned": (45, 240), "bed_assigned_to_departure": (50, 150)}


@pytest.mark.parametrize("name", ["door_to_provider", "arrival_to_decision", "discharged_los"])
def test_medians_and_p90s_within_15_percent(view, name):
    xs = _intervals(view)[name]
    med, p90 = TARGETS[name]
    assert within_pct(median(xs), med, 15), (name, "median", median(xs))
    assert within_pct(quantile(xs, 0.9), p90, 15), (name, "p90", quantile(xs, 0.9))


def test_boarding_base_and_crowding_effect(view):
    """Boarding is split by destination unit occupancy at the decision hour: the uncrowded part
    must sit on the spec's lognormal (150, 480) and the crowded part must be 2 to 3 times longer.
    Tolerance 30 percent on the medians: about 90 uncrowded admits with lognormal sigma 0.9 give a
    standard error near 10 percent of the median, and the crowded group is 20 to 25 admits, so the
    ratio is accepted between 1.5 and 4."""
    base, crowd = [], []
    for e in view.admits:
        if not (e.decision_at and e.departure_at and e.unit):
            continue
        occ = view.occupancy_at(e.facility, e.unit, e.decision_at)
        if occ is None:
            continue
        (crowd if occ >= CFG.crowding_threshold else base).append(minutes(e.decision_at, e.departure_at))
    assert len(base) >= 40 and len(crowd) >= 8, (len(base), len(crowd))
    assert within_pct(median(base), 150, 30), median(base)
    assert within_pct(quantile(base, 0.9), 480, 30), quantile(base, 0.9)
    assert 1.5 <= median(crowd) / median(base) <= 4.0, (median(crowd), median(base))
    assert within_pct(median(_intervals(view)["bed_assigned_to_departure"]), 50, 30)


@gap("the crowding multiplier (2 to 3x for about 20 percent of admits) sits on top of the full lognormal (150, 480), "
     "so the overall boarding p90 lands near 750 min against the spec's 480; lower config.boarding p90 to about 330 "
     "or state the spec target as the uncrowded p90")
def test_boarding_overall_p90_within_30_percent(view):
    xs = _intervals(view)["boarding"]
    assert within_pct(median(xs), 150, 30), median(xs)
    assert within_pct(quantile(xs, 0.9), 480, 30), quantile(xs, 0.9)


@gap("bed request to assignment is the config lognormal (35, 190) plus the emergent wait for a free bed, so the "
     "median is about 73 min against the spec's 45; lower config.bed_request_to_assigned to about (25, 120)")
def test_bed_request_to_assignment_within_30_percent(view):
    xs = _intervals(view)["bed_request_to_assigned"]
    assert within_pct(median(xs), 45, 30), median(xs)
    assert within_pct(quantile(xs, 0.9), 240, 30), quantile(xs, 0.9)


# ---------------------------------------------------------------- 9. recorded versus effective
def test_recorded_times_are_monotonic_except_backdated_faults(view):
    """EVN-2 analog (recorded_at) never precedes EVN-6 (effective_at); it lags by more than a few minutes
    only for F07 (late documented transfer) and the F09 wrong patient registration, and is monotonic per
    CSN except in those encounters."""
    for e in view.all_encounters:
        evs = view.adt(e.csn)
        backdated = {"F07", "F09"} & set(e.flags)
        for a in evs:
            assert a.recorded_at >= a.effective_at, (e.csn, a.kind)
            if a.recorded_at - a.effective_at > timedelta(minutes=5):
                assert backdated and a.kind in ("transfer", "arrival"), (e.csn, a.kind, a.recorded_at - a.effective_at)
        recorded = [a.recorded_at for a in evs]
        if not backdated:
            assert recorded == sorted(recorded), e.csn
    for x in view.w.ed_events:
        assert timedelta(0) <= x.recorded_at - x.event_at <= timedelta(minutes=5)


def test_clinical_streams_have_consistent_clocks(view):
    """Results are released after collection, vitals are charted after they are taken, MAR follows the
    verified order, notes are signed after they are created and at or after the service time."""
    for r in view.w.results:
        assert r.resulted_at >= r.observed_at, r.order_id
    for v in view.w.vitals:
        assert timedelta(0) <= v.entered_at - v.taken_at <= timedelta(minutes=75), (v.csn, v.line)
    orders = {m.order_med_id: m for m in view.w.med_orders}
    for m in view.w.mar:
        o = orders.get(m.order_med_id)
        assert o is not None and o.verified_at >= o.ordered_at, m.order_med_id
        if m.line == 1:
            assert m.taken_at >= o.verified_at, (m.csn, o.name)
    for n in view.w.notes:
        assert n.created_at >= n.service_at - timedelta(minutes=1), n.note_id


@gap("world._notes signs the ED provider note at the departure time when the visit is shorter than the note's "
     "20 min minimum writing time (signed = dep if dep <= lo), so a few short discharges carry a signature "
     "earlier than the note's creation; use max(dep, created + 1 min)")
def test_notes_are_signed_after_they_are_created(view):
    for n in view.w.notes:
        if n.signed_at:
            assert n.signed_at >= n.created_at, (n.note_id, n.note_type, n.created_at, n.signed_at)
        for status, at in n.versions:
            assert at <= (n.signed_at or view.end), n.note_id


# ---------------------------------------------------------------- 10. vitals cadence
def _gaps(vs):
    return [minutes(a.taken_at, b.taken_at) for a, b in zip(vs, vs[1:])]


def test_vitals_cadence_by_esi_and_phase(view):
    """Section 8.7: ESI 1 q15 min for the first hour, ESI 2 q1h for two hours then q2h, ESI 3 q2h,
    ESI 4 and 5 only at triage and disposition, boarders q4h (q2h step down, q1h ICU). Medians within
    20 percent; bins with fewer than 10 intervals are not judged."""
    ed = defaultdict(list)
    boarding = defaultdict(list)
    for e in view.encounters:
        if not e.triage_at:
            continue
        cut = e.decision_at if (e.disposition.startswith("ADMIT") and e.decision_at) else (e.departure_at or view.end)
        edv = [v for v in view.vitals(e.csn) if v.taken_at <= cut]
        t0 = e.triage_at
        if e.esi == 1:
            ed["1 first hour"] += _gaps([v for v in edv if v.taken_at <= t0 + timedelta(minutes=62)])
        elif e.esi == 2:
            ed["2 first two hours"] += _gaps([v for v in edv if v.taken_at <= t0 + timedelta(minutes=125)])
            ed["2 after"] += _gaps([v for v in edv if v.taken_at >= t0 + timedelta(minutes=115)])
        elif e.esi == 3:
            ed["3"] += _gaps(edv)
        else:
            assert len(edv) <= 2, (e.csn, e.esi, len(edv))
        if e.disposition.startswith("ADMIT") and e.decision_at:
            bv = [v for v in view.vitals(e.csn) if e.decision_at < v.taken_at < (e.departure_at or view.end)]
            level = e.level_of_care if e.level_of_care in ("ICU", "STEPDOWN") else "q4h units"
            boarding[level] += _gaps(bv)
    expected = {"1 first hour": 15, "2 first two hours": 60, "2 after": 120, "3": 120}
    for key, target in expected.items():
        xs = ed[key]
        if len(xs) >= 10:
            assert within_pct(median(xs), target, 20), (key, median(xs), len(xs))
    for level, target in (("ICU", 60), ("STEPDOWN", 120), ("q4h units", 240)):
        xs = boarding[level]
        if len(xs) >= 10:
            assert within_pct(median(xs), target, 20), (level, median(xs), len(xs))
    assert len(ed["3"]) >= 10 and len(ed["2 first two hours"]) >= 10 and len(boarding["ICU"]) >= 10


def test_no_ed_vitals_after_departure(view):
    for e in view.admits:
        if not e.departure_at:
            continue
        for v in view.vitals(e.csn):
            if v.taken_at >= e.departure_at:
                assert v.dept_id == e.unit_dept_id and v.dept_id != view.ed_dept[e.facility], (e.csn, v.line)
            else:
                assert v.dept_id == view.ed_dept[e.facility], (e.csn, v.line)
