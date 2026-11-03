"""Checklist items 18 to 22: vitals against scenario ranges and treatment
response, lab plausibility and derived values, medication realism, coding, and
note text against the structured record.
(tests/test_world.py already checks physiologic bounds, anion gap, MAP and the
vitals quoted in notes.)"""
import json
import re
from datetime import timedelta

import pytest

from realism_helpers import CODE_VERIFICATION, REAL, gap, local, minutes, pct

from edip_synth.reference import LABS
from edip_synth.scenarios import DISCHARGE, GENERIC_ADMITS, SCENARIOS


@pytest.fixture(scope="module")
def verified():
    data = json.loads(CODE_VERIFICATION.read_text())
    out = {}
    for row in data["codes"]:
        if row["verified"]:
            out.setdefault(row["system"], set()).add(row["code"])
    return out


# ---------------------------------------------------------------- 18. vitals
# Charting noise in world._vitals (gauss sd): hr 4, sbp 6, dbp 4, rr 1.5, temp 0.12, spo2 1, pain 0.8.
NOISE = {"hr": 4, "sbp": 6, "dbp": 4, "rr": 1.5, "temp": 0.12, "spo2": 1, "pain": 0.8, "gcs": 0.3}


def test_triage_vitals_inside_scenario_ranges(view):
    """First charted set equals the scenario draw plus charting noise, so each value must sit inside the
    section 10.1 range widened by four noise sd; with about 4,500 values a 1 percent allowance covers
    the rounding and the DBP cap at SBP minus 20."""
    checked = outside = 0
    for e in view.encounters:
        vs = view.vitals(e.csn)
        if not e.triage_at or not vs:
            continue
        v = vs[0]
        assert abs(minutes(e.triage_at, v.taken_at)) < 1, (e.csn, "first set is the triage set")
        for k, (lo, hi) in view.scenario(e).vitals.items():
            x = getattr(v, k)
            if x is None:
                continue
            checked += 1
            if not (lo - 4 * NOISE[k] <= x <= hi + 4 * NOISE[k]):
                outside += 1
    assert checked > 3000 and pct(outside, checked) <= 1.0, (outside, checked)


# (scenario, vital, direction after treatment) from section 10.1
TREND = [("sepsis_uti", "hr", -1), ("sepsis_uti", "temp", -1), ("sepsis_pna", "hr", -1), ("sepsis_pna", "spo2", 1),
         ("sepsis_pna", "rr", -1), ("chf", "spo2", 1), ("chf", "rr", -1), ("chf", "sbp", -1), ("copd", "spo2", 1),
         ("copd", "rr", -1), ("dka", "hr", -1), ("dka", "rr", -1), ("cap", "temp", -1), ("cap", "spo2", 1),
         ("hip_fx", "pain", -1), ("nstemi", "pain", -1), ("chest_pain_ro", "pain", -1), ("ugib", "hr", -1),
         ("ugib", "sbp", 1), ("af_rvr", "hr", -1)]


def test_vitals_move_in_the_treatment_direction(view):
    """For treated scenarios, compare the triage set with the last set at least two hours later and
    after the first MAR administration. At least 75 percent of pairs move the right way overall, and
    every scenario vital with 5 or more pairs moves the right way on average."""
    per = {}
    for e in view.admits:
        if not e.triage_at:
            continue
        mar = view.mar_for(e.csn)
        if not mar:
            continue
        vs = [v for v in view.vitals(e.csn) if v.taken_at < (e.departure_at or view.end)]
        if len(vs) < 3 or minutes(vs[0].taken_at, vs[-1].taken_at) < 120 or vs[-1].taken_at < mar[0].taken_at:
            continue
        for scen, k, d in TREND:
            if e.scenario != scen:
                continue
            a, b = getattr(vs[0], k), getattr(vs[-1], k)
            if a is None or b is None:
                continue
            per.setdefault((scen, k), []).append((b - a) * d)
    deltas = [x for xs in per.values() for x in xs]
    assert len(deltas) >= 60, len(deltas)
    assert pct(sum(x > 0 for x in deltas), len(deltas)) >= 75, pct(sum(x > 0 for x in deltas), len(deltas))
    for key, xs in per.items():
        if len(xs) >= 5:
            assert sum(xs) / len(xs) > 0, (key, xs)


def test_oxygen_started_for_hypoxic_scenarios(view):
    for e in view.admits:
        s = view.scenario(e)
        vs = view.vitals(e.csn)
        if not s.o2 or not vs or vs[0].spo2 >= 92:
            continue
        later = [v for v in vs if v.taken_at > vs[0].taken_at + timedelta(minutes=30)]
        if later:
            assert any(v.o2_device == "Nasal cannula" and v.o2_flow for v in later), e.csn
            assert all(v.o2_flow is None or 1 <= v.o2_flow <= 6 for v in vs), e.csn


def test_chest_pain_ecg_within_10_minutes(view):
    times = []
    for e in view.encounters:
        if e.scenario in ("chest_pain_ro", "nstemi", "chest_pain_d", "stemi_transfer"):
            ecg = [o for o in view.orders(e.csn) if o.name == "12 lead ECG" and o.collected_at]
            if ecg:
                times.append(minutes(e.arrival_at, ecg[0].collected_at))
    assert len(times) >= 20
    assert pct(sum(t <= 10 for t in times), len(times)) >= 90, (sorted(round(t) for t in times))


def test_sepsis_antibiotics_within_an_hour(view):
    """SSC 2021: antimicrobials within 1 h for possible septic shock, within 3 h otherwise; the spec's own
    example note gives ceftriaxone within 1 h of recognition."""
    times = []
    for e in view.admits:
        if not e.scenario.startswith("sepsis"):
            continue
        given = [view.mar(m.order_med_id) for m in view.med_orders(e.csn)
                 if m.name.startswith(("cefTRIAXone", "piperacillin", "cefepime", "vancomycin"))]
        first = [rows[0].taken_at for rows in given if rows]
        if first:
            times.append(minutes(e.triage_at, min(first)))
    assert len(times) >= 8
    assert all(t <= 180 for t in times), sorted(round(t) for t in times)
    assert sorted(times)[len(times) // 2] <= 60, sorted(round(t) for t in times)


def _series(view, csn, lab, status=("F",)):
    return [r for r in view.results(csn) if r.lab == lab and r.status in status and r.numeric is not None]


def test_dka_glucose_falls_after_insulin(view):
    """Section 10.1: glucose falls 50 to 75 mg/dL per hour after the insulin infusion starts. Accepted
    25 to 125 per hour between consecutive ED draws, always falling, until dextrose is added under 250
    mg/dL, after which glucose is held near 150 to 200."""
    checked = 0
    for e in view.admits:
        if e.scenario != "dka":
            continue
        ins = [m for m in view.med_orders(e.csn) if m.name.startswith("insulin")]
        if not ins:
            continue
        start = view.mar(ins[0].order_med_id)[0].taken_at
        glu = [r for r in _series(view, e.csn, "GLU") if r.observed_at < (e.departure_at or view.end)]
        for a, b in zip(glu, glu[1:]):
            if a.observed_at < start or a.numeric <= 250:
                continue
            rate = (a.numeric - b.numeric) / (minutes(a.observed_at, b.observed_at) / 60)
            checked += 1
            assert 25 <= rate <= 125, (e.csn, a.value, b.value, round(rate))
    assert checked >= 1


def test_nstemi_troponin_rises(view):
    checked = 0
    for e in view.admits:
        if e.scenario != "nstemi":
            continue
        trop = _series(view, e.csn, "TROP")
        if len(trop) >= 2:
            checked += 1
            assert trop[1].numeric >= 1.2 * trop[0].numeric, (e.csn, trop[0].value, trop[1].value)
    assert checked >= 2


def test_chf_troponins_are_flat(view):
    checked = 0
    for e in view.admits:
        if e.scenario != "chf":
            continue
        trop = _series(view, e.csn, "TROP")
        if len(trop) >= 2:
            checked += 1
            assert abs(trop[1].numeric - trop[0].numeric) <= 0.3 * trop[0].numeric, (e.csn, trop[0].value, trop[1].value)
    assert checked >= 5


def test_critical_results_after_the_decision_get_a_response(view):
    checked = 0
    for e in view.admits:
        if not e.decision_at:
            continue
        for r in view.results(e.csn):
            if r.flag not in ("HH", "LL") or r.resulted_at < e.decision_at or r.resulted_at > view.end - timedelta(hours=3):
                continue
            checked += 1
            window_end = r.resulted_at + timedelta(hours=3)
            repeat = [o for o in view.orders(e.csn) if o.order_type == "Lab" and r.resulted_at < o.ordered_at <= window_end]
            treat = [m for m in view.med_orders(e.csn) if r.resulted_at < m.ordered_at <= window_end]
            assert repeat or treat, (e.csn, r.lab, r.value, r.resulted_at)
    assert checked >= 5


def test_heparin_infusions_have_a_bolus_and_monitoring(view):
    checked = 0
    for e in view.admits:
        hep = [m for m in view.med_orders(e.csn) if m.name.startswith("heparin")]
        if not hep:
            continue
        checked += 1
        rows = view.mar(hep[0].order_med_id)
        start = rows[0].taken_at if rows else hep[0].ordered_at
        assert any(a.action == "Given" and a.unit == "units" for a in view.mar_for(e.csn)), (e.csn, "no heparin bolus")
        if start < view.end - timedelta(hours=8):
            assert any(o.name.startswith("PT/INR") and start < o.ordered_at <= start + timedelta(hours=8)
                       for o in view.orders(e.csn)), (e.csn, "no aPTT after heparin start")
    assert checked >= 2


def test_sepsis_lactate_clears(view):
    pairs = [(_series(view, e.csn, "LACT")) for e in view.admits if e.scenario.startswith("sepsis")]
    pairs = [p for p in pairs if len(p) >= 2]
    assert len(pairs) >= 8
    assert all(p[1].numeric < p[0].numeric for p in pairs)


def test_ugib_hemoglobin_does_not_rise_without_transfusion(view):
    checked = 0
    for e in view.admits:
        if e.scenario != "ugib":
            continue
        hgb = _series(view, e.csn, "HGB", status=("F", "C"))
        units = [o.ordered_at for o in view.orders(e.csn) if o.order_type == "Blood Product"]
        for a, b in zip(hgb, hgb[1:]):
            checked += 1
            given = sum(1 for t in units if a.resulted_at < t < b.observed_at)   # about 1 g/dL per unit of PRBC
            assert b.numeric - a.numeric <= 1.0 + 1.2 * given, (e.csn, a.value, b.value, given)
    assert checked >= 5


# ---------------------------------------------------------------- 19. labs
ANALYZER = {  # reportable range of a typical core lab analyzer, in catalog units
    "WBC": (0.1, 400), "HGB": (1.0, 25.0), "HCT": (3, 75), "PLT": (1, 3000), "NA": (100, 190), "K": (1.0, 10.0),
    "CL": (60, 140), "CO2": (2, 60), "BUN": (1, 250), "CREAT": (0.1, 30.0), "GLU": (10, 2000), "CA": (3.0, 20.0),
    "AGAP": (-10, 60), "AST": (3, 10000), "ALT": (3, 10000), "ALKP": (5, 3000), "TBILI": (0.1, 60), "ALB": (0.5, 8),
    "TP": (1, 15), "LACT": (0.1, 30), "TROP": (0, 50000), "BNP": (0, 50000), "INR": (0.5, 20), "PTT": (10, 300),
    "LIPASE": (1, 30000), "MG": (0.3, 10), "PHOS": (0.3, 20), "VPH": (6.5, 8.0), "VPCO2": (5, 250), "VHCO3": (2, 60),
    "BHB": (0.0, 30), "PCT": (0.0, 500), "CRP": (0, 600), "CK": (5, 100000), "A1C": (3, 20), "POCGLU": (10, 600),
    "UA_WBC": (0, 500),
}


def test_lab_values_inside_analyzer_ranges_and_well_formed(view):
    for r in view.w.results:
        if r.numeric is None:
            continue
        lo, hi = ANALYZER[r.lab]
        assert lo <= r.numeric <= hi, (r.csn, r.lab, r.value)
        assert float(r.value) == r.numeric and r.units == LABS[r.lab][2], (r.csn, r.lab)
        if r.lab in ("CREAT", "INR", "LACT"):
            assert re.fullmatch(r"\d+\.\d{1,2}", r.value), (r.lab, r.value)   # two decimals
        elif r.lab in ("WBC", "HGB", "HCT", "CA", "TBILI", "ALB", "TP", "MG", "PHOS", "BHB", "A1C", "K", "VPH", "PCT"):
            assert "." in r.value, (r.lab, r.value)
        else:
            assert re.fullmatch(r"-?\d+", r.value), (r.lab, r.value)              # whole numbers


def test_first_draws_follow_the_scenario_panels(view):
    """Each lab the scenario specifies lands inside its section 10.2 range on the first final result
    (rounding of 0.01 to 1 unit allowed). Later draws follow the repeat and trend ranges, and the F17 and
    F19 faults only touch later results."""
    checked = 0
    for e in view.encounters:
        for lab, (lo, hi) in view.scenario(e).labs.items():
            r = view.first_result(e.csn, lab)
            if r is None:
                continue
            step = 0.01 if lab in ("CREAT", "INR", "LACT", "VPH", "PCT") else 0.1 if "." in r.value else 1
            assert lo - step <= r.numeric <= hi + step, (e.csn, e.scenario, lab, r.value, (lo, hi))
            checked += 1
    assert checked > 250, checked        # about 120 admits x 3 to 6 scenario labs plus the troponin templates


def test_derived_values_are_consistent(view):
    """Hematocrit about three times hemoglobin; BUN to creatinine ratio over 20 in UGIB; corrected sodium in
    DKA (Na plus 1.6 per 100 mg/dL glucose over 100) inside 130 to 150; potassium never reported above the
    sodium minus chloride gap in a way that breaks the anion gap check (covered in test_world)."""
    by_order = {}
    for r in view.w.results:
        if r.status == "F" and r.numeric is not None and r.lab in ("HGB", "HCT", "BUN", "CREAT", "NA", "GLU"):
            by_order.setdefault((r.order_id, r.resulted_at), {})[r.lab] = r.numeric
    pairs = 0
    for vals in by_order.values():
        if "HGB" in vals and "HCT" in vals:
            assert abs(vals["HCT"] - 3 * vals["HGB"]) <= 3.0, vals
            pairs += 1
    assert pairs > 300
    for e in view.admits:
        if e.scenario == "ugib":
            bun, cr = view.first_result(e.csn, "BUN"), view.first_result(e.csn, "CREAT")
            if bun and cr:
                assert bun.numeric / cr.numeric > 20, (e.csn, bun.value, cr.value)
        if e.scenario == "dka":
            na, glu = view.first_result(e.csn, "NA"), view.first_result(e.csn, "GLU")
            if na and glu:
                corrected = na.numeric + 1.6 * (glu.numeric - 100) / 100
                assert 130 <= corrected <= 150, (e.csn, na.value, glu.value, round(corrected, 1))


# ---------------------------------------------------------------- 20. medications
# Spec 10.5 dose ranges by order name prefix: (low, high, unit).
DOSES = {
    "insulin regular IV push": (10, 10, "units"), "dextrose 50": (25, 25, "g"),
    "sodium chloride": (500, 1000, "mL"), "acetaminophen": (650, 1000, "mg"), "ondansetron": (4, 8, "mg"),
    "morphine": (2, 6, "mg"), "hydromorphone": (0.25, 1, "mg"), "cefTRIAXone": (1, 2, "g"), "azithromycin": (500, 500, "mg"),
    "piperacillin": (4.5, 4.5, "g"), "vancomycin": (750, 2500, "mg"), "norepinephrine": (2, 30, "mcg/min"),
    "furosemide": (40, 80, "mg"), "nitroglycerin": (0.4, 0.4, "mg"), "aspirin": (81, 325, "mg"), "metoprolol": (5, 5, "mg"),
    "alteplase": (0, 90, "mg"), "labetalol": (10, 20, "mg"), "pantoprazole": (40, 80, "mg"), "octreotide": (50, 50, "mcg"),
    "insulin": (3, 16, "units/hr"), "potassium chloride": (10, 40, "mEq"), "cefazolin": (2, 2, "g"), "LORazepam": (1, 2, "mg"),
    "ketorolac": (15, 30, "mg"), "albuterol-ipratropium": (3, 3, "mL"), "methylPREDNISolone": (125, 125, "mg"),
    "enoxaparin": (40, 40, "mg"), "cefepime": (2, 2, "g"), "lactated": (1000, 4000, "mL"), "diltiazem": (10, 25, "mg"),
    "heparin": (500, 1000, "units/hr"),
}
PENICILLINS = ("piperacillin", "amoxicillin", "ampicillin", "penicillin", "nafcillin", "oxacillin")


def _dose_rule(name):
    for prefix, rule in DOSES.items():
        if name.startswith(prefix):
            return rule
    raise AssertionError(f"no dose rule for {name}")


def test_every_administered_drug_has_a_verified_rxcui(view, verified):
    rx = verified["RxNorm"]
    for m in view.w.med_orders:
        assert m.rxcui in rx, (m.name, m.rxcui)
    given = {m.order_med_id for m in view.w.mar}
    orders = {m.order_med_id for m in view.w.med_orders}
    assert given <= orders


def test_fixed_doses_within_reference_ranges(view):
    for m in view.w.med_orders:
        if m.name.startswith(("heparin", "diltiazem")):
            continue                         # judged separately below
        lo, hi, unit = _dose_rule(m.name)
        assert m.unit == unit, (m.name, m.dose, m.unit)
        assert lo <= float(m.dose) <= hi, (m.name, m.dose, m.unit)
        assert m.route in ("IV", "PO", "SL", "SC", "INH", "IM"), m.route
    for a in view.w.mar:
        assert a.action in ("Given", "New Bag", "Rate Change", "Stopped", "Held")


def test_weight_based_doses_use_the_charted_weight(view):
    checked = 0
    for m in view.w.med_orders:
        if not m.comment.startswith("Weight based"):
            continue
        e = view.enc[m.csn]
        w = float(re.search(r"weight ([\d.]+) kg", m.comment)[1])
        assert w == e.weight_kg == view.patients[e.pat_id].weight_kg, (m.csn, w, e.weight_kg)
        dose = float(m.dose)
        if m.name.startswith("lactated"):          # 30 mL/kg, capped at 3 L
            assert dose == min(3000, round(30 * w / 100) * 100) and m.unit == "mL"
        elif m.name.startswith("heparin"):         # 12 units/kg/hr, nomogram rounding to 50, max 1000
            assert dose == min(1000, round(12 * w / 50) * 50) and m.unit == "units/hr"
        elif m.name.startswith("insulin"):         # 0.1 units/kg/hr, rounded to 0.5
            assert abs(dose - 0.1 * w) <= 0.25 and m.unit == "units/hr"
        elif m.name.startswith("diltiazem"):       # 0.25 mg/kg, rounded to 5 mg, max 25
            assert dose == min(25, round(0.25 * w / 5) * 5) and m.unit == "mg"
        elif m.name.startswith("vancomycin"):
            assert dose == min(2500, round(20 * w / 250) * 250) and m.unit == "mg"
        elif m.name.startswith("alteplase"):
            assert abs(dose - min(90.0, 0.9 * w)) < 0.06 and m.unit == "mg"
        else:
            raise AssertionError(m.name)
        checked += 1
    assert checked >= 15, checked


def test_heparin_infusion_follows_the_nomogram(view):
    hep = [m for m in view.w.med_orders if m.name.startswith("heparin")]
    assert hep
    for m in hep:
        dose = float(m.dose)
        assert 500 <= dose <= 1000 and dose % 50 == 0, (m.csn, m.dose, m.rate)


def test_no_per_kilogram_units_reach_the_mar(view):
    for m in view.w.med_orders:
        assert "/kg" not in m.unit, (m.name, m.dose, m.unit)
    for m in view.w.mar:
        assert "/kg" not in m.unit, (m.csn, m.dose, m.unit)


def test_antibiotics_follow_blood_cultures_in_sepsis(view):
    """Section 10.1: cultures before antimicrobials in 90 percent or more of sepsis encounters."""
    ok = total = 0
    for e in view.admits:
        if not e.scenario.startswith("sepsis"):
            continue
        cx = [o.collected_at for o in view.orders(e.csn) if o.name == "Blood culture" and o.collected_at]
        abx = [view.mar(m.order_med_id) for m in view.med_orders(e.csn)
               if m.name.startswith(("cefTRIAXone", "piperacillin", "cefepime", "vancomycin", "azithromycin"))]
        first = [rows[0].taken_at for rows in abx if rows]
        if cx and first:
            total += 1
            ok += min(first) > min(cx)
    assert total >= 8 and pct(ok, total) >= 90, (ok, total)


def test_penicillin_allergy_is_respected(view):
    """No penicillin class drug to a penicillin allergic patient without an override comment; the fixed
    override text names the risk benefit decision."""
    for e in view.encounters:
        if not any(a.allergen == "Penicillins" for a in view.allergies(e.pat_id)):
            continue
        for m in view.med_orders(e.csn):
            if m.name.startswith(PENICILLINS):
                assert "Allergy override" in m.comment, (e.csn, m.name)
                assert "PCN_OVERRIDE" in e.flags


def test_exactly_one_deliberate_allergy_override(view):
    overrides = [m for m in view.w.med_orders if "Allergy override" in m.comment]
    assert len(overrides) == 1, len(overrides)
    assert sum("PCN_OVERRIDE" in e.flags for e in view.encounters) == 1


# ---------------------------------------------------------------- 21. coding
def test_diagnosis_codes_are_valid(view, verified):
    icd, sno = verified["ICD-10-CM"], verified["SNOMED CT"]
    for e in view.encounters:
        assert e.dx, e.csn
        for code, snomed, display in e.dx:
            assert code in icd, (e.csn, code)
            assert display
            if snomed:
                assert snomed in sno, (e.csn, snomed)
    for book in (SCENARIOS, GENERIC_ADMITS, DISCHARGE):
        for s in book.values():
            for code, snomed, _ in s.dx:
                assert code in icd, (s.key, code)


def test_problems_have_snomed_and_icd10(view):
    problems = [p for pat in view.patients.values() for p in pat.problems]
    assert problems
    for p in problems:
        assert re.fullmatch(r"\d{6,18}", str(p["snomed"])), p
        assert p.get("icd10") and re.fullmatch(r"[A-Z]\d\d(\.\w{1,4})?", p["icd10"]), p


CKD_STAGES = {"431855005", "431856006", "433144002", "431857002", "46177005"}


def test_problem_list_has_a_single_ckd_stage(view):
    for p in view.patients.values():
        stages = {x["snomed"] for x in p.problems} & CKD_STAGES
        assert len(stages) <= 1, (p.pat_id, [x["display"] for x in p.problems if x["snomed"] in CKD_STAGES])


# Class allergen -> words that identify a member of the class in a home medication display.
CLASS_MEMBERS = {"ACE inhibitors": ("lisinopril", "enalapril", "ramipril", "captopril", "benazepril"), "Statins": ("statin",),
                 "NSAIDs": ("ibuprofen", "naproxen", "meloxicam", "diclofenac"), "Sulfa (sulfonamide antibiotics)": ("sulfamethoxazole",),
                 "Penicillins": ("amoxicillin", "ampicillin", "penicillin"), "Codeine": ("codeine",), "Morphine": ("morphine",),
                 "Cephalexin": ("cephalexin",), "Ciprofloxacin": ("ciprofloxacin",)}


def test_allergies_do_not_conflict_with_home_medications(view):
    for a in view.w.allergies:
        p = view.patients[a.pat_id]
        words = CLASS_MEMBERS.get(a.allergen, ())
        clash = [m["display"] for m in p.home_meds if any(wd in m["display"].lower() for wd in words)]
        assert not clash, (p.pat_id, a.allergen, clash)


def test_code_status_is_plausible_for_age(view):
    serious = {"46177005", "254837009", "126906006", "26929004"}     # ESRD, breast cancer, prostate neoplasm, Alzheimer's
    for e in view.admits:
        c = view.code_status.get(e.csn)
        if c and c.status != "Full Code" and view.age(e) < 50:
            problems = {x["snomed"] for x in view.patients[e.pat_id].problems}
            assert problems & serious, (e.csn, view.age(e), c.status, e.scenario)


def test_home_medications_have_rxnorm(view):
    meds = [m for pat in view.patients.values() for m in pat.home_meds]
    if view.real:
        assert meds
    for m in meds:
        assert re.fullmatch(r"\d{1,8}", str(m["rxcui"])), m
        assert m.get("display")


# ---------------------------------------------------------------- 22. note text
SHORT = {"WBC": "WBC", "HGB": "Hgb", "PLT": "Plt", "NA": "Na", "K": "K", "CO2": "CO2", "AGAP": "AG", "BUN": "BUN", "CREAT": "Cr",
         "GLU": "Glu", "LACT": "lactate", "TROP": "hs-TnT", "BNP": "BNP", "INR": "INR", "VPH": "VBG pH", "VPCO2": "pCO2",
         "BHB": "BHB", "PCT": "PCT", "LIPASE": "lipase", "CK": "CK"}
LAB_OF = {v: k for k, v in SHORT.items()}


def test_note_results_match_the_structured_record(view):
    """Every lab quoted in a note is a result of that CSN released before the note was signed (or before the
    window end for an unsigned note), and every medication time quoted is a MAR time of that CSN."""
    checked = 0
    for n in view.w.notes:
        cutoff = n.signed_at or view.end
        results = view.results(n.csn)
        m = re.search(r"^Results: (.*)\.$", n.text, re.M)
        if m and m[1] != "pending":
            for part in m[1].split(", "):
                mm = re.match(r"(.+) (\S+)$", part)
                if mm and mm[1] in LAB_OF:
                    lab = LAB_OF[mm[1]]
                    assert any(r.lab == lab and r.value == mm[2] and r.resulted_at <= cutoff for r in results), (n.note_id, part)
                    checked += 1
        m = re.search(r"^ED course: (.*)\.$", n.text, re.M)
        if m:
            times = {local(a.taken_at).strftime("%H:%M") for a in view.mar_for(n.csn)}
            for hm in re.findall(r" at (\d\d:\d\d)", m[1]):
                assert hm in times, (n.note_id, hm)
                checked += 1
    assert checked > 2000, checked


def test_triage_notes_quote_the_triage_vitals(view):
    notes = [n for n in view.w.notes if n.note_type == "ED Triage Notes"]
    assert notes
    for n in notes:
        assert "not yet obtained" not in n.text, n.note_id
        v = view.vitals(n.csn)[0]
        assert f"HR {v.hr}, BP {v.sbp}/{v.dbp}" in n.text, (n.note_id, n.text)


def test_reassessment_notes_quote_fresh_vitals(view):
    checked = 0
    for n in view.w.notes:
        if n.note_type != "ED Notes":
            continue
        m = re.search(r"HR (\d+), BP (\d+)/(\d+)", n.text)
        if not m:
            continue
        match = [v for v in view.vitals(n.csn) if (v.hr, v.sbp, v.dbp) == (int(m[1]), int(m[2]), int(m[3]))]
        assert match, n.note_id
        checked += 1
        assert minutes(match[-1].taken_at, n.created_at) <= 75, (n.note_id, round(minutes(match[-1].taken_at, n.created_at)))
    assert checked > 300


def test_reassessment_wording_matches_the_vitals(view):
    for n in view.w.notes:
        if n.note_type == "ED Notes" and "Resting comfortably" in n.text:
            m = re.search(r"HR (\d+), BP (\d+)/\d+, RR (\d+)", n.text)
            if m:
                assert int(m[1]) <= 110 and int(m[2]) >= 90 and int(m[3]) <= 28, (n.note_id, m[0])


def test_triage_note_allergy_status_matches_the_record(view):
    for n in view.w.notes:
        if n.note_type == "ED Triage Notes" and "Allergies: NKDA" in n.text:
            p = view.patients[view.enc[n.csn].pat_id]
            assert p.allergy_review != "Not on File", (n.note_id, p.pat_id)


def test_history_line_reflects_the_problem_list(view):
    checked = 0
    for n in view.w.notes:
        if n.note_type == "ED Triage Notes" and "Hx: none reported" in n.text:
            checked += 1
            assert not view.patients[view.enc[n.csn].pat_id].problems, n.note_id
    assert checked >= 0


def test_hpi_claims_agree_with_home_medications(view):
    """A heart failure HPI says the patient ran out of furosemide only when furosemide is a home med."""
    checked = 0
    for n in view.w.notes:
        e = view.enc.get(n.csn)
        if not e or e.scenario != "chf" or n.note_type != "ED Provider Notes":
            continue
        checked += 1
        on_it = any("furosemide" in m["display"].lower() for m in view.patients[e.pat_id].home_meds)
        assert ("ran out of furosemide" in n.text) == on_it, (n.note_id, on_it)
    assert checked >= 3


def test_note_history_and_allergies_come_from_the_chart(view):
    for n in view.w.notes:
        if n.note_type != "ED Triage Notes":
            continue
        e = view.enc[n.csn]
        m = re.search(r"Allergies: (.*)\.$", n.text, re.M)
        assert m, n.note_id
        charted = {a.allergen for a in view.allergies(e.pat_id) if a.noted_at <= e.arrival_at}
        if m[1] in ("NKDA", "not on file, unable to obtain"):
            assert not charted, (n.note_id, charted)
            if m[1] != "NKDA":
                assert view.patients[e.pat_id].allergy_review == "Not on File", n.note_id
        else:
            # Strip the trailing "(reaction)" but keep a parenthetical that belongs to the allergen name.
            quoted = {re.sub(r" \((?!sulfonamide).*\)$", "", x) for x in m[1].split("; ")}
            assert quoted == charted, (n.note_id, quoted, charted)
        assert f"ESI {e.esi}." in n.text


def test_notes_carry_no_synthea_semantic_tags(view):
    for n in view.w.notes:
        assert "(finding)" not in n.text and "(disorder)" not in n.text, (n.note_id, n.text[:200])
    for a in view.w.allergies:
        assert "(finding)" not in a.reaction and "(disorder)" not in a.reaction, a.allergen


def test_patient_names_are_clean(view):
    for p in view.patients.values():
        assert not re.search(r"\d", p.first + p.middle + p.last), (p.pat_id, p.first, p.last)
        assert p.first and p.last
        assert re.fullmatch(r"9\d\d-\d\d-\d{4}", p.ssn), p.ssn
