"""Clinical review report for a generated world.

Prints the key distributions against the research spec targets (restated for the
approved 700 visit, 120 admit design) and then five complete encounters as
clinician style timelines: one sepsis, one heart failure, one NSTEMI, one
discharged visit and one patient still boarding when the window closes.

    python3 synthetic/tools/realism_report.py synthetic/out/world/truth
    python3 synthetic/tools/realism_report.py --standin            # built in population
    python3 synthetic/tools/realism_report.py <truth> --csn 40011200123

Standard library only.
"""
import argparse
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from edip_synth import synthea  # noqa: E402
from edip_synth.config import TZ, Config  # noqa: E402
from edip_synth.scenarios import ADMIT_COUNTS, get as scenario  # noqa: E402
from edip_synth.world import Simulator  # noqa: E402
from truth_io import load_truth, window  # noqa: E402

W = 100


def mins(a, b):
    return (b - a) / timedelta(minutes=1)


def q(xs, p):
    xs = sorted(xs)
    if not xs:
        return float("nan")
    pos = (len(xs) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def pct(k, n):
    return 100.0 * k / n if n else float("nan")


def local(dt):
    return dt.astimezone(TZ)


def stamp(dt):
    return local(dt).strftime("%a %m-%d %H:%M")


def spearman(a, b):
    def ranks(xs):
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        return [order.index(i) for i in range(len(xs))]
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb))
    return num / den


class Index:
    def __init__(self, w):
        self.w = w
        self.start, self.end = window(w)
        self.enc = [e for e in w.encounters if e.disposition != "CANCELED"]
        self.adm = [e for e in self.enc if e.disposition.startswith("ADMIT")]
        self.pats = {p.pat_id: p for p in w.patients}
        self.prov = {p.prov_id: p for p in w.providers}
        self.dept = {d.dept_id: d for d in w.departments}
        self.by = {}
        for name in ("adt", "ed_events", "bed_requests", "orders", "results", "med_orders", "mar", "vitals", "notes",
                     "code_status", "infections"):
            idx = defaultdict(list)
            for r in getattr(w, name):
                idx[r.csn].append(r)
            self.by[name] = idx
        self.allergies = defaultdict(list)
        for a in w.allergies:
            self.allergies[a.pat_id].append(a)
        self.census = defaultdict(dict)
        for c in w.census:
            self.census[(c["facility"], c["unit"])][c["at"]] = c["occupied"] / c["beds"]

    def age(self, e):
        b = self.pats[e.pat_id].birth_date
        return e.arrival_at.year - int(b[:4]) - ((e.arrival_at.month, e.arrival_at.day) < (int(b[5:7]), int(b[8:10])))

    def name(self, prov_id):
        p = self.prov.get(prov_id)
        return p.display if p else (prov_id or "")


# ------------------------------------------------------------------ distributions
def row(label, observed, target, ok=None):
    mark = "" if ok is None else ("  ok" if ok else "  <-- off target")
    print(f"  {label:<46} {observed:<28} {target:<22}{mark}")


def within(x, lo, hi):
    return lo <= x <= hi


def distributions(ix: Index):
    enc, adm, n = ix.enc, ix.adm, len(ix.enc)
    print("=" * W)
    print("DISTRIBUTIONS VERSUS TARGETS (spec section 3, 8 and 12; admit rate dependent targets restated for 17 percent)")
    print("=" * W)
    print(f"  {'metric':<46} {'observed':<28} {'target':<22}")
    row("ED encounters / admitted", f"{n} / {len(adm)}", "700 / 120", n == 700 and len(adm) == 120)
    rate = pct(len(adm), n)
    row("admission rate (percent)", f"{rate:.1f}", "15 to 19 (spec 28 to 32 at 400)", within(rate, 15, 19))
    obs = pct(sum(e.disposition == "ADMIT_OBS" for e in adm), len(adm))
    row("observation share of admits", f"{obs:.1f}", "22 to 28", within(obs, 22, 28))
    decided = [e for e in adm if e.decision_at]
    loc = Counter(e.level_of_care for e in decided)
    icu = pct(loc["ICU"], len(decided))
    row("level of care of admits", ", ".join(f"{k} {v}" for k, v in sorted(loc.items())), "ICU 10 to 14 pct (+/- 3)", within(icu, 7, 17))
    esi = Counter(e.esi for e in enc)
    row("ESI mix 1..5 (percent)", " / ".join(f"{pct(esi[k], n):.1f}" for k in range(1, 6)),
        "1.6/16.6/50.9/28.0/2.8 +/-3", all(abs(pct(esi[k], n) - t) <= max(3, 2 * 100 * math.sqrt(t / 100 * (1 - t / 100) / n))
                                          for k, t in zip(range(1, 6), (1.6, 16.6, 50.9, 28.0, 2.8))))
    bands = Counter()
    for e in enc:
        a = ix.age(e)
        bands["18-44" if a < 45 else "45-64" if a < 65 else "65-74" if a < 75 else "75+"] += 1
    row("age bands 18-44/45-64/65-74/75+ (pct)", " / ".join(f"{pct(bands[b], n):.1f}" for b in ("18-44", "45-64", "65-74", "75+")),
        "30/28/18/24 +/-3", all(abs(pct(bands[b], n) - t) <= 3.5 for b, t in zip(("18-44", "45-64", "65-74", "75+"), (30, 28, 18, 24))))
    older = pct(sum(ix.age(e) >= 65 for e in adm), len(adm))
    row("admitted age 65 plus (pct)", f"{older:.1f}", "50 to 60 (+/- 4.5)", within(older, 45.5, 64.5))
    ems = pct(sum(e.arrival_mode == "EMS" for e in adm), len(adm))
    row("EMS share of admits / all visits (pct)", f"{ems:.1f} / {pct(sum(e.arrival_mode == 'EMS' for e in enc), n):.1f}",
        "40 to 50 / 24", within(ems, 35.5, 54.5))
    fem = pct(sum(ix.pats[e.pat_id].sex == "F" for e in enc), n)
    row("female (pct)", f"{fem:.1f}", "51 to 57", within(fem, 49, 59))
    hours = Counter(local(e.arrival_at).hour for e in enc)
    rho = spearman([hours[h] for h in range(24)], list(Config().hourly))
    row("arrival hour curve Spearman", f"{rho:.3f}", "over 0.9", rho > 0.9)
    days = Counter(local(e.arrival_at).weekday() for e in enc)
    row("day of week shares Mon..Sun (pct)", "/".join(f"{pct(days[d], n):.1f}" for d in range(7)), "Mon 15 to 17, Sat Sun < 14",
        within(pct(days[0], n), 15, 17) and pct(days[5], n) < 14 and pct(days[6], n) < 14)

    def med_p90(xs, label, med, p90, tol):
        ok = xs and abs(q(xs, .5) - med) <= med * tol and abs(q(xs, .9) - p90) <= p90 * tol
        row(label, f"median {q(xs, .5):.0f}, p90 {q(xs, .9):.0f} (n={len(xs)})", f"{med} / {p90} +/-{int(tol * 100)}pct", bool(ok))

    d2p = [mins(e.arrival_at, e.provider_at) for e in enc if e.provider_at]
    a2d = [mins(e.arrival_at, e.decision_at) for e in adm if e.decision_at]
    board = [mins(e.decision_at, e.departure_at) for e in adm if e.decision_at and e.departure_at]
    dlos = [mins(e.arrival_at, e.departure_at) for e in enc if e.disposition == "DISCHARGE" and e.departure_at]
    bq = [mins(e.bed_request_at, e.bed_assigned_at) for e in adm if e.bed_request_at and e.bed_assigned_at]
    b2d = [mins(e.bed_assigned_at, e.departure_at) for e in adm if e.bed_assigned_at and e.departure_at]
    med_p90(d2p, "door to provider (min)", 20, 75, .15)
    med_p90(a2d, "arrival to admit decision (min)", 200, 360, .15)
    med_p90(board, "boarding, all admits (min)", 150, 480, .30)
    base, crowd = [], []
    for e in adm:
        if e.decision_at and e.departure_at and e.unit:
            occ = ix.census.get((e.facility, e.unit), {}).get(e.decision_at.replace(minute=0, second=0, microsecond=0))
            if occ is not None:
                (crowd if occ >= 0.9 else base).append(mins(e.decision_at, e.departure_at))
    med_p90(base, "boarding, unit under 90 pct occupied", 150, 480, .30)
    if base and crowd:
        ratio = q(crowd, .5) / q(base, .5)
        row("boarding crowded / base median ratio", f"{ratio:.2f} (n crowded={len(crowd)})", "2 to 3", within(ratio, 1.5, 4))
    med_p90(bq, "bed request to assignment (min)", 45, 240, .30)
    med_p90(b2d, "bed assigned to departure (min)", 50, 150, .30)
    med_p90(dlos, "discharged ED LOS (min)", 160, 330, .15)
    los_bins = Counter()
    for e in enc:
        if e.departure_at:
            h = mins(e.arrival_at, e.departure_at) / 60
            los_bins["<1h" if h < 1 else "1-2h" if h < 2 else "2-4h" if h < 4 else "4-6h" if h < 6 else "6-10h" if h < 10 else "10h+"] += 1
    done = sum(los_bins.values())
    row("time in ED, all completed visits (pct)", " ".join(f"{k} {pct(v, done):.0f}" for k, v in sorted(los_bins.items(), key=lambda kv: ["<1h", "1-2h", "2-4h", "4-6h", "6-10h", "10h+"].index(kv[0]))),
        "NHAMCS 8/18/33/18/11/7")

    num = [r for r in ix.w.results if r.numeric is not None]
    abn = pct(sum(1 for r in num if r.flag), len(num))
    crit = pct(sum(1 for r in num if r.flag in ("HH", "LL")), len(num))
    row("abnormal / critical numeric components (pct)", f"{abn:.1f} / {crit:.2f} (n={len(num)})", "5 to 8 / 0.5 to 1.5",
        within(abn, 5, 8) and within(crit, .5, 1.5))
    labs_adm = [sum(1 for o in ix.by["orders"][e.csn] if o.order_type == "Lab" and o.ordered_at < (e.departure_at or ix.end)) for e in adm if e.provider_at]
    comps = [sum(1 for r in ix.by["results"][e.csn] if r.numeric is not None) for e in adm if e.provider_at]
    row("lab orders / result components per admit (mean)", f"{statistics.mean(labs_adm):.1f} / {statistics.mean(comps):.1f}", "6 / 28")
    img = pct(sum(any(o.order_type == "Imaging" for o in ix.by["orders"][e.csn]) for e in adm), len(adm))
    row("admits with any imaging (pct)", f"{img:.0f}", "90")
    mar_adm = []
    for e in adm:
        cut = e.departure_at or ix.end
        mar_adm.append(sum(1 for m in ix.by["mar"][e.csn] if m.taken_at < cut and m.action in ("Given", "New Bag")))
    row("ED med administrations per admit", f"mean {statistics.mean(mar_adm):.2f}, any {pct(sum(c > 0 for c in mar_adm), len(mar_adm)):.0f} pct",
        "mean 3.5, any 92 pct", within(statistics.mean(mar_adm), 3.0, 4.0))
    iso = pct(sum(bool(e.isolation) for e in adm), len(adm))
    row("isolation share of admits (pct)", f"{iso:.1f}", "13 to 17", within(iso, 13, 17))
    cs = {c.csn: c.status for c in ix.w.code_status}
    cbands = defaultdict(Counter)
    for e in adm:
        if e.csn in cs:
            a = ix.age(e)
            cbands["<65" if a < 65 else "65-84" if a < 85 else "85+"][cs[e.csn]] += 1
    for b, target in (("<65", "full 96.9, DNR 2.4"), ("65-84", "full 79.2, DNR 17.9"), ("85+", "full 45.9, DNR 48.7")):
        c = cbands[b]
        t = sum(c.values())
        row(f"code status age {b} (n={t})", f"full {pct(c['Full Code'], t):.0f}, DNR {pct(c['DNR/DNI'], t):.0f}, other {pct(t - c['Full Code'] - c['DNR/DNI'], t):.0f}", target)
    pats = [p for p in ix.w.patients if not p.merged_into]
    allergic = [p for p in pats if ix.allergies[p.pat_id]]
    share = pct(len(allergic), len(pats))
    mean_a = statistics.mean(len(ix.allergies[p.pat_id]) for p in allergic)
    row("allergy prevalence (pct) / mean per allergic", f"{share:.1f} / {mean_a:.2f}", "35 to 41 / 1.9", within(share, 33, 43) and within(mean_a, 1.6, 2.2))
    top = Counter(a.allergen for a in ix.w.allergies).most_common(4)
    row("leading allergens", ", ".join(f"{k} {v}" for k, v in top), "penicillins first", top[0][0] == "Penicillins")
    enc_of = {e.csn: e for e in ix.w.encounters}
    pn = [nt for nt in ix.w.notes if nt.note_type == "ED Provider Notes" and enc_of[nt.csn].departure_at]
    before = pct(sum(1 for nt in pn if nt.signed_at and nt.signed_at <= enc_of[nt.csn].departure_at), len(pn))
    adm_n = [nt for nt in pn if enc_of[nt.csn].disposition.startswith("ADMIT")]
    after = pct(sum(1 for nt in adm_n if not (nt.signed_at and nt.signed_at <= enc_of[nt.csn].departure_at)), len(adm_n))
    row("ED provider note signed before departure (pct)", f"{before:.1f}; after departure in admits {after:.1f}", "50 to 60; 35 to 45",
        within(before, 48, 62) and within(after, 30, 50))
    vit = [len(ix.by["vitals"][e.csn]) for e in enc]
    notes = [len(ix.by["notes"][e.csn]) for e in enc]
    row("vitals sets / notes per encounter (mean)", f"{statistics.mean(vit):.1f} / {statistics.mean(notes):.1f}", "")
    states = Counter(e.state_at_end for e in enc)
    row("state at window end", ", ".join(f"{k} {v}" for k, v in sorted(states.items())), "boarding and in_ed non zero",
        states["boarding"] > 0 and states["in_ed"] > 0)
    sc = Counter(e.scenario for e in adm)
    off = {k: (sc[k], v) for k, v in ADMIT_COUNTS.items() if sc[k] != v}
    row("admit scenario counts match section 8.4", "exact" if not off else str(off), "exact", not off)
    faults = Counter(f["fault_id"] for f in ix.w.faults)
    print("  fault manifest: " + ", ".join(f"{k} {v}" for k, v in sorted(faults.items())))
    print()


# ------------------------------------------------------------------ timelines
def pick(ix: Index):
    def best(cands):
        return max(cands, key=lambda e: len(ix.by["adt"][e.csn]) + len(ix.by["results"][e.csn]), default=None)
    done = [e for e in ix.adm if e.departure_at]
    return [
        ("SEPSIS", best([e for e in done if e.scenario == "sepsis_uti"]) or best([e for e in done if e.scenario.startswith("sepsis")])),
        ("HEART FAILURE", best([e for e in done if e.scenario == "chf"])),
        ("NSTEMI", best([e for e in done if e.scenario == "nstemi"]) or best([e for e in ix.adm if e.scenario == "nstemi"])),
        ("DISCHARGED", best([e for e in ix.enc if e.disposition == "DISCHARGE" and e.departure_at and ix.by["med_orders"][e.csn]
                             and ix.by["results"][e.csn]])),
        ("BOARDING AT WINDOW END", best([e for e in ix.enc if e.state_at_end == "boarding"])),
    ]


def header(ix: Index, e):
    p = ix.pats[e.pat_id]
    print("=" * W)
    print(f"CSN {e.csn}  HAR {e.har}  {e.facility}  {e.scenario_label} ({e.scenario})  ESI {e.esi}  {e.disposition}  state at end: {e.state_at_end}")
    print(f"  {p.first} {p.last}  MRN {p.mrn}  {ix.age(e)} {p.sex}  DOB {p.birth_date}  {p.language}{' (interpreter)' if p.interpreter else ''}  "
          f"{p.payer}  wt {e.weight_kg or p.weight_kg} kg  ht {p.height_cm} cm")
    print(f"  CC: {e.chief_complaint}  arrival: {e.arrival_mode}  dx: " + "; ".join(f"{d[0]} {d[2]}" for d in e.dx))
    probs = ", ".join(f"{x['display']} ({x.get('icd10') or 'no ICD-10'})" for x in p.problems[:8]) or "none"
    print(f"  problems: {probs}")
    meds = ", ".join(m["display"] for m in p.home_meds[:8]) or "none on file"
    print(f"  home meds: {meds}")
    alg = "; ".join(f"{a.allergen} ({a.reaction}, {a.severity})" for a in ix.allergies[e.pat_id]) or p.allergy_review
    print(f"  allergies: {alg}")
    print(f"  ED team: {ix.name(e.ed_attending)} / {ix.name(e.ed_resident) or 'no resident'} / RN {ix.name(e.ed_nurse)}  bay {e.ed_bay}")
    if e.decision_at:
        print(f"  admitting: {ix.name(e.admitting_prov)}  service {e.admit_service}  level {e.level_of_care}  unit {e.unit} "
              f"{e.room}-{e.bed}  RN {ix.name(e.unit_nurse)}  isolation: {e.isolation or 'none'}  consults: {', '.join(e.consults) or 'none'}")
    t = []
    if e.provider_at:
        t.append(f"door to provider {mins(e.arrival_at, e.provider_at):.0f} min")
    if e.decision_at:
        t.append(f"arrival to decision {mins(e.arrival_at, e.decision_at):.0f} min")
    if e.decision_at and e.departure_at and e.disposition.startswith("ADMIT"):
        t.append(f"boarding {mins(e.decision_at, e.departure_at) / 60:.1f} h")
    if e.departure_at:
        t.append(f"ED LOS {mins(e.arrival_at, e.departure_at) / 60:.1f} h")
    print("  timing: " + "; ".join(t) + f"   flags: {', '.join(e.flags) or 'none'}")
    print("-" * W)


def vitals_text(v):
    o2 = "RA" if not v.o2_flow else f"{v.o2_flow:g} L NC"
    parts = [f"HR {v.hr}", f"BP {v.sbp}/{v.dbp} (MAP {v.map})", f"RR {v.rr}", f"SpO2 {v.spo2}% {o2}"]
    if v.temp is not None:
        parts.insert(0, f"T {v.temp:.1f}")
    if v.pain is not None:
        parts.append(f"pain {v.pain}")
    if v.gcs is not None:
        parts.append(f"GCS {v.gcs}")
    late = mins(v.taken_at, v.entered_at)
    return "VS    " + ", ".join(parts) + (f"   [entered {late:.0f} min late]" if late > 20 else "")


def timeline(ix: Index, e):
    header(ix, e)
    ev = []
    dept = lambda d: (ix.dept[d].code if d in ix.dept else d)  # noqa: E731
    for a in ix.by["adt"][e.csn]:
        loc = f"{a.unit} {a.room}-{a.bed}".rstrip("-") if a.room else a.unit
        det = {k: v for k, v in a.details.items() if k not in ("esi", "chief_complaint", "arrival_mode")}
        extra = ("  " + ", ".join(f"{k}={ix.name(v) if k in ('admitting', 'provider', 'surviving', 'retired') and isinstance(v, str) and v.startswith('E1') else v}"
                                 for k, v in det.items())) if det else ""
        rec = f"   [recorded {mins(a.effective_at, a.recorded_at):.0f} min later]" if a.recorded_at - a.effective_at > timedelta(minutes=5) else ""
        ev.append((a.effective_at, 0, f"ADT   {a.kind:<18} class {a.patient_class:<3} {loc}{extra}{rec}"))
    for x in ix.by["ed_events"][e.csn]:
        ev.append((x.event_at, 1, f"TRACK {x.name}"))
    for b in ix.by["bed_requests"][e.csn]:
        ev.append((b.requested_at, 2, f"BED   request {b.pend_id} for {b.unit} ({b.level_of_care}{', ' + b.isolation if b.isolation else ''})"))
        if b.assigned_at:
            ev.append((b.assigned_at, 2, f"BED   assigned {b.unit} {b.room}-{b.bed}" + (f"  DELETED: {b.delete_cause}" if b.deleted else "")))
    for v in ix.by["vitals"][e.csn]:
        ev.append((v.taken_at, 3, vitals_text(v) + f"  ({dept(v.dept_id)})"))
    res_by = defaultdict(list)
    for r in ix.by["results"][e.csn]:
        res_by[(r.order_id, r.resulted_at, r.status)].append(r)
    for o in ix.by["orders"][e.csn]:
        ev.append((o.ordered_at, 4, f"ORDER {o.order_type:<8} {o.name} [{o.priority}] by {ix.name(o.provider)}  status {o.status}"))
        if o.collected_at and o.order_type == "Lab":
            ev.append((o.collected_at, 4, f"LAB   collected {o.name}"))
    for (oid, at, status), rows in sorted(res_by.items(), key=lambda kv: kv[0][1]):
        name = next((o.name for o in ix.by["orders"][e.csn] if o.order_id == oid), oid)
        if rows[0].numeric is None and rows[0].lab in ("CXR", "CTH", "CTA", "CTAP", "CTPE", "XRHIP", "RUQUS", "ECG"):
            txt = rows[0].value
        else:
            txt = ", ".join(f"{r.lab} {r.value}{' ' + r.units if r.units and r.units not in ('%',) and r.lab not in ('INR',) else ''}"
                            f"{' (' + r.flag + ')' if r.flag else ''}" for r in rows)
        tag = {"P": "PRELIM", "F": "FINAL", "C": "CORRECTED"}[status]
        ev.append((at, 5, f"RSLT  {tag:<9} {name}: {txt}"))
        for r in rows:
            if r.comment:
                ev.append((at, 5, f"      note: {r.comment}"))
    for m in ix.by["med_orders"][e.csn]:
        ev.append((m.ordered_at, 6, f"MED   order {m.name} {m.dose} {m.unit} {m.route} {m.freq}{' rate ' + m.rate if m.rate else ''} by {ix.name(m.prescriber)}"
                   + (f"  [{m.comment}]" if m.comment else "")))
    med_name = {m.order_med_id: m.name for m in ix.by["med_orders"][e.csn]}
    for a in ix.by["mar"][e.csn]:
        ev.append((a.taken_at, 7, f"MAR   {a.action:<11} {med_name.get(a.order_med_id, a.order_med_id)} {a.dose} {a.unit} {a.route}"
                   f"{' ' + a.rate if a.rate else ''}  ({dept(a.dept_id)})"))
    for c in ix.by["code_status"][e.csn]:
        ev.append((c.activated_at, 8, f"CODE  {c.status} entered by {ix.name(c.entered_by)}"))
    for i in ix.by["infections"][e.csn]:
        ev.append((i.added_at if i.added_at >= e.arrival_at else e.arrival_at, 8, f"ISO   {i.infection} -> {i.isolation} ({i.source}{', from ' + i.added_at.date().isoformat() if i.source == 'history' else ''})"))
    for a in ix.allergies[e.pat_id]:
        if a.added_csn == e.csn:
            ev.append((a.noted_at, 8, f"ALLG  new allergy documented: {a.allergen} ({a.reaction})"))
    for nt in ix.by["notes"][e.csn]:
        signed = f"signed {stamp(nt.signed_at)}" if nt.signed_at else "UNSIGNED at window end"
        body = "\n".join("            | " + line for line in nt.text.splitlines())
        ev.append((nt.created_at, 9, f"NOTE  {nt.note_type} by {ix.name(nt.author)} (service {stamp(nt.service_at)}, {signed})\n{body}"))
    for at, _, text in sorted(ev, key=lambda x: (x[0], x[1])):
        print(f"{stamp(at)}  {text}")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("truth", nargs="?", help="truth folder (or its parent)")
    ap.add_argument("--standin", action="store_true", help="generate the world with the built in population")
    ap.add_argument("--csn", action="append", help="print this encounter instead of the five samples (repeatable)")
    ap.add_argument("--no-timelines", action="store_true")
    a = ap.parse_args()
    if a.standin or not a.truth:
        cfg = Config()
        w = Simulator(cfg, synthea.stand_in(3000, cfg.seed)).run()
        print("world: generated with the stand in population")
    else:
        w = load_truth(a.truth)
        print(f"world: {a.truth}")
    ix = Index(w)
    distributions(ix)
    if a.no_timelines:
        return
    if a.csn:
        for csn in a.csn:
            e = next((e for e in w.encounters if e.csn == csn), None)
            if e is None:
                print(f"no encounter {csn}")
            else:
                timeline(ix, e)
        return
    for label, e in pick(ix):
        print()
        print(f"##### {label}")
        if e is None:
            print("  (no matching encounter)")
            continue
        timeline(ix, e)


if __name__ == "__main__":
    main()
