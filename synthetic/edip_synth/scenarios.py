"""Clinical scenario library (spec section 10).

Ranges are (low, high) for a uniform draw. Vital targets are where a treated
patient trends over the ED stay; labs are first draw values, with optional
trends for repeat draws. RxNorm, ICD-10-CM and SNOMED codes come from the
verified tables in the spec (10.5, 10.6).
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Med:
    name: str
    rxcui: str
    dose: str
    unit: str
    route: str
    freq: str = "ONCE"
    kind: str = "bolus"          # bolus, infusion, neb, po
    offset: tuple = (5, 40)      # minutes after provider contact
    prob: float = 1.0
    rate: str = ""               # infusions: starting rate
    repeat_h: int = 0            # scheduled repeat interval once inpatient (0 = none)
    weight_based: str = ""       # e.g. "30 mL/kg" (documented in the order comment)


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    admit: bool
    complaints: tuple
    esi: dict
    vitals: dict                 # triage ranges
    targets: dict = field(default_factory=dict)   # treated ranges
    tau_min: int = 120           # time constant of the treatment response
    o2: tuple = ()               # (device, flow L/min range) when hypoxic
    panels: tuple = ("CBC", "BMP")
    optional_panels: dict = field(default_factory=dict)  # panel -> probability
    labs: dict = field(default_factory=dict)       # lab code -> (low, high) first draw
    repeats: tuple = ()          # (panel, minutes after first result, {lab: (low, high)})
    imaging: dict = field(default_factory=dict)    # code -> (probability, impressions)
    meds: tuple = ()
    dx: tuple = ()               # ((icd10, snomed, display), ...) first is principal
    units: dict = field(default_factory=dict)      # level -> weight
    consult: tuple = ()
    conditions: tuple = ()       # Synthea SNOMED codes that make a patient a good fit
    age: dict = field(default_factory=lambda: {"18-44": 30, "45-64": 28, "65-74": 18, "75+": 24})
    isolation_bias: float = 1.0
    hpi: str = ""
    mdm: str = ""
    plan: tuple = ()
    pending: tuple = ()


NORMAL = {"hr": (68, 96), "sbp": (112, 142), "dbp": (64, 88), "rr": (14, 18),
          "temp": (36.4, 37.3), "spo2": (96, 100), "pain": (0, 3), "gcs": (15, 15)}


def V(**kw):
    out = dict(NORMAL)
    out.update(kw)
    return out


NS = Med("sodium chloride 0.9 % bolus", "313002", "1000", "mL", "IV", kind="infusion", offset=(10, 40), rate="999 mL/hr")
LR = Med("lactated Ringer's bolus", "847630", "30", "mL/kg", "IV", kind="infusion", offset=(10, 35), rate="999 mL/hr",
         weight_based="30 mL/kg")
APAP_PO = Med("acetaminophen 500 mg tablet", "198440", "1000", "mg", "PO", kind="po", offset=(10, 60))
APAP_IV = Med("acetaminophen 10 mg/mL injection", "483017", "1000", "mg", "IV", offset=(15, 60))
ONDAN = Med("ondansetron 2 mg/mL injection", "1740467", "4", "mg", "IV", offset=(10, 45), prob=0.5)
MORPH = Med("morphine 4 mg/mL injection", "1732006", "4", "mg", "IV", offset=(10, 45))
HYDRO = Med("hydromorphone 1 mg/mL injection", "897653", "0.5", "mg", "IV", offset=(10, 45))
CEFTRI1 = Med("cefTRIAXone 1 g injection", "1665021", "1", "g", "IV", "Q24H", offset=(30, 120), repeat_h=24)
CEFTRI2 = Med("cefTRIAXone 2 g injection", "1665046", "2", "g", "IV", "Q24H", offset=(8, 35), repeat_h=24)
# Sepsis: antibiotics within the first hour of recognition (Surviving Sepsis Campaign).
CEFTRI1_SEPSIS = Med("cefTRIAXone 1 g injection", "1665021", "1", "g", "IV", "Q24H", offset=(8, 35), repeat_h=24)
AZITH_SEPSIS = Med("azithromycin 500 mg injection", "1668238", "500", "mg", "IV", "Q24H", offset=(20, 55), repeat_h=24)
AZITH = Med("azithromycin 500 mg injection", "1668238", "500", "mg", "IV", "Q24H", offset=(30, 120), repeat_h=24)
ENOX = Med("enoxaparin 100 mg/mL syringe", "854235", "40", "mg", "SC", "DAILY", offset=(240, 480), prob=0.0, repeat_h=24)

SCENARIOS = {s.key: s for s in [
    Scenario(
        "sepsis_uti", "Sepsis, urinary source", True,
        ("Fever", "Altered mental status", "Dysuria", "Weakness"),
        {2: 82, 1: 18},
        V(hr=(105, 130), sbp=(85, 105), dbp=(45, 60), rr=(22, 28), temp=(38.5, 39.8), spo2=(93, 97), pain=(3, 6), gcs=(14, 15)),
        {"hr": (88, 105), "sbp": (100, 120), "dbp": (55, 70), "rr": (18, 22), "temp": (37.4, 38.4), "pain": (1, 3)},
        tau_min=150,
        panels=("CBC", "CMP", "LACT", "UA", "BCX", "UCX", "COAG", "PCT", "VBG"),
        labs={"WBC": (14, 24), "LACT": (2.5, 5.6), "CREAT": (1.4, 2.5), "BUN": (28, 48), "PCT": (2, 20),
              "INR": (1.1, 1.5), "UA_WBC": (50, 182), "CO2": (17, 22), "PLT": (110, 260)},
        repeats=(("LACT", 150, {"LACT": (1.3, 2.4)}),),
        imaging={"CXR": (0.9, ("No acute cardiopulmonary process.",))},
        meds=(LR, CEFTRI2, APAP_PO,
              Med("piperacillin-tazobactam 4.5 g injection", "1659149", "4.5", "g", "IV", "Q6H", offset=(20, 60), prob=0.25, repeat_h=6),
              Med("norepinephrine 1 mg/mL injection", "242969", "4", "mcg/min", "IV", "CONTINUOUS", "infusion", (90, 200), 0.12, "4 mcg/min")),
        dx=(("A41.9", "91302008", "Sepsis, unspecified organism"), ("N10", "36689008", "Acute pyelonephritis"),
            ("N17.9", "14669001", "Acute kidney injury")),
        units={"STEPDOWN": 60, "ICU": 40},
        conditions=("197927001", "44054006", "26929004"),
        age={"18-44": 10, "45-64": 28, "65-74": 30, "75+": 32},
        hpi="fever, dysuria and new confusion for 1 to 2 days",
        mdm="Sepsis due to complicated UTI or pyelonephritis with AKI and lactic acidosis.",
        plan=("Continue cefTRIAXone, narrow per cultures", "Trend lactate until under 2", "Goal MAP over 65",
              "Hold nephrotoxins, renally dose meds"),
        pending=("Blood cultures x2", "Urine culture"),
    ),
    Scenario(
        "sepsis_pna", "Sepsis, pneumonia source", True,
        ("Shortness of breath", "Fever", "Cough"),
        {2: 92, 1: 8},
        V(hr=(100, 125), sbp=(90, 110), dbp=(50, 65), rr=(24, 32), temp=(38.3, 39.5), spo2=(86, 92), pain=(2, 5), gcs=(14, 15)),
        {"hr": (88, 105), "sbp": (102, 122), "rr": (20, 24), "spo2": (92, 96), "temp": (37.4, 38.3)},
        tau_min=150, o2=("Nasal cannula", (2, 6)),
        panels=("CBC", "CMP", "LACT", "BCX", "PCT", "VBG", "RESP"),
        labs={"WBC": (12, 22), "LACT": (2.1, 4.6), "PCT": (1, 10), "VPH": (7.30, 7.38), "CREAT": (1.0, 1.8)},
        repeats=(("LACT", 150, {"LACT": (1.2, 2.2)}),),
        imaging={"CXR": (1.0, ("Right lower lobe consolidation.", "Left lower lobe airspace opacity concerning for pneumonia.",
                               "Multifocal opacities, right greater than left."))},
        meds=(LR, CEFTRI1_SEPSIS, AZITH_SEPSIS, APAP_PO,
              Med("vancomycin 1000 mg injection", "1807513", "20", "mg/kg", "IV", "Q12H", offset=(40, 120), prob=0.3, repeat_h=12,
                  weight_based="20 mg/kg")),
        dx=(("A41.9", "91302008", "Sepsis, unspecified organism"), ("J18.9", "233604007", "Pneumonia"),
            ("J96.01", "65710008", "Acute respiratory failure with hypoxia")),
        units={"STEPDOWN": 40, "TELE": 25, "ICU": 35},
        conditions=("26929004", "87433001", "185086009"),
        age={"18-44": 10, "45-64": 28, "65-74": 30, "75+": 32},
        hpi="productive cough, fever and worsening shortness of breath for 3 days",
        mdm="Sepsis from community acquired pneumonia with hypoxia.",
        plan=("cefTRIAXone and azithromycin", "Wean O2 to SpO2 92 to 96", "Repeat lactate", "Incentive spirometry"),
        pending=("Blood cultures x2", "Respiratory viral PCR"),
    ),
    Scenario(
        "chf", "Acute decompensated heart failure", True,
        ("Shortness of breath", "Leg swelling", "Orthopnea"),
        {2: 100},
        V(hr=(95, 120), sbp=(150, 190), dbp=(85, 105), rr=(26, 36), spo2=(84, 91), pain=(0, 3)),
        {"hr": (80, 98), "sbp": (125, 150), "dbp": (70, 88), "rr": (18, 24), "spo2": (94, 98)},
        tau_min=110, o2=("Nasal cannula", (2, 4)),
        panels=("CBC", "BMP", "BNP", "TROP", "MGPHOS"),
        labs={"BNP": (600, 3000), "TROP": (20, 60), "NA": (128, 136), "CREAT": (1.2, 2.0), "BUN": (24, 45)},
        repeats=(("TROP", 120, {"TROP": (20, 62)}),),
        imaging={"CXR": (1.0, ("Cardiomegaly with pulmonary vascular congestion and small bilateral effusions.",
                               "Interstitial edema, bilateral pleural effusions.")), "ECG": (1.0, ("Sinus tachycardia, LVH, no acute ST changes.",))},
        meds=(Med("furosemide 10 mg/mL injection", "1719291", "40", "mg", "IV", "BID", offset=(15, 50), repeat_h=12),
              Med("nitroglycerin 0.4 mg SL tablet", "198039", "0.4", "mg", "SL", offset=(5, 20), prob=0.7)),
        dx=(("I50.23", "56675007", "Acute on chronic systolic heart failure"), ("I11.0", "42343007", "Hypertensive heart disease with heart failure")),
        units={"TELE": 65, "STEPDOWN": 25, "ICU": 10},
        conditions=("88805009", "59621000", "414545008", "60573004"),
        age={"18-44": 3, "45-64": 22, "65-74": 30, "75+": 45},
        hpi="3 days of worsening dyspnea, orthopnea and leg swelling",
        mdm="Acute on chronic HFrEF exacerbation, likely dietary and medication nonadherence.",
        plan=("IV furosemide, goal net negative 1 to 2 L", "Strict I and O, daily weights", "Repeat BMP and Mg in AM",
              "Echo if none in 12 months"),
        pending=("Repeat troponin", "AM BMP"),
    ),
    Scenario(
        "copd", "COPD exacerbation", True,
        ("Shortness of breath", "Wheezing", "Cough"),
        {2: 85, 3: 15},
        V(hr=(100, 120), sbp=(130, 160), rr=(24, 32), temp=(37.0, 38.2), spo2=(84, 90), pain=(0, 2)),
        {"hr": (98, 110), "rr": (20, 24), "spo2": (88, 92)}, tau_min=120, o2=("Nasal cannula", (1, 3)),
        panels=("CBC", "BMP", "VBG", "BNP"), optional_panels={"PCT": 0.6},
        labs={"VPH": (7.28, 7.38), "VPCO2": (50, 75), "VHCO3": (28, 36), "CO2": (28, 35), "WBC": (9, 15)},
        imaging={"CXR": (1.0, ("Hyperinflation without focal consolidation.",)), "ECG": (0.8, ("Sinus tachycardia.",))},
        meds=(Med("albuterol-ipratropium 2.5-0.5 mg/3 mL neb", "1437702", "3", "mL", "INH", "Q4H", "neb", (5, 20), repeat_h=4),
              Med("methylPREDNISolone 125 mg injection", "1743704", "125", "mg", "IV", offset=(15, 50)),
              Med("azithromycin 500 mg injection", "1668238", "500", "mg", "IV", "Q24H", offset=(40, 120), prob=0.6, repeat_h=24)),
        dx=(("J44.1", "195951007", "COPD with acute exacerbation"), ("J96.02", "65710008", "Acute respiratory failure with hypercapnia")),
        units={"MEDSURG": 40, "TELE": 30, "STEPDOWN": 22, "ICU": 8},
        conditions=("87433001", "185086009"),
        age={"18-44": 2, "45-64": 33, "65-74": 35, "75+": 30},
        hpi="increased dyspnea, sputum and wheeze for 2 days despite home inhalers",
        mdm="COPD exacerbation with acute on chronic hypercapnia, no consolidation.",
        plan=("Scheduled nebs", "Prednisone 40 mg daily x5 days", "O2 target 88 to 92", "BiPAP if pCO2 rising"),
        pending=("Repeat VBG",),
    ),
    Scenario(
        "chest_pain_ro", "Chest pain, NSTEMI rule out", True,
        ("Chest pain",),
        {2: 100},
        V(hr=(70, 105), sbp=(130, 170), dbp=(75, 95), rr=(16, 22), spo2=(95, 99), pain=(4, 8)),
        {"hr": (66, 88), "sbp": (120, 150), "pain": (0, 2)}, tau_min=60,
        panels=("CBC", "BMP", "TROP", "COAG"),
        labs={"TROP": (4, 9)}, repeats=(("TROP", 90, {"TROP": (4, 10)}),),
        imaging={"ECG": (1.0, ("Normal sinus rhythm, no acute ST changes.", "Sinus rhythm, nonspecific T wave changes.")),
                 "CXR": (0.9, ("No acute cardiopulmonary process.",))},
        meds=(Med("aspirin 81 mg chewable tablet", "318272", "324", "mg", "PO", kind="po", offset=(2, 15)),
              Med("nitroglycerin 0.4 mg SL tablet", "198039", "0.4", "mg", "SL", offset=(5, 25), prob=0.7)),
        dx=(("R07.9", "29857009", "Chest pain, unspecified"),),
        units={"OBS": 70, "TELE": 30},
        conditions=("59621000", "55822004", "414545008"),
        age={"18-44": 12, "45-64": 40, "65-74": 28, "75+": 20},
        hpi="substernal chest pressure at rest, intermittent for 6 hours",
        mdm="Chest pain, low to intermediate risk, serial troponins negative so far.",
        plan=("Serial troponins", "Telemetry", "Stress test or CCTA in AM"),
        pending=("Troponin 3 h",),
    ),
    Scenario(
        "nstemi", "NSTEMI", True,
        ("Chest pain", "Shortness of breath"),
        {2: 100},
        V(hr=(78, 108), sbp=(135, 175), dbp=(78, 98), rr=(16, 22), spo2=(94, 98), pain=(5, 9)),
        {"hr": (66, 85), "sbp": (118, 140), "pain": (0, 2)}, tau_min=70,
        panels=("CBC", "BMP", "TROP", "COAG"),
        labs={"TROP": (40, 200)}, repeats=(("TROP", 90, {"TROP": (150, 1500)}),),
        imaging={"ECG": (1.0, ("ST depression V4 to V6, no ST elevation.",)), "CXR": (0.9, ("No acute cardiopulmonary process.",))},
        meds=(Med("aspirin 81 mg chewable tablet", "318272", "324", "mg", "PO", kind="po", offset=(2, 15)),
              Med("nitroglycerin 0.4 mg SL tablet", "198039", "0.4", "mg", "SL", offset=(5, 25)),
              Med("heparin 100 units/mL premix", "1658717", "12", "units/kg/hr", "IV", "CONTINUOUS", "infusion", (90, 180),
                  rate="12 units/kg/hr", weight_based="60 units/kg bolus then 12 units/kg/hr"),
              Med("metoprolol tartrate 5 mg injection", "866508", "5", "mg", "IV", offset=(30, 90), prob=0.5)),
        dx=(("I21.4", "401314000", "NSTEMI"),),
        units={"TELE": 50, "STEPDOWN": 35, "ICU": 15}, consult=("Cardiology",),
        conditions=("414545008", "59621000", "55822004", "44054006"),
        age={"18-44": 4, "45-64": 36, "65-74": 30, "75+": 30},
        hpi="crushing chest pressure radiating to the left arm with diaphoresis for 3 hours",
        mdm="NSTEMI with rising troponin, no STEMI criteria. Cardiology consulted.",
        plan=("Heparin infusion per ACS nomogram", "Aspirin, high intensity statin", "Cath per cardiology"),
        pending=("Troponin 3 h", "Echo"),
    ),
    Scenario(
        "stroke", "Acute ischemic stroke", True,
        ("Weakness", "Slurred speech", "Facial droop"),
        {1: 40, 2: 60},
        V(hr=(70, 100), sbp=(160, 210), dbp=(85, 110), rr=(16, 20), spo2=(94, 98), pain=(0, 2), gcs=(11, 15)),
        {"sbp": (140, 178), "dbp": (75, 100)}, tau_min=90,
        panels=("POCGLU", "CBC", "BMP", "COAG", "TROP"),
        labs={"POCGLU": (110, 180)},
        imaging={"CTH": (1.0, ("No acute intracranial hemorrhage.",)),
                 "CTA": (0.9, ("Left M1 occlusion.", "No large vessel occlusion.", "No large vessel occlusion.")),
                 "ECG": (1.0, ("Atrial fibrillation, rate 90.", "Normal sinus rhythm."))},
        meds=(Med("alteplase 100 mg injection", "1804799", "0.9", "mg/kg", "IV", offset=(25, 55), prob=0.25,
                  weight_based="0.9 mg/kg, max 90 mg"),
              Med("labetalol 5 mg/mL injection", "896771", "10", "mg", "IV", offset=(15, 45), prob=0.6)),
        dx=(("I63.9", "432504007", "Cerebral infarction, unspecified"),),
        units={"ICU": 35, "STEPDOWN": 65}, consult=("Neurology",),
        conditions=("49436004", "59621000", "44054006"),
        age={"18-44": 5, "45-64": 33, "65-74": 30, "75+": 32},
        hpi="sudden right sided weakness and slurred speech, last known well 2 hours before arrival",
        mdm="Acute ischemic stroke, stroke alert activated, CT negative for hemorrhage.",
        plan=("Neuro checks q1h", "BP goal per lytic status", "Dysphagia screen before PO", "MRI brain, echo, lipids, A1c"),
        pending=("MRI brain", "Lipid panel", "Hemoglobin A1c"),
    ),
    Scenario(
        "ugib", "Upper GI bleed", True,
        ("Vomiting blood", "Black stools", "Dizziness"),
        {2: 80, 1: 20},
        V(hr=(100, 130), sbp=(85, 110), dbp=(45, 65), rr=(18, 24), spo2=(94, 98), pain=(0, 4), gcs=(14, 15)),
        {"hr": (85, 100), "sbp": (105, 125), "dbp": (58, 72)}, tau_min=120,
        panels=("CBC", "CMP", "COAG", "TS", "LACT"),
        labs={"HGB": (5.8, 11.0), "BUN": (30, 60), "CREAT": (0.8, 1.4), "INR": (1.0, 1.3), "LACT": (1.5, 3.5)},
        repeats=(("CBC", 240, {"HGB": (6.2, 9.5)}),),
        meds=(NS, Med("pantoprazole 40 mg injection", "283669", "80", "mg", "IV", offset=(15, 50)),
              Med("octreotide 50 mcg/mL injection", "312068", "50", "mcg", "IV", offset=(30, 90), prob=0.2)),
        dx=(("K92.2", "74474003", "Gastrointestinal hemorrhage, unspecified"), ("D62", "", "Acute posthemorrhagic anemia")),
        units={"STEPDOWN": 50, "ICU": 30, "TELE": 20}, consult=("Gastroenterology",),
        conditions=("271737000",),
        age={"18-44": 10, "45-64": 30, "65-74": 25, "75+": 35},
        hpi="2 episodes of coffee ground emesis and black stools since yesterday, on aspirin",
        mdm="Upper GI bleed, Glasgow Blatchford high risk, hemodynamically responsive to fluids.",
        plan=("Two large bore IVs", "Pantoprazole infusion", "Transfuse for Hgb under 7", "EGD within 24 h"),
        pending=("Repeat H and H", "EGD"),
    ),
    Scenario(
        "dka", "Diabetic ketoacidosis", True,
        ("Hyperglycemia", "Nausea", "Abdominal pain"),
        {2: 100},
        V(hr=(110, 135), sbp=(95, 120), dbp=(55, 75), rr=(24, 34), temp=(36.5, 37.5), spo2=(96, 100), pain=(4, 7), gcs=(13, 15)),
        {"hr": (90, 105), "rr": (18, 22), "pain": (1, 3)}, tau_min=240,
        panels=("CBC", "BMP", "VBG", "BHB", "MGPHOS", "UA", "LIPASE", "POCGLU"),
        labs={"GLU": (350, 700), "POCGLU": (350, 600), "BHB": (4, 12), "VPH": (6.95, 7.28), "CO2": (5, 17),
              "VHCO3": (5, 17), "K": (4.5, 6.8), "NA": (128, 135), "CREAT": (1.3, 2.2), "WBC": (12, 25), "PHOS": (1.0, 2.0),
              "MG": (1.5, 1.9)},
        repeats=(("BMP", 120, {"GLU": (250, 420), "CO2": (10, 19), "K": (3.8, 5.0)}),
                 ("BMP", 240, {"GLU": (180, 280), "CO2": (14, 21), "K": (3.4, 4.6)})),
        meds=(NS, Med("insulin regular 100 units/mL infusion", "311034", "0.1", "units/kg/hr", "IV", "CONTINUOUS", "infusion",
                      (60, 120), rate="0.1 units/kg/hr", weight_based="0.1 units/kg/hr"),
              Med("potassium chloride 2 mEq/mL injection", "204520", "20", "mEq", "IV", offset=(90, 180), prob=0.7), ONDAN),
        dx=(("E11.10", "421750000", "Type 2 diabetes mellitus with ketoacidosis without coma"),),
        units={"STEPDOWN": 70, "ICU": 30},
        conditions=("44054006",),
        age={"18-44": 40, "45-64": 40, "65-74": 12, "75+": 8},
        hpi="polyuria, polydipsia, nausea and abdominal pain for 2 days, missed insulin doses",
        mdm="DKA, anion gap acidosis with ketonemia, no clear infectious trigger.",
        plan=("Insulin infusion per DKA protocol", "BMP q2h, add dextrose when glucose under 250", "Replete K, Mg, Phos"),
        pending=("BMP q2h",),
    ),
    Scenario(
        "cap", "Community acquired pneumonia", True,
        ("Cough", "Fever", "Shortness of breath"),
        {3: 60, 2: 40},
        V(hr=(90, 115), sbp=(110, 140), rr=(22, 28), temp=(38.0, 39.2), spo2=(88, 93), pain=(2, 5)),
        {"hr": (80, 100), "rr": (18, 22), "spo2": (92, 96), "temp": (37.2, 38.1)}, tau_min=180, o2=("Nasal cannula", (1, 3)),
        panels=("CBC", "BMP", "PCT", "CRP", "RESP"), optional_panels={"BCX": 0.3},
        labs={"WBC": (11, 20), "PCT": (0.5, 5), "CRP": (50, 250), "NA": (130, 136)},
        imaging={"CXR": (1.0, ("Right middle lobe pneumonia.", "Left lower lobe consolidation."))},
        meds=(CEFTRI1, AZITH, APAP_PO),
        dx=(("J18.9", "233604007", "Pneumonia, unspecified organism"), ("R09.02", "", "Hypoxemia")),
        units={"MEDSURG": 70, "TELE": 30},
        conditions=("195967001", "26929004"),
        age={"18-44": 15, "45-64": 25, "65-74": 28, "75+": 32},
        hpi="4 days of cough, fevers and pleuritic chest pain",
        mdm="CAP, CURB-65 2, new O2 requirement, admit.",
        plan=("cefTRIAXone and azithromycin", "Wean O2", "Pneumococcal and Legionella urine antigens"),
        pending=("Respiratory viral PCR",),
    ),
    Scenario(
        "hip_fx", "Fall with hip fracture", True,
        ("Fall", "Hip pain"),
        {2: 60, 3: 40},
        V(hr=(85, 110), sbp=(130, 170), dbp=(70, 95), rr=(18, 22), temp=(36.2, 37.2), spo2=(93, 97), pain=(7, 10), gcs=(14, 15)),
        {"hr": (75, 90), "sbp": (120, 150), "pain": (3, 5)}, tau_min=60,
        panels=("CBC", "BMP", "COAG", "TS", "CK", "UA"),
        labs={"HGB": (10, 13), "CK": (250, 1800), "NA": (130, 138), "CREAT": (0.9, 1.6)},
        imaging={"XRHIP": (1.0, ("Displaced right femoral neck fracture.", "Left intertrochanteric fracture.")),
                 "CXR": (0.9, ("No acute cardiopulmonary process.",)), "CTH": (0.5, ("No acute intracranial abnormality.",)),
                 "ECG": (1.0, ("Normal sinus rhythm.",))},
        meds=(HYDRO, APAP_IV, ONDAN),
        dx=(("S72.001A", "5913000", "Fracture of unspecified part of neck of right femur, initial"), ("W19.XXXA", "1912002", "Fall")),
        units={"MEDSURG": 100}, consult=("Orthopedics",),
        conditions=("64859006", "26929004", "239872002"),
        age={"18-44": 0, "45-64": 5, "65-74": 25, "75+": 70},
        hpi="mechanical fall at home, unable to bear weight, right leg shortened and externally rotated",
        mdm="Closed hip fracture after mechanical fall, no head strike or syncope.",
        plan=("Ortho for OR within 24 to 48 h", "NPO after midnight", "Hold anticoagulant", "Delirium precautions"),
        pending=("Ortho surgical timing",),
    ),
]}


def _generic(key, label, complaints, esi, vitals, units, dx, hpi, mdm, panels=("CBC", "BMP"), imaging=None, meds=(),
             labs=None, age=None, admit=True, plan=(), pending=(), conditions=(), consult=(), isolation_bias=1.0):
    return Scenario(key, label, admit, complaints, esi, vitals, {}, 120, (), panels, {}, labs or {}, (), imaging or {}, meds,
                    dx, units, consult, conditions,
                    age or ({"18-44": 25, "45-64": 30, "65-74": 20, "75+": 25} if admit else {"18-44": 35, "45-64": 30, "65-74": 16, "75+": 19}),
                    isolation_bias, hpi, mdm, plan, pending)


GENERIC_ADMITS = {s.key: s for s in [
    _generic("af_rvr", "Atrial fibrillation with RVR", ("Palpitations", "Shortness of breath"), {2: 100},
             V(hr=(125, 165), sbp=(100, 140), rr=(18, 24)), {"TELE": 100}, (("I48.91", "49436004", "Atrial fibrillation"),),
             "palpitations and dyspnea since this morning", "AF with RVR, rate controlled with diltiazem.",
             ("CBC", "BMP", "TROP", "MGPHOS"), {"ECG": (1.0, ("Atrial fibrillation with rapid ventricular response.",))},
             (Med("diltiazem 5 mg/mL injection", "1791229", "0.25", "mg/kg", "IV", offset=(10, 30), weight_based="0.25 mg/kg"),),
             conditions=("49436004",), age={"18-44": 8, "45-64": 32, "65-74": 30, "75+": 30}),
    _generic("aki", "Acute kidney injury or electrolyte disorder", ("Weakness", "Decreased urination"), {3: 70, 2: 30},
             V(hr=(80, 105)), {"MEDSURG": 70, "TELE": 30}, (("N17.9", "14669001", "Acute kidney injury"),),
             "poor oral intake and weakness for several days", "AKI, likely prerenal, potassium elevated.",
             ("CBC", "BMP", "UA", "MGPHOS"), {"CXR": (0.5, ("No acute cardiopulmonary process.",))}, (NS,), {"CREAT": (2.2, 4.5), "BUN": (40, 90), "K": (5.3, 7.0)},
             conditions=("433144002", "431857002")),
    _generic("pyelo", "Pyelonephritis", ("Flank pain", "Fever"), {3: 80, 2: 20},
             V(hr=(95, 115), temp=(38.2, 39.4), pain=(5, 8)), {"MEDSURG": 100},
             (("N10", "36689008", "Acute pyelonephritis"),), "flank pain, fever and dysuria", "Pyelonephritis, failed outpatient therapy.",
             ("CBC", "BMP", "UA", "UCX"), {"CTAP": (0.4, ("Right perinephric stranding, no obstructing stone.",)),
                                           "CXR": (0.4, ("No acute cardiopulmonary process.",))}, (CEFTRI1, ONDAN), {"WBC": (12, 19), "UA_WBC": (40, 182)},
             pending=("Urine culture",)),
    _generic("cellulitis", "Cellulitis", ("Leg redness", "Swelling"), {3: 100}, V(temp=(37.4, 38.8), pain=(4, 7)),
             {"MEDSURG": 100}, (("L03.115", "", "Cellulitis of right lower limb"),), "spreading redness of the lower leg for 3 days",
             "Nonpurulent cellulitis failing oral antibiotics.", ("CBC", "BMP", "CRP"), {"CXR": (0.3, ("No acute cardiopulmonary process.",))},
             (Med("cefazolin 2 g injection", "1665060", "2", "g", "IV", "Q8H", offset=(30, 90), repeat_h=8),),
             {"WBC": (11, 16)}, isolation_bias=2.0),
    _generic("trauma_other", "Trauma, other", ("Fall", "Motor vehicle collision"), {1: 12, 2: 60, 3: 28}, V(pain=(5, 9)),
             {"MEDSURG": 60, "STEPDOWN": 25, "ICU": 15}, (("S22.41XA", "", "Multiple fractures of ribs, right side, initial"),),
             "fall from standing with right chest wall pain", "Multiple rib fractures, pain control and pulmonary toilet.",
             ("CBC", "BMP", "COAG", "TS"), {"CXR": (1.0, ("Right 5th to 7th rib fractures, no pneumothorax.",)),
                                           "CTH": (0.6, ("No acute intracranial abnormality.",))}, (HYDRO, APAP_IV)),
    _generic("abdominal", "Acute abdomen", ("Abdominal pain", "Vomiting"), {3: 65, 2: 30, 1: 5}, V(hr=(90, 112), pain=(6, 9)),
             {"MEDSURG": 100}, (("K85.90", "", "Acute pancreatitis"), ("K81.0", "", "Acute cholecystitis")),
             "epigastric pain radiating to the back with vomiting", "Acute pancreatitis versus cholecystitis, surgery aware.",
             ("CBC", "CMP", "LIPASE", "UA"), {"CTAP": (0.7, ("Peripancreatic fat stranding.", "Distended gallbladder with wall thickening.")),
                                              "RUQUS": (0.4, ("Cholelithiasis with gallbladder wall thickening.",))},
             (NS, MORPH, ONDAN), {"LIPASE": (300, 2500)}, consult=("General Surgery",)),
    _generic("syncope", "Syncope or weakness", ("Syncope", "Weakness", "Dizziness"), {2: 50, 3: 50}, V(),
             {"OBS": 60, "TELE": 40}, (("R55", "", "Syncope and collapse"),), "brief loss of consciousness while standing",
             "Syncope, likely orthostatic, telemetry observation.", ("CBC", "BMP", "TROP"),
             {"ECG": (1.0, ("Sinus bradycardia, first degree AV block.", "Normal sinus rhythm.")), "CTH": (0.5, ("No acute intracranial abnormality.",))},
             (NS,), age={"18-44": 15, "45-64": 30, "65-74": 27, "75+": 28}),
    _generic("psych_hold", "Psychiatric hold", ("Suicidal ideation", "Agitation"), {2: 70, 3: 30}, V(hr=(80, 110)),
             {"MEDSURG": 100}, (("R45.851", "", "Suicidal ideations"),), "suicidal ideation with a plan, brought by police",
             "Suicidal ideation, medically cleared, awaiting psychiatric bed, 1:1 sitter.", ("CBC", "BMP"), None,
             (Med("LORazepam 2 mg/mL injection", "1665188", "1", "mg", "IV", offset=(10, 120), prob=0.4),),
             age={"18-44": 60, "45-64": 30, "65-74": 6, "75+": 4}),
    _generic("flu_covid", "Influenza or COVID-19", ("Fever", "Cough", "Shortness of breath"), {3: 60, 2: 40},
             V(temp=(38.1, 39.4), spo2=(88, 94), rr=(20, 26)), {"MEDSURG": 70, "TELE": 30},
             (("J10.1", "", "Influenza with other respiratory manifestations"),), "fever, myalgias and cough for 3 days",
             "Influenza A with hypoxia, droplet precautions.", ("CBC", "BMP", "RESP"), {"CXR": (1.0, ("Patchy bilateral opacities.",))},
             (APAP_PO,), isolation_bias=4.0),
    _generic("other_medical", "Other medical admission", ("Weakness", "Fever", "Abdominal pain", "Confusion"), {3: 60, 2: 40},
             V(), {"MEDSURG": 70, "TELE": 30}, (("R53.1", "", "Weakness"),), "generalized weakness and poor intake",
             "Failure to thrive, unable to care for self at home, PT and case management.", ("CBC", "CMP", "UA"),
             {"CXR": (0.6, ("No acute cardiopulmonary process.",))}, (NS,), age={"18-44": 15, "45-64": 30, "65-74": 25, "75+": 30}),
]}

# ED visits that end in discharge, LWBS or AMA. Weighted by NHAMCS 2022 reasons for visit (spec 8.4).
DISCHARGE = {s.key: s for s in [
    _generic("abd_pain_d", "Abdominal pain", ("Abdominal pain",), {3: 74, 2: 12, 4: 14}, V(pain=(4, 7)), {},
             (("R10.9", "", "Unspecified abdominal pain"),), "intermittent abdominal pain", "Abdominal pain, benign exam and labs.",
             ("CBC", "CMP", "LIPASE", "UA"), {"CTAP": (0.35, ("No acute abnormality.",))}, (ONDAN, Med("ketorolac 15 mg injection", "860092", "15", "mg", "IV", offset=(10, 40), prob=0.5)), admit=False),
    _generic("chest_pain_d", "Chest pain, low risk", ("Chest pain",), {2: 60, 3: 40}, V(pain=(2, 6)), {},
             (("R07.89", "", "Other chest pain"),), "atypical chest pain", "Low risk chest pain, two negative troponins, HEART score 2.",
             ("CBC", "BMP", "TROP"), {"ECG": (1.0, ("Normal sinus rhythm.",)), "CXR": (0.8, ("No acute cardiopulmonary process.",))},
             (Med("aspirin 81 mg chewable tablet", "318272", "324", "mg", "PO", kind="po", offset=(2, 15), prob=0.7),), {"TROP": (3, 9)}, admit=False),
    _generic("sob_d", "Shortness of breath", ("Shortness of breath", "Cough"), {3: 68, 2: 14, 4: 18}, V(rr=(16, 22)), {},
             (("J06.9", "", "Acute upper respiratory infection"),), "cough and mild dyspnea", "Viral URI, ambulatory SpO2 normal.",
             ("CBC",), {"CXR": (0.6, ("No acute cardiopulmonary process.",))}, (), admit=False),
    _generic("fever_d", "Fever", ("Fever",), {3: 70, 4: 24, 2: 6}, V(temp=(37.8, 39.0)), {}, (("R50.9", "", "Fever, unspecified"),),
             "fever and malaise for 2 days", "Viral syndrome, tolerating PO.", ("CBC", "BMP", "UA", "RESP"), None, (APAP_PO,), admit=False),
    _generic("headache_d", "Headache", ("Headache",), {3: 72, 4: 22, 2: 6}, V(pain=(5, 8)), {}, (("R51.9", "", "Headache, unspecified"),),
             "gradual onset headache similar to prior migraines", "Migraine, improved with treatment.", (), {"CTH": (0.25, ("No acute intracranial abnormality.",))},
             (APAP_PO, Med("ketorolac 15 mg injection", "860092", "15", "mg", "IV", offset=(10, 40))), admit=False),
    _generic("back_pain_d", "Back pain", ("Back pain",), {4: 40, 3: 60}, V(pain=(5, 8)), {}, (("M54.50", "", "Low back pain, unspecified"),),
             "low back pain after lifting", "Mechanical low back pain, no red flags.", (), None,
             (Med("ketorolac 15 mg injection", "860092", "15", "mg", "IV", offset=(10, 40), prob=0.6),), admit=False),
    _generic("injury_d", "Minor injury", ("Ankle injury", "Wrist injury", "Laceration", "Fall"), {4: 42, 3: 48, 5: 6, 2: 4}, V(pain=(3, 7)), {},
             (("S93.401A", "", "Sprain of right ankle, initial"),), "twisting injury, able to bear weight with pain", "Sprain, splinted, ortho follow up.",
             (), None, (APAP_PO,), admit=False),
    _generic("vomiting_d", "Nausea and vomiting", ("Vomiting", "Nausea"), {3: 76, 4: 24}, V(hr=(85, 105)), {},
             (("R11.2", "", "Nausea with vomiting, unspecified"),), "vomiting since last night", "Gastroenteritis, tolerating PO after fluids.",
             ("CBC", "BMP"), None, (NS, ONDAN), admit=False),
    _generic("psych_d", "Psychological symptoms", ("Anxiety", "Depression"), {3: 50, 2: 30, 4: 20}, V(hr=(80, 105)), {},
             (("F41.9", "", "Anxiety disorder, unspecified"),), "anxiety and insomnia", "Anxiety, safety plan, outpatient follow up.",
             (), None, (), admit=False),
    _generic("uti_d", "Urinary tract infection", ("Dysuria",), {4: 50, 3: 50}, V(), {}, (("N39.0", "68566005", "Urinary tract infection"),),
             "dysuria and frequency", "Uncomplicated cystitis, oral antibiotics.", ("UA",), None, (), admit=False),
    _generic("dizzy_d", "Dizziness", ("Dizziness", "Weakness"), {3: 78, 2: 12, 4: 10}, V(), {}, (("R42", "", "Dizziness and giddiness"),),
             "positional dizziness", "Peripheral vertigo.", ("CBC", "BMP"), {"ECG": (0.7, ("Normal sinus rhythm.",))}, (ONDAN,), admit=False),
    _generic("minor_d", "Other minor complaint", ("Rash", "Ear pain", "Sore throat", "Medication refill", "Eye redness"),
             {4: 52, 5: 18, 3: 30}, V(), {}, (("R21", "", "Rash and other nonspecific skin eruption"),), "minor complaint",
             "Stable for outpatient care.", (), None, (), admit=False),
    _generic("stemi_transfer", "STEMI, transfer for PCI", ("Chest pain",), {1: 100},
             V(hr=(85, 115), sbp=(100, 150), pain=(7, 10)), {}, (("I21.3", "401303003", "STEMI"),),
             "acute crushing chest pain", "STEMI, transferred for primary PCI.", ("CBC", "BMP", "TROP", "COAG"),
             {"ECG": (1.0, ("ST elevation II, III, aVF.",))},
             (Med("aspirin 81 mg chewable tablet", "318272", "324", "mg", "PO", kind="po", offset=(2, 10)),), {"TROP": (80, 600)}, admit=False),
]}

# Admit mix for 120 admits (spec 8.4, scaled): scenario -> count.
ADMIT_COUNTS = {"sepsis_uti": 7, "sepsis_pna": 5, "chf": 9, "copd": 6, "chest_pain_ro": 9, "nstemi": 3, "stroke": 6,
                "ugib": 5, "dka": 3, "cap": 7, "hip_fx": 4, "af_rvr": 4, "aki": 5, "pyelo": 4, "cellulitis": 4,
                "trauma_other": 4, "abdominal": 10, "syncope": 6, "psych_hold": 3, "flu_covid": 4, "other_medical": 12}

# Weights for non admitted visits (NHAMCS 2022 T9 reasons for visit, grouped).
DISCHARGE_WEIGHTS = {"abd_pain_d": 14, "chest_pain_d": 8, "sob_d": 10, "fever_d": 6, "headache_d": 5, "back_pain_d": 6,
                     "injury_d": 18, "vomiting_d": 5, "psych_d": 4, "uti_d": 5, "dizzy_d": 4, "minor_d": 15}


# Medications the simulator orders outside a scenario (admission order set, critical value responses).
INSULIN_PUSH = Med("insulin regular IV push 100 units/mL", "311034", "10", "units", "IV")
DEXTROSE_50 = Med("dextrose 50 % injection", "1795514", "25", "g", "IV")
EXTRA_MEDS = (INSULIN_PUSH, DEXTROSE_50,
              Med("potassium chloride 2 mEq/mL injection", "204520", "40", "mEq", "IV"),
              Med("cefepime 2 g injection", "1665088", "2", "g", "IV", "Q8H"),
              Med("sodium chloride 0.9 % infusion", "313002", "1000", "mL", "IV", "CONTINUOUS", "infusion"))

# Codes added beyond the spec's verified tables. The realism tests look these up
# (RxNav, LOINC) before the dataset is published.
UNVERIFIED = {"rxcui": ["1665060"],
              "loinc": ["92142-9", "94500-6", "36813-4", "36916-5", "24558-9"],
              "icd10": ["L03.115", "S22.41XA", "K85.90", "K81.0", "R45.851", "J10.1", "R53.1", "R10.9", "J06.9",
                        "R51.9", "M54.50", "S93.401A", "R11.2", "F41.9", "R21", "I21.3"]}


def get(key: str) -> Scenario:
    return SCENARIOS.get(key) or GENERIC_ADMITS.get(key) or DISCHARGE[key]
