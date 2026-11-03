"""Load the Synthea population (history only; spec section 9) or, when no Synthea
output is available (CI, quick runs), build a small stand in population.

Synthea quirks handled here:
  * acute conditions (sepsis, MI, fractures) are often left open with no STOP
    date, so only chronic disorders are kept on the problem list;
  * dental, social and screening findings are dropped;
  * names carry trailing digits ("Kiyoko456"); they are stripped.
"""
import csv
import random
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REF_DATE = date(2026, 11, 1)

# SNOMED codes treated as acute episodes, never as chronic problems.
_ACUTE = {"91302008", "401314000", "401303003", "22298006", "1201005", "70995007", "359817006", "5913000",
          "263225007", "16114001", "65966004", "444814009", "10509002", "195662009", "233604007", "68566005",
          "75498004", "36971009", "43878008", "38822007", "62106007", "403190006", "284551006", "370247008"}
_DROP_WORDS = ("teeth", "molar", "gingiv", "dental", "periodont", "scoliosis", "pregnan", "sprain", "laceration",
               "fracture", "burn", "concussion", "bite", "abrasion", "viral", "acute", "streptococcal", "otitis",
               "sinusitis", "contact dermatitis")
_CHRONIC_ICD10 = {
    "59621000": "I10", "44054006": "E11.9", "88805009": "I50.9", "49436004": "I48.91", "433144002": "N18.30",
    "431857002": "N18.4", "431855005": "N18.1", "431856006": "N18.2", "46177005": "N18.6", "26929004": "G30.9",
    "195967001": "J45.909", "87433001": "J43.9", "185086009": "J44.9", "55822004": "E78.5", "414545008": "I25.10",
    "271737000": "D64.9", "64859006": "M81.0", "239873007": "M17.9", "239872002": "M16.9", "78275009": "G47.33",
    "128613002": "G40.909", "84757009": "G40.909", "83664006": "E03.9", "60573004": "I35.0", "399211009": "I25.2",
    "302870006": "E78.1", "237602007": "E88.81", "127013003": "E11.29", "368581000119106": "E11.40",
    "6525002": "F19.20", "124171000119105": "G43.719", "254837009": "C50.919", "126906006": "D40.0",
    "197927001": "N39.0", "714628002": "R73.03",
}


@dataclass
class Person:
    synthea_id: str
    first: str
    middle: str
    last: str
    birth_date: date
    sex: str                      # F or M
    race: str
    ethnicity: str
    marital: str
    address: str
    city: str
    zip: str
    ssn: str
    problems: list = field(default_factory=list)    # dicts: snomed, icd10, display, onset
    home_meds: list = field(default_factory=list)   # dicts: rxcui, display, start
    drug_allergies: list = field(default_factory=list)
    prior_ed_visits: int = 0
    prior_admissions: int = 0

    @property
    def age(self) -> int:
        b = self.birth_date
        return REF_DATE.year - b.year - ((REF_DATE.month, REF_DATE.day) < (b.month, b.day))

    @property
    def snomed(self) -> set:
        return {p["snomed"] for p in self.problems}


def _clean(name: str) -> str:
    return re.sub(r"\d+$", "", name or "").strip()


def _keep_condition(code: str, desc: str) -> bool:
    d = desc.lower()
    if code in _ACUTE or "(disorder)" not in d and code not in ("714628002", "399211009"):
        return False
    return not any(w in d for w in _DROP_WORDS)


def load(csv_dir: Path) -> list:
    csv_dir = Path(csv_dir)
    people = {}
    for r in csv.DictReader(open(csv_dir / "patients.csv", newline="")):
        if r["DEATHDATE"]:
            continue
        ssn = "9" + r["SSN"][1:] if r["SSN"] else ""     # force the 900 series (synthetic range)
        people[r["Id"]] = Person(r["Id"], _clean(r["FIRST"]), _clean(r["MIDDLE"]), _clean(r["LAST"]),
                                 date.fromisoformat(r["BIRTHDATE"]), r["GENDER"], r["RACE"], r["ETHNICITY"],
                                 r["MARITAL"] or "S", r["ADDRESS"], r["CITY"], r["ZIP"], ssn)
    for r in csv.DictReader(open(csv_dir / "conditions.csv", newline="")):
        p = people.get(r["PATIENT"])
        if p and not r["STOP"] and _keep_condition(r["CODE"], r["DESCRIPTION"]):
            if r["CODE"] not in p.snomed:
                p.problems.append({"snomed": r["CODE"], "icd10": _CHRONIC_ICD10.get(r["CODE"]),
                                   "display": r["DESCRIPTION"].replace(" (disorder)", "").replace(" (finding)", "")
                                   .replace(" (situation)", ""), "onset": r["START"][:10]})
    for r in csv.DictReader(open(csv_dir / "medications.csv", newline="")):
        p = people.get(r["PATIENT"])
        if p and not r["STOP"] and len(p.home_meds) < 12 and r["CODE"] not in {m["rxcui"] for m in p.home_meds}:
            p.home_meds.append({"rxcui": r["CODE"], "display": r["DESCRIPTION"], "start": r["START"][:10]})
    for r in csv.DictReader(open(csv_dir / "allergies.csv", newline="")):
        p = people.get(r["PATIENT"])
        if p and not r["STOP"] and r["CATEGORY"] == "medication":
            p.drug_allergies.append({"code": r["CODE"], "system": r["SYSTEM"], "display": r["DESCRIPTION"],
                                     "reaction": r["DESCRIPTION1"], "severity": r["SEVERITY1"]})
    for r in csv.DictReader(open(csv_dir / "encounters.csv", newline="")):
        p = people.get(r["PATIENT"])
        if p and r["START"][:4] >= "2024":
            if r["ENCOUNTERCLASS"] == "emergency":
                p.prior_ed_visits += 1
            elif r["ENCOUNTERCLASS"] == "inpatient":
                p.prior_admissions += 1
    return [p for p in people.values() if p.age >= 18]


_FIRST_F = ["Maria", "Linda", "Patricia", "Susan", "Dorothy", "Jennifer", "Ana", "Mei", "Gloria", "Ruth", "Keisha", "Rosa"]
_FIRST_M = ["James", "Robert", "John", "Michael", "William", "David", "Jose", "Wei", "Thomas", "Carlos", "Darnell", "Paul"]
_LAST = ["Halvorsen", "Mbeki", "Ortega", "Kaminski", "Byrne", "Tran", "Silva", "Novak", "Gallagher", "Reyes", "Petrov", "Osei"]
_STAND_IN_PROBLEMS = [("59621000", "Essential hypertension"), ("44054006", "Diabetes mellitus type 2"),
                      ("55822004", "Hyperlipidemia"), ("88805009", "Chronic congestive heart failure"),
                      ("185086009", "Chronic obstructive bronchitis"), ("49436004", "Atrial fibrillation"),
                      ("433144002", "Chronic kidney disease stage 3"), ("26929004", "Alzheimer's disease"),
                      ("64859006", "Osteoporosis"), ("414545008", "Ischemic heart disease")]


def stand_in(n: int, seed: int) -> list:
    """Small Synthea shaped population for tests and runs without Synthea."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        sex = rng.choice("FM")
        age = rng.choices([rng.randint(18, 44), rng.randint(45, 64), rng.randint(65, 74), rng.randint(75, 95)],
                          [34, 26, 13, 27])[0]
        p = Person(f"standin-{i:05d}", rng.choice(_FIRST_F if sex == "F" else _FIRST_M), "", rng.choice(_LAST),
                   date(REF_DATE.year - age, rng.randint(1, 12), rng.randint(1, 28)), sex,
                   rng.choice(["white"] * 7 + ["black", "asian", "other"]),
                   rng.choice(["nonhispanic"] * 8 + ["hispanic"] * 2), rng.choice("MSWD"),
                   f"{rng.randint(10, 999)} Example St", "Boston", f"021{rng.randint(10, 99)}",
                   f"9{rng.randint(10, 99)}-{rng.randint(10, 99)}-{rng.randint(1000, 9999)}")
        for code, disp in _STAND_IN_PROBLEMS:
            if rng.random() < (0.05 + age / 250):
                p.problems.append({"snomed": code, "icd10": _CHRONIC_ICD10.get(code), "display": disp, "onset": "2019-01-01"})
        out.append(p)
    return out
