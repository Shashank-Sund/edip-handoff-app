"""World model simulator (spec section 4.2, stages 0 to 2 plus truth level faults).

Three passes:
  1. plan and milestones: arrival times, disposition, scenario, patient, ED
     milestones up to the admit or discharge decision;
  2. beds: inpatient beds are allocated in decision order against a background
     census, so boarding emerges from capacity, with the spec's base delays;
  3. detail: vitals, orders and results, meds and MAR, notes, code status,
     ADT and ED tracking events.
Everything is generated past the window end, then clipped to the window, so
day seven ends with patients still in the ED and still boarding.
"""
import math
import random
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from . import notes as N
from .config import TZ, Config
from .ids import Ids
from .model import (Allergy, AdtEvent, BedRequest, CodeStatus, EdEvent, Encounter, Infection, MarAdmin, MedOrder,
                    Note, Order, Patient, Result, Vital, World)
from .reference import FACILITIES, IMAGING, LABS, PANELS, build_departments, build_providers
from .scenarios import ADMIT_COUNTS, DEXTROSE_50, DISCHARGE_WEIGHTS, INSULIN_PUSH, Med, get

UTC = timezone.utc
M = lambda x: timedelta(minutes=x)  # noqa: E731
H = lambda x: timedelta(hours=x)    # noqa: E731

OBS_ELIGIBLE = {"chest_pain_ro": 0.75, "syncope": 0.7, "cellulitis": 0.6, "pyelo": 0.5, "aki": 0.5, "af_rvr": 0.5,
                "abdominal": 0.3, "cap": 0.25, "copd": 0.2, "other_medical": 0.3, "flu_covid": 0.3}
LEVEL_UNIT = {("MGH", "ICU"): "MICU", ("MGH", "STEPDOWN"): "5N", ("MGH", "TELE"): "6W", ("MGH", "MEDSURG"): "7E",
              ("MGH", "OBS"): "4C", ("LKS", "ICU"): "ICU", ("LKS", "STEPDOWN"): "ICU", ("LKS", "TELE"): "3S",
              ("LKS", "MEDSURG"): "3S", ("LKS", "OBS"): "2E"}
OVERFLOW = {"STEPDOWN": ("ICU", "TELE"), "ICU": ("STEPDOWN",), "TELE": ("STEPDOWN", "MEDSURG"), "MEDSURG": ("TELE", "OBS"),
            "OBS": ("MEDSURG", "TELE")}
BG_LOS_H = {"ICU": (60, 160), "STEPDOWN": (72, 190), "TELE": (80, 200), "MEDSURG": (84, 220), "OBS": (22, 36)}
IP_LOS_DAYS = [((1, 2), 26.1), ((3, 4), 29.8), ((5, 6), 16.2), ((7, 8), 8.6), ((9, 10), 4.6), ((11, 20), 10.4)]
ALLERGENS = [("Penicillins", "7980", "RxNorm", 30), ("Sulfa (sulfonamide antibiotics)", "10180", "RxNorm", 15),
             ("Codeine", "2670", "RxNorm", 7), ("Morphine", "7052", "RxNorm", 5), ("NSAIDs", "", "Local", 8),
             ("Latex", "111088007", "SNOMED", 4), ("Iodinated contrast media", "", "Local", 4),
             ("Shellfish", "44027008", "SNOMED", 4), ("ACE inhibitors", "", "Local", 5), ("Statins", "", "Local", 4),
             ("Cephalexin", "2231", "RxNorm", 4), ("Ciprofloxacin", "2551", "RxNorm", 5), ("Adhesive tape", "", "Local", 5)]
REACTIONS = [("Hives", 30), ("Rash", 30), ("Anaphylaxis", 6), ("Swelling", 10), ("Nausea", 12), ("Other", 12)]
ISOLATION = [("MRSA", "Contact", 28), ("VRE", "Contact", 10), ("ESBL", "Contact", 10), ("CRE", "Contact", 2),
             ("C. difficile", "Contact Plus (enteric)", 12), ("Influenza", "Droplet", 9), ("COVID-19", "Droplet", 6),
             ("Rule out TB", "Airborne", 3)]


def zfmt(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def poisson(rng, lam):
    k, p, target = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= target:
            return k
        k += 1


# Allergen classes that conflict with a home medication (substring of the RxNorm display).
ALLERGY_CONFLICT = {"Penicillins": ("amoxicillin", "penicillin", "ampicillin"), "Statins": ("statin",),
                    "ACE inhibitors": ("pril ",), "NSAIDs": ("ibuprofen", "naproxen", "meloxicam", "diclofenac"),
                    "Sulfa (sulfonamide antibiotics)": ("sulfamethoxazole",), "Codeine": ("codeine",),
                    "Morphine": ("morphine",), "Cephalexin": ("cephalexin",), "Ciprofloxacin": ("ciprofloxacin",)}


def lgn(rng, median, p90, lo=None, hi=None):
    sigma = math.log(p90 / median) / 1.2816
    x = median * math.exp(rng.gauss(0, sigma))
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x


def wpick(rng, weights: dict):
    keys = list(weights)
    return rng.choices(keys, [weights[k] for k in keys])[0]


def u(rng, r):
    lo, hi = r
    return rng.uniform(lo, hi)


def local(dt):
    return dt.astimezone(TZ)


def age_band(age):
    return "18-44" if age < 45 else "45-64" if age < 65 else "65-74" if age < 75 else "75+"


class Simulator:
    def __init__(self, cfg: Config, population: list):
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.ids = Ids(cfg.seed)
        self.pop = population
        self.start = cfg.window_start.astimezone(UTC)
        # Local wall clock arithmetic: the window ends at local midnight even though DST ends inside it,
        # so the last day is 25 hours long.
        self.end = (cfg.window_start + timedelta(days=cfg.days)).astimezone(UTC)
        self.sim_end = self.end + H(cfg.inpatient_horizon_h)
        cfg_dict = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in asdict(cfg).items()}
        self.w = World(cfg_dict, FACILITIES, build_departments(random.Random(cfg.seed + 1)),
                       build_providers(random.Random(cfg.seed + 2)))
        self.dept = {(d.facility, d.code): d for d in self.w.departments}
        self.prov = {}
        for p in self.w.providers:
            self.prov.setdefault((p.facility, p.prov_type, p.specialty), []).append(p)
        self.pats = {}
        self.person_of = {}
        self.encs = []
        self._enc = {}
        self.bed_intervals = []     # (facility, unit, room, bed, start, end, kind)
        self.pcn_override_used = False

    # ------------------------------------------------------------------ helpers
    def pick_prov(self, fac, ptype, spec, rng=None):
        pool = self.prov.get((fac, ptype, spec)) or self.prov[(fac, "Physician", "Hospital Medicine")]
        return (rng or self.rng).choice(pool)

    def fault(self, fid, csn, stream, at, desc, expected):
        self.w.faults.append({"fault_id": fid, "csn": csn, "stream": stream,
                              "event_time": zfmt(at) if at else None, "description": desc,
                              "expected": expected, "level": "truth"})

    # ------------------------------------------------------------------ run
    def run(self) -> World:
        slots = self._plan()
        self._assign_patients(slots)
        for s in sorted(slots, key=lambda s: s["arrival"]):
            self.encs.append(self._milestones(s))
        self._returns()
        self._pre_bed_faults()
        self._beds()
        self._ed_bays()
        for e in self.encs:
            self._detail(e)
        self._post_faults()
        self._clip()
        self._render_plan()
        self._census()
        self.w.encounters = self.encs
        self.w.patients = list(self.pats.values())
        return self.w

    # ------------------------------------------------------------------ pass 1
    def _arrivals(self, fac, n):
        rng, cfg = self.rng, self.cfg
        per_day = [n * w / sum(cfg.daily) for w in cfg.daily]
        counts = [int(x) for x in per_day]
        for i in sorted(range(7), key=lambda i: per_day[i] - counts[i], reverse=True)[: n - sum(counts)]:
            counts[i] += 1
        out = []
        for day, c in enumerate(counts):
            d = (cfg.window_start + timedelta(days=day)).date()
            for _ in range(c):
                hour = rng.choices(range(24), cfg.hourly)[0]
                ldt = datetime(d.year, d.month, d.day, hour, rng.randrange(60), rng.randrange(60), tzinfo=TZ,
                               fold=rng.randrange(2))
                out.append(ldt.astimezone(UTC))
        return out

    def _plan(self):
        rng, cfg = self.rng, self.cfg
        n_mgh = round(cfg.encounters * cfg.facility_share["MGH"])
        slots = [{"facility": "MGH", "arrival": t} for t in self._arrivals("MGH", n_mgh)]
        slots += [{"facility": "LKS", "arrival": t} for t in self._arrivals("LKS", cfg.encounters - n_mgh)]
        rng.shuffle(slots)

        # Admitted scenarios, with exactly cfg.admitted_observation observation stays.
        admits = [k for k, c in ADMIT_COUNTS.items() for _ in range(c)]
        assert len(admits) == cfg.admitted, (len(admits), cfg.admitted)
        rng.shuffle(admits)
        scored = sorted(range(len(admits)), key=lambda i: -(OBS_ELIGIBLE.get(admits[i], 0) * rng.random()))
        obs_idx = set(scored[: cfg.admitted_observation])

        # Admitted patients skew to daytime arrivals (EMS heavy); weighted sampling without replacement.
        def key(s):
            h = local(s["arrival"]).hour
            w = 1.35 if 8 <= h <= 21 else 0.7
            return rng.random() ** (1 / w)
        order = sorted(slots, key=key, reverse=True)
        for i, (s, k) in enumerate(zip(order, admits)):
            s["scenario"], s["disposition"] = k, "ADMIT_OBS" if i in obs_idx else "ADMIT_IP"
        rest = order[len(admits):]
        rng.shuffle(rest)
        special = (["LWBS"] * cfg.lwbs + ["AMA"] * cfg.ama + ["LBTC"] * cfg.left_before_complete
                   + ["TRANSFER"] * cfg.transfer_out)
        for i, s in enumerate(rest):
            s["disposition"] = special[i] if i < len(special) else "DISCHARGE"
            if s["disposition"] == "TRANSFER":
                s["scenario"] = "stemi_transfer" if i % 3 == 0 else "psych_d"
            else:
                s["scenario"] = wpick(rng, DISCHARGE_WEIGHTS)
        # 72 hour returns: chosen among later arrivals; 4 of them are admissions.
        late = [s for s in slots if s["arrival"] > self.start + H(30)]
        rng.shuffle(late)
        adm = [s for s in late if s["disposition"].startswith("ADMIT")][: cfg.returns_admitted]
        oth = [s for s in late if s["disposition"] == "DISCHARGE"][: cfg.returns_72h - cfg.returns_admitted]
        for s in adm + oth:
            s["return"] = True
        return slots

    def _new_patient(self, person, rng):
        pat_id, mrn, epi = self.ids.patient()
        age = person.age
        if age >= 65:
            payer = wpick(rng, {"Medicare": 80, "Medicare Advantage": 12, "Medicaid": 5, "Commercial": 3 if age < 80 else 0})
        else:
            payer = wpick(rng, {"Commercial": 52, "Medicaid": 34, "Self-pay": 10, "Other": 4})
        lang = wpick(rng, {"English": 86, "Spanish": 8, "Portuguese": 2, "Haitian Creole": 2, "Vietnamese": 1, "Mandarin": 1})
        weight = rng.gauss(76 if person.sex == "F" else 89, 17)
        p = Patient(pat_id, mrn, epi, person.synthea_id, person.first, person.middle, person.last,
                    person.birth_date.isoformat(), person.sex, person.race, person.ethnicity, lang,
                    lang != "English" and rng.random() < 0.85, person.marital, person.address, person.city, "MA",
                    person.zip, f"617-555-01{rng.randrange(100):02d}", person.ssn, payer,
                    round(min(160, max(42, weight)), 1), round(rng.gauss(163 if person.sex == "F" else 177, 7), 1),
                    {"name": f"{rng.choice(['Pat', 'Chris', 'Sam', 'Lee', 'Jamie', 'Robin'])} {person.last}",
                     "relation": rng.choice(["Spouse", "Daughter", "Son", "Sibling", "Friend"]),
                     "phone": f"617-555-01{rng.randrange(100):02d}"},
                    person.problems, person.home_meds, person.prior_ed_visits, person.prior_admissions)
        self.pats[pat_id] = p
        self.person_of[pat_id] = person
        self._allergies(p, person, rng)
        return p

    def _allergies(self, p, person, rng):
        prob = self.cfg.allergy_any * (1.1 if p.sex == "F" else 0.9) * (1.1 if person.age >= 65 else 0.95)
        noted = datetime(2015 + rng.randrange(10), rng.randint(1, 12), rng.randint(1, 28), 12, tzinfo=UTC)
        roll = rng.random()
        p.allergy_review = "Reviewed"
        if roll < 0.04:
            p.allergy_review = "Not on File"
            return
        if roll < 0.04 + prob:
            n = min(5, 1 + poisson(rng, 0.9))
            meds = " ".join(m["display"].lower() for m in person.home_meds)
            chosen = {k for k, words in ALLERGY_CONFLICT.items() if any(w in meds for w in words)}
            blocked = set(chosen)
            for a in person.drug_allergies[:2]:
                chosen.add(a["display"])
                self.w.allergies.append(Allergy(self.ids.misc(), p.pat_id, a["display"], a["code"], a["system"],
                                                a["reaction"] or "Rash", (a["severity"] or "Mild").title(), noted))
            while len(chosen) - len(blocked) < n:
                name, code, system, _ = rng.choices(ALLERGENS, [a[3] for a in ALLERGENS])[0]
                if name in chosen:
                    continue
                chosen.add(name)
                self.w.allergies.append(Allergy(self.ids.misc(), p.pat_id, name, code, system,
                                                wpick(rng, dict(REACTIONS)),
                                                wpick(rng, {"High": 10, "Medium": 40, "Low": 50}), noted))
        elif roll < 0.04 + prob + 0.05:
            p.allergy_review = "No Known Allergies"

    def _assign_patients(self, slots):
        rng = random.Random(self.cfg.seed + 7)
        bands = {}
        for person in self.pop:
            bands.setdefault(age_band(person.age), []).append(person)
        for b in bands.values():
            rng.shuffle(b)
        used = set()
        for s in sorted(slots, key=lambda s: 0 if s["scenario"] in ADMIT_COUNTS else 1):
            if s.get("return"):
                continue
            scen = get(s["scenario"])
            band = wpick(rng, {k: v for k, v in scen.age.items() if v > 0})
            pool = [p for p in bands.get(band, []) if p.synthea_id not in used] or \
                   [p for p in self.pop if p.synthea_id not in used]
            fit = [p for p in pool if p.snomed & set(scen.conditions)] if scen.conditions else []
            if fit and rng.random() < 0.85:
                pool = fit
            sex = "F" if rng.random() < 0.55 else "M"          # NHAMCS: 54.6 percent of visits are female
            pool = [p for p in pool if p.sex == sex] or pool
            person = rng.choice(pool)
            used.add(person.synthea_id)
            s["person"] = person

    def _milestones(self, s) -> Encounter:
        rng = random.Random(f"{self.cfg.seed}-{s['arrival'].isoformat()}-{s['facility']}")
        s["rng_seed"] = rng.random()
        scen = get(s["scenario"])
        disp = s["disposition"]
        esi = int(wpick(rng, scen.esi))
        if disp in ("LWBS",):
            esi = max(esi, 3)
        admit = disp.startswith("ADMIT")
        mode_w = {"EMS": 36, "Walk-in": 30, "Private vehicle": 34} if admit else {"EMS": 18, "Walk-in": 41, "Private vehicle": 41}
        if esi == 1:
            mode_w = {"EMS": 92, "Private vehicle": 8}
        if scen.key in ("psych_hold", "psych_d"):
            mode_w = {"Police": 45, "EMS": 30, "Walk-in": 25}
        mode = wpick(rng, mode_w)
        arr = s["arrival"]
        enc = Encounter(self.ids.csn(), self.ids.har(), "", s["facility"], scen.key, scen.label, disp, mode, arr, esi,
                        rng.choice(scen.complaints), [list(d) for d in scen.dx])
        enc._rng = rng
        enc._slot = s
        fac = s["facility"]
        enc.ed_attending = self.pick_prov(fac, "Physician", "Emergency Medicine", rng).prov_id
        if fac == "MGH" and rng.random() < 0.6:
            enc.ed_resident = self.pick_prov(fac, "Resident", "Emergency Medicine", rng).prov_id
        elif fac == "LKS" and esi >= 4 and rng.random() < 0.6:
            enc.ed_resident = self.pick_prov(fac, "Nurse Practitioner", "Emergency Medicine", rng).prov_id
        elif fac == "MGH" and esi >= 4 and rng.random() < 0.5:
            enc.ed_resident = self.pick_prov(fac, "Physician Assistant", "Emergency Medicine", rng).prov_id
        enc.ed_nurse = self.pick_prov(fac, "Registered Nurse", "Emergency Medicine", rng).prov_id

        cfg = self.cfg
        hour = local(arr).hour
        crowd = 1.35 if 14 <= hour <= 23 else 1.0
        if esi == 1:
            tri = rng.uniform(1, 3)
        elif mode == "EMS":
            tri = rng.uniform(2, 5)
        else:
            tri = lgn(rng, *cfg.door_to_triage, lo=1, hi=120) * crowd
        enc.triage_at = arr + M(tri)
        if disp == "LWBS":
            enc.departure_at = enc.triage_at + M(rng.uniform(40, 260))
            return enc
        # Door to provider is drawn from the target distribution; the patient is roomed
        # shortly before the provider arrives (immediate bedding), keeping
        # triage <= roomed <= provider.
        d2p = lgn(rng, *(cfg.door_to_provider_high_acuity if esi <= 2 else cfg.door_to_provider), lo=2, hi=420) * \
            (1 if esi <= 2 else (crowd if crowd > 1 else 0.85))
        enc.provider_at = max(arr + M(d2p), enc.triage_at + M(rng.uniform(2, 6)))
        lead = rng.uniform(0.5, 4) if esi <= 2 else min(rng.uniform(1, 25), (enc.provider_at - enc.triage_at) / M(1) - 1)
        enc.roomed_at = max(enc.triage_at + M(0.5), enc.provider_at - M(max(0.5, lead)))

        if disp == "LBTC":
            enc.departure_at = enc.provider_at + M(rng.uniform(30, 150))
        elif disp == "AMA":
            enc.departure_at = enc.provider_at + M(rng.uniform(60, 240))
        elif disp == "DISCHARGE":
            los = lgn(rng, *cfg.discharged_los, lo=35, hi=900) * (0.88 if esi >= 4 else 1.05)
            enc.departure_at = max(arr + M(los), enc.provider_at + M(rng.uniform(20, 45)))
            enc.decision_at = max(enc.provider_at + M(rng.uniform(4, 10)), enc.departure_at - M(rng.uniform(10, 40)))
            enc.departure_at = max(enc.departure_at, enc.decision_at + M(rng.uniform(5, 15)))
        elif disp == "TRANSFER":
            if scen.key == "stemi_transfer":
                enc.decision_at = enc.provider_at + M(rng.uniform(10, 30))
                enc.departure_at = enc.decision_at + M(rng.uniform(40, 95))
            else:
                enc.decision_at = enc.provider_at + M(lgn(rng, 200, 360, lo=90))
                enc.departure_at = enc.decision_at + M(lgn(rng, 500, 1300, lo=120))   # psychiatric bed search
        else:
            if scen.key in ("stroke", "nstemi"):
                dec = lgn(rng, 120, 220, lo=cfg.decision_floor_min)
            elif esi <= 2 and scen.key in ("sepsis_uti", "sepsis_pna", "ugib", "dka", "chf", "trauma_other"):
                dec = lgn(rng, 170, 300, lo=cfg.decision_floor_min, hi=600)      # sick patients are dispositioned sooner
            else:
                dec = lgn(rng, *cfg.arrival_to_decision, lo=cfg.decision_floor_min, hi=720)
            enc.decision_at = max(arr + M(dec), enc.provider_at + M(rng.uniform(40, 80)))
            enc.bed_request_at = enc.decision_at + M(rng.uniform(3, 20))
            enc.class_change_at = max(enc.decision_at + M(lgn(rng, 25, 90, lo=5)), enc.bed_request_at + M(rng.uniform(1, 10)))
            level = "OBS" if disp == "ADMIT_OBS" else wpick(rng, scen.units)
            enc.level_of_care = level
            enc.unit = LEVEL_UNIT[(fac, level)]
            dept = self.dept[(fac, enc.unit)]
            enc.unit_dept_id = dept.dept_id
            spec = "Critical Care" if level == "ICU" else "Hospital Medicine"
            enc.admitting_prov = self.pick_prov(fac, "Physician", spec, rng).prov_id
            enc.admit_service = {"ICU": "Critical Care", "OBS": "Observation Medicine"}.get(level, "Hospital Medicine")
            if scen.key in ("hip_fx",):
                enc.admit_service = "Orthopedics"
            enc.consults = list(scen.consult)
            enc.unit_nurse = self.pick_prov(fac, "Registered Nurse", "Inpatient", rng).prov_id
            if rng.random() < cfg.isolation_share * scen.isolation_bias * 1.15:
                enc.isolation = "pending"     # resolved in _pre_bed_faults
            if disp == "ADMIT_OBS":
                enc.inpatient_los_h = rng.uniform(14, 36)
            else:
                lo_hi = rng.choices([b for b, _ in IP_LOS_DAYS], [w for _, w in IP_LOS_DAYS])[0]
                enc.inpatient_los_h = rng.uniform(*lo_hi) * 24
        return enc

    def _returns(self):
        """Attach 72 hour return visits to an earlier discharged visit of a real patient."""
        rng = random.Random(self.cfg.seed + 11)
        taken = set()
        for e in self.encs:
            if not e._slot.get("return"):
                if e.pat_id == "":
                    e.pat_id = self._new_patient(e._slot["person"], e._rng).pat_id
                continue
            cands = [p for p in self.encs if p.disposition == "DISCHARGE" and p.pat_id and p.csn not in taken
                     and p.departure_at and H(6) < e.arrival_at - p.departure_at < H(72)]
            if not cands:
                e.pat_id = self._new_patient(e._slot["person"] if "person" in e._slot else rng.choice(self.pop),
                                             e._rng).pat_id
                continue
            prior = rng.choice(cands)
            taken.add(prior.csn)
            e.pat_id, e.return_of = prior.pat_id, prior.csn
            e.flags.append("F29")
            self.fault("F29", e.csn, "all", e.arrival_at, f"Return within 72 h of CSN {prior.csn}",
                       "Prior visit shown in history; no cross CSN leakage")

    # ------------------------------------------------------------------ faults chosen before beds
    def _pre_bed_faults(self):
        rng = random.Random(self.cfg.seed + 13)
        admits = [e for e in self.encs if e.disposition.startswith("ADMIT")]
        iso = [e for e in admits if e.isolation == "pending"]
        target = round(self.cfg.isolation_share * len(admits))
        extra = [e for e in admits if not e.isolation]
        rng.shuffle(extra)
        iso += extra[: max(0, target - len(iso))]
        iso = iso[:target]
        for e in admits:
            if e.isolation == "pending" and e not in iso:
                e.isolation = ""
        flu = {"flu_covid"}
        late = [e for e in iso if e.scenario not in flu][:6]
        for e in iso:
            if e.scenario in flu:
                name, prec = ("Influenza", "Droplet") if e._rng.random() < 0.6 else ("COVID-19", "Droplet")
            else:
                name, prec, _ = e._rng.choices(ISOLATION[:5] + ISOLATION[7:], [x[2] for x in ISOLATION[:5] + ISOLATION[7:]])[0]
            e.isolation = prec
            e._iso_name = name
            e._iso_late = e in late
        # Bed reassignment (F15): three from late isolation, three level changes.
        others = [e for e in admits if not getattr(e, "_iso_late", False) and e.level_of_care in ("TELE", "STEPDOWN", "MEDSURG")
                  and e.facility == "MGH"]
        rng.shuffle(others)
        for e in late[:3] + others[:3]:
            e._reassign = True

        early = [e for e in admits if e.arrival_at < self.end - H(30) and not getattr(e, "_reassign", False)]
        rng.shuffle(early)
        taken = set()

        def take(cands, n, flag):
            out = [e for e in cands if e.csn not in taken][:n]
            for e in out:
                taken.add(e.csn)
                e.flags.append(flag)
            return out
        take(early, 3, "F10")
        for e in take([e for e in early if e.decision_at - e.provider_at > M(110)], 2, "F11"):
            e._false_discharge_at = e.decision_at - M(rng.uniform(40, 70))
            self.fault("F11", e.csn, "adt", e._false_discharge_at, "Discharged, discharge canceled, then admitted",
                       "Encounter reopens; disposition history preserved")
        obs = [e for e in admits if e.disposition == "ADMIT_OBS" and e.arrival_at < self.end - H(48)]
        for e in take(obs, self.cfg.obs_to_inpatient, "F14"):
            e._conv_h = rng.uniform(8, 20)
            e.inpatient_los_h += 48
        for e in take(early, self.cfg.missing_code_status, "F20"):
            self.fault("F20", e.csn, "code_status", None, "No code status until 2 to 6 h after transfer",
                       "Completeness rule blocks sign with code status not documented")
        for e in admits:
            if rng.random() < 0.10:
                e.flags.append("F07")
        # F09: wrong patient registered, canceled within minutes, real registration follows.
        pool = [e for e in self.encs if e.triage_at and not e.return_of and e.arrival_at < self.end - H(6)]
        rng.shuffle(pool)
        for e in pool[:2]:
            e.flags.append("F09")
            e._cancel_at = e.arrival_at + M(rng.uniform(3, 10))
            e._arrival_recorded = e._cancel_at + M(rng.uniform(1, 3))
        # F12: quick registration created a duplicate record; merged after triage.
        for e in [x for x in pool[2:] if not x.return_of][:2]:
            orig = self.pats[e.pat_id]
            pat_id, mrn, epi = self.ids.patient()
            dup = Patient(**{**asdict(orig), "pat_id": pat_id, "mrn": mrn, "epi": epi, "problems": [], "home_meds": [],
                             "prior_ed_visits": 0, "prior_admissions": 0, "middle": ""})
            e._merge_at = e.triage_at + M(rng.uniform(15, 60))
            dup.merged_into, dup.merged_at = orig.pat_id, e._merge_at
            self.pats[pat_id] = dup
            e.registered_pat_id = pat_id
            e.flags.append("F12")
            self.fault("F12", e.csn, "all", e._merge_at, f"Duplicate MRN {mrn} merged into {orig.mrn}",
                       "All events link to the surviving patient; handoff shows the merged history")
        # F21: allergy reported during the visit.
        for e in rng.sample([e for e in self.encs if e.provider_at and e.provider_at < self.end - H(3)], 10):
            e._new_allergy = (e.provider_at + M(rng.uniform(20, 120)),
                              rng.choice([("Iodinated contrast media", "", "Local"), ("Morphine", "7052", "RxNorm"),
                                          ("Ondansetron", "26225", "RxNorm"), ("Latex", "111088007", "SNOMED")]))
        # Code status by age band, allocated by quota (Kaiser table) so a 120 admit sample matches it.
        kaiser = {"<65": {"Full Code": 96.9, "DNR/DNI": 2.4, "Partial Code": 0.5, "Comfort Care": 0.2},
                  "65-84": {"Full Code": 79.2, "DNR/DNI": 17.9, "Partial Code": 2.2, "Comfort Care": 0.7},
                  "85+": {"Full Code": 45.9, "DNR/DNI": 48.7, "Partial Code": 3.0, "Comfort Care": 2.4}}
        bands = {}
        for e in admits:
            a = N.pat_age(self.pats[e.pat_id], e)
            bands.setdefault("<65" if a < 65 else "65-84" if a < 85 else "85+", []).append(e)
        for band, encs in bands.items():
            w = kaiser[band]
            exact = {k: v * len(encs) / 100 for k, v in w.items()}
            counts = {k: int(x) for k, x in exact.items()}
            for k in sorted(exact, key=lambda k: exact[k] - counts[k], reverse=True)[: len(encs) - sum(counts.values())]:
                counts[k] += 1
            quota = [k for k, c in counts.items() for _ in range(c)]
            rng.shuffle(quota)
            for e, status in zip(encs, quota):
                e._planned_code_status = status
        # Spec item 20: exactly one documented penicillin allergy override (pip tazo in a septic patient).
        septic = [e for e in admits if e.scenario == "sepsis_uti" and e.arrival_at < self.end - H(12)]
        if septic:
            e = septic[0]
            e._force_piptazo = True
            if not any(a.pat_id == e.pat_id and a.allergen == "Penicillins" for a in self.w.allergies):
                self.w.allergies.append(Allergy(self.ids.misc(), e.pat_id, "Penicillins", "7980", "RxNorm", "Rash", "Low",
                                                e.arrival_at - timedelta(days=900)))
        # F27: a non ASCII surname must survive every interface.
        p = self.pats[pool[5].pat_id]
        p.last = "Nuñez"

    # ------------------------------------------------------------------ pass 2: beds
    def _beds(self):
        rng = random.Random(self.cfg.seed + 17)
        cfg = self.cfg
        beds = {}
        for d in self.w.departments:
            if d.level == "ED":
                continue
            occ0 = rng.uniform(*cfg.initial_occupancy)
            for room, bed, private in d.rooms:
                st = {"fac": d.facility, "unit": d.code, "room": room, "bed": bed, "private": private, "free_at": self.start - H(1)}
                if rng.random() < occ0:
                    med, p90 = BG_LOS_H[d.level]
                    rem = rng.uniform(0, 1.6 * med)
                    st["free_at"] = self._discharge_clock(self.start + H(rem), d.level, rng)
                    self.bed_intervals.append((d.facility, d.code, room, bed, self.start - H(rng.uniform(4, 120)),
                                               st["free_at"], "background"))
                    st["free_at"] += M(rng.uniform(30, 90))
                beds.setdefault((d.facility, d.code), []).append(st)
            by_room = {}
            for st in beds.get((d.facility, d.code), []):
                by_room.setdefault(st["room"], []).append(st)
            for pair in by_room.values():
                if len(pair) == 2:
                    pair[0]["partner"], pair[1]["partner"] = pair[1], pair[0]

        # Requests: ED admits plus background admissions (OR, direct admits, transfers in).
        reqs = []
        ed_admits = [e for e in self.encs if e.disposition.startswith("ADMIT")]
        ed_per_unit = {}
        for e in ed_admits:
            in_horizon = min(e.inpatient_los_h, (self.sim_end - e.bed_request_at).total_seconds() / 3600)
            ed_per_unit[(e.facility, e.unit)] = ed_per_unit.get((e.facility, e.unit), 0) + max(0, in_horizon)
        horizon_h = (self.sim_end - self.start).total_seconds() / 3600
        for d in self.w.departments:
            if d.level == "ED":
                continue
            med, p90 = BG_LOS_H[d.level]
            mean_los = med * math.exp((math.log(p90 / med) / 1.2816) ** 2 / 2)    # lognormal mean, not median
            target_bed_hours = cfg.target_occupancy * d.beds * horizon_h
            ed_bed_hours = ed_per_unit.get((d.facility, d.code), 0)
            rate = max(0.02, (target_bed_hours - ed_bed_hours) / mean_los / horizon_h)   # admissions per hour
            t = self.start
            while True:
                t += H(rng.expovariate(rate))
                if t > self.sim_end:
                    break
                lt = local(t)
                if lt.hour < 7 and rng.random() < 0.6:   # fewer background admits overnight
                    continue
                reqs.append(("bg", t, d, None))
        for e in ed_admits:
            base = lgn(rng, *self.cfg.bed_request_to_assigned, lo=5, hi=1400)
            if e.isolation:
                base *= cfg.isolation_request_multiplier
            if e.level_of_care == "ICU":
                base *= 1.3
            e._assign_target = e.bed_request_at + M(base)
            reqs.append(("ed", e._assign_target, self.dept[(e.facility, e.unit)], e))
        reqs.sort(key=lambda r: r[1])

        for kind, t, d, e in reqs:
            pool = beds[(d.facility, d.code)]
            if kind == "bg":
                st = min(pool, key=lambda b: b["free_at"])
                st["_block"] = False
                start = max(t, st["free_at"])
                los = lgn(rng, *BG_LOS_H[d.level], lo=6)
                end = self._discharge_clock(start + H(los), d.level, rng)
                self.bed_intervals.append((d.facility, d.code, st["room"], st["bed"], start, end, "background"))
                st["free_at"] = end + M(rng.uniform(30, 90))
                continue
            occ = sum(1 for b in pool if b["free_at"] > e.decision_at) / len(pool)
            target = e._assign_target
            if occ >= cfg.crowding_threshold:
                target = e.bed_request_at + (target - e.bed_request_at) * rng.uniform(*cfg.crowding_multiplier)
            e._occ_at_decision = round(occ, 3)
            st, assigned = self._take_bed(pool, target, need_private=bool(e.isolation))
            # Overflow: when the home unit is hours away, take a bed on an acceptable alternate unit.
            if assigned - target > H(4) and not getattr(e, "_reassign", False):
                for alt_level in OVERFLOW.get(e.level_of_care, ()):
                    code = LEVEL_UNIT.get((e.facility, alt_level))
                    if not code or code == e.unit:
                        continue
                    st2, a2 = self._take_bed(beds[(e.facility, code)], target, need_private=bool(e.isolation))
                    if a2 + H(2) < assigned:
                        d = self.dept[(e.facility, code)]
                        st, assigned = st2, a2
                        e.unit, e.unit_dept_id = code, d.dept_id
                        e.flags.append("OVERFLOW")
                        pool = beds[(e.facility, code)]
                        break
            req = BedRequest(self.ids.pend(), e.csn, e.bed_request_at, e.unit, d.dept_id, e.level_of_care,
                             e.isolation if not getattr(e, "_iso_late", False) else "")
            req.assigned_at, req.room, req.bed = assigned, st["room"], st["bed"]
            if getattr(e, "_reassign", False):
                freed = assigned + M(rng.uniform(25, 100))
                req.deleted, req.delete_cause, req.deleted_at = True, "Manually Freed", freed
                self._occupy(st, freed + M(15))
                self.bed_intervals.append((d.facility, d.code, st["room"], st["bed"], assigned, freed, "held"))
                self.w.bed_requests.append(req)
                e._first_bed = (e.unit, st["room"], st["bed"], assigned, freed)
                if getattr(e, "_iso_late", False):
                    new_d = d
                    reason = "Isolation identified, private room required"
                else:
                    new_level = {"TELE": "STEPDOWN", "STEPDOWN": "TELE", "MEDSURG": "TELE"}[e.level_of_care]
                    new_d = self.dept[(e.facility, LEVEL_UNIT[(e.facility, new_level)])]
                    e.level_of_care, e.unit, e.unit_dept_id = new_level, new_d.code, new_d.dept_id
                    reason = "Level of care changed"
                e._reassign_reason = reason
                pool2 = beds[(new_d.facility, new_d.code)]
                st, assigned = self._take_bed(pool2, freed + M(rng.uniform(5, 40)), need_private=bool(e.isolation))
                req = BedRequest(self.ids.pend(), e.csn, freed, e.unit, new_d.dept_id, e.level_of_care, e.isolation)
                req.assigned_at, req.room, req.bed = assigned, st["room"], st["bed"]
                d = new_d
            e.bed_assigned_at, e.room, e.bed = assigned, st["room"], st["bed"]
            dep = assigned + M(lgn(rng, *cfg.bed_assigned_to_departure, lo=10, hi=600))
            if "F10" in e.flags:
                dep += M(40)
            e.departure_at = dep
            # The hospitalist's class change always precedes the physical transfer.
            if e.class_change_at >= dep - M(5):
                e.class_change_at = max(e.decision_at + M(3), dep - M(rng.uniform(5, 20)))
            req.completed_at = dep
            leave = dep + H(e.inpatient_los_h)
            self.bed_intervals.append((d.facility, d.code, st["room"], st["bed"], assigned, leave, "ed_admit"))
            self._occupy(st, leave + M(rng.uniform(30, 90)))
            self.w.bed_requests.append(req)

    def _take_bed(self, pool, target, need_private):
        """Isolation needs a private room, or a semi-private room with the second bed
        blocked (both beds must be free)."""
        def ready(b):
            if not need_private or b["private"]:
                return b["free_at"]
            return max(b["free_at"], b["partner"]["free_at"]) if "partner" in b else b["free_at"]
        free = [b for b in pool if ready(b) <= target]
        st = self.rng.choice(free) if free else min(pool, key=ready)
        at = target if free else ready(st)
        st["_block"] = need_private and not st["private"] and "partner" in st
        return st, at

    @staticmethod
    def _occupy(st, until):
        st["free_at"] = until
        if st.get("_block"):
            st["partner"]["free_at"] = max(st["partner"]["free_at"], until)

    def _discharge_clock(self, t, level, rng):
        """Background discharges cluster late morning to afternoon."""
        if level == "OBS":
            return t
        lt = local(t)
        day = lt.date()
        h = rng.choices(range(9, 20), [3, 8, 12, 14, 14, 13, 11, 9, 7, 5, 4])[0]
        out = datetime(day.year, day.month, day.day, h, rng.randrange(60), tzinfo=TZ).astimezone(UTC)
        return out if out >= t - H(10) else out + H(24)

    def _ed_bays(self):
        for fac in ("MGH", "LKS"):
            d = self.dept[(fac, "ED")]
            free = {f"{r}": self.start - H(1) for r, _, _ in d.rooms}
            hall = {f"H{i}": self.start - H(1) for i in range(1, 9)}
            for e in sorted((e for e in self.encs if e.facility == fac and e.roomed_at), key=lambda e: e.roomed_at):
                end = (e.departure_at or self.sim_end) + M(e._rng.uniform(10, 25))
                open_bays = [b for b, t in free.items() if t <= e.roomed_at]
                if open_bays:
                    b = e._rng.choice(open_bays)
                    free[b] = end
                else:
                    b = min(hall, key=hall.get)
                    hall[b] = end
                e.ed_bay = b

    # ------------------------------------------------------------------ pass 3: detail
    def _detail(self, e: Encounter):
        rng = e._rng
        pat = self.pats[e.pat_id]
        scen = get(e.scenario)
        e.weight_kg = pat.weight_kg
        ed = self.dept[(e.facility, "ED")]
        e._ed_dept = ed.dept_id
        admit = e.disposition.startswith("ADMIT")
        stop = e.departure_at or self.sim_end
        horizon = (e.departure_at + H(self.cfg.inpatient_horizon_h)) if (admit and e.departure_at) else stop
        e._latest = {}
        if e.provider_at:
            self._orders(e, pat, scen, rng, horizon)
        meds_given = self._meds(e, pat, scen, rng, horizon) if e.provider_at else []
        e._tx_start = min([t for _, t in meds_given], default=(e.provider_at or e.triage_at) + M(30))
        self._vitals(e, pat, scen, rng, horizon)
        if (not getattr(self, "_f23_done", False) and admit and e.esi == 2 and e.decision_at and e.departure_at
                and e.departure_at - e.decision_at > H(7)):
            gap = e.decision_at + H(1)
            self.w.vitals = [v for v in self.w.vitals if not (v.csn == e.csn and gap < v.taken_at < gap + H(5))]
            self._f23_done = True
            e.flags.append("F23")
            self.fault("F23", e.csn, "vitals", gap, "5 hour gap in charted vitals while boarding",
                       "Last vitals X hours ago warning")
        if admit:
            self._code_status(e, pat, rng)
        if getattr(e, "_iso_name", None):
            hist = not getattr(e, "_iso_late", False) and e._iso_name in ("MRSA", "VRE", "ESBL", "CRE")
            at = (e.arrival_at - timedelta(days=rng.randint(60, 1500))) if hist else \
                ((e.bed_request_at + M(rng.uniform(20, 90))) if getattr(e, "_iso_late", False) else e.provider_at + M(rng.uniform(30, 120)))
            self.w.infections.append(Infection(self.ids.misc(), pat.pat_id, e.csn, e._iso_name, e.isolation, at,
                                               "history" if hist else "this_visit"))
            e._iso_at = at
            if getattr(e, "_iso_late", False):
                e.flags.append("F22")
                self.fault("F22", e.csn, "all", at, f"{e._iso_name} isolation identified after the bed request",
                           "Isolation and any bed change surface in the handoff")
        if getattr(e, "_new_allergy", None):
            at, (name, code, system) = e._new_allergy
            if not any(a.pat_id == e.pat_id and a.allergen == name for a in self.w.allergies):
                self.w.allergies.append(Allergy(self.ids.misc(), e.pat_id, name, code, system,
                                                rng.choice(["Hives", "Rash", "Itching"]), "Medium", at, added_csn=e.csn))
                e.flags.append("F21")
                self.fault("F21", e.csn, "all", at, f"New allergy ({name}) documented during the visit",
                           "Handoff allergies reflect the same visit change, not just the nightly batch")
        if "F10" in e.flags and e.departure_at:
            d = self.dept[(e.facility, e.unit)]
            e._wrong_bed = rng.choice([(r, b) for r, b, _ in d.rooms if (r, b) != (e.room, e.bed)])
        if "F14" in e.flags and e.departure_at:
            e._conversion_at = max(e.class_change_at + H(e._conv_h), e.departure_at + H(1))
            self.fault("F14", e.csn, "adt", e._conversion_at, "Observation converted to inpatient",
                       "Patient class history has two steps; the handoff shows the status change")
        self._notes(e, pat, scen, rng, meds_given, horizon)
        self._adt(e, pat, rng)

    # ---- vitals
    def _vital_track(self, e, scen, rng):
        tr = {}
        for k, rng_k in scen.vitals.items():
            base = u(rng, rng_k)
            tgt = scen.targets.get(k)
            if tgt is None:
                normal = {"hr": (70, 92), "sbp": (112, 140), "dbp": (64, 86), "rr": (14, 18), "temp": (36.5, 37.2),
                          "spo2": (95, 99), "pain": (0, 3), "gcs": (15, 15)}[k]
                tgt_v = base + (u(rng, normal) - base) * 0.6
            else:
                tgt_v = u(rng, tgt)
            tr[k] = (base, tgt_v)
        if e.esi == 1:
            k = rng.choice(["sbp", "spo2", "gcs"])
            b, t = tr[k]
            tr[k] = ({"sbp": min(b, u(rng, (72, 88))), "spo2": min(b, u(rng, (82, 89))), "gcs": min(b, u(rng, (9, 12)))}[k], t)
        return tr

    def _vitals(self, e, pat, scen, rng, horizon):
        if not e.triage_at:
            return
        track = self._vital_track(e, scen, rng)
        tau = scen.tau_min
        times = [(e.triage_at, "triage")]
        t = e.triage_at
        ed_end = min(e.decision_at or e.departure_at or self.sim_end, e.departure_at or self.sim_end)
        if e.disposition in ("LWBS",):
            ed_end = e.triage_at
        cadence = {1: [(60, 15), (10 ** 6, 60)], 2: [(120, 60), (10 ** 6, 120)], 3: [(10 ** 6, 120)]}.get(e.esi)
        if cadence:
            elapsed = 0
            for span, step in cadence:
                while elapsed < span:
                    elapsed += step
                    nt = e.triage_at + M(elapsed)
                    if nt >= ed_end:
                        break
                    times.append((nt, "ed"))
                if e.triage_at + M(elapsed) >= ed_end:
                    break
        if e.disposition in ("DISCHARGE", "AMA") and e.departure_at:
            times.append((e.departure_at - M(rng.uniform(5, 20)), "discharge"))
        if e.decision_at and e.disposition.startswith("ADMIT"):
            step = {"ICU": 60, "STEPDOWN": 120}.get(e.level_of_care, 240)
            t = e.decision_at
            while True:
                t += M(step)
                if t >= (e.departure_at or self.sim_end):
                    break
                times.append((t, "boarding"))
            if e.departure_at:
                t = e.departure_at + M(rng.uniform(10, 30))
                times.append((t, "inpatient"))
                istep = {"ICU": 60, "STEPDOWN": 120}.get(e.level_of_care, 240)
                while True:
                    t += M(istep)
                    if t >= horizon:
                        break
                    times.append((t, "inpatient"))
        o2_on = None
        if scen.o2 and track.get("spo2", (99, 99))[0] < 93:     # margin for charting noise around 92
            o2_on = (e.provider_at or e.triage_at) + M(rng.uniform(3, 15))
            flow = round(u(rng, scen.o2[1]))
        o2_off = None
        line = 0
        for i, (t, phase) in enumerate(sorted(times)):
            if phase == "boarding" and rng.random() < 0.22:
                continue                                  # missed set (spec 8.7, F23)
            if phase in ("boarding", "inpatient") and rng.random() < 0.12:
                t += M(rng.uniform(15, 45))               # late set
            line += 1
            dt = (t - e._tx_start).total_seconds() / 60
            r = 0 if dt <= 0 else 1 - math.exp(-dt / tau)

            def val(k, noise):
                base, tgt = track[k]
                return base + (tgt - base) * r + rng.gauss(0, noise)
            hr = int(round(min(200, max(35, val("hr", 4)))))
            sbp = int(round(min(240, max(65, val("sbp", 6)))))
            dbp = int(round(min(140, max(32, min(sbp - 20, val("dbp", 4))))))
            rr = int(round(min(48, max(8, val("rr", 1.5)))))
            temp = round(min(41.5, max(34.5, val("temp", 0.12))), 1) if (i % 2 == 0 or phase == "triage") else None
            o2 = o2_on is not None and t >= o2_on and o2_off is None
            spo2_base, spo2_tgt = track["spo2"]
            spo2 = spo2_base + (spo2_tgt - spo2_base) * (r if (o2 or o2_off or not scen.o2) else r * 0.3) + rng.gauss(0, 1)
            spo2 = int(round(min(100, max(70, spo2))))
            if o2 and phase in ("boarding", "inpatient") and t > o2_on + H(6) and spo2 >= 94 and rng.random() < 0.35:
                flow -= 1                                 # wean toward room air once at target
                if flow <= 0:
                    o2, o2_off = False, t
            pain = int(round(min(10, max(0, val("pain", 0.8)))))
            gcs = int(round(min(15, max(3, val("gcs", 0.3))))) if scen.vitals["gcs"][0] < 15 else None
            in_ed = e.departure_at is None or t < e.departure_at or not e.disposition.startswith("ADMIT")
            user = e.ed_nurse if in_ed else e.unit_nurse
            dept = e._ed_dept if in_ed else e.unit_dept_id
            late_entry = M(rng.uniform(30, 70)) if rng.random() < 0.08 else M(rng.uniform(0, 12))
            v = Vital(e.csn, line, t, t + late_entry, user, dept, hr, sbp, dbp, round((sbp + 2 * dbp) / 3), rr, temp, spo2,
                      "Nasal cannula" if o2 else "Room air", float(flow) if o2 else None, pain, gcs)
            self.w.vitals.append(v)

    # ---- labs and imaging
    def _lab_value(self, code, scen, pat, override=None):
        rng = self.rng
        cat = LABS[code]
        rngs = (override or {}).get(code) or scen.labs.get(code)
        probs = {p["snomed"] for p in pat.problems}
        if rngs is None:
            lo, hi = cat[7], cat[8]
            if code == "CREAT" and probs & {"433144002", "431857002", "46177005"}:
                lo, hi = 1.4, 2.6
            if code == "HGB" and "271737000" in probs:
                lo, hi = 10.0, 11.8
            if code in ("GLU", "POCGLU") and "44054006" in probs:
                lo, hi = 130, 230
            if code == "INR" and any("warfarin" in m["display"].lower() for m in pat.home_meds):
                lo, hi = 2.0, 4.6
            if code == "HGB" and pat.sex == "M":
                lo, hi = lo + 1.0, hi + 1.0
            rngs = (lo, hi)
        return rng.uniform(*rngs)

    def _fmt(self, code, x):
        if code in ("WBC", "HGB", "HCT", "CREAT", "CA", "TBILI", "ALB", "TP", "LACT", "MG", "PHOS", "INR", "BHB", "A1C",
                    "K"):
            return round(x, 1) if code not in ("CREAT", "INR", "LACT") else round(x, 2)
        if code in ("VPH",):
            return round(x, 2)
        if code in ("PCT",):
            return round(x, 2)
        return int(round(x))

    def _flag(self, code, x):
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

    def _panel_results(self, e, pat, scen, order, panel, collected, resulted, override=None):
        comps = PANELS[panel][2]
        vals = {}
        for c in comps:
            if c in ("AGAP", "CL", "HCT") or LABS[c][7] is None:
                continue
            vals[c] = self._lab_value(c, scen, pat, override)
        if "HGB" in vals:
            vals["HCT"] = vals["HGB"] * 3 + self.rng.uniform(-1.5, 1.5)
        if "NA" in vals and "CO2" in vals:
            gap = self.rng.uniform(20, 32) if e.scenario == "dka" and not override else \
                self.rng.uniform(14, 22) if e.scenario == "dka" else \
                self.rng.uniform(13, 19) if e.scenario.startswith("sepsis") else self.rng.uniform(8, 13)
            vals["CL"] = min(118, max(88, vals["NA"] - vals["CO2"] - gap))
        rows = []
        for line, c in enumerate(comps, 1):
            cat = LABS[c]
            if c == "AGAP":
                if not {"NA", "CL", "CO2"} <= set(vals):
                    continue
                x = self._fmt("NA", vals["NA"]) - self._fmt("CL", vals["CL"]) - self._fmt("CO2", vals["CO2"])
            elif c in vals:
                x = self._fmt(c, vals[c])
            else:
                rows.extend(self._qual(e, scen, order, line, c, collected, resulted))
                continue
            flag = self._flag(c, x)
            comment = ""
            if flag in ("HH", "LL"):
                rn = e.ed_nurse if (not e.departure_at or resulted < e.departure_at) else e.unit_nurse
                comment = f"Critical value called to {self._prov_name(rn)} at {N._hm(resulted + M(4))}, read back."
            rows.append(Result(order.order_id, e.csn, line, c, cat[0], cat[1], str(x), float(x), cat[2], cat[3], cat[4],
                               flag, "F", collected, resulted, comment))
            e._latest[c] = rows[-1]
        return rows

    def _qual(self, e, scen, order, line, c, collected, resulted):
        rng = self.rng
        cat = LABS[c]
        mk = lambda v, flag, st, at, cm="": Result(order.order_id, e.csn, line, c, cat[0], cat[1], v, None, cat[2], None,  # noqa: E731
                                                     None, flag, st, collected, at, cm)
        uti = e.scenario in ("sepsis_uti", "pyelo", "uti_d")
        if c == "UA_LE":
            return [mk(rng.choice(["2+", "3+"]) if uti else rng.choice(["Negative"] * 5 + ["Trace"]), "A" if uti else "", "F", resulted)]
        if c == "UA_NIT":
            pos = uti and rng.random() < 0.7
            return [mk("Positive" if pos else "Negative", "A" if pos else "", "F", resulted)]
        if c == "BCX":
            pos = (e.scenario == "sepsis_uti" and rng.random() < 0.33) or (e.scenario == "sepsis_pna" and rng.random() < 0.08)
            prelim_at = collected + H(rng.uniform(10, 20)) if pos else collected + H(24)
            rows = [mk("Gram negative rods in 1 of 2 bottles" if pos else "No growth to date", "HH" if pos else "", "P", prelim_at,
                       f"Critical value called to {self._prov_name(e.unit_nurse or e.ed_nurse)}, read back." if pos else "")]
            final_at = collected + H(rng.uniform(40, 54))
            rows.append(mk("Escherichia coli" if pos else "No growth at 48 hours",
                           "A" if pos else "", "P" if not pos else "F", final_at))
            return rows
        if c == "UCX":
            pos = uti and rng.random() < 0.8
            return [mk(">100,000 CFU/mL Escherichia coli" if pos else "No growth", "A" if pos else "", "F",
                       collected + H(rng.uniform(20, 36)))]
        if c == "ABORH":
            return [mk(rng.choices(["O Pos", "A Pos", "B Pos", "AB Pos", "O Neg", "A Neg"], [37, 34, 9, 3, 7, 6])[0], "", "F", resulted)]
        if c in ("FLU", "COVID"):
            det = e.scenario == "flu_covid" and ((c == "FLU") == (getattr(e, "_iso_name", "Influenza") == "Influenza"))
            return [mk("Detected" if det else "Not detected", "A" if det else "", "F", resulted)]
        return []

    def _chain(self, e, override, collected, rng):
        """Repeat draws move from the prior value at a physiologic rate instead of a fresh draw."""
        out = dict(override or {})
        prev = e._latest
        hrs = lambda k: max(0.5, (collected - prev[k].observed_at).total_seconds() / 3600)  # noqa: E731

        def put(k, v):
            out[k] = (v, v)
        if e.scenario == "dka" and "GLU" in prev:
            put("GLU", max(150, prev["GLU"].numeric - rng.uniform(50, 75) * hrs("GLU")))
            if "CO2" in prev:
                put("CO2", min(22, prev["CO2"].numeric + rng.uniform(1.0, 2.0) * hrs("CO2")))
            if "K" in prev:
                put("K", max(3.4, prev["K"].numeric - rng.uniform(0.2, 0.4) * hrs("K")))
        if e.scenario == "ugib" and "HGB" in prev:
            put("HGB", prev["HGB"].numeric - rng.uniform(0.5, 1.5))
        if "TROP" in prev:
            f = rng.uniform(2, 6) if e.scenario in ("nstemi", "stemi_transfer") else rng.uniform(0.9, 1.15)
            put("TROP", prev["TROP"].numeric * f)
        if e.scenario.startswith("sepsis") and "LACT" in prev:
            put("LACT", prev["LACT"].numeric * rng.uniform(0.45, 0.75))
        return out

    def _timed_lab(self, e, pat, scen, panel, at, override, prov, priority="Timed", comment=""):
        o = self._add_order(e, "Lab", PANELS[panel][1], PANELS[panel][0], "", at, prov, priority)
        o.accession = self.ids.accession()
        o.collected_at = at + M(self.rng.uniform(2, 15))
        resulted = o.collected_at + M(lgn(self.rng, *PANELS[panel][3], lo=5))
        rows = self._panel_results(e, pat, scen, o, panel, o.collected_at, resulted, override)
        if comment:
            for r in rows:
                r.comment = (r.comment + " " + comment).strip()
        self.w.results.extend(rows)
        return o, resulted

    def _prov_name(self, prov_id):
        for p in self.w.providers:
            if p.prov_id == prov_id:
                return p.display
        return "RN"

    def _add_order(self, e, otype, code, name, loinc, at, prov, priority="STAT"):
        o = Order(self.ids.order(), e.csn, otype, code, name, loinc, priority, at, prov)
        self.w.orders.append(o)
        return o

    def _orders(self, e, pat, scen, rng, horizon):
        admit = e.disposition.startswith("ADMIT")
        prov = e.ed_resident or e.ed_attending
        protocol = scen.key.startswith("sepsis") or scen.key == "stroke"
        t0 = (e.triage_at + M(rng.uniform(3, 12))) if protocol else e.provider_at + M(rng.uniform(1, 15))
        panels = list(scen.panels)
        for p, prob in scen.optional_panels.items():
            if rng.random() < prob:
                panels.append(p)
        if not admit and rng.random() > 0.65:
            panels = []
        if e.disposition in ("LBTC", "AMA") and rng.random() < 0.5:
            panels = panels[:2]
        first_result = {}
        for p in panels:
            disp, proc, comps, tat = PANELS[p]
            o = self._add_order(e, "Lab", proc, disp, "", t0 + M(rng.uniform(0, 6)), prov)
            o.accession = self.ids.accession()
            o.collected_at = o.ordered_at + M(lgn(rng, 8 if e.esi <= 2 else 15, 25 if e.esi <= 2 else 45, lo=1))
            resulted = o.collected_at + M(lgn(rng, *tat, lo=tat[0] * 0.4))
            rows = self._panel_results(e, pat, scen, o, p, o.collected_at, resulted)
            self.w.results.extend(rows)
            first_result[p] = resulted
        for p, after, override in scen.repeats:
            if p not in first_result:
                continue
            at = first_result[p] + M(after * rng.uniform(0.8, 1.3))
            o = self._add_order(e, "Lab", PANELS[p][1], PANELS[p][0], "", at, prov, "Timed")
            o.accession = self.ids.accession()
            o.collected_at = at + M(rng.uniform(2, 20))
            resulted = o.collected_at + M(lgn(rng, *PANELS[p][3], lo=5))
            self.w.results.extend(self._panel_results(e, pat, scen, o, p, o.collected_at, resulted,
                                                      self._chain(e, override, o.collected_at, rng)))
        low_hgb = [r for r in self.w.results if r.csn == e.csn and r.lab == "HGB" and r.numeric is not None and r.numeric < 7]
        if low_hgb:
            self._add_order(e, "Blood Product", "BLD0001", "Transfuse packed red blood cells, 1 unit", "",
                            low_hgb[0].resulted_at + M(rng.uniform(15, 40)), prov)
        for code, (prob, impressions) in scen.imaging.items():
            if rng.random() >= prob or (not admit and rng.random() > 0.7):
                continue
            disp, loinc, pre_tat, fin_tat = IMAGING[code]
            chest = scen.key in ("chest_pain_ro", "nstemi", "chest_pain_d", "stemi_transfer")
            if code == "ECG" and chest:
                at = e.arrival_at + M(rng.uniform(1, 6))       # ECG on arrival: door to ECG within 10 minutes
            elif code in ("ECG", "CTH") and protocol:
                at = e.triage_at + M(rng.uniform(2, 8))
            else:
                at = t0 + M(rng.uniform(0, 20))
            o = self._add_order(e, "Imaging", code, disp, loinc, at, prov)
            o.collected_at = at + M(rng.uniform(5, 40) if code != "ECG" else rng.uniform(1, 4) if chest else rng.uniform(1, 8))
            imp = rng.choice(impressions) if impressions else "No acute abnormality."
            if code == "ECG":
                self.w.results.append(Result(o.order_id, e.csn, 1, "ECG", loinc, "ECG interpretation", imp, None, "", None, None,
                                             "", "F", o.collected_at, o.collected_at + M(rng.uniform(2, 15))))
                continue
            pre = o.collected_at + M(lgn(rng, *pre_tat, lo=8))
            fin = pre + M(lgn(rng, *fin_tat, lo=20))
            self.w.results.append(Result(o.order_id, e.csn, 1, code, loinc, "Impression", "Preliminary: " + imp, None, "",
                                         None, None, "", "P", o.collected_at, pre))
            self.w.results.append(Result(o.order_id, e.csn, 1, code, loinc, "Impression", imp, None, "", None, None, "", "F",
                                         o.collected_at, fin))
        if not admit:
            return
        adt_name = "Place in observation" if e.disposition == "ADMIT_OBS" else "Admit to inpatient"
        self._add_order(e, "ADT", "ADT0001", adt_name, "", e.decision_at + M(rng.uniform(2, 15)), e.admitting_prov, "Routine")
        for spec in e.consults:
            self._add_order(e, "Consult", "CON" + spec[:3].upper(), f"Consult to {spec}", "",
                            e.decision_at - M(rng.uniform(-20, 60)), prov, "Urgent")
        # Inpatient morning labs (CBC, BMP) about 04:30 local each day after transfer.
        if e.departure_at:
            day = local(e.departure_at + H(4)).date()
            while True:
                draw = datetime(day.year, day.month, day.day, 4, rng.randrange(15, 50), tzinfo=TZ).astimezone(UTC)
                if draw > horizon:
                    break
                if draw > e.departure_at + H(3):
                    for p in ("CBC", "BMP"):
                        o = self._add_order(e, "Lab", PANELS[p][1], PANELS[p][0], "", draw - H(6), e.admitting_prov, "AM Draw")
                        o.accession = self.ids.accession()
                        o.collected_at = draw
                        resulted = draw + M(lgn(rng, *PANELS[p][3], lo=10))
                        trend = None
                        if e.scenario == "ugib":
                            last = e._latest.get("HGB")
                            units = sum(1 for o in self.w.orders if o.csn == e.csn and o.order_type == "Blood Product"
                                        and last and last.resulted_at < o.ordered_at < draw)
                            base = (last.numeric + units * 1.0 - rng.uniform(0, 0.6)) if last else rng.uniform(7.0, 9.5)
                            trend = {"HGB": (base, base)}
                        if e.scenario == "dka":
                            trend = {"GLU": (140, 220), "CO2": (19, 24), "K": (3.6, 4.4)}
                        self.w.results.extend(self._panel_results(e, pat, get(e.scenario), o, p, draw, resulted,
                                                                  trend or {k: LABS[k][7:9] for k in ("WBC",)}))
                day += timedelta(days=1)

    # ---- medications
    def _dose(self, m: Med, weight):
        if not m.weight_based:
            return m.dose, m.unit, m.rate
        per = float(m.dose)
        if m.unit == "mL/kg":
            vol = int(min(3000, round(per * weight / 100) * 100))      # 30 mL/kg, capped at 3 L
            return str(vol), "mL", m.rate
        if m.name.startswith("alteplase"):
            return f"{min(90.0, per * weight):.1f}", "mg", ""
        if m.name.startswith("vancomycin"):
            return str(int(min(2500, round(per * weight / 250) * 250))), "mg", ""
        if m.name.startswith("diltiazem"):
            return str(int(min(25, round(per * weight / 5) * 5))), "mg", ""
        if m.unit in ("units/kg/hr",):
            if per >= 1:      # heparin: 12 units/kg/hr, rounded to 50, max 1000 (ACS nomogram)
                x = min(1000, int(round(per * weight / 50) * 50))
                return str(x), "units/hr", f"{x} units/hr"
            x = round(per * weight * 2) / 2   # insulin: 0.1 units/kg/hr, rounded to 0.5
            return f"{x:g}", "units/hr", f"{x:g} units/hr"
        return m.dose, m.unit, m.rate

    def _meds(self, e, pat, scen, rng, horizon):
        prov = e.ed_resident or e.ed_attending
        allergic_pcn = any(a.allergen == "Penicillins" for a in self.w.allergies if a.pat_id == pat.pat_id)
        given = []
        meds = list(scen.meds)
        admit = e.disposition.startswith("ADMIT")
        if admit and not any(m.kind == "infusion" and "bolus" in m.name for m in meds) and rng.random() < 0.45:
            meds.append(get("vomiting_d").meds[0])   # NS bolus
        if e.disposition in ("AMA", "LBTC"):
            meds = meds[:1]
        if not admit and e.disposition == "DISCHARGE" and rng.random() > 0.65:
            meds = []
        for m in meds:
            forced = m.name.startswith("piperacillin") and getattr(e, "_force_piptazo", False)
            if rng.random() >= m.prob and not forced:
                continue
            name, rxcui, comment = m.name, m.rxcui, ""
            if allergic_pcn and m.name.startswith("piperacillin"):
                if getattr(e, "_force_piptazo", False) and not self.pcn_override_used:
                    self.pcn_override_used = True
                    comment = "Allergy override: prior reaction was mild rash; benefit outweighs risk per ED attending."
                    e.flags.append("PCN_OVERRIDE")
                else:
                    name, rxcui = "cefepime 2 g injection", "1665088"
                    m = Med(name, rxcui, "2", "g", "IV", "Q8H", offset=m.offset, repeat_h=8)
            dose, unit, rate = self._dose(m, e.weight_kg)
            ordered = e.provider_at + M(u(rng, m.offset))
            if e.decision_at and ordered > e.decision_at + H(2) and not admit:
                continue
            verified = ordered + M(rng.uniform(2, 12))
            mo = MedOrder(self.ids.order(), e.csn, rxcui, name, dose, unit, m.route, m.freq, m.kind, ordered, verified, prov,
                          rate, comment or (f"Weight based: {m.weight_based}, weight {e.weight_kg} kg" if m.weight_based else ""))
            self.w.med_orders.append(mo)
            first = verified + M(rng.uniform(3, 25))
            bcx = [o.collected_at for o in self.w.orders if o.csn == e.csn and o.code == "LAB0040" and o.collected_at]
            if bcx and any(w in name for w in ("cefTRIAXone", "azithromycin", "piperacillin", "vancomycin", "cefepime")):
                first = max(first, min(bcx) + M(rng.uniform(2, 10)))   # cultures before antibiotics
            line = 1
            if name.startswith("heparin"):     # 60 units/kg bolus (max 4000) before the infusion, aPTT at 6 h
                bolus = str(min(4000, int(round(60 * e.weight_kg / 100) * 100)))
                self.w.mar.append(MarAdmin(mo.order_med_id, e.csn, line, "Given", first - M(2), ordered, bolus, "units", "IV", "",
                                           e.ed_nurse, e._ed_dept))
                line += 1
                self._timed_lab(e, pat, scen, "COAG", first + H(6), {"PTT": (58, 92)}, e.admitting_prov or prov)
            action = "New Bag" if m.kind == "infusion" else "Given"
            self.w.mar.append(MarAdmin(mo.order_med_id, e.csn, line, action, first, ordered, dose, unit, m.route, rate,
                                       e.ed_nurse, e._ed_dept))
            given.append((mo, first))
            if m.name.startswith("insulin"):
                self._timed_lab(e, pat, scen, "POCGLU", first + H(1), None, prov)
            if m.freq == "CONTINUOUS" and rng.random() < 0.6:
                line += 1
                self.w.mar.append(MarAdmin(mo.order_med_id, e.csn, line, "Rate Change", first + M(rng.uniform(30, 90)), first,
                                           dose, unit, m.route, rate, e.ed_nurse, e._ed_dept))
            if admit and m.repeat_h:
                t = first + H(m.repeat_h)
                while t < horizon:
                    line += 1
                    in_ed = not e.departure_at or t < e.departure_at
                    act = "Held" if rng.random() < 0.03 else "Given"
                    self.w.mar.append(MarAdmin(mo.order_med_id, e.csn, line, act, t + M(rng.uniform(-20, 45)), t, dose, unit,
                                               m.route, rate, e.ed_nurse if in_ed else e.unit_nurse,
                                               e._ed_dept if in_ed else e.unit_dept_id))
                    t += H(m.repeat_h)
        if admit and e.decision_at:
            given += self._admission_orders(e, pat, scen, rng, horizon)
        return sorted(given, key=lambda g: g[1])

    def _admission_orders(self, e, pat, scen, rng, horizon):
        """Admitting team's order set, written after the decision and given while boarding."""
        out = []
        prov = e.admitting_prov
        start = e.decision_at + M(rng.uniform(30, 120))
        bleeding = e.scenario in ("ugib", "stroke", "nstemi", "hip_fx", "trauma_other")
        items = []
        if not bleeding and rng.random() < 0.85:
            items.append((Med("enoxaparin 100 mg/mL syringe", "854235", "40", "mg", "SC", "DAILY", "bolus", repeat_h=24), 60))
        if rng.random() < 0.5 and not any(m.kind == "infusion" for m in scen.meds):
            items.append((Med("sodium chloride 0.9 % infusion", "313002", "1000", "mL", "IV", "CONTINUOUS", "infusion",
                              rate="75 mL/hr"), 20))
        items.append((Med("acetaminophen 500 mg tablet", "198440", "650", "mg", "PO", "Q6H PRN", "po"), 0))
        items.append((Med("ondansetron 2 mg/mL injection", "1740467", "4", "mg", "IV", "Q6H PRN"), 0))
        for m, delay in items:
            ordered = start + M(rng.uniform(0, 10))
            verified = ordered + M(rng.uniform(5, 30))
            mo = MedOrder(self.ids.order(), e.csn, m.rxcui, m.name, m.dose, m.unit, m.route, m.freq, m.kind, ordered, verified,
                          prov, m.rate, "Admission order set")
            self.w.med_orders.append(mo)
            prn = "PRN" in m.freq
            t = verified + M(delay + rng.uniform(5, 60))
            line = 0
            while t < horizon:
                if not prn or rng.random() < 0.45:
                    line += 1
                    in_ed = not e.departure_at or t < e.departure_at
                    self.w.mar.append(MarAdmin(mo.order_med_id, e.csn, line, "New Bag" if m.kind == "infusion" else "Given", t, t,
                                               m.dose, m.unit, m.route, m.rate, e.ed_nurse if in_ed else e.unit_nurse,
                                               e._ed_dept if in_ed else e.unit_dept_id))
                    if line == 1:
                        out.append((mo, t))
                if m.kind == "infusion" or not (m.repeat_h or prn):
                    break
                t += H(m.repeat_h or 6) + M(rng.uniform(-20, 40))
        return out

    # ---- code status
    def _code_status(self, e, pat, rng):
        age = N.pat_age(pat, e)
        if age < 65:
            w = {"Full Code": 96.9, "DNR/DNI": 2.4, "Partial Code": 0.5, "Comfort Care": 0.2}
        elif age < 85:
            w = {"Full Code": 79.2, "DNR/DNI": 17.9, "Partial Code": 2.2, "Comfort Care": 0.7}
        else:
            w = {"Full Code": 45.9, "DNR/DNI": 48.7, "Partial Code": 3.0, "Comfort Care": 2.4}
        status = getattr(e, "_planned_code_status", None) or wpick(rng, w)
        serious = {"46177005", "26929004", "254837009", "92691004"} & {p["snomed"] for p in pat.problems} or \
            any("malignant" in p["display"].lower() for p in pat.problems)
        if age < 50 and not serious:
            status = "Full Code"
        e._code_status = status
        if "F20" in e.flags:
            at = (e.departure_at or self.sim_end) + H(rng.uniform(2, 6))
        else:
            at = e.decision_at + M(rng.uniform(15, 75))
        e._code_status_at = at
        self.w.code_status.append(CodeStatus(self.ids.misc(), pat.pat_id, e.csn, status, at, e.admitting_prov))
        self._add_order(e, "Code Status", "COD0001", status, "", at - M(1), e.admitting_prov, "Routine")

    # ---- notes
    def _results_at(self, e, t):
        t = min(t, self.end)   # a note unsigned at the window end only knows what was charted by then
        out = {}
        for r in e._res:
            if r.resulted_at <= t and (r.lab not in out or out[r.lab].resulted_at <= r.resulted_at):
                out[r.lab] = r
        return out

    def _vital_at(self, e, t):
        t = min(t, self.end)
        best = None
        for v in e._vit:
            if v.entered_at <= t and (best is None or v.taken_at > best.taken_at):
                best = v
        return best

    def _add_note(self, e, ntype, loinc, author, created, service, signed, text):
        n = Note(self.ids.note(), e.csn, ntype, loinc, author, created, service, signed, text,
                 [("In Progress", created)] + ([("Signed", signed)] if signed else []))
        self.w.notes.append(n)
        return n

    def _notes(self, e, pat, scen, rng, meds_given, horizon):
        e._res = [r for r in self.w.results if r.csn == e.csn]
        e._vit = [v for v in self.w.vitals if v.csn == e.csn]
        allergies = [a for a in self.w.allergies if a.pat_id == pat.pat_id and a.noted_at <= e.arrival_at]
        if e.triage_at:
            created = e.triage_at + M(rng.uniform(1, 6))
            signed = created + M(rng.uniform(1, 8) if rng.random() < 0.98 else rng.uniform(20, 90))
            tri_v = min(e._vit, key=lambda v: v.taken_at) if e._vit else None
            self._add_note(e, "ED Triage Notes", "84249-2", e.ed_nurse, created, e.triage_at, signed,
                           N.triage(e, pat, scen, tri_v, allergies))
        if not e.provider_at:
            return
        admit = e.disposition.startswith("ADMIT")
        dep = e.departure_at or self.sim_end
        created = e.provider_at + M(rng.uniform(5, 30))
        before = rng.random() < (0.60 if admit else 0.58)
        if before:
            lo = max(created + M(20), (e.decision_at or dep) - M(10))
            signed = lo + (dep - lo) * rng.random() if dep > lo else dep
        else:
            r = rng.random()
            signed = dep + (H(rng.uniform(0.5, 20)) if r < 0.75 else H(rng.uniform(24, 72)) if r < 0.95 else H(rng.uniform(72, 120)))
        signed = max(signed, created + M(rng.uniform(5, 15)))
        as_of = min(signed, e.decision_at or dep, self.end)
        loc = {"ICU": "ICU", "STEPDOWN": "step down", "TELE": "telemetry", "MEDSURG": "medical surgical", "OBS": "observation"}
        dispo = {"ADMIT_IP": f"Admit to {e.admit_service.lower()}, {loc.get(e.level_of_care, 'inpatient')} level of care.",
                 "ADMIT_OBS": "Place in observation.", "DISCHARGE": "Discharge home with return precautions.",
                 "AMA": "Left against medical advice after risks discussed.", "LBTC": "Left before treatment complete.",
                 "TRANSFER": "Transfer to outside facility."}[e.disposition]
        if e.decision_at is None or e.decision_at > as_of:
            dispo = "Pending workup."
        author = e.ed_resident or e.ed_attending
        n = self._add_note(e, "ED Provider Notes", "28568-4", author, created, e.provider_at, signed,
                           N.provider(e, pat, scen, self._vital_at(e, as_of), self._results_at(e, as_of),
                                      [g for g in meds_given if g[1] <= as_of], dispo))
        if admit and signed > dep:
            e.flags.append("F16")
            self.fault("F16", e.csn, "notes", signed, f"ED provider note {n.note_id} signed after ED departure",
                       "Handoff drafted before signature cites the draft and flags it; regenerate on sign")
        # Nursing reassessments roughly every 2 h while in the ED.
        if e.roomed_at:
            last = e.roomed_at
            for v in sorted(e._vit, key=lambda v: v.taken_at):
                if (v.taken_at < e.roomed_at + M(60) or v.taken_at - last < M(100) or v.taken_at > dep - M(20)
                        or v.entered_at - v.taken_at > M(20)):
                    continue
                if rng.random() < 0.85:
                    t = v.entered_at + M(rng.uniform(1, 10))
                    self._add_note(e, "ED Notes", "57053-1", e.ed_nurse, t, v.taken_at, t + M(rng.uniform(3, 30)),
                                   N.reassessment(v, scen, bool(e.decision_at and admit and t > e.decision_at)))
                    last = v.taken_at
        if not admit:
            return
        created = e.decision_at + M(rng.uniform(30, 120))
        signed = created + M(rng.uniform(60, 360))
        self._add_note(e, "H&P", "104530-1", e.admitting_prov, created, created, signed,
                       N.hp(e, pat, scen, self._vital_at(e, signed), self._results_at(e, signed),
                            getattr(e, "_code_status", None) if getattr(e, "_code_status_at", signed) <= signed else None))
        for spec in e.consults:
            c = e.decision_at + M(rng.uniform(20, 150))
            consultant = self.pick_prov(e.facility, "Physician", spec, rng).prov_id
            self._add_note(e, "Consults", "11488-4", consultant, c, c, c + M(rng.uniform(40, 180)), N.consult(e, spec, scen))
        if e.departure_at:
            t = e.departure_at - M(rng.uniform(5, 25))
            self._add_note(e, "ED to Hosp-Admission Nursing Transfer", "28651-8", e.ed_nurse, t, t, t + M(rng.uniform(2, 10)),
                           f"Report called to {self._prov_name(e.unit_nurse)}. Transported to {e.unit} {e.room}-{e.bed} "
                           f"on monitor{' with isolation precautions' if e.isolation else ''}.")
            day = local(e.departure_at + H(6)).date()
            pt = datetime(day.year, day.month, day.day, rng.randint(8, 11), rng.randrange(60), tzinfo=TZ).astimezone(UTC)
            if pt < horizon and pt > e.departure_at + H(4):
                self._add_note(e, "Progress Notes", "11506-3", e.admitting_prov, pt, pt, pt + M(rng.uniform(30, 240)),
                               N.progress(e, pat, scen, self._vital_at(e, pt), self._results_at(e, pt)))

    # ---- ADT and ED tracking events
    def _adt(self, e, pat, rng):
        """Collect every ADT moment, sort by effective time, then apply location and
        class changes in that order so each event carries the state at its time."""
        fac, ed = e.facility, e._ed_dept
        items, ed_only = [], []
        merge_at = getattr(e, "_merge_at", None)

        def pid(t):
            return e.registered_pat_id if (e.registered_pat_id and t < merge_at) else None

        def add(kind, at, upd=None, recorded=None, ed_name=None, ed_at=None, user=None, **details):
            items.append((at, len(items), kind, upd or {}, recorded, ed_name, ed_at, user, details))

        def ed_ev(name, at, user=None):
            ed_only.append((name, at, user, ""))

        a_at = e.arrival_at
        rec_delay = min(rng.uniform(0.3, 3), max(0.05, (e.triage_at - a_at) / M(1) - 0.1))
        add("arrival", a_at, recorded=getattr(e, "_arrival_recorded", None) or a_at + M(rec_delay),
            ed_name="Patient Arrived in ED",
            ed_at=e.arrival_at, arrival_mode=e.arrival_mode)
        add("registration", e.arrival_at + M(rng.uniform(10, 60)))
        ed_ev("Triage Started", max(e.arrival_at + M(0.5), e.triage_at - M(rng.uniform(1, 4))))
        add("triage", e.triage_at, ed_name="Triage Completed", ed_at=e.triage_at, esi=e.esi, chief_complaint=e.chief_complaint)
        if e.registered_pat_id:
            add("merge", merge_at, surviving=e.pat_id, retired=e.registered_pat_id)
        for al in self.w.allergies:
            if al.added_csn == e.csn:
                add("allergy_update", al.noted_at, allergen=al.allergen, reaction=al.reaction)
        if e.roomed_at:
            add("roomed", e.roomed_at, {"room": e.ed_bay, "bed": "" if e.ed_bay.startswith("H") else "A"},
                ed_name="Patient Roomed in ED", ed_at=e.roomed_at)
        if e.provider_at:
            add("provider", e.provider_at, user=e.ed_attending, ed_name="Provider Assigned", ed_at=e.provider_at,
                provider=e.ed_attending, resident=e.ed_resident)
        if getattr(e, "_iso_at", None) and e._iso_at >= e.arrival_at:
            add("isolation_update", e._iso_at, infection=e._iso_name, isolation=e.isolation)
        admit = e.disposition.startswith("ADMIT")
        if "F11" in e.flags:
            t = e._false_discharge_at
            add("discharge", t, ed_name="Disposition Selected: Discharge", ed_at=t, user=e.ed_attending, disposition="Home",
                discharge_disposition_code="01")
            add("cancel_discharge", t + M(rng.uniform(10, 30)), reason="Disposition changed after repeat labs")
        label = {"ADMIT_IP": "Admit", "ADMIT_OBS": "Observation", "DISCHARGE": "Discharge", "TRANSFER": "Transfer"}.get(e.disposition)
        if e.decision_at and label:
            ed_ev(f"Disposition Selected: {label}", e.decision_at, e.ed_attending)
        if admit:
            first = getattr(e, "_first_bed", None)
            add("pending_admit", e.bed_request_at, ed_name="Bed Requested", ed_at=e.bed_request_at,
                level_of_care=e.level_of_care, service=e.admit_service, pending_unit=first[0] if first else e.unit,
                admitting=e.admitting_prov)
            if e.class_change_at:
                add("class_change", e.class_change_at,
                    {"class": "OBS" if e.disposition == "ADMIT_OBS" else "I", "attending": e.admitting_prov},
                    user=e.admitting_prov, service=e.admit_service, admitting=e.admitting_prov)
            if hasattr(e, "_code_status_at"):
                add("code_status_update", e._code_status_at, user=e.admitting_prov, code_status=e._code_status)
            if first:
                unit, room, bed, at, freed = first
                add("bed_assigned", at, ed_name="Bed Assigned", ed_at=at, pending_unit=unit, pending_room=room, pending_bed=bed)
                add("bed_unassigned", freed, reason=e._reassign_reason)
                self.fault("F15", e.csn, "all", freed, f"Bed {unit} {room}-{bed} released: {e._reassign_reason}",
                           "Receiving unit changes; handoff recipients change")
            if e.bed_assigned_at:
                add("bed_assigned", e.bed_assigned_at, ed_name="Bed Assigned", ed_at=e.bed_assigned_at,
                    pending_unit=e.unit, pending_room=e.room, pending_bed=e.bed)
                ready = e.bed_assigned_at + M(rng.uniform(5, 30))
                if e.departure_at:
                    ready = min(ready, e.departure_at - M(rng.uniform(1, 4)))
                ed_ev("Ready for Transport", max(ready, e.bed_assigned_at))
            if e.departure_at:
                unit_state = {"unit": e.unit, "room": e.room, "bed": e.bed, "dept": e.unit_dept_id}
                if "F10" in e.flags:
                    t = e.departure_at - M(40)
                    add("transfer", t, {**unit_state, "room": e._wrong_bed[0], "bed": e._wrong_bed[1]}, user=e.ed_nurse)
                    add("cancel_transfer", t + M(8), {"unit": "ED", "room": e.ed_bay, "bed": "A", "dept": ed},
                        reason="Transferred to wrong bed in error")
                    self.fault("F10", e.csn, "adt", t + M(8), "A02 to the wrong bed, canceled, then the correct A02",
                               "Bed history shows the reversal; the handoff shows the final bed")
                recorded = None
                if "F07" in e.flags:
                    recorded = e.departure_at + H(rng.uniform(1, 4))
                    self.fault("F07", e.csn, "adt", e.departure_at, "Transfer documented 1 to 4 h after it happened",
                               "Use EVN-6 (effective time); boarding metric uses the effective time")
                add("transfer", e.departure_at, unit_state, recorded=recorded, user=e.ed_nurse,
                    ed_name="Patient Departed from ED", ed_at=e.departure_at)
                if getattr(e, "_conversion_at", None):
                    add("class_change", e._conversion_at, {"class": "I"}, user=e.admitting_prov, service="Hospital Medicine",
                        conversion="Observation to Inpatient")
        elif e.departure_at:
            dispo = {"DISCHARGE": ("Home", "01", "Patient Discharged"), "LWBS": ("Left without being seen", "07", "Left Without Being Seen"),
                     "AMA": ("Against medical advice", "07", "Left Against Medical Advice"),
                     "LBTC": ("Left before treatment complete", "07", "Left Before Treatment Complete"),
                     "TRANSFER": ("Transfer to another facility", "02", "Patient Transferred")}[e.disposition]
            add("discharge", e.departure_at, ed_name=dispo[2], ed_at=e.departure_at, disposition=dispo[0],
                discharge_disposition_code=dispo[1])

        state = {"class": "E", "unit": "ED", "room": "WR", "bed": "", "dept": ed, "attending": e.ed_attending}
        tracked = []
        for seq, (at, _, kind, upd, recorded, ed_name, ed_at, user, details) in enumerate(sorted(items, key=lambda x: (x[0], x[1])), 1):
            prior = {k: state[k] for k in ("unit", "room", "bed", "class")}
            state.update(upd)
            ev = AdtEvent(self.ids.adt_event(), e.csn, pid(at) or e.pat_id, seq, kind, at, recorded or at, state["class"], fac,
                          state["dept"], state["unit"], state["room"], state["bed"], prior, user or e.ed_nurse,
                          state["attending"], details)
            self.w.adt.append(ev)
            if ed_name:
                tracked.append((ed_name, ed_at, user, ev))
            if kind == "transfer" and not upd.get("room") == getattr(e, "_wrong_bed", (None,))[0]:
                for b in self.w.bed_requests:
                    if b.csn == e.csn and not b.deleted:
                        b.linked_event_id = ev.event_id
        ed_only = [(n, t, us, None) for n, t, us, _ in ed_only]
        for line, (name, at, user, ev) in enumerate(sorted(tracked + ed_only, key=lambda x: x[1]), 1):
            rec = at + M(rng.uniform(0, 3))
            if ev is not None:
                rec = max(rec, ev.recorded_at)   # a tracking event is filed with its ADT event
            self.w.ed_events.append(EdEvent(e.csn, line, name, at, rec, user or e.ed_nurse, ev.event_id if ev else ""))

    # ------------------------------------------------------------------ remaining truth faults
    def _post_faults(self):
        rng = random.Random(self.cfg.seed + 19)
        self._enc = {e.csn: e for e in self.encs}
        self._f17_keys = set()
        for e in [x for x in self.encs if "F09" in x.flags]:
            wrong = self._new_patient(rng.choice(self.pop), rng)
            c = Encounter(self.ids.csn(), self.ids.har(), wrong.pat_id, e.facility, e.scenario, e.scenario_label, "CANCELED",
                          e.arrival_mode, e.arrival_at, e.esi, e.chief_complaint, [], ed_attending=e.ed_attending,
                          ed_nurse=e.ed_nurse, flags=["F09"])
            ed = self.dept[(e.facility, "ED")].dept_id
            for seq, (kind, at) in enumerate((("arrival", e.arrival_at + M(1)), ("cancel_admit", e._cancel_at)), 1):
                self.w.adt.append(AdtEvent(self.ids.adt_event(), c.csn, wrong.pat_id, seq, kind, at, at, "E", e.facility, ed,
                                           "ED", "WR", "", {}, e.ed_nurse, e.ed_attending,
                                           {"reason": "Registered to the wrong patient"} if kind == "cancel_admit" else {}))
            self.encs.append(c)
            self.fault("F09", c.csn, "adt", e._cancel_at, f"Wrong patient registered, canceled; real visit is CSN {e.csn}",
                       "Encounter marked canceled; boarding list excludes it")
        admits = [e for e in self.encs if e.disposition.startswith("ADMIT") and e.departure_at and e.departure_at < self.end]
        # F17: critical result shortly before or after transfer, after a handoff would be signed.
        for e in rng.sample(admits, min(8, len(admits))):
            pat, scen = self.pats[e.pat_id], get(e.scenario)
            code, val = rng.choice([("K", rng.uniform(6.3, 6.9)), ("LACT", rng.uniform(4.2, 6.5)), ("HGB", rng.uniform(5.4, 5.9)),
                                    ("K", rng.uniform(2.3, 2.7))])
            resulted = e.departure_at + M(rng.uniform(-30, 90))
            panel = {"K": "BMP", "LACT": "LACT", "HGB": "CBC"}[code]
            o = self._add_order(e, "Lab", PANELS[panel][1], PANELS[panel][0], "", resulted - M(rng.uniform(50, 80)),
                                e.admitting_prov, "STAT")
            o.accession = self.ids.accession()
            o.collected_at = resulted - M(rng.uniform(30, 50))
            rows = self._panel_results(e, pat, scen, o, panel, o.collected_at, resulted, {code: (val, val)})
            self.w.results.extend(rows)
            self._critical_response(e, pat, scen, code, val, resulted, rng)
            self._f17_keys.add((e.csn, code, resulted))
            e.flags.append("F17")
            self.fault("F17", e.csn, "results", resulted, f"Critical {LABS[code][1]} resulted near ED departure",
                       "Changed since signed badge; completeness rule forces acknowledgement")
        # Every critical lab value on an admitted patient after the decision is acted on.
        handled = {(r.csn, r.lab) for r in self.w.results if "Repeat requested" in r.comment or "Post transfusion" in r.comment}
        for r in list(self.w.results):
            e = self._enc.get(r.csn)
            if (r.flag in ("HH", "LL") and (r.numeric is not None or r.lab == "BCX") and e and e.disposition.startswith("ADMIT") and e.decision_at
                    and r.resulted_at >= e.decision_at and r.lab in ("K", "LACT", "HGB", "GLU", "NA", "INR", "BCX")
                    and (r.csn, r.lab, r.resulted_at) not in self._f17_keys and (r.csn, r.lab, r.resulted_at) not in handled):
                handled.add((r.csn, r.lab, r.resulted_at))
                self._critical_response(e, self.pats[e.pat_id], get(e.scenario), r.lab, r.numeric, r.resulted_at, rng)
        # F19: corrected hemoglobin 30 min after final.
        hgb = [r for r in self.w.results if r.lab == "HGB" and r.status == "F" and r.resulted_at < self.end - H(2)
               and self._enc[r.csn].scenario != "ugib"]      # a correction must not read as a bleed or a transfusion
        for r in rng.sample(hgb, min(4, len(hgb))):
            x = round(r.numeric + rng.choice([-1.5, 1.5]), 1)
            self.w.results.append(Result(r.order_id, r.csn, r.line, r.lab, r.loinc, r.name, str(x), x, r.units, r.ref_low,
                                         r.ref_high, self._flag("HGB", x), "C", r.observed_at, r.resulted_at + M(30),
                                         "Corrected result: specimen rerun, previously reported value in error."))
            self.fault("F19", r.csn, "results", r.resulted_at + M(30), f"Hemoglobin corrected on order {r.order_id}",
                       "Latest status wins; audit trail keeps both")
        for e in self.encs:
            if e.disposition in ("LWBS", "AMA", "LBTC"):
                self.fault("F31", e.csn, "adt", e.departure_at, f"{e.disposition} with no admission",
                           "Excluded from the boarding list; no handoff")
            if e.disposition.startswith("ADMIT") and e.departure_at:
                pend = [o for o in self.w.orders if o.csn == e.csn and o.order_type in ("Lab", "Imaging")
                        and not any(r.order_id == o.order_id and r.status in ("F", "C") and r.resulted_at <= e.departure_at
                                    for r in self.w.results)]
                if pend:
                    self.fault("F18", e.csn, "results", e.departure_at, f"{len(pend)} orders pending at ED departure",
                               "Handoff pending list with owner; results arrive later")
        for fac, prof in self.cfg.site_profile.items():
            self.fault("F13", "", "adt", None, f"{fac} renders the admit class change as {prof['class_change']}",
                       "Both paths yield the same encounter state")

    def _critical_response(self, e, pat, scen, code, val, resulted, rng):
        """What the team does after a critical call: treat and recheck, or recheck a hemolyzed sample."""
        prov = e.admitting_prov
        act = resulted + M(rng.uniform(15, 40))
        if code == "BCX":     # positive blood culture: repeat cultures, confirm gram negative coverage
            for _ in range(2):
                o = self._add_order(e, "Lab", PANELS["BCX"][1], PANELS["BCX"][0], "", act, prov, "STAT")
                o.accession = self.ids.accession()
                o.collected_at = act + M(rng.uniform(10, 40))
            return
        if code in ("GLU", "NA", "INR"):
            panel = {"GLU": "POCGLU", "NA": "BMP", "INR": "COAG"}[code]
            self._timed_lab(e, pat, scen, panel, act, None, prov, "STAT", "Repeat after critical value.")
            return
        if code == "K" and val > 6 and e.scenario in ("aki", "dka") and "F17" not in e.flags:
            for med in (INSULIN_PUSH, DEXTROSE_50):
                mo = MedOrder(self.ids.order(), e.csn, med.rxcui, med.name, med.dose, med.unit, med.route, "ONCE", "bolus",
                              act, act + M(rng.uniform(3, 8)), prov, "", "Hyperkalemia protocol")
                self.w.med_orders.append(mo)
                self.w.mar.append(MarAdmin(mo.order_med_id, e.csn, 1, "Given", mo.verified_at + M(rng.uniform(3, 12)), act,
                                           med.dose, med.unit, med.route, "", e.ed_nurse if (not e.departure_at or act < e.departure_at)
                                           else e.unit_nurse, e._ed_dept if (not e.departure_at or act < e.departure_at) else e.unit_dept_id))
            self._timed_lab(e, pat, scen, "BMP", act + H(rng.uniform(1.5, 2.5)), {"K": (5.0, 5.7)}, prov, "STAT")
            return
        if code == "K" and val > 6:
            self._timed_lab(e, pat, scen, "BMP", act, {"K": (4.3, 5.1)}, prov, "STAT",
                            "Repeat requested: prior specimen hemolyzed.")
            return
        if code == "K":
            med = Med("potassium chloride 2 mEq/mL injection", "204520", "40", "mEq", "IV")
            recheck = {"K": (3.1, 3.6)}
        elif code == "HGB":
            self._add_order(e, "Blood Product", "BLD0001", "Transfuse packed red blood cells, 1 unit", "", act, prov)
            self._timed_lab(e, pat, scen, "CBC", act + H(rng.uniform(1.5, 2.5)), {"HGB": (6.9, 7.9)}, prov, "STAT",
                            "Post transfusion.")
            return
        else:
            med = Med("sodium chloride 0.9 % bolus", "313002", "1000", "mL", "IV", kind="infusion", rate="999 mL/hr")
            recheck = {"LACT": (2.0, 3.3)}
        mo = MedOrder(self.ids.order(), e.csn, med.rxcui, med.name, med.dose, med.unit, med.route, "ONCE", med.kind, act,
                      act + M(rng.uniform(3, 10)), prov, med.rate, "Ordered after critical value call")
        self.w.med_orders.append(mo)
        given = mo.verified_at + M(rng.uniform(5, 20))
        in_ed = not e.departure_at or given < e.departure_at
        self.w.mar.append(MarAdmin(mo.order_med_id, e.csn, 1, "New Bag" if med.kind == "infusion" else "Given", given, act,
                                   med.dose, med.unit, med.route, med.rate, e.ed_nurse if in_ed else e.unit_nurse,
                                   e._ed_dept if in_ed else e.unit_dept_id))
        self._timed_lab(e, pat, scen, "BMP" if code == "K" else "LACT", given + H(rng.uniform(2, 3)), recheck, prov, "STAT")

    def _render_plan(self):
        """Faults injected by renderers, chosen here so every renderer agrees (spec section 11)."""
        rng = random.Random(self.cfg.seed + 23)
        csns = [e.csn for e in self.encs if e.disposition != "CANCELED"]
        moved = {a.csn for a in self.w.adt if a.kind == "transfer"}
        admits = [e.csn for e in self.encs if e.disposition.startswith("ADMIT") and e.csn in moved]
        final = {r.order_id for r in self.w.results if r.status == "F"}
        cbc = [o.order_id for o in self.w.orders if o.name.startswith("CBC") and o.order_id in final]
        vit = [(v.csn, v.line) for v in self.w.vitals]
        long_notes = [n.note_id for n in self.w.notes if n.note_type in ("ED Provider Notes", "H&P")]
        self.w.render_plan = {
            "F01_a08_flood": sorted(rng.sample(csns, round(0.15 * len(csns)))),
            "F02_retransmit_rate": 0.03,
            "F03_content_duplicate_rate": 0.02,
            "F04_empty_a08": sorted(rng.sample(csns, 3)),
            "F05_out_of_order": sorted(rng.sample(admits, 3)),
            "F06_oru_before_adt_rate": 0.05,
            "F08_stale_location_replay": sorted(rng.sample(admits, 3)),
            "F24_unvalidated_device_values": sorted(rng.sample(vit, 20)),
            "F25_offset_mix": {"MGH": "-0500 style offsets", "LKS": "no offset", "inconsistent_messages": 1},
            "F26_z_segments": {"MGH": ["ZPV", "ZPD"], "LKS": [], "unknown_segment_messages": 1},
            "F27_encoding_quirks": sorted(rng.sample(long_notes, 10)),
            "F28_mixed_status_panel": sorted(rng.sample(cbc, 6)),
            "F30_class_string_variants": "all",
            "F32_clarity_lag": "Clarity snapshot as of the prior local midnight",
            "seed": self.cfg.seed + 29,
        }
        # F24: monitor values that a nurse never validated; flowsheets (Clarity) must leave them out.
        unvalidated = set(self.w.render_plan["F24_unvalidated_device_values"])
        for v in self.w.vitals:
            if (v.csn, v.line) in unvalidated:
                v.validated = False

    # ------------------------------------------------------------------ clip to the window
    def _clip(self):
        end = self.end
        w = self.w
        w.adt = [a for a in w.adt if a.recorded_at <= end]
        w.ed_events = [x for x in w.ed_events if x.recorded_at <= end]
        w.results = [r for r in w.results if r.resulted_at <= end]
        w.mar = [m for m in w.mar if m.taken_at <= end]
        w.med_orders = [m for m in w.med_orders if m.ordered_at <= end]
        w.vitals = [v for v in w.vitals if v.entered_at <= end]
        w.code_status = [c for c in w.code_status if c.activated_at <= end]
        w.infections = [i for i in w.infections if i.added_at <= end]
        w.allergies = [a for a in w.allergies if a.noted_at <= end]
        w.faults = [f for f in w.faults if not f["event_time"] or f["event_time"] <= zfmt(end)]
        w.orders = [o for o in w.orders if o.ordered_at <= end]
        final = {}
        for r in w.results:
            final.setdefault(r.order_id, set()).add(r.status)
        for o in w.orders:
            if o.collected_at and o.collected_at > end:
                o.collected_at = None
            st = final.get(o.order_id, set())
            o.status = "Resulted" if st & {"F", "C"} else "Preliminary" if "P" in st else \
                "In Progress" if o.collected_at else "Ordered"
            if o.order_type in ("ADT", "Consult", "Code Status"):
                o.status = "Completed"
        notes = []
        for n in w.notes:
            if n.created_at > end:
                continue
            if n.signed_at and n.signed_at > end:
                n.signed_at = None
            n.versions = [v for v in n.versions if v[1] <= end]
            notes.append(n)
        w.notes = notes
        reqs = []
        for b in w.bed_requests:
            if b.requested_at > end:
                continue
            if b.assigned_at and b.assigned_at > end:
                b.assigned_at, b.room, b.bed = None, "", ""
            if b.completed_at and b.completed_at > end:
                b.completed_at = None
            reqs.append(b)
        w.bed_requests = reqs
        for e in self.encs:
            for f in ("triage_at", "roomed_at", "provider_at", "decision_at", "bed_request_at", "bed_assigned_at",
                      "departure_at", "class_change_at"):
                if getattr(e, f) and getattr(e, f) > end:
                    setattr(e, f, None)
            if e.disposition == "CANCELED":
                e.state_at_end = "canceled"
            elif e.departure_at:
                e.state_at_end = {"DISCHARGE": "discharged", "TRANSFER": "transferred"}.get(e.disposition) or \
                    ("inpatient" if e.disposition.startswith("ADMIT") else "left")
            elif e.decision_at and e.disposition.startswith("ADMIT"):
                e.state_at_end = "boarding"
            else:
                e.state_at_end = "in_ed"
            if not e.bed_assigned_at:
                e.room, e.bed = "", ""
            if not e.decision_at:
                for f in ("level_of_care", "unit", "unit_dept_id", "admitting_prov", "admit_service"):
                    setattr(e, f, "")

    def _census(self):
        t = self.start
        while t < self.end:
            for d in self.w.departments:
                if d.level == "ED":
                    n = sum(1 for e in self.encs if e.facility == d.facility and e.arrival_at <= t
                            and (e.departure_at is None or e.departure_at > t) and e.disposition != "CANCELED")
                    b = sum(1 for e in self.encs if e.facility == d.facility and e.decision_at and e.decision_at <= t
                            and e.disposition.startswith("ADMIT") and (e.departure_at is None or e.departure_at > t))
                    self.w.census.append({"at": t, "facility": d.facility, "unit": d.code, "beds": d.beds, "occupied": n,
                                          "boarders": b})
                else:
                    n = sum(1 for (f, un, _, _, s, en, _) in self.bed_intervals if f == d.facility and un == d.code and s <= t < en)
                    self.w.census.append({"at": t, "facility": d.facility, "unit": d.code, "beds": d.beds,
                                          "occupied": min(n, d.beds), "boarders": None})
            t += H(1)
