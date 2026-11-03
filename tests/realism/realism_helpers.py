"""Shared view of a world plus the small statistics the realism checks need.

Tolerance policy (used by every module here): the spec intervals in section 12
were written as targets on expected values. The generated world is one sample,
so each proportion gets the spec interval widened by one binomial standard
deviation at its sample size (about 4.5 points for the 120 admits, under 2
points for the 700 visits), and each median gets the spec's 15 percent except
for boarding, where 113 admits with a lognormal sigma near 0.9 move the median
about 10 percent per standard error, so 30 percent is used (see README).
"""
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "synthetic"))
sys.path.insert(0, str(ROOT / "synthetic" / "tools"))

from edip_synth.config import TZ  # noqa: E402
from edip_synth.scenarios import get as scenario  # noqa: E402
from truth_io import window  # noqa: E402

REAL = bool(os.environ.get("EDIP_WORLD"))
N_ENCOUNTERS, N_ADMITS = 700, 120
# Approved design: 700 encounters, 120 admitted (17 percent). The spec's section 12
# ranges that depend on the 30 percent admit rate are restated where used.
ADMIT_RATE_RANGE = (15.0, 19.0)
CODE_VERIFICATION = ROOT / "synthetic" / "tools" / "code_verification.json"


def gap(reason, when=True):
    """Strict expected failure naming the concrete generator gap."""
    # EDIP_GAPS_NONSTRICT=1 lists fixed gaps as XPASS instead of failing, which helps when tuning the generator.
    strict = os.environ.get("EDIP_GAPS_NONSTRICT") != "1"
    return pytest.mark.xfail(when, strict=strict, reason="generator gap: " + reason)


def minutes(a, b):
    return (b - a) / timedelta(minutes=1)


def local(dt):
    return dt.astimezone(TZ)


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def median(xs):
    return statistics.median(xs) if xs else None


def pct(k, n):
    return 100.0 * k / n if n else float("nan")


def binom_sd_pts(p_pct, n):
    """Standard deviation of a sample percentage, in percentage points."""
    p = p_pct / 100.0
    return 100.0 * math.sqrt(p * (1 - p) / n)


# Per world checks widen spec intervals by SD_K binomial standard deviations: with about 70 checks run on
# several seeds, one sd would fail a perfect generator about 30 percent of the time per check, while 2.5 sd
# keeps false alarms near 1 percent. Metrics dominated by the tails of 120 admits are pooled across seeds.
SD_K = 2.5


def widened(lo, hi, n):
    """Spec interval (percent) widened by SD_K binomial sd at its center for n samples."""
    sd = SD_K * binom_sd_pts((lo + hi) / 2, n)
    return lo - sd, hi + sd


def within_pct(x, target, tol_pct):
    return abs(x - target) <= target * tol_pct / 100.0


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def pearson(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0.0


def spearman(a, b):
    return pearson(_ranks(a), _ranks(b))


def _group(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[getattr(r, key)].append(r)
    return out


class View:
    """Indexes over one World, identical whether generated in memory or loaded from JSONL."""

    def __init__(self, world, real: bool):
        self.w = world
        self.real = real
        self.start, self.end = window(world)
        self.all_encounters = list(world.encounters)
        self.encounters = [e for e in world.encounters if e.disposition != "CANCELED"]
        self.enc = {e.csn: e for e in world.encounters}
        self.admits = [e for e in self.encounters if e.disposition.startswith("ADMIT")]
        self.patients = {p.pat_id: p for p in world.patients}
        self.providers = {p.prov_id: p for p in world.providers}
        self.departments = {d.dept_id: d for d in world.departments}
        self.ed_dept = {d.facility: d.dept_id for d in world.departments if d.level == "ED"}
        self._vitals = _group(world.vitals, "csn")
        self._results = _group(world.results, "csn")
        self._orders = _group(world.orders, "csn")
        self._meds = _group(world.med_orders, "csn")
        self._mar = _group(world.mar, "order_med_id")
        self._mar_csn = _group(world.mar, "csn")
        self._notes = _group(world.notes, "csn")
        self._adt = _group(world.adt, "csn")
        self._ed_events = _group(world.ed_events, "csn")
        self._allergies = _group(world.allergies, "pat_id")
        self.code_status = {c.csn: c for c in world.code_status}
        self.census = defaultdict(dict)
        for c in world.census:
            self.census[(c["facility"], c["unit"])][c["at"]] = c["occupied"] / c["beds"]
        self.flags = {f["fault_id"] for f in world.faults}

    # ---- per encounter accessors (sorted)
    def vitals(self, csn):
        return sorted(self._vitals.get(csn, []), key=lambda v: v.taken_at)

    def results(self, csn):
        return sorted(self._results.get(csn, []), key=lambda r: (r.observed_at, r.resulted_at))

    def orders(self, csn):
        return sorted(self._orders.get(csn, []), key=lambda o: o.ordered_at)

    def med_orders(self, csn):
        return sorted(self._meds.get(csn, []), key=lambda m: m.ordered_at)

    def mar(self, order_med_id):
        return sorted(self._mar.get(order_med_id, []), key=lambda m: m.taken_at)

    def mar_for(self, csn):
        return sorted(self._mar_csn.get(csn, []), key=lambda m: m.taken_at)

    def notes(self, csn):
        return sorted(self._notes.get(csn, []), key=lambda n: n.created_at)

    def adt(self, csn):
        return sorted(self._adt.get(csn, []), key=lambda a: a.seq)

    def ed_events(self, csn):
        return sorted(self._ed_events.get(csn, []), key=lambda x: x.line)

    def allergies(self, pat_id):
        return self._allergies.get(pat_id, [])

    def scenario(self, e):
        return scenario(e.scenario)

    def age(self, e):
        b = self.patients[e.pat_id].birth_date
        a = e.arrival_at
        return a.year - int(b[:4]) - ((a.month, a.day) < (int(b[5:7]), int(b[8:10])))

    def occupancy_at(self, facility, unit, t):
        """Share of the destination unit's beds occupied in the census hour containing t."""
        return self.census.get((facility, unit), {}).get(t.replace(minute=0, second=0, microsecond=0))

    def first_result(self, csn, lab):
        rows = [r for r in self.results(csn) if r.lab == lab and r.status == "F" and r.numeric is not None]
        return rows[0] if rows else None

    def prov_name(self, prov_id):
        p = self.providers.get(prov_id)
        return p.display if p else prov_id
