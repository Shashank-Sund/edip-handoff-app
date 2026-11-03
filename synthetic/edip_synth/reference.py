"""Reference entities shared by every rendering: facilities, departments, beds,
providers, and the lab catalog (LOINC codes verified per spec section 10.3).
"""
import random
from dataclasses import dataclass, field

from .ids import luhn_npi


@dataclass
class Facility:
    code: str
    name: str
    address: str
    npi: str


@dataclass
class Department:
    dept_id: str          # 7 digit DEPARTMENT_ID
    facility: str
    code: str             # PV1-3.1 point of care
    name: str             # DEPARTMENT_NAME
    level: str            # ED, ICU, STEPDOWN, TELE, MEDSURG, OBS
    beds: int
    private_rooms: float  # share of rooms that are private
    rooms: list = field(default_factory=list)   # [(room, bed, private)]


@dataclass
class Provider:
    prov_id: str
    npi: str
    first: str
    last: str
    prov_type: str        # Physician, Resident, Nurse Practitioner, Physician Assistant, Registered Nurse
    specialty: str
    facility: str
    user_id: str          # MSH-8 / EVN-5 style user ID

    @property
    def display(self) -> str:
        suffix = {"Physician": "MD", "Resident": "MD", "Nurse Practitioner": "NP",
                  "Physician Assistant": "PA", "Registered Nurse": "RN"}[self.prov_type]
        return f"{self.first} {self.last}, {suffix}"


FACILITIES = [
    Facility("MGH", "Metro General Hospital", "100 Harbor Way, Boston, MA 02114", luhn_npi("199900101")),
    Facility("LKS", "Lakeside Community Hospital", "45 Lakeview Rd, Quincy, MA 02169", luhn_npi("199900202")),
]

# (facility, code, name, level, beds, private share). Bed counts from spec 2.1.
_UNITS = [
    ("MGH", "ED", "MGH EMERGENCY DEPT", "ED", 20, 0.0),
    ("MGH", "MICU", "MGH MEDICAL ICU", "ICU", 12, 1.0),
    ("MGH", "5N", "MGH 5 NORTH STEP DOWN", "STEPDOWN", 16, 0.5),
    ("MGH", "6W", "MGH 6 WEST TELEMETRY", "TELE", 28, 0.4),
    ("MGH", "7E", "MGH 7 EAST MED SURG", "MEDSURG", 32, 0.3),
    ("MGH", "4C", "MGH 4C CLINICAL DECISION UNIT", "OBS", 16, 0.2),
    ("LKS", "ED", "LKS EMERGENCY DEPT", "ED", 14, 0.0),
    ("LKS", "ICU", "LKS INTENSIVE CARE UNIT", "ICU", 8, 1.0),
    ("LKS", "3S", "LKS 3 SOUTH MED SURG", "MEDSURG", 30, 0.3),
    ("LKS", "2E", "LKS 2 EAST OBSERVATION", "OBS", 10, 0.2),
]


def build_departments(rng: random.Random) -> list:
    depts = []
    for i, (fac, code, name, level, beds, private) in enumerate(_UNITS):
        d = Department(str(1010100 + i * 37 + (0 if fac == "MGH" else 5000)), fac, code, name, level, beds, private)
        if level == "ED":
            d.rooms = [(f"{n:02d}", "A", False) for n in range(1, beds + 1)]
        else:
            floor = {"MICU": 3, "5N": 5, "6W": 6, "7E": 7, "4C": 4, "ICU": 2, "3S": 3, "2E": 2}[code]
            room_no, left = 1, beds
            while left > 0:
                is_private = rng.random() < private or left == 1
                for b in (["01"] if is_private else ["01", "02"]):
                    d.rooms.append((f"{floor}{room_no:03d}", b, is_private))
                    left -= 1
                room_no += 1
        depts.append(d)
    return depts


_FIRST = ["Alex", "Jordan", "Priya", "Marcus", "Elena", "Samuel", "Grace", "Omar", "Hannah", "Wei",
          "Daniel", "Fatima", "Lucas", "Maya", "Noah", "Aisha", "Ethan", "Sofia", "Kevin", "Laura",
          "Miguel", "Rachel", "Tomas", "Nina", "Brian", "Leah", "Andre", "Julia", "Ravi", "Claire"]
_LAST = ["Abernathy", "Brennan", "Castillo", "Delgado", "Ellison", "Fairbanks", "Galloway", "Hartwell",
         "Iverson", "Jablonski", "Kowalczyk", "Lindqvist", "Marchetti", "Nakamura", "Okafor", "Pellegrino",
         "Quintero", "Rasmussen", "Sorensen", "Thibodeau", "Underhill", "Valdivia", "Whitcomb", "Yamada",
         "Zielinski", "Ashworth", "Bramwell", "Corbett", "Dunleavy", "Esposito", "Fenwick", "Greaves"]

# (facility, type, specialty, count)
_STAFF = [
    ("MGH", "Physician", "Emergency Medicine", 8), ("MGH", "Resident", "Emergency Medicine", 6),
    ("MGH", "Physician Assistant", "Emergency Medicine", 3), ("MGH", "Physician", "Hospital Medicine", 7),
    ("MGH", "Physician", "Critical Care", 3), ("MGH", "Physician", "Cardiology", 3),
    ("MGH", "Physician", "Neurology", 2), ("MGH", "Physician", "Orthopedics", 2),
    ("MGH", "Physician", "Gastroenterology", 2), ("MGH", "Registered Nurse", "Emergency Medicine", 10),
    ("MGH", "Registered Nurse", "Inpatient", 10),
    ("LKS", "Physician", "Emergency Medicine", 5), ("LKS", "Nurse Practitioner", "Emergency Medicine", 2),
    ("LKS", "Physician", "Hospital Medicine", 4), ("LKS", "Physician", "Critical Care", 1),
    ("LKS", "Physician", "Orthopedics", 1), ("LKS", "Registered Nurse", "Emergency Medicine", 6),
    ("LKS", "Registered Nurse", "Inpatient", 6),
]


def build_providers(rng: random.Random) -> list:
    out, used, n = [], set(), 0
    for fac, ptype, spec, count in _STAFF:
        for _ in range(count):
            while True:
                first, last = rng.choice(_FIRST), rng.choice(_LAST)
                if (first, last) not in used:
                    used.add((first, last))
                    break
            n += 1
            out.append(Provider(f"E{100200 + n}", luhn_npi(f"19990{1000 + n:04d}"), first, last, ptype,
                                spec, fac, f"{last[:4].upper()}{first[0]}{n:02d}"))
    return out


# Lab catalog: code -> (LOINC, display, units, ref_low, ref_high, crit_low, crit_high, normal_low, normal_high).
# Critical limits: University of Iowa list plus lactate over 4.0 (spec 10.4). Normal draw ranges (c).
LABS = {
    "WBC": ("6690-2", "WBC", "K/uL", 4.0, 11.0, 1.0, 50.0, 4.5, 10.0),
    "HGB": ("718-7", "Hemoglobin", "g/dL", 12.0, 17.5, 6.0, 22.0, 12.5, 15.5),
    "HCT": ("4544-3", "Hematocrit", "%", 36.0, 52.0, None, None, 37.0, 46.0),
    "PLT": ("777-3", "Platelets", "K/uL", 150, 400, 10, 1000, 170, 350),
    "NA": ("2951-2", "Sodium", "mmol/L", 135, 145, 120, 160, 136, 142),
    "K": ("2823-3", "Potassium", "mmol/L", 3.5, 5.1, 2.8, 6.2, 3.7, 4.6),
    "CL": ("2075-0", "Chloride", "mmol/L", 98, 107, None, None, 99, 106),
    "CO2": ("2028-9", "CO2 total", "mmol/L", 22, 29, 10, 50, 23, 28),
    "BUN": ("3094-0", "BUN", "mg/dL", 7, 20, None, None, 9, 18),
    "CREAT": ("2160-0", "Creatinine", "mg/dL", 0.6, 1.3, None, None, 0.7, 1.1),
    "GLU": ("2345-7", "Glucose", "mg/dL", 70, 99, 50, 450, 82, 118),
    "CA": ("17861-6", "Calcium", "mg/dL", 8.5, 10.5, 6.0, 13.0, 8.8, 10.0),
    "AGAP": ("33037-3", "Anion gap", "mmol/L", 7, 16, None, None, 8, 13),
    "AST": ("1920-8", "AST", "U/L", 10, 40, None, None, 14, 32),
    "ALT": ("1742-6", "ALT", "U/L", 7, 56, None, None, 12, 38),
    "ALKP": ("6768-6", "Alkaline phosphatase", "U/L", 40, 129, None, None, 50, 110),
    "TBILI": ("1975-2", "Bilirubin total", "mg/dL", 0.2, 1.2, None, None, 0.3, 0.9),
    "ALB": ("1751-7", "Albumin", "g/dL", 3.5, 5.0, None, None, 3.7, 4.6),
    "TP": ("2885-2", "Protein total", "g/dL", 6.0, 8.3, None, None, 6.4, 7.8),
    "LACT": ("32693-4", "Lactate", "mmol/L", 0.5, 2.0, None, 4.0, 0.7, 1.6),
    "TROP": ("67151-1", "Troponin T, high sensitivity", "ng/L", 0, 14, None, None, 3, 9),
    "BNP": ("30934-4", "BNP", "pg/mL", 0, 100, None, None, 15, 80),
    "INR": ("6301-6", "INR", "", 0.9, 1.1, None, 4.0, 0.95, 1.1),
    "PTT": ("3173-2", "aPTT", "s", 25, 36, None, None, 27, 34),
    "LIPASE": ("3040-3", "Lipase", "U/L", 13, 60, None, None, 18, 50),
    "MG": ("19123-9", "Magnesium", "mg/dL", 1.7, 2.2, 1.0, 4.7, 1.8, 2.2),
    "PHOS": ("2777-1", "Phosphate", "mg/dL", 2.5, 4.5, None, None, 2.8, 4.2),
    "VPH": ("2746-6", "pH venous", "", 7.31, 7.41, 7.20, 7.60, 7.33, 7.40),
    "VPCO2": ("2021-4", "pCO2 venous", "mmHg", 41, 51, 20, 70, 42, 50),
    "VHCO3": ("14627-4", "Bicarbonate venous", "mmol/L", 22, 29, 10, 50, 23, 28),
    "BHB": ("104816-4", "Beta hydroxybutyrate", "mmol/L", 0.0, 0.4, None, None, 0.05, 0.3),
    "PCT": ("33959-8", "Procalcitonin", "ng/mL", 0.0, 0.25, None, None, 0.02, 0.1),
    "CRP": ("1988-5", "C reactive protein", "mg/L", 0, 10, None, None, 0.5, 6),
    "CK": ("2157-6", "CK", "U/L", 39, 308, None, None, 60, 220),
    "A1C": ("4548-4", "Hemoglobin A1c", "%", 4.0, 5.6, None, None, 4.8, 5.6),
    "POCGLU": ("41653-7", "Glucose, POC", "mg/dL", 70, 99, 50, 450, 85, 125),
    "UA_LE": ("5799-2", "Leukocyte esterase, urine", "", None, None, None, None, None, None),
    "UA_NIT": ("5802-4", "Nitrite, urine", "", None, None, None, None, None, None),
    "UA_WBC": ("5821-4", "WBC, urine", "/hpf", 0, 5, None, None, 0, 3),
    "BCX": ("600-7", "Blood culture", "", None, None, None, None, None, None),
    "UCX": ("630-4", "Urine culture", "", None, None, None, None, None, None),
    "ABORH": ("882-1", "ABO and Rh", "", None, None, None, None, None, None),
    "FLU": ("92142-9", "Influenza A RNA", "", None, None, None, None, None, None),
    "COVID": ("94500-6", "SARS-CoV-2 RNA", "", None, None, None, None, None, None),
}

# Order panels: code -> (display, procedure code, components, turnaround minutes (median, p90))
PANELS = {
    "CBC": ("CBC with differential", "LAB0001", ["WBC", "HGB", "HCT", "PLT"], (35, 70)),
    "BMP": ("Basic metabolic panel", "LAB0002", ["NA", "K", "CL", "CO2", "AGAP", "BUN", "CREAT", "GLU", "CA"], (50, 90)),
    "CMP": ("Comprehensive metabolic panel", "LAB0003",
            ["NA", "K", "CL", "CO2", "AGAP", "BUN", "CREAT", "GLU", "CA", "AST", "ALT", "ALKP", "TBILI", "ALB", "TP"], (55, 95)),
    "LACT": ("Lactate, whole blood", "LAB0010", ["LACT"], (15, 35)),
    "TROP": ("Troponin T, high sensitivity", "LAB0011", ["TROP"], (45, 75)),
    "BNP": ("BNP", "LAB0012", ["BNP"], (50, 90)),
    "COAG": ("PT/INR and aPTT", "LAB0013", ["INR", "PTT"], (40, 75)),
    "LIPASE": ("Lipase", "LAB0014", ["LIPASE"], (50, 90)),
    "MGPHOS": ("Magnesium and phosphorus", "LAB0015", ["MG", "PHOS"], (50, 90)),
    "VBG": ("Venous blood gas", "LAB0016", ["VPH", "VPCO2", "VHCO3"], (10, 25)),
    "BHB": ("Beta hydroxybutyrate", "LAB0017", ["BHB"], (45, 80)),
    "PCT": ("Procalcitonin", "LAB0018", ["PCT"], (70, 140)),
    "CRP": ("C reactive protein", "LAB0019", ["CRP"], (60, 110)),
    "CK": ("CK, total", "LAB0020", ["CK"], (50, 90)),
    "A1C": ("Hemoglobin A1c", "LAB0021", ["A1C"], (240, 720)),
    "POCGLU": ("Glucose, point of care", "LAB0022", ["POCGLU"], (2, 5)),
    "UA": ("Urinalysis with microscopy", "LAB0030", ["UA_LE", "UA_NIT", "UA_WBC"], (40, 80)),
    "BCX": ("Blood culture", "LAB0040", ["BCX"], (1440, 2400)),
    "UCX": ("Urine culture", "LAB0041", ["UCX"], (1200, 2000)),
    "TS": ("Type and screen", "LAB0050", ["ABORH"], (45, 90)),
    "RESP": ("Influenza and SARS-CoV-2 PCR", "LAB0060", ["FLU", "COVID"], (60, 150)),
}

# Imaging: code -> (display, LOINC, prelim turnaround (median, p90), final after prelim (median, p90))
IMAGING = {
    "CXR": ("XR chest 1 view portable", "30746-2", (35, 90), (120, 480)),
    "CTH": ("CT head without contrast", "30799-1", (25, 60), (60, 240)),
    "CTA": ("CTA head and neck", "37498-3", (40, 90), (90, 300)),
    "CTAP": ("CT abdomen and pelvis with contrast", "36813-4", (70, 150), (120, 480)),
    "CTPE": ("CTA chest pulmonary embolism protocol", "88322-3", (60, 130), (120, 480)),
    "XRHIP": ("XR pelvis and hip", "30767-8", (40, 100), (120, 600)),
    "ECG": ("12 lead ECG", "34534-8", (5, 15), (120, 720)),
    "RUQUS": ("US abdomen", "24558-9", (80, 180), (60, 300)),
}

VITAL_LOINC = {
    "hr": ("8867-4", "Heart rate", "/min"), "sbp": ("8480-6", "Systolic blood pressure", "mm[Hg]"),
    "dbp": ("8462-4", "Diastolic blood pressure", "mm[Hg]"), "rr": ("9279-1", "Respiratory rate", "/min"),
    "temp": ("8310-5", "Body temperature", "Cel"), "spo2": ("59408-5", "Oxygen saturation by pulse oximetry", "%"),
    "pain": ("72514-3", "Pain severity 0 to 10", "{score}"), "gcs": ("9269-2", "Glasgow coma score total", "{score}"),
}
