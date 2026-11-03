"""Deterministic identifiers shaped like Epic's observed formats (spec section 2.3).

All counters start from fixed bases, so the same seed always yields the same IDs.
"""
import itertools
import random
import uuid


def luhn_npi(base9: str) -> str:
    """NPI check digit: Luhn over the 9 digits prefixed with 80840 (CMS rule)."""
    digits = [int(d) for d in "80840" + base9]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return base9 + str((10 - total % 10) % 10)


class Ids:
    def __init__(self, seed: int):
        self._rng = random.Random(seed ^ 0x1D5)
        self._csn = itertools.count(40011200000 + self._rng.randrange(10000))
        self._har = itertools.count(3100450000 + self._rng.randrange(1000))
        self._order = itertools.count(700120000 + self._rng.randrange(1000))
        self._accession = itertools.count(26300000 + self._rng.randrange(1000))
        self._note = itertools.count(900330000 + self._rng.randrange(1000))
        self._adt_event = itertools.count(510200000 + self._rng.randrange(1000))
        self._pend = itertools.count(88100000)
        self._misc = itertools.count(1000000)
        self._used_mrn: set = set()

    def csn(self) -> str:
        return str(next(self._csn))

    def har(self) -> str:
        return str(next(self._har))

    def order(self) -> str:
        return str(next(self._order))

    def accession(self) -> str:
        return str(next(self._accession))

    def note(self) -> str:
        return str(next(self._note))

    def adt_event(self) -> str:
        return str(next(self._adt_event))

    def pend(self) -> str:
        return str(next(self._pend))

    def misc(self) -> str:
        return str(next(self._misc))

    def patient(self):
        """Returns (PAT_ID, MRN, EPI)."""
        while True:
            mrn = str(40000000 + self._rng.randrange(9_999_999))
            if mrn not in self._used_mrn:
                self._used_mrn.add(mrn)
                break
        n = self._rng.randrange(1_000_000, 9_999_999)
        return f"Z{n}", mrn, f"E{self._rng.randrange(1_000_000, 9_999_999)}"

    def uuid(self) -> str:
        return str(uuid.UUID(int=self._rng.getrandbits(128), version=4))
