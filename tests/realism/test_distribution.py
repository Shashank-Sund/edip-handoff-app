"""Checklist items 11 to 17: acuity and admission mix, demographics, admitting
diagnoses, lab ordering patterns, abnormal and critical shares, isolation, code
status, allergies and note signing.

Admit rate dependent targets are restated for the approved design (700 visits,
120 admits, 17 percent) and the restatement is written next to each check."""
from collections import Counter
from datetime import timedelta

import pytest

from realism_helpers import (ADMIT_RATE_RANGE, binom_sd_pts, gap, median, minutes, pct, widened)

from edip_synth.reference import LABS
from edip_synth.scenarios import ADMIT_COUNTS, SCENARIOS


# ---------------------------------------------------------------- 11. ESI, admission rate, splits
def test_esi_mix(view):
    """The spec's mix (2, 22, 52, 21, 3) was tuned for a 30 percent admit cohort and the national
    NHAMCS 2022 mix (1.5, 15.4, 50.6, 29.7, 2.8) goes with a 14 percent admit rate. The target for the
    approved 17 percent design is the linear interpolation between them (weight 0.81 toward NHAMCS).
    Tolerance: the spec's 3 points or two binomial sd at n = 700, whichever is larger."""
    spec = {1: 2.0, 2: 22.0, 3: 52.0, 4: 21.0, 5: 3.0}
    national = {1: 1.5, 2: 15.4, 3: 50.6, 4: 29.7, 5: 2.8}
    w = (30 - 17) / (30 - 14)
    target = {k: spec[k] + w * (national[k] - spec[k]) for k in spec}
    n = len(view.encounters)
    mix = Counter(e.esi for e in view.encounters)
    for k, t in target.items():
        tol = max(3.0, 2 * binom_sd_pts(t, n))
        assert abs(pct(mix[k], n) - t) <= tol, (k, pct(mix[k], n), round(t, 1), round(tol, 1))


def test_admission_rate_and_class_split(view):
    """Spec 28 to 32 percent restated as 15 to 19 for the approved design; observation share 22 to 28
    and ICU share 10 to 14 percent of admits, each widened by one binomial sd at n = 120."""
    n, adm = len(view.encounters), view.admits
    assert ADMIT_RATE_RANGE[0] <= pct(len(adm), n) <= ADMIT_RATE_RANGE[1], pct(len(adm), n)
    obs = pct(sum(e.disposition == "ADMIT_OBS" for e in adm), len(adm))
    lo, hi = widened(22, 28, len(adm))
    assert lo <= obs <= hi, obs
    decided = [e for e in adm if e.decision_at]
    icu = pct(sum(e.level_of_care == "ICU" for e in decided), len(decided))
    lo, hi = widened(10, 14, len(decided))
    assert lo <= icu <= hi, icu
    assert sum(e.disposition == "ADMIT_IP" for e in adm) == 90 and sum(e.disposition == "ADMIT_OBS" for e in adm) == 30


# ---------------------------------------------------------------- 12. demographics
def test_admitted_demographics(view):
    adm, enc = view.admits, view.encounters
    older = pct(sum(view.age(e) >= 65 for e in adm), len(adm))
    lo, hi = widened(50, 60, len(adm))
    assert lo <= older <= hi, older
    ems = pct(sum(e.arrival_mode == "EMS" for e in adm), len(adm))
    lo, hi = widened(40, 50, len(adm))
    assert lo <= ems <= hi, ems
    female = pct(sum(view.patients[e.pat_id].sex == "F" for e in enc), len(enc))
    lo, hi = widened(51, 57, len(enc))
    assert lo <= female <= hi, female
    ems_all = pct(sum(e.arrival_mode == "EMS" for e in enc), len(enc))
    assert 19 <= ems_all <= 29, ems_all          # spec 24 percent, NHAMCS 17; two sd either side


@gap("DISCHARGE templates use _generic's default age weights (25, 30, 20, 25), so visits by 18 to 44 year olds "
     "are about 23 percent against the spec's 30; set the discharge default to about (32, 28, 17, 23)")
def test_overall_age_mix(view):
    """Spec 8.11: 18 to 44 30 percent, 45 to 64 28, 65 to 74 18, 75 plus 24. These weights already imply a
    17 percent admit rate through the NHAMCS age specific admission rates, so they are not restated.
    Tolerance max(3 points, two binomial sd at n = 700)."""
    n = len(view.encounters)
    bands = Counter()
    for e in view.encounters:
        a = view.age(e)
        bands["18-44" if a < 45 else "45-64" if a < 65 else "65-74" if a < 75 else "75+"] += 1
    for band, target in (("18-44", 30), ("45-64", 28), ("65-74", 18), ("75+", 24)):
        tol = max(3.0, 2 * binom_sd_pts(target, n))
        assert abs(pct(bands[band], n) - target) <= tol, (band, pct(bands[band], n), tol)


# ---------------------------------------------------------------- 13. admitting diagnoses
def test_admitting_diagnosis_mix(view):
    """Scenario counts follow the section 8.4 weights exactly (they are allocated, not sampled).
    Per hospital presence is only required where it is statistically expected: with a 60/40 split a
    scenario with 3 admits lands in one hospital 28 percent of the time, so scenarios with at least 6
    admits must appear at both hospitals and each hospital must see at least 6 of the 9 detailed ones."""
    counts = Counter(e.scenario for e in view.admits)
    for key, want in ADMIT_COUNTS.items():
        assert counts[key] == want, (key, counts[key], want)
    for key in SCENARIOS:
        assert counts[key] >= 3, key
    per_site = Counter((e.scenario, e.facility) for e in view.admits)
    for key, want in ADMIT_COUNTS.items():
        if key in SCENARIOS and want >= 6:
            assert per_site[(key, "MGH")] and per_site[(key, "LKS")], key
    for fac in ("MGH", "LKS"):
        assert sum(1 for key in SCENARIOS if per_site[(key, fac)]) >= 6, fac
    mgh = pct(sum(e.facility == "MGH" for e in view.admits), len(view.admits))
    assert 50 <= mgh <= 70, mgh


# ---------------------------------------------------------------- 14. lab ordering patterns
def _has(view, e, prefix):
    return any(o.order_type == "Lab" and o.name.startswith(prefix) for o in view.orders(e.csn))


def test_lab_ordering_patterns(view):
    seen = [e for e in view.admits if e.provider_at]
    settled = [e for e in seen if e.departure_at or (e.decision_at and e.decision_at < view.end - timedelta(hours=3))]
    assert pct(sum(_has(view, e, "CBC") and (_has(view, e, "Basic") or _has(view, e, "Comprehensive")) for e in seen), len(seen)) >= 95
    sepsis = [e for e in seen if e.scenario.startswith("sepsis")]
    assert sepsis and pct(sum(_has(view, e, "Lactate") for e in sepsis), len(sepsis)) >= 90
    chest = [e for e in settled if e.scenario in ("chest_pain_ro", "nstemi")]
    two = sum(sum(o.name.startswith("Troponin") for o in view.orders(e.csn)) >= 2 for e in chest)
    assert chest and pct(two, len(chest)) >= 95, (two, len(chest))
    for key, panel in (("dka", "Beta hydroxybutyrate"), ("ugib", "Type and screen"), ("hip_fx", "Type and screen")):
        group = [e for e in seen if e.scenario == key]
        assert group and all(_has(view, e, panel) for e in group), key


# ---------------------------------------------------------------- 15. abnormal and critical shares
IOWA_PLUS_LACTATE = {  # analyte: (critical low, critical high), spec 10.4 (University of Iowa, lactate over 4.0)
    "K": (2.8, 6.2), "NA": (120, 160), "GLU": (50, 450), "POCGLU": (50, 450), "CA": (6.0, 13.0), "MG": (1.0, 4.7),
    "CO2": (10, 50), "VHCO3": (10, 50), "LACT": (None, 4.0), "HGB": (6.0, 22.0), "PLT": (10, 1000), "WBC": (1.0, 50.0),
    "INR": (None, 4.0), "VPH": (7.20, 7.60), "VPCO2": (20, 70),
}


def _expected_flag(code, x):
    cat = LABS[code]
    ref_lo, ref_hi, c_lo, c_hi = cat[3], cat[4], cat[5], cat[6]
    if c_lo is not None and x < c_lo:
        return "LL"
    if c_hi is not None and x > c_hi:
        return "HH"
    if ref_lo is not None and x < ref_lo:
        return "L"
    if ref_hi is not None and x > ref_hi:
        return "H"
    return ""


def test_critical_thresholds_match_the_chosen_list(view):
    for code, (lo, hi) in IOWA_PLUS_LACTATE.items():
        assert (LABS[code][5], LABS[code][6]) == (lo, hi), (code, LABS[code][5:7])
    for code, cat in LABS.items():
        if code not in IOWA_PLUS_LACTATE:
            assert cat[5] is None and cat[6] is None, (code, "critical limit not on the Iowa list")
    for r in view.w.results:
        if r.numeric is not None and r.lab in LABS:
            assert r.flag == _expected_flag(r.lab, r.numeric), (r.csn, r.lab, r.value, r.flag)
            if r.flag in ("HH", "LL"):
                assert "called to" in r.comment and "read back" in r.comment, (r.csn, r.lab)


@gap("scenario lab ranges in scenarios.py put 14 to 18 percent of numeric components outside the reference range, "
     "against the spec's 5 to 8 percent; the spec figure is itself below published ED panel audits (20 to 30 "
     "percent), so the target in section 12 item 15 should be revised rather than the generator")
def test_abnormal_component_share(view):
    num = [r for r in view.w.results if r.numeric is not None]
    share = pct(sum(1 for r in num if r.flag), len(num))
    assert 5 <= share <= 8, share


@gap("only 0.25 percent of numeric components reach a critical limit against the spec's 0.5 to 1.5 percent; the "
     "scenario first draw ranges rarely cross the Iowa limits (sepsis LACT (2.5, 5.0), AKI K (5.2, 6.4), UGIB HGB "
     "(7.0, 11.0)); widen them (for example LACT (2.5, 6.0), K (5.4, 6.8), HGB (5.8, 11.0)) or add a few "
     "critical draws per scenario")
def test_critical_component_share(view):
    num = [r for r in view.w.results if r.numeric is not None]
    share = pct(sum(1 for r in num if r.flag in ("HH", "LL")), len(num))
    assert 0.5 <= share <= 1.5, share


# ---------------------------------------------------------------- 16. isolation, code status, allergies
KAISER = {"<65": {"Full Code": 96.9, "DNR/DNI": 2.4}, "65-84": {"Full Code": 79.2, "DNR/DNI": 17.9},
          "85+": {"Full Code": 45.9, "DNR/DNI": 48.7}}


def test_isolation_share(view):
    adm = view.admits
    iso = pct(sum(bool(e.isolation) for e in adm), len(adm))
    assert 13 <= iso <= 17, iso


def test_code_status_by_age(view):
    """Kaiser table within 5 points or two binomial sd, whichever is larger: the 85 plus band holds
    about 17 admits, where one patient is 6 points."""
    bands = {}
    for e in view.admits:
        c = view.code_status.get(e.csn)
        if c:
            a = view.age(e)
            bands.setdefault("<65" if a < 65 else "65-84" if a < 85 else "85+", []).append(c.status)
    for band, want in KAISER.items():
        xs = bands.get(band, [])
        assert len(xs) >= 10, (band, len(xs))
        for status, target in want.items():
            tol = max(5.0, 2 * binom_sd_pts(target, len(xs)))
            got = pct(sum(s == status for s in xs), len(xs))
            assert abs(got - target) <= tol, (band, status, got, target, round(tol, 1))
    assert set().union(*bands.values()) <= {"Full Code", "DNR/DNI", "Partial Code", "Comfort Care"}
    pending = [e for e in view.admits if e.departure_at and e.departure_at < view.end - timedelta(hours=6)
               and e.csn not in view.code_status]
    assert len(pending) <= 4, [e.csn for e in pending]     # F20: at most 4 undocumented after transfer


def test_allergy_prevalence_and_leading_allergen(view):
    pats = [p for p in view.patients.values() if not p.merged_into]
    allergic = [p for p in pats if view.allergies(p.pat_id)]
    share = pct(len(allergic), len(pats))
    lo, hi = widened(35, 41, len(pats))
    assert lo <= share <= hi, share
    top = Counter(a.allergen for a in view.w.allergies).most_common(2)
    assert top[0][0] == "Penicillins", top
    assert all(a.reaction and a.severity for a in view.w.allergies)
    reviews = Counter(p.allergy_review for p in pats)
    assert 2 <= pct(reviews["Not on File"], len(pats)) <= 7 and 2 <= pct(reviews["No Known Allergies"], len(pats)) <= 8, reviews


@gap("world._allergies draws 1 + int(expovariate(1/0.9)) allergens, whose mean is about 1.4 against the spec's "
     "Poisson mean 1.9 per allergic patient; use 1 + a Poisson(0.9) draw (or weights 50, 27, 13, 6, 4 for 1 to 5)")
def test_allergies_per_allergic_patient(view):
    counts = [len(view.allergies(p.pat_id)) for p in view.patients.values() if view.allergies(p.pat_id)]
    mean = sum(counts) / len(counts)
    assert 1.6 <= mean <= 2.2, mean


# ---------------------------------------------------------------- 17. note signing
def test_provider_note_signing(view):
    """Signed before ED departure 50 to 60 percent of visits; signed after departure 35 to 45 percent of
    admits (F16). Each widened by one binomial sd at its n (about 2 and 4.6 points)."""
    notes = [n for n in view.w.notes if n.note_type == "ED Provider Notes" and view.enc[n.csn].departure_at]
    before = [n for n in notes if n.signed_at and n.signed_at <= view.enc[n.csn].departure_at]
    lo, hi = widened(50, 60, len(notes))
    assert lo <= pct(len(before), len(notes)) <= hi, pct(len(before), len(notes))
    adm_notes = [n for n in notes if view.enc[n.csn].disposition.startswith("ADMIT")]
    after = [n for n in adm_notes if not (n.signed_at and n.signed_at <= view.enc[n.csn].departure_at)]
    lo, hi = widened(35, 45, len(adm_notes))
    assert lo <= pct(len(after), len(adm_notes)) <= hi, pct(len(after), len(adm_notes))
    # The note is opened 5 to 30 min after first contact, so patients seen in the last half hour have none yet.
    seen = [e for e in view.encounters if e.provider_at and e.provider_at < view.end - timedelta(minutes=30)]
    with_note = {n.csn for n in view.w.notes if n.note_type == "ED Provider Notes"}
    assert all(e.csn in with_note for e in seen), [e.csn for e in seen if e.csn not in with_note]
    assert all(e.csn not in with_note for e in view.encounters if e.disposition == "LWBS")


def test_other_note_timing(view):
    """Section 8.8: triage notes signed within 15 min in 98 percent; nursing reassessments within 30 min;
    H and P started 30 to 120 min after the decision and signed 1 to 6 h later."""
    triage = [n for n in view.w.notes if n.note_type == "ED Triage Notes" and n.signed_at]
    quick = sum(minutes(n.created_at, n.signed_at) <= 15 for n in triage)
    assert pct(quick, len(triage)) >= 95, pct(quick, len(triage))
    reassess = [n for n in view.w.notes if n.note_type == "ED Notes" and n.signed_at]
    assert reassess and all(minutes(n.created_at, n.signed_at) <= 30 for n in reassess)
    hp = [n for n in view.w.notes if n.note_type == "H&P"]
    assert hp
    for n in hp:
        e = view.enc[n.csn]
        assert 30 <= minutes(e.decision_at, n.created_at) <= 120, n.note_id
        if n.signed_at:
            assert 60 <= minutes(n.created_at, n.signed_at) <= 360, n.note_id
    for e in view.admits:
        if e.departure_at and e.departure_at < view.end - timedelta(hours=1):
            kinds = {n.note_type for n in view.notes(e.csn)}
            assert "ED to Hosp-Admission Nursing Transfer" in kinds, e.csn


@gap("admitted patients average about 2.3 MAR administrations in the ED against the spec's 3.5 (section 3): "
     "nothing is ordered after the admit decision although the nursing note says 'Admission orders active' "
     "(ENOX has prob 0.0, no home medication continuation, no PRN or maintenance fluids); add a small admission "
     "order set per scenario in scenarios.py and a boarding order pass in world._meds")
def test_ed_medication_administrations_per_admit(view):
    counts = []
    for e in view.admits:
        if not e.provider_at:
            continue
        cut = e.departure_at or view.end
        counts.append(sum(1 for m in view.mar_for(e.csn) if m.taken_at < cut and m.action in ("Given", "New Bag")))
    mean = sum(counts) / len(counts)
    assert pct(sum(c > 0 for c in counts), len(counts)) >= 88, pct(sum(c > 0 for c in counts), len(counts))
    assert 3.0 <= mean <= 4.0, mean            # spec mean 3.5, 15 percent
    assert median(counts) is not None
