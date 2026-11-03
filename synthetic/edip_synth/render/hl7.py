"""Raw HL7 v2 renderer: the truth world as an Epic interface engine would push it
(spec section 5). One message per file, CR separated segments, named so that the
lexical order of the files is the delivery order.

    python -m edip_synth.render.hl7 --truth out/world/truth --out out/world

Output: <out>/hl7/<facility>/<interface>/<seq>_<msh10>.hl7, <out>/hl7/messages.jsonl
(index) and <out>/hl7/fault_manifest.jsonl (feed level faults, spec section 11).
Site behavior comes from config.site_profile; feed faults from render_plan.json.
"""
import argparse
import json
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .common import TZ, load_truth, rng_for, ts, write_manifest

CR = "\r"
UTC = timezone.utc
APP ={"ADT": "EPIC", "LAB": "BEAKER", "RAD": "RADIANT", "FLO": "EPIC", "RDE": "WILLOW", "RAS": "WILLOW", "MDM": "EPIC"}
MSH10_BASE = {"ADT": 40000000, "ORU": 50000000, "RAS": 60000000, "MDM": 70000000, "RDE": 80000000}
STRUCT = {"A04": "ADT_A01", "A08": "ADT_A01", "A13": "ADT_A01", "A14": "ADT_A05", "A06": "ADT_A06", "A02": "ADT_A02",
          "A15": "ADT_A15", "A26": "ADT_A21", "A03": "ADT_A03", "A11": "ADT_A09", "A12": "ADT_A12", "A40": "ADT_A39",
          "R01": "ORU_R01", "O11": "RDE_O11", "O17": "RAS_O17", "T02": "MDM_T02"}
RACE = {"white": "2106-3^White^CDCREC", "black": "2054-5^Black or African American^CDCREC", "asian": "2028-9^Asian^CDCREC",
        "hawaiian": "2076-8^Native Hawaiian or Other Pacific Islander^CDCREC",
        "native": "1002-5^American Indian or Alaska Native^CDCREC", "other": "2131-1^Other Race^CDCREC"}
ETHNICITY = {"hispanic": "2135-2^Hispanic or Latino^CDCREC", "nonhispanic": "2186-5^Not Hispanic or Latino^CDCREC"}
LANGUAGE = {"English": "ENG^English", "Spanish": "SPA^Spanish", "Portuguese": "POR^Portuguese", "Haitian Creole": "HAT^Haitian Creole",
            "Vietnamese": "VIE^Vietnamese", "Mandarin": "ZHO^Chinese (Mandarin)"}
SERVICE = {"": "EMR", "Hospital Medicine": "MED", "Critical Care": "CCM", "Observation Medicine": "OBS", "Orthopedics": "ORT"}
ARRIVAL = {"EMS": "A^Ambulance", "Private vehicle": "C^Car", "Walk-in": "F^On foot", "Police": "O^Other", "Helicopter": "H^Helicopter"}
RELATION = {"Spouse": "SPO^Spouse", "Daughter": "CHD^Child", "Son": "CHD^Child", "Sibling": "SIB^Sibling", "Friend": "FND^Friend"}
PAYER = {"Medicare": ("MCR", "Medicare Part A and B", "MEDICARE"), "Medicare Advantage": ("MCA", "Medicare Advantage HMO", "MA PLAN"),
         "Medicaid": ("MCD", "Medicaid Managed Care", "MEDICAID"), "Commercial": ("COM", "Commercial PPO", "COMMERCIAL PLAN"),
         "Self-pay": ("SELF", "Self Pay", "SELF PAY"), "Other": ("OTH", "Other Coverage", "OTHER")}
SEVERITY = {"High": "SV", "Medium": "MO", "Moderate": "MO", "Low": "MI", "Mild": "MI"}
ALLERGY_SYSTEM = {"RxNorm": "RXNORM", "SNOMED": "SCT", "Local": "L", "Unknown": ""}
ALLERGY_TYPE = {"Shellfish": "FA", "Latex": "EA", "Adhesive tape": "EA"}
ROUTE = {"IV": "IV^Intravenous^HL70162", "PO": "PO^Oral^HL70162", "SL": "SL^Sublingual^HL70162", "INH": "IH^Inhalation^L",
         "IM": "IM^Intramuscular^HL70162", "SC": "SC^Subcutaneous^HL70162"}
EVN_REASON = {"arrival": "ED_ARRIVAL", "registration": "FIN", "pending_admit": "ADMIT", "class_change": "ADMIT",
              "bed_assigned": "BED_ASSIGN", "bed_unassigned": "BED_RELEASE", "transfer": "TRANSFER", "discharge": "DISCHARGE",
              "cancel_admit": "CANCEL", "cancel_transfer": "CANCEL", "cancel_discharge": "CANCEL", "merge": "MERGE"}
TRIGGER = {"arrival": "A04", "registration": "A08", "triage": "A08", "roomed": "A08", "provider": "A08", "allergy_update": "A08",
           "isolation_update": "A08", "code_status_update": "A08", "transfer": "A02", "cancel_transfer": "A12",
           "discharge": "A03", "cancel_discharge": "A13", "cancel_admit": "A11", "merge": "A40"}
# Flowsheet row IDs (FLO_MEAS_ID, spec 13.2 (c)) with LOINC alternates.
FLO = [("hr", "8^Pulse^FLO^8867-4^Heart rate^LN", "/min", "NM"), ("rr", "9^Resp^FLO^9279-1^Respiratory rate^LN", "/min", "NM"),
       ("temp", "6^Temp^FLO^8310-5^Body temperature^LN", "Cel", "NM"), ("spo2", "10^SpO2^FLO^59408-5^Oxygen saturation^LN", "%", "NM"),
       ("sbp", "5^BP^FLO^8480-6^Systolic blood pressure^LN", "mm[Hg]", "NM"),
       ("dbp", "5^BP^FLO^8462-4^Diastolic blood pressure^LN", "mm[Hg]", "NM"), ("bp", "5^BP^FLO", "mm[Hg]", "ST"),
       ("o2_device", "301070^O2 Device^FLO", "", "TX"), ("o2_flow", "250026^O2 Flow Rate^FLO", "L/min", "NM"),
       ("pain", "1140^Pain Score^FLO^72514-3^Pain severity 0 to 10^LN", "{score}", "NM"),
       ("gcs", "1500^GCS Total^FLO^9269-2^Glasgow coma score total^LN", "{score}", "NM")]
IMAGING_SERVICE = {"ECG": "EC"}
FLOOD_STATUS = ["WAITING", "TRIAGED", "IN_ROOM", "WITH_PROVIDER"]


def esc(s) -> str:
    """HL7 escaping for free text; line breaks become \\.br\\ (spec F27)."""
    s = "" if s is None else str(s)
    for a, b in (("\\", "\\E\\"), ("|", "\\F\\"), ("^", "\\S\\"), ("~", "\\R\\"), ("&", "\\T\\"), ("\n", "\\.br\\")):
        s = s.replace(a, b)
    return s


def seg(name, fields: dict) -> str:
    """Build a segment from {field number: value}; trailing fields end at the highest set field."""
    n = max(fields) if fields else 0
    return "|".join([name] + [str(fields.get(i, "")) for i in range(1, n + 1)])


def name_parts(full: str):
    parts = full.split()
    return (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (full, "")


class Msg:
    __slots__ = ("interface", "app", "fac", "csn", "mtype", "trigger", "user", "at", "segs", "faults", "ref", "msh10",
                 "delivery", "bad_offset", "idx", "file")

    def __init__(self, interface, app, fac, csn, mtype, trigger, user, at, segs, ref=""):
        self.interface, self.app, self.fac, self.csn = interface, app, fac, csn
        self.mtype, self.trigger, self.user, self.at, self.segs, self.ref = mtype, trigger, user, at, segs, ref
        self.faults, self.msh10, self.delivery, self.bad_offset, self.idx, self.file = [], None, None, False, 0, None

    def copy(self):
        m = Msg(self.interface, self.app, self.fac, self.csn, self.mtype, self.trigger, self.user, self.at, list(self.segs), self.ref)
        m.faults, m.msh10, m.delivery, m.bad_offset = list(self.faults), self.msh10, self.delivery, self.bad_offset
        return m


class Renderer:
    def __init__(self, truth: dict):
        self.t = truth
        self.plan = truth["render_plan"]
        self.profile = truth["config"]["site_profile"]
        self.w_start = datetime.fromisoformat(truth["config"]["window_start"])
        self.w_end = self.w_start + timedelta(days=truth["config"]["days"])
        self.pats = {p["pat_id"]: p for p in truth["patients"]}
        self.encs = {e["csn"]: e for e in truth["encounters"]}
        self.provs = {p["prov_id"]: p for p in truth["providers"]}
        self.depts = {d["dept_id"]: d for d in truth["departments"]}
        self.allergies, self.infections, self.code_status, self.results = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
        self.adt, self.mar, self.vitals = defaultdict(list), defaultdict(list), defaultdict(list)
        for a in truth["allergies"]:
            self.allergies[a["pat_id"]].append(a)
        for i in truth["infections"]:
            self.infections[i["pat_id"]].append(i)
        for c in truth["code_status"]:
            self.code_status[c["csn"]].append(c)
        for r in truth["results"]:
            self.results[r["order_id"]].append(r)
        for a in truth["adt"]:
            self.adt[a["csn"]].append(a)
        for m in truth["mar"]:
            self.mar[m["order_med_id"]].append(m)
        for v in truth["vitals"]:
            self.vitals[v["csn"]].append(v)
        self.orders = {o["order_id"]: o for o in truth["orders"]}
        self.msgs = []
        self.manifest = []
        self.snapshots = defaultdict(list)     # csn -> [(recorded_at, snapshot)] for flood and replay faults

    # ------------------------------------------------------------------ formatting helpers
    def fmt(self, dt, fac, bad_offset=False):
        if dt is None:
            return ""
        loc = dt.astimezone(TZ)
        s = loc.strftime("%Y%m%d%H%M%S")
        if self.profile[fac]["msh7_offset"]:
            off = "-0500" if bad_offset else loc.strftime("%z")
            s += off
        return s

    @staticmethod
    def date(iso):
        return iso.replace("-", "")[:8] if iso else ""

    def xcn(self, prov_id):
        p = self.provs.get(prov_id)
        if not p:
            return ""
        if p["prov_type"] == "Registered Nurse":
            return f"{p['user_id']}^{esc(p['last'])}^{esc(p['first'])}^^^^^^EPIC"
        return f"{p['npi']}^{esc(p['last'])}^{esc(p['first'])}^^^^^^NPI^^^^NPI"

    def operator(self, prov_id):
        p = self.provs.get(prov_id)
        return f"{p['user_id']}^{esc(p['last'])}^{esc(p['first'])}" if p else ""

    def user(self, prov_id):
        p = self.provs.get(prov_id)
        return p["user_id"] if p else ""

    def pat_at(self, csn, at):
        """Patient record in effect: the retired duplicate before a merge (F12)."""
        e = self.encs[csn]
        if e["registered_pat_id"]:
            dup = self.pats[e["registered_pat_id"]]
            if dup.get("merged_at") and at < ts(dup["merged_at"]):
                return dup
        return self.pats[e["pat_id"]]

    def pid3(self, pat):
        return f"{pat['mrn']}^^^MRN^MR~{pat['epi']}^^^EPI^EPI"

    def pid(self, pat, enc, full, variant=None):
        f = {1: "1", 3: self.pid3(pat), 5: f"{esc(pat['last'])}^{esc(pat['first'])}^{esc(pat['middle'])}^^^^L",
             7: self.date(pat["birth_date"]), 8: pat["sex"], 18: enc["har"]}
        if full:
            area, rest = pat["phone"][:3], pat["phone"][4:]
            addr = esc(pat["address"]) + (f"^Apt {variant}" if variant and variant.startswith("addr") else "")
            f.update({10: RACE.get(pat["race"], ""), 11: f"{addr}^^{esc(pat['city'])}^{pat['state']}^{pat['zip']}^USA^L",
                      13: f"({area}){rest}^PRN^PH", 15: LANGUAGE.get(pat["language"], ""), 16: pat["marital"],
                      19: pat["ssn"].replace("-", ""), 22: ETHNICITY.get(pat["ethnicity"], "")})
            if variant == "phone":
                f[13] += f"~({area})555-02{int(pat['mrn']) % 100:02d}^WPN^PH"
        return seg("PID", f)

    def nk1(self, pat):
        ec = pat["emergency_contact"]
        last, first = name_parts(ec["name"])
        return seg("NK1", {1: "1", 2: f"{esc(last)}^{esc(first)}", 3: RELATION.get(ec["relation"], "OTH^Other") + "^HL70063",
                           5: f"({ec['phone'][:3]}){ec['phone'][4:]}^PRN^PH", 7: "EC^Emergency Contact"})

    def gt1_in1(self, pat):
        code, plan, company = PAYER.get(pat["payer"], PAYER["Other"])
        name = f"{esc(pat['last'])}^{esc(pat['first'])}^{esc(pat['middle'])}"
        addr = f"{esc(pat['address'])}^^{esc(pat['city'])}^{pat['state']}^{pat['zip']}^USA"
        gt1 = seg("GT1", {1: "1", 3: name, 5: addr, 6: f"({pat['phone'][:3]}){pat['phone'][4:]}", 11: "SELF"})
        in1 = seg("IN1", {1: "1", 2: f"{code}^{plan}", 3: code, 4: company, 16: name, 17: "SELF", 18: self.date(pat["birth_date"]),
                          36: f"{code}{pat['mrn']}"})
        return [gt1, in1]

    def al1(self, pat, at):
        rows = sorted((a for a in self.allergies.get(pat["pat_id"], []) if ts(a["noted_at"]) <= at), key=lambda a: a["noted_at"])
        out = []
        for i, a in enumerate(rows, 1):
            system = ALLERGY_SYSTEM.get(a["system"], "")
            code = f"{a['code']}^{esc(a['allergen'])}^{system}" if a["code"] else f"^{esc(a['allergen'])}^L"
            out.append(seg("AL1", {1: str(i), 2: ALLERGY_TYPE.get(a["allergen"], "DA"), 3: code,
                                   4: SEVERITY.get(a["severity"], "U"), 5: esc(a["reaction"].upper()), 6: self.date(a["noted_at"])}))
        if not out and pat["allergy_review"] == "No Known Allergies":
            out.append(seg("AL1", {1: "1", 2: "DA", 3: "NKA^No Known Allergies^L"}))
        return out

    def dg1(self, enc, dtype, at, fac):
        return [seg("DG1", {1: str(i), 2: "I10", 3: f"{code}^{esc(name)}^I10", 4: esc(name), 5: self.fmt(at, fac), 6: dtype})
                for i, (code, _, name) in enumerate(enc["dx"], 1)]

    def isolation_at(self, pat_id, at):
        rows = [i for i in self.infections.get(pat_id, []) if ts(i["added_at"]) <= at]
        return rows[-1]["isolation"] if rows else ""

    def code_status_at(self, csn, at):
        rows = [c for c in self.code_status.get(csn, []) if ts(c["activated_at"]) <= at]
        return rows[-1]["status"] if rows else ""

    def msh(self, m: Msg):
        prof = self.profile[m.fac]
        mtype = f"{m.mtype}^{m.trigger}" + (f"^{STRUCT[m.trigger]}" if prof["hl7_version"] != "2.3" else "")
        return seg("MSH", {1: "^~\\&", 2: m.app, 3: m.fac, 4: "RHAPSODY", 5: m.fac, 6: self.fmt(m.at, m.fac, m.bad_offset), 7: m.user,
                           8: mtype, 9: str(m.msh10), 10: "T", 11: prof["hl7_version"], 14: "AL", 15: "NE", 17: "UNICODE UTF-8"})

    # ------------------------------------------------------------------ ADT
    def loc(self, unit, room, bed, fac):
        return f"{unit}^{room}^{bed}^{fac}"

    def pv1(self, s, trigger):
        fac, enc = s["fac"], s["enc"]
        f = {1: "1", 2: s["class"], 3: self.loc(*s["loc"], fac), 4: "E", 7: self.xcn(s["attending"]), 10: SERVICE.get(s["service"], "MED"),
             14: "7", 18: s["ptype"], 19: f"{enc['csn']}^^^EPI^CSN", 39: fac, 44: self.fmt(ts(enc["arrival_at"]), fac)}
        if trigger in ("A02", "A06", "A12", "A13") and s.get("prior_loc"):
            f[6] = self.loc(*s["prior_loc"], fac)
        if s.get("admitting"):
            f[17] = self.xcn(s["admitting"])
        if s.get("pending"):
            f[42] = self.loc(*s["pending"], fac)
        if trigger == "A03":
            f[36] = s["disposition_code"]
            f[45] = self.fmt(s["effective"], fac)
        return seg("PV1", f)

    def pv2(self, s, variant=None):
        f = {}
        if s.get("cc"):
            cc = s["cc"] + (" x 2 days" if variant == "cc" else "")
            f[3] = "^" + esc(cc)
        if variant and variant.startswith("status"):
            f[24] = FLOOD_STATUS[int(variant[-1]) % len(FLOOD_STATUS)]
        if s.get("arrival_mode"):
            f[38] = ARRIVAL.get(s["arrival_mode"], "O^Other")
        return [seg("PV2", f)] if f else []

    def adt_obx(self, s, trigger):
        rows = []
        if trigger == "A04":
            rows.append(("TX", "ARRIVALMODE^Arrival Mode^L", esc(s["arrival_mode"]), ""))
        if s.get("esi"):
            rows.append(("NM", "ACUITY^ESI Acuity Level^L", str(s["esi"]), ""))
        if s.get("code_status"):
            rows.append(("TX", "CODESTATUS^Code Status^L", esc(s["code_status"]), ""))
        if s.get("isolation"):
            rows.append(("TX", "ISOLATION^Isolation Precautions^L", esc(s["isolation"]), ""))
        if s.get("esi") and s["enc"]["weight_kg"]:
            rows.append(("NM", "3141-9^Body weight^LN", f"{s['enc']['weight_kg']:g}", "kg"))
        return [seg("OBX", {1: str(i), 2: typ, 3: code, 5: val, 6: unit, 11: "F"}) for i, (typ, code, val, unit) in enumerate(rows, 1)]

    def z_segments(self, s):
        if not self.profile[s["fac"]]["z_segments"]:
            return []
        enc, fac = s["enc"], s["fac"]
        label = {"ADMIT_IP": "Admit", "ADMIT_OBS": "Observation", "DISCHARGE": "Discharge", "LWBS": "LWBS", "AMA": "AMA",
                 "LBTC": "LBTC", "TRANSFER": "Transfer"}.get(enc["disposition"], "")
        decided = enc["decision_at"] and ts(enc["decision_at"]) <= s["effective"]
        zpv = seg("ZPV", {1: ARRIVAL.get(enc["arrival_mode"], "O^Other")[0], 2: str(s.get("esi") or ""), 3: label if decided else "",
                          4: self.fmt(ts(enc["decision_at"]), fac) if decided else "",
                          5: self.fmt(ts(enc["bed_request_at"]), fac) if enc["bed_request_at"] and ts(enc["bed_request_at"]) <= s["effective"] else "",
                          6: self.fmt(ts(enc["bed_assigned_at"]), fac) if enc["bed_assigned_at"] and ts(enc["bed_assigned_at"]) <= s["effective"] else ""})
        pat = s["pat"]
        zpd = seg("ZPD", {1: LANGUAGE.get(pat["language"], "").split("^")[0], 2: "Y" if pat["interpreter"] else "N",
                          3: "Active" if int(pat["mrn"]) % 3 else "Inactive"})
        return [zpv, zpd]

    def adt_body(self, s, trigger, variant=None):
        """Segments after MSH for one ADT message from a state snapshot."""
        fac = s["fac"]
        evn = seg("EVN", {2: self.fmt(s["recorded"], fac), 4: s.get("reason", ""), 5: self.operator(s["user"]), 6: self.fmt(s["effective"], fac)})
        full = s["registered"] and trigger != "A11"
        out = [evn, self.pid(s["pat"], s["enc"], full, variant)]
        if trigger == "A40":
            r = s["retired"]
            out.append(seg("MRG", {1: self.pid3(r), 7: f"{esc(r['last'])}^{esc(r['first'])}^^^^^L"}))
            out.append(self.pv1(s, trigger))
            return out
        if trigger in ("A04", "A08", "A13", "A06", "A14") and full:
            out.append(self.nk1(s["pat"]))
        out.append(self.pv1(s, trigger))
        out += self.pv2(s, variant)
        if trigger == "A03":
            out += self.dg1(s["enc"], "F", s["effective"], fac) + self.adt_obx(s, trigger)
        elif trigger in ("A02", "A15", "A26", "A11", "A12"):
            out += self.adt_obx(s, trigger)
        else:
            out += self.adt_obx(s, trigger) + self.al1(s["pat"], s["recorded"])
            if s.get("dx_type"):
                out += self.dg1(s["enc"], s["dx_type"], s["effective"], fac)
            if full:
                out += self.gt1_in1(s["pat"])
            out += self.z_segments(s)
        return out

    def trigger_for(self, a, prof):
        k = a["kind"]
        if k == "pending_admit":
            return "A14" if prof["a14"] else "A08"
        if k == "class_change":
            return prof["class_change"]
        if k == "bed_assigned":
            return "A15" if prof["a14"] else "A08"
        if k == "bed_unassigned":
            return "A26" if prof["a14"] else "A08"
        return TRIGGER[k]

    def build_adt(self):
        for csn in sorted(self.adt):
            enc = self.encs[csn]
            fac = enc["facility"]
            prof = self.profile[fac]
            state = {"registered": False, "esi": None, "cc": "", "attending": enc["ed_attending"], "admitting": "", "service": "",
                     "pending": None, "dx_type": "", "ptype": "ED", "class": "E", "arrival_mode": enc["arrival_mode"]}
            for a in sorted(self.adt[csn], key=lambda a: a["seq"]):
                k, d = a["kind"], a["details"]
                eff, rec = ts(a["effective_at"]), ts(a["recorded_at"])
                if k == "registration":
                    state["registered"] = True
                elif k == "triage":
                    state["esi"], state["cc"] = d["esi"], d["chief_complaint"]
                elif k == "provider":
                    state["dx_type"] = "W" if enc["dx"] else ""
                elif k == "pending_admit":
                    state.update(admitting=d["admitting"], service=d["service"], pending=(d["pending_unit"], "", ""), dx_type="A" if enc["dx"] else "")
                elif k == "class_change":
                    state.update(service=d["service"], ptype="OBS" if a["patient_class"] == "OBS" else "IP")
                    if d.get("admitting"):
                        state["admitting"] = d["admitting"]
                elif k == "bed_assigned":
                    state["pending"] = (d["pending_unit"], d["pending_room"], d["pending_bed"])
                elif k == "bed_unassigned":
                    state["pending"] = (enc["unit"], "", "")
                elif k == "transfer":
                    state["pending"] = None
                cls = a["patient_class"]
                if cls == "OBS" and prof["class_change"] == "A08":
                    cls = "O"                                   # F30: site string variant for observation
                s = {**state, "fac": fac, "enc": enc, "pat": self.pats[a["pat_id"]], "class": cls, "loc": (a["unit"], a["room"], a["bed"]),
                     "prior_loc": (a["prior"]["unit"], a["prior"]["room"], a["prior"]["bed"]) if a["prior"] else None,
                     "attending": a["attending"], "effective": eff, "recorded": rec, "user": a["user_id"], "reason": EVN_REASON.get(k, ""),
                     "code_status": self.code_status_at(csn, rec), "isolation": self.isolation_at(a["pat_id"], rec)}
                if k == "code_status_update":
                    s["code_status"] = d["code_status"]
                if k == "isolation_update":
                    s["isolation"] = d["isolation"]
                if k == "discharge":
                    s["disposition_code"] = d["discharge_disposition_code"]
                if k == "merge":
                    s["retired"] = self.pats[d["retired"]]
                trigger = self.trigger_for(a, prof)
                m = Msg("ADT", APP["ADT"], fac, csn, "ADT", trigger, self.user(a["user_id"]), rec, self.adt_body(s, trigger), a["event_id"])
                if k == "class_change" and trigger == "A08":
                    m.faults.append("F13")
                    self.fault("F13", csn, rec, "Class change rendered as A08 with PV1-2 change, no A06", "Both paths yield the same encounter state", m)
                self.msgs.append(m)
                if k != "merge":
                    self.snapshots[csn].append((rec, s))

    # ------------------------------------------------------------------ ORU
    def oru_head(self, csn, at, app):
        enc = self.encs[csn]
        fac = enc["facility"]
        pat = self.pat_at(csn, at)
        snap = self.state_at(csn, at)
        pv1 = seg("PV1", {1: "1", 2: snap["class"] if snap else "E", 3: self.loc(*(snap["loc"] if snap else ("ED", "", "")), fac),
                          10: SERVICE.get(snap["service"] if snap else "", "MED"), 19: f"{csn}^^^EPI^CSN"})
        return fac, pat, [self.pid(pat, enc, False), pv1]

    def state_at(self, csn, at):
        best = None
        for rec, s in self.snapshots.get(csn, []):
            if rec <= at:
                best = s
        return best

    def lab_oru(self, o, rows, at, status, extra_fault=None):
        """One ORU for an order: rows carry (result row, obx status, value shown)."""
        csn = o["csn"]
        fac, pat, head = self.oru_head(csn, at, APP["LAB"])
        collected = ts(o["collected_at"]) or ts(rows[0][0]["observed_at"])
        placer, filler = f"{o['order_id']}^EPC", f"{o['accession']}^BEAKER"
        prio = "S" if o["priority"] == "STAT" else "R"
        orc = seg("ORC", {1: "RE", 2: placer, 3: filler, 5: "CM" if status in ("F", "C") else "IP",
                          7: f"^^^{self.fmt(ts(o['ordered_at']), fac)}^^{prio}", 9: self.fmt(ts(o["ordered_at"]), fac), 12: self.xcn(o["provider"])})
        first = rows[0][0]
        code = f"{o['code']}^{esc(o['name'])}^L" + (f"^{first['loinc']}^{esc(first['name'])}^LN" if len(rows) == 1 and first["loinc"] else "")
        obr = seg("OBR", {1: "1", 2: placer, 3: filler, 4: code, 7: self.fmt(collected, fac), 14: self.fmt(collected + timedelta(minutes=4), fac),
                          16: self.xcn(o["provider"]), 22: self.fmt(at, fac), 25: status})
        segs = head + [orc, obr]
        n = 0
        for r, st, val in rows:
            n += 1
            rng_txt = f"{r['ref_low']:g}-{r['ref_high']:g}" if r["ref_low"] is not None and r["ref_high"] is not None else ""
            typ = "NM" if r["numeric"] is not None else "ST"
            f = {1: str(n), 2: typ if st != "I" else "NM", 3: f"{r['lab']}^{esc(r['name'])}^L^{r['loinc']}^{esc(r['name'])}^LN",
                 5: esc(val), 6: r["units"], 7: rng_txt, 8: r["flag"] if st != "I" else "", 11: st}
            if st != "I":
                f[14] = self.fmt(ts(r["observed_at"]), fac)
            segs.append(seg("OBX", f))
            if r["comment"] and st in ("F", "C", "P"):
                segs.append(seg("NTE", {1: "1", 2: "L", 3: esc(r["comment"])}))
        segs.append(seg("SPM", {1: "1", 2: f"{o['accession']}&BEAKER", 4: "BLD^Blood^HL70487" if not o["name"].startswith("Urin") else "UR^Urine^HL70487",
                                17: self.fmt(collected, fac), 18: self.fmt(collected + timedelta(minutes=4), fac)}))
        m = Msg("ORU", APP["LAB"], fac, csn, "ORU", "R01", "", at, segs, o["order_id"])
        if extra_fault:
            m.faults.append(extra_fault)
        return m

    def build_oru(self):
        rng = rng_for(self.plan, "hl7-F28")
        f28 = set(self.plan.get("F28_mixed_status_panel", []))
        for oid in sorted(self.results):
            o = self.orders.get(oid)
            rows = sorted(self.results[oid], key=lambda r: (r["resulted_at"], r["line"]))
            if not o:
                continue
            if o["order_type"] == "Imaging":
                for r in rows:
                    self.msgs.append(self.imaging_oru(o, r))
                continue
            # Labs: every component of a panel results together; corrections resend the panel with one OBX C.
            final = [r for r in rows if r["status"] in ("F", "P")]
            by_time = defaultdict(list)
            for r in final:
                by_time[r["resulted_at"]].append(r)
            for t in sorted(by_time):
                batch = by_time[t]
                status = "F" if all(r["status"] == "F" for r in batch) else "P"
                at = ts(t)
                if oid in f28 and status == "F" and len(batch) >= 3:
                    early = at - timedelta(minutes=rng.uniform(5, 15))
                    mixed = [(r, ["F", "P", "I", "I"][min(i, 3)], r["value"] if i < 2 else "") for i, r in enumerate(batch)]
                    m = self.lab_oru(o, mixed, early, "P", "F28")
                    self.fault("F28", o["csn"], early, f"Mixed status panel on order {oid}: OBX-11 F, P and I under one OBR", "Partial result handling", m)
                    self.msgs.append(m)
                self.msgs.append(self.lab_oru(o, [(r, r["status"], r["value"]) for r in batch], at, status))
            for c in [r for r in rows if r["status"] == "C"]:
                at = ts(c["resulted_at"])
                latest = {r["line"]: r for r in final if ts(r["resulted_at"]) <= at}
                latest[c["line"]] = c
                panel = [(r, "C" if r is c else "F", r["value"]) for _, r in sorted(latest.items())]
                self.msgs.append(self.lab_oru(o, panel, at, "C"))
        self.build_vitals()

    def imaging_oru(self, o, r):
        csn = o["csn"]
        at = ts(r["resulted_at"])
        fac, pat, head = self.oru_head(csn, at, APP["RAD"])
        placer = f"{o['order_id']}^EPC"
        filler = f"{int(o['order_id']) + 100000000}^RADIANT"
        performed = ts(o["collected_at"]) or ts(r["observed_at"])
        status = r["status"]
        orc = seg("ORC", {1: "RE", 2: placer, 3: filler, 5: "CM" if status == "F" else "IP", 9: self.fmt(ts(o["ordered_at"]), fac), 12: self.xcn(o["provider"])})
        obr = seg("OBR", {1: "1", 2: placer, 3: filler, 4: f"{o['code']}^{esc(o['name'])}^L^{o['loinc']}^{esc(o['name'])}^LN", 7: self.fmt(performed, fac),
                          16: self.xcn(o["provider"]), 22: self.fmt(at, fac), 24: IMAGING_SERVICE.get(o["code"], "RAD"), 25: status})
        obx = seg("OBX", {1: "1", 2: "TX", 3: "&IMP^Impression^L", 5: esc(r["value"]), 11: status, 14: self.fmt(performed, fac)})
        return Msg("ORU", APP["RAD"], fac, csn, "ORU", "R01", "", at, head + [orc, obr, obx], o["order_id"])

    def build_vitals(self):
        for csn in sorted(self.vitals):
            fac = self.encs[csn]["facility"]
            for v in sorted(self.vitals[csn], key=lambda v: v["line"]):
                device = not v["validated"]                 # F24: monitor value a nurse never validated
                if not self.profile[fac]["flowsheet_oru"] and not device:
                    continue
                at = ts(v["entered_at"])
                taken = ts(v["taken_at"])
                _, pat, head = self.oru_head(csn, at, APP["FLO"])
                obr = seg("OBR", {1: "1", 4: "VITALS^Vital Signs^L", 7: self.fmt(taken, fac), 22: self.fmt(at, fac), 25: "F"})
                segs = head + [obr]
                n = 0
                vals = dict(v)
                vals["bp"] = f"{v['sbp']}/{v['dbp']}"
                for key, code, unit, typ in FLO:
                    val = vals.get(key)
                    if val is None or val == "":
                        continue
                    n += 1
                    f = {1: str(n), 2: typ, 3: code, 5: esc(val), 6: unit, 11: "P" if device else "F", 14: self.fmt(at, fac)}
                    if device:
                        f[18] = "MONITOR^Bedside monitor^L"
                    else:
                        f[16] = self.xcn(v["user_id"])
                    segs.append(seg("OBX", f))
                m = Msg("ORU", APP["FLO"], fac, csn, "ORU", "R01", "" if device else self.user(v["user_id"]), at, segs, f"vitals:{csn}:{v['line']}")
                if device:
                    m.faults.append("F24")
                    self.fault("F24", csn, at, f"Device vitals line {v['line']} sent unvalidated (OBX-11 P), not in flowsheet tables",
                               "Pipeline distinguishes validated from unvalidated vitals", m)
                self.msgs.append(m)

    # ------------------------------------------------------------------ medications
    def rate_parts(self, rate):
        parts = rate.split(" ", 1)
        return (parts[0], parts[1]) if len(parts) == 2 else ("", "")

    def build_meds(self):
        for mo in sorted(self.t["med_orders"], key=lambda m: m["order_med_id"]):
            csn, fac = mo["csn"], self.encs[mo["csn"]]["facility"]
            at = ts(mo["verified_at"])
            _, pat, head = self.oru_head(csn, at, APP["RDE"])
            timing = f"^{mo['freq']}^^{self.fmt(ts(mo['ordered_at']), fac)}^^R"
            drug = f"{mo['rxcui']}^{esc(mo['name'])}^RXNORM"
            orc = seg("ORC", {1: "NW", 2: f"{mo['order_med_id']}^EPC", 5: "IP", 7: timing, 9: self.fmt(ts(mo["ordered_at"]), fac),
                              10: self.xcn(mo["prescriber"]), 11: "RPH01^Pharmacist^Verifying^^^^^^EPIC", 12: self.xcn(mo["prescriber"])})
            rxe = {1: timing, 2: drug, 3: mo["dose"], 5: f"{mo['unit']}^^UCUM", 7: esc(mo["comment"]) if mo["comment"] else "", 13: self.xcn(mo["prescriber"]), 31: mo["rxcui"]}
            if mo["rate"]:
                rxe[23], rxe[24] = self.rate_parts(mo["rate"])
            segs = head + self.al1(pat, at) + [orc, seg("RXE", rxe), seg("RXR", {1: ROUTE.get(mo["route"], f"{mo['route']}^^L")})]
            self.msgs.append(Msg("RDE", APP["RDE"], fac, csn, "RDE", "O11", "RPH01", at, segs, mo["order_med_id"]))
            for a in sorted(self.mar.get(mo["order_med_id"], []), key=lambda a: a["line"]):
                taken = ts(a["taken_at"])
                at = taken + timedelta(seconds=30 + (int(mo["order_med_id"]) * 7 + a["line"] * 13) % 240)
                _, pat, head = self.oru_head(csn, at, APP["RAS"])
                orc = seg("ORC", {1: "RE", 2: f"{mo['order_med_id']}^EPC", 5: "CM", 7: timing, 9: self.fmt(ts(mo["ordered_at"]), fac), 12: self.xcn(mo["prescriber"])})
                held = a["action"] == "Held"
                rxa = {1: "0", 2: str(a["line"]), 3: self.fmt(taken, fac), 4: self.fmt(taken, fac), 5: drug, 6: a["dose"], 7: f"{a['unit']}^^UCUM",
                       9: esc(a["action"]), 10: self.xcn(a["user_id"]), 20: "NA" if held else "CP", 21: "A"}
                if held:
                    rxa[18] = "HELD^Held per clinician^L"
                segs = head + [orc, seg("RXA", rxa), seg("RXR", {1: ROUTE.get(a["route"], f"{a['route']}^^L")})]
                if a["rate"]:
                    val, unit = self.rate_parts(a["rate"])
                    segs.append(seg("OBX", {1: "1", 2: "NM", 3: "RATE^Infusion Rate^L", 5: val, 6: unit, 11: "F", 14: self.fmt(taken, fac)}))
                self.msgs.append(Msg("RAS", APP["RAS"], fac, csn, "RAS", "O17", self.user(a["user_id"]), at, segs, f"{mo['order_med_id']}:{a['line']}"))

    # ------------------------------------------------------------------ notes
    def build_mdm(self):
        quirks = list(self.plan.get("F27_encoding_quirks", []))
        big_done = False
        for n in sorted(self.t["notes"], key=lambda n: n["note_id"]):
            csn, fac = n["csn"], self.encs[n["csn"]]["facility"]
            text = n["text"]
            quirk = n["note_id"] in quirks
            if quirk:
                text += ("\nAddendum: d/w pt & family; ~500 mL NS given | repeat K+ 3.9 (ref 3.5-5.1) ^ see MAR."
                         f"\nDictation file: ED\\notes\\{n['note_id']}.txt")
                if not big_done:
                    big_done = True
                    filler = "\n".join(f"Outside record {i:03d}: prior visit summary imported from scanned media, see media tab for the full document."
                                       for i in range(1, 370))
                    text += "\nOutside records (imported):\n" + filler
            for status, at_iso in n["versions"]:
                at = ts(at_iso)
                _, pat, head = self.oru_head(csn, at, APP["MDM"])
                au = status == "Signed"
                txa = seg("TXA", {1: "1", 2: esc(n["note_type"]), 3: "TX", 4: self.fmt(at, fac), 6: self.fmt(ts(n["created_at"]), fac),
                                  9: self.xcn(n["author"]), 10: self.xcn(n["author"]) if au else "", 12: f"{n['note_id']}^EPIC",
                                  17: "AU" if au else "IP", 19: "AV"})
                segs = [seg("EVN", {1: "T02", 2: self.fmt(at, fac)})] + head + [txa]
                for i, para in enumerate(text.split("\n"), 1):
                    segs.append(seg("OBX", {1: str(i), 2: "TX", 3: f"{esc(n['note_type'])}^{esc(n['note_type'])}^L", 5: esc(para), 11: "F"}))
                m = Msg("MDM", APP["MDM"], fac, csn, "MDM", "T02", self.user(n["author"]), at, segs, f"{n['note_id']}:{status}")
                if quirk:
                    m.faults.append("F27")
                    size = sum(len(s) for s in segs)
                    self.fault("F27", csn, at, f"Note {n['note_id']} with HL7 escape sequences" + (f", about {size // 1024} KB" if size > 50000 else ""),
                               "Escape handling; VARIANT accepts", m)
                self.msgs.append(m)

    # ------------------------------------------------------------------ feed level faults
    def fault(self, fid, csn, at, desc, expected, m: Msg):
        self.manifest.append({"fault_id": fid, "csn": csn, "stream": "hl7",
                              "event_time": at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if at else None,
                              "description": desc, "expected": expected, "_msg": m})

    def inject_adt_faults(self):
        rng = rng_for(self.plan, "hl7-adt-faults")
        # F01: A08 floods inside 30 minutes of arrival, half with no material change.
        for csn in self.plan.get("F01_a08_flood", []):
            snaps = self.snapshots.get(csn)
            if not snaps:
                continue
            arrival = ts(self.encs[csn]["arrival_at"])
            n = rng.randint(8, 12)
            times = sorted(arrival + timedelta(seconds=rng.uniform(60, 1800)) for _ in range(n))
            for i, t in enumerate(times):
                if t > self.w_end:
                    break
                base = self.state_at(csn, t) or snaps[0][1]
                s = {**base, "effective": t, "recorded": t, "reason": "EDIT_ENCOUNTER", "prior_loc": None}
                variant = None if i % 2 == 0 else ["cc", "addr%d" % rng.randint(1, 40), "status%d" % rng.randint(0, 3), "phone"][i // 2 % 4]
                m = Msg("ADT", APP["ADT"], s["fac"], csn, "ADT", "A08", self.user(s["user"]), t, self.adt_body(s, "A08", variant), "")
                m.faults.append("F01")
                self.fault("F01", csn, t, "A08 flood: " + ("no material change" if variant is None else f"registration edit ({variant})"),
                           "Dedupe on content hash per CSN; encounter state unchanged", m)
                self.msgs.append(m)
        # F04: empty A08 (MSH, EVN, PID, blank PV1).
        for csn in self.plan.get("F04_empty_a08", []):
            snaps = self.snapshots.get(csn)
            if not snaps:
                continue
            t = snaps[0][0] + timedelta(seconds=rng.uniform(300, 2400))
            s = self.state_at(csn, t) or snaps[0][1]
            segs = [seg("EVN", {2: self.fmt(t, s["fac"]), 6: self.fmt(t, s["fac"])}), self.pid(s["pat"], s["enc"], False), "PV1|1"]
            m = Msg("ADT", APP["ADT"], s["fac"], csn, "ADT", "A08", "", t, segs, "")
            m.faults.append("F04")
            self.fault("F04", csn, t, "A08 with MSH, EVN, PID and a blank PV1", "Quarantine with a reason; do not null out location", m)
            self.msgs.append(m)
        # F08: stale ED location replayed after the A02 (queue replay, new control ID, old EVN-2).
        for csn in self.plan.get("F08_stale_location_replay", []):
            a02 = [m for m in self.msgs if m.csn == csn and m.trigger == "A02" and not m.faults]
            if not a02:
                continue
            last = a02[-1]
            stale = [(rec, s) for rec, s in self.snapshots[csn] if rec < last.at and s["loc"][0] == "ED"]
            if not stale:
                continue
            rec, s = stale[-1]
            t = last.at + timedelta(seconds=rng.uniform(30, 300))
            m = Msg("ADT", APP["ADT"], s["fac"], csn, "ADT", "A08", self.user(s["user"]), t, self.adt_body(s, "A08"), "")
            m.faults.append("F08")
            self.fault("F08", csn, t, f"A08 with stale ED location {self.loc(*s['loc'], s['fac'])} delivered after the A02 (EVN-2 {rec.isoformat()})",
                       "Location must not revert; compare EVN-2 against the last applied event", m)
            self.msgs.append(m)

    def assign_control_ids(self):
        """Per facility and interface counters in send order; both facilities start at the
        same base, so MSH-10 alone is not unique and receivers must key on MSH-3/4/10."""
        counters = {}
        for i, m in enumerate(self.msgs):
            m.idx = i
        for m in sorted(self.msgs, key=lambda m: (m.fac, m.interface, m.at, m.idx)):
            key = (m.fac, m.interface)
            if key not in counters:
                counters[key] = MSH10_BASE[m.interface] + rng_for(self.plan, f"hl7-msh10-{m.interface}").randrange(100000)
            m.msh10 = counters[key]
            counters[key] += 1
        lat = rng_for(self.plan, "hl7-latency")
        for m in self.msgs:
            m.delivery = m.at + timedelta(seconds=lat.uniform(0.2, 4.0))

    def inject_delivery_faults(self):
        rng = rng_for(self.plan, "hl7-delivery")
        by_csn = defaultdict(list)
        for m in self.msgs:
            by_csn[m.csn].append(m)
        # F05: the class change message is held in the engine queue until after the A02.
        for csn in self.plan.get("F05_out_of_order", []):
            adt = [m for m in by_csn[csn] if m.interface == "ADT" and m.ref]
            a02 = [m for m in adt if m.trigger == "A02"]
            cc = [m for m in adt if m.ref and self.event_kind(m.ref) == "class_change" and a02 and m.at <= a02[-1].at]
            if not a02 or not cc:
                continue
            held = cc[-1]
            held.delivery = a02[-1].delivery + timedelta(seconds=rng.uniform(20, 120))
            held.faults.append("F05")
            self.fault("F05", csn, held.at, f"A02 delivered {int((held.delivery - a02[-1].delivery).total_seconds())} s before the {held.trigger} class change",
                       "Sequence by EVN-2 (fallback MSH-7), not arrival order", held)
        # F06: ADT interface outage; early lab results arrive before the A04.
        for csn in sorted(by_csn):
            adt = sorted((m for m in by_csn[csn] if m.interface == "ADT"), key=lambda m: m.delivery)
            oru = sorted((m for m in by_csn[csn] if m.interface == "ORU" and m.app == APP["LAB"]), key=lambda m: m.delivery)
            if not adt or not oru or adt[0].trigger != "A04" or oru[0].delivery - adt[0].delivery > timedelta(minutes=90):
                continue
            if rng.random() >= self.plan.get("F06_oru_before_adt_rate", 0):
                continue
            t0 = oru[0].delivery + timedelta(seconds=rng.uniform(60, 300))
            shifted = 0
            for i, m in enumerate(adt):
                if m.delivery <= oru[0].delivery:
                    m.delivery = t0 + timedelta(seconds=i * 0.5)
                    shifted += 1
            adt[0].faults.append("F06")
            self.fault("F06", csn, oru[0].at, f"Lab ORU {oru[0].msh10} delivered before the A04; {shifted} ADT messages delayed",
                       "Hold or late join; the result must not be dropped", adt[0])

    def event_kind(self, event_id):
        if not hasattr(self, "_kind"):
            self._kind = {a["event_id"]: a["kind"] for a in self.t["adt"]}
        return self._kind.get(event_id, "")

    def inject_duplicates(self):
        rng = rng_for(self.plan, "hl7-dupes")
        extra = []
        for m in list(self.msgs):
            if m.interface in ("ADT", "ORU") and rng.random() < self.plan.get("F02_retransmit_rate", 0):
                d = m.copy()
                d.delivery = m.delivery + timedelta(seconds=rng.uniform(5, 90))
                d.faults.append("F02")
                self.fault("F02", m.csn, m.at, f"Retransmit of {m.mtype}^{m.trigger} with the same MSH-10 {m.msh10} (lost ACK)",
                           "Dedupe on MSH-3, MSH-4, MSH-10", d)
                extra.append(d)
            if m.interface == "ADT" and rng.random() < self.plan.get("F03_content_duplicate_rate", 0):
                d = m.copy()
                d.at = m.at + timedelta(seconds=rng.uniform(10, 300))
                # A resave goes through the same queue, so a delayed original (F05, F06) delays the copy too.
                d.delivery = max(d.at, m.delivery) + timedelta(seconds=rng.uniform(0.2, 4.0))
                d.msh10 = None
                d.faults = [f for f in m.faults if f not in ("F05", "F06")] + ["F03"]
                self.fault("F03", m.csn, d.at, f"Content duplicate of {m.mtype}^{m.trigger} {m.msh10} with a new MSH-10 and MSH-7",
                           "Content hash dedupe; keep the first", d)
                extra.append(d)
        # New control IDs for the F03 copies, continuing each counter.
        top = {}
        for m in self.msgs:
            top[(m.fac, m.interface)] = max(top.get((m.fac, m.interface), 0), m.msh10)
        for d in sorted((d for d in extra if d.msh10 is None), key=lambda d: (d.fac, d.at)):
            top[(d.fac, d.interface)] += 1
            d.msh10 = top[(d.fac, d.interface)]
        self.msgs += extra

    def inject_envelope_faults(self):
        rng = rng_for(self.plan, "hl7-envelope")
        mgh = [m for m in self.msgs if m.interface == "ADT" and self.profile[m.fac]["msh7_offset"] and not m.faults
               and m.at.astimezone(TZ).utcoffset() == timedelta(hours=-4)]
        for _ in range(int(self.plan.get("F25_offset_mix", {}).get("inconsistent_messages", 0))):
            m = rng.choice(mgh)
            m.bad_offset = True
            m.faults.append("F25")
            self.fault("F25", m.csn, m.at, "MSH-7 carries -0500 while local time is EDT (-0400): one hour off after normalization",
                       "Normalize to UTC; flag the inconsistent message", m)
        for fac, prof in sorted(self.profile.items()):
            self.manifest.append({"fault_id": "F25", "csn": "", "stream": "hl7", "event_time": None,
                                  "description": f"{fac} MSH-7 " + ("with -0400/-0500 offsets" if prof["msh7_offset"] else "without offset"),
                                  "expected": "Normalize to UTC", "_msg": None})
            self.manifest.append({"fault_id": "F26", "csn": "", "stream": "hl7", "event_time": None,
                                  "description": f"{fac} " + ("sends ZPV and ZPD" if prof["z_segments"] else "sends no Z segments"),
                                  "expected": "Parser tolerates absent and unknown segments", "_msg": None})
            self.manifest.append({"fault_id": "F30", "csn": "", "stream": "hl7", "event_time": None,
                                  "description": f"{fac} PV1-2 uses E, I and " + ("OBS" if prof["class_change"] == "A06" else "O") + " for observation",
                                  "expected": "Mapping table handles all class strings", "_msg": None})
        z_cands = [m for m in self.msgs if m.interface == "ADT" and self.profile[m.fac]["z_segments"] and not m.faults]
        for _ in range(int(self.plan.get("F26_z_segments", {}).get("unknown_segment_messages", 0))):
            m = rng.choice(z_cands)
            m.segs.append(seg("ZZZ", {1: "SITE", 2: "1", 3: "unknown site segment"}))
            m.faults.append("F26")
            self.fault("F26", m.csn, m.at, "Unknown ZZZ segment appended to an ADT message", "Parser tolerates unknown segments", m)

    # ------------------------------------------------------------------ output
    def write(self, out_dir) -> dict:
        root = Path(out_dir) / "hl7"
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        order = sorted(self.msgs, key=lambda m: (m.delivery, m.fac, m.interface, m.msh10, m.idx))
        counts = defaultdict(int)
        with open(root / "messages.jsonl", "w", encoding="utf-8") as idx:
            for n, m in enumerate(order, 1):
                rel = f"{m.fac}/{m.interface}/{n:07d}_{m.msh10}.hl7"
                m.file = rel
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(CR.join([self.msh(m)] + m.segs) + CR, encoding="utf-8")
                counts[(m.fac, f"{m.mtype}^{m.trigger}")] += 1
                idx.write(json.dumps({"file": rel, "delivery_ts": m.delivery.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{m.delivery.microsecond // 1000:03d}Z",
                                      "msh7": self.fmt(m.at, m.fac, m.bad_offset), "msh10": str(m.msh10), "type": m.mtype, "trigger": m.trigger,
                                      "interface": m.interface, "csn": m.csn, "facility": m.fac, "ref": m.ref, "faults": m.faults},
                                     ensure_ascii=False) + "\n")
        rows = []
        for r in self.manifest:
            m = r.pop("_msg")
            rows.append({**r, "file": m.file if m else None, "msh10": str(m.msh10) if m else None})
        write_manifest(rows, root / "fault_manifest.jsonl")
        return {"messages": len(order), "files": len(order), "by_type": {f"{f}:{t}": c for (f, t), c in sorted(counts.items())},
                "faults": dict(sorted(((fid, sum(1 for r in rows if r["fault_id"] == fid)) for fid in {r["fault_id"] for r in rows})))}

    def run(self, out_dir):
        self.build_adt()
        self.build_oru()
        self.build_meds()
        self.build_mdm()
        self.inject_adt_faults()
        self.assign_control_ids()
        self.inject_delivery_faults()
        self.inject_envelope_faults()
        self.inject_duplicates()
        return self.write(out_dir)


def render(truth_dir, out_dir) -> dict:
    return Renderer(load_truth(truth_dir)).run(out_dir)


def main():
    ap = argparse.ArgumentParser(description="Render the truth world as raw HL7 v2 messages.")
    ap.add_argument("--truth", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    s = render(a.truth, a.out)
    print(json.dumps(s, indent=1))


if __name__ == "__main__":
    main()
