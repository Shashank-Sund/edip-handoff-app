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
          "75498004", "36971009", "43878008", "38822007", "62106007", "403190006", "284551006", "370247008",
          "76571007", "4557003", "53827007", "6072007", "267064002", "19169002", "156073000", "398254007", "90460009",
          "39848009", "127294003"}
_DROP_WORDS = ("teeth", "molar", "gingiv", "dental", "periodont", "scoliosis", "pregnan", "sprain", "laceration",
               "fracture", "burn", "concussion", "bite", "abrasion", "viral", "acute", "streptococcal", "otitis",
               "sinusitis", "contact dermatitis")
_CHRONIC_ICD10 = {
    "59621000": "I10", "44054006": "E11.9", "88805009": "I50.9", "49436004": "I48.91", "433144002": "N18.30",
    "431857002": "N18.4", "431855005": "N18.1", "431856006": "N18.2", "46177005": "N18.6", "26929004": "G30.9",
    "195967001": "J45.909", "87433001": "J43.9", "185086009": "J44.9", "55822004": "E78.5", "414545008": "I25.10",
    "271737000": "D64.9", "64859006": "M81.0", "239873007": "M17.9", "239872002": "M16.9", "78275009": "G47.33",
    "128613002": "G40.909", "84757009": "G40.909", "83664006": "E03.9", "60573004": "I35.0", "399211009": "I25.2",
    "302870006": "E78.1", "237602007": "E88.810", "127013003": "E11.29", "368581000119106": "E11.40",
    "6525002": "F19.20", "124171000119105": "G43.719", "254837009": "C50.919", "126906006": "D40.0",
    "197927001": "N39.0", "714628002": "R73.03", "90781000119102": "E11.29", "157141000119108": "E11.29",
    "24079001": "L20.9", "7200002": "F10.20", "427419006": "G43.709", "230265002": "G30.0", "128188000": "G80.9",
    "235595009": "K21.9", "69896004": "M06.9", "109989006": "C90.00", "60234000": "I35.1", "370143000": "F32.9",
    "111287006": "I36.1", "363406005": "C18.9", "233678006": "J45.909", "232353008": "J30.9", "91434003": "I37.1",
    "60951000119105": "E11.39", "79619009": "I05.0", "94503003": "C79.82", "110359009": "F79", "109838007": "C18.8",
    "95417003": "M79.7",
    "68496003": "K63.5", "80394007": "R73.9", "39898005": "G47.9", "201834006": "M19.049", "203082005": "M79.7",
    "713197008": "K62.1", "73430006": "G47.30", "92691004": "D07.5", "48724000": "I34.0",
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


_CKD_ORDER = ["431855005", "431856006", "433144002", "431857002", "46177005"]   # stage 1, 2, 3, 4, ESRD


def _strip(text: str) -> str:
    return re.sub(r"\s*\((finding|disorder|situation|substance|morphologic abnormality)\)", "", text or "").strip()


def _clean(name: str) -> str:
    return re.sub(r"\d+$", "", name or "").strip()


def _keep_condition(code: str, desc: str) -> bool:
    d = desc.lower()
    if code in _ACUTE or "(disorder)" not in d and code not in ("714628002", "399211009"):
        return False
    return not any(w in d for w in _DROP_WORDS)


def save_population(people: list, path: Path) -> None:
    """Write the slice of a Synthea population the generator uses (a few MB instead of the 2.5 GB CSV export)."""
    import gzip
    import json
    from dataclasses import asdict
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for p in people:
            f.write(json.dumps(asdict(p) | {"birth_date": p.birth_date.isoformat()}, separators=(",", ":")) + "\n")


def load_population(path: Path) -> list:
    import gzip
    import json
    out = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            out.append(Person(**(d | {"birth_date": date.fromisoformat(d["birth_date"])})))
    return out


def load(csv_dir: Path) -> list:
    """A Synthea CSV export folder, or a population file written by save_population (.json.gz)."""
    csv_dir = Path(csv_dir)
    if csv_dir.is_file():
        return load_population(csv_dir)
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
        # Epic problem list entries are diagnosis coded, so only problems with a verified ICD-10-CM mapping are kept.
        if p and not r["STOP"] and _keep_condition(r["CODE"], r["DESCRIPTION"]) and r["CODE"] in _CHRONIC_ICD10:
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
            p.drug_allergies.append({"code": r["CODE"], "system": r["SYSTEM"], "display": _strip(r["DESCRIPTION"]),
                                     "reaction": _strip(r["DESCRIPTION1"]), "severity": r["SEVERITY1"]})
    for r in csv.DictReader(open(csv_dir / "encounters.csv", newline="")):
        p = people.get(r["PATIENT"])
        if p and r["START"][:4] >= "2024":
            if r["ENCOUNTERCLASS"] == "emergency":
                p.prior_ed_visits += 1
            elif r["ENCOUNTERCLASS"] == "inpatient":
                p.prior_admissions += 1
    for p in people.values():
        # Synthea leaves each CKD stage open as it progresses; keep only the highest stage.
        ckd = [q for q in p.problems if q["snomed"] in _CKD_ORDER]
        if len(ckd) > 1:
            top = max(ckd, key=lambda q: _CKD_ORDER.index(q["snomed"]))
            p.problems = [q for q in p.problems if q["snomed"] not in _CKD_ORDER or q is top]
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
            min_age = {"26929004": 65, "185086009": 45, "88805009": 50, "49436004": 50, "64859006": 55}.get(code, 25)
            if age >= min_age and rng.random() < (0.05 + age / 250):
                p.problems.append({"snomed": code, "icd10": _CHRONIC_ICD10.get(code), "display": disp, "onset": "2019-01-01"})
        out.append(p)
    return out
