"""HL7 v2 parser for the silver layer: one raw message in, contract records out
(pipelines/edip_contract.py). Standard library only; the pipeline wraps parse_hl7 as a UDF.

Handles what an Epic interface engine sends for this dataset: ADT (A04, A08, A14, A06, A15, A26, A02, A12,
A03, A13, A11, A40), ORU R01 (labs, imaging, ECG, flowsheet and device vitals), RDE O11, RAS O17 and MDM T02.
Unknown segments (Z segments, ZZZ) are ignored; a message without a usable MSH or PV1 is quarantined.
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from .edip_contract import ADT_KINDS, empty
except ImportError:                                   # run as a plain script
    from edip_contract import ADT_KINDS, empty

TZ = ZoneInfo("America/New_York")
SKEW = timedelta(minutes=2)          # clock skew allowed between a sender and the engine
UTC = timezone.utc

TRIGGER_KIND = {"A04": "arrival", "A14": "pending_admit", "A06": "class_change", "A15": "bed_assigned", "A26": "bed_unassigned",
                "A02": "transfer", "A12": "cancel_transfer", "A03": "discharge", "A13": "cancel_discharge", "A11": "cancel_admit",
                "A40": "merge"}
# EVN-4 event reason codes the site configures on A08 (spec 5.2: site defined list).
REASON_KIND = {"FIN": "registration", "BED_ASSIGN": "bed_assigned", "BED_RELEASE": "bed_unassigned"}
CLASS = {"E": "E", "I": "I", "O": "OBS", "OBS": "OBS"}
SERVICE = {"EMR": "", "MED": "Hospital Medicine", "CCM": "Critical Care", "OBS": "Observation Medicine", "ORT": "Orthopedics"}
DISPOSITION = {"01": "Home", "02": "Transfer to another facility", "03": "Skilled nursing facility", "06": "Home health",
               "07": "Left against medical advice or discontinued care", "09": "Admitted as inpatient", "20": "Expired"}
NOTE_LOINC = {"ED Triage Notes": "84249-2", "ED Provider Notes": "28568-4", "ED Notes": "57053-1", "H&P": "104530-1",
              "Consults": "11488-4", "ED to Hosp-Admission Nursing Transfer": "28651-8", "Progress Notes": "11506-3"}
NOTE_STATUS = {"AU": "Signed", "IP": "In Progress", "PA": "Signed", "DO": "In Progress", "IN": "In Progress", "DI": "In Progress"}
ROUTE = {"IH": "INH"}
# Flowsheet row id (and LOINC for the shared BP row) to the truth vital key.
FLO_KEY = {"8": "hr", "9": "rr", "6": "temp", "10": "spo2", "301070": "o2_device", "250026": "o2_flow", "1140": "pain", "1500": "gcs"}
BP_KEY = {"8480-6": "sbp", "8462-4": "dbp", "": "bp"}
UNESCAPE = (("\\.br\\", "\n"), ("\\F\\", "|"), ("\\S\\", "^"), ("\\R\\", "~"), ("\\T\\", "&"), ("\\E\\", "\\"))
TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:(\d{2})(\d{2})(\d{2})?)?(?:\.\d+)?([+-]\d{4})?$")


def unescape(s: str) -> str:
    for a, b in UNESCAPE:
        s = s.replace(a, b)
    return s


def parse_ts(s, ref=None):
    """HL7 TS to aware UTC. Without an offset the time is America/New_York. In the repeated hour when DST
    ends (01:00 to 01:59 twice), the later reading (standard time) is taken when ref, the time the value
    cannot follow (receipt time for MSH-7, the message time for fields inside it), is at or within an hour
    after it; otherwise the earlier reading (daylight time). A value sent more than an hour after the
    repeated hour (a note signed the next morning) stays ambiguous and takes the earlier reading. Naive refs
    are UTC."""
    m = TS_RE.match(s or "")
    if not m:
        return None
    y, mo, d, hh, mm, ss, off = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
    if off:
        sign = 1 if off[0] == "+" else -1
        tz = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
        return dt.replace(tzinfo=tz).astimezone(UTC)
    first, later = (dt.replace(tzinfo=TZ, fold=f).astimezone(UTC) for f in (0, 1))
    if first != later and ref is not None:
        ref = ref if ref.tzinfo else ref.replace(tzinfo=UTC)
        if later <= ref + SKEW and ref - later < timedelta(hours=1):
            return later
    return first


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class Seg:
    __slots__ = ("name", "f")

    def __init__(self, raw):
        self.f = raw.split("|")
        self.name = self.f[0]
        if self.name == "MSH":
            self.f.insert(1, "|")                     # so MSH-n is f[n] like every other segment

    def __getitem__(self, n):
        return self.f[n] if n < len(self.f) else ""

    def c(self, n, i=1):
        """Component i of field n (first repetition)."""
        parts = self[n].split("~")[0].split("^")
        return parts[i - 1] if i <= len(parts) else ""

    def reps(self, n):
        return [r for r in self[n].split("~") if r]


def split_segments(content: str):
    text = content.replace("\r\n", "\r").replace("\n", "\r")
    return [Seg(s) for s in text.split("\r") if s.strip()]


def content_hash(content: str) -> str:
    """Hash of everything but MSH-7 and MSH-10, so retransmits and content duplicates (F02, F03) collide."""
    segs = content.replace("\r\n", "\r").replace("\n", "\r").split("\r")
    msh = segs[0].split("|")
    for i in (6, 9):                                   # MSH-7, MSH-10 (MSH-1 is the separator itself)
        if i < len(msh):
            msh[i] = ""
    segs[0] = "|".join(msh)
    return hashlib.sha256("\r".join(segs).encode("utf-8")).hexdigest()


def mrn_of(pid: Seg) -> str:
    for rep in pid.reps(3):
        parts = rep.split("^")
        if len(parts) >= 5 and parts[4] == "MR":
            return parts[0]
    return pid.c(3)


def ident(xcn: str) -> str:
    return xcn.split("^")[0] if xcn else ""


def _quarantine(out, base, reason, ts):
    out["quarantine"].append({"source": "hl7", "msg_key": base.get("msg_key", ""), "facility": base.get("facility", ""),
                              "csn": base.get("csn", ""), "reason": reason, "recorded_ts": ts})


def parse_hl7(content: str, received_ts=None) -> dict:
    out = empty()
    segs = split_segments(content)
    if not segs or segs[0].name != "MSH":
        _quarantine(out, {}, "no MSH segment", received_ts)
        return out
    msh = segs[0]
    base = {"source": "hl7", "msg_key": f"{msh[3]}|{msh[4]}|{msh[10]}", "facility": msh[4]}
    mtype, trigger = msh.c(9, 1), msh.c(9, 2)
    msh_ts = parse_ts(msh[7], received_ts) or received_ts
    by = {}
    for s in segs:
        by.setdefault(s.name, []).append(s)
    pid = by.get("PID", [None])[0]
    pv1 = by.get("PV1", [None])[0]
    csn = pv1.c(19) if pv1 else ""
    if pid is None or pv1 is None or not csn:
        _quarantine(out, {**base, "csn": csn}, "blank PV1, no visit number" if pv1 is not None and pid is not None else "missing PID or PV1", msh_ts)
        return out
    base.update(csn=csn, mrn=mrn_of(pid))
    ctx = {"segs": segs, "by": by, "msh": msh, "pid": pid, "pv1": pv1, "msh_ts": msh_ts, "received": received_ts}
    if mtype == "ADT":
        _adt(out, base, ctx, trigger, content)
    elif mtype == "ORU":
        _oru(out, base, ctx)
    elif mtype == "RDE":
        _rde(out, base, ctx)
    elif mtype == "RAS":
        _ras(out, base, ctx)
    elif mtype == "MDM":
        _mdm(out, base, ctx)
    else:
        _quarantine(out, base, f"unsupported message type {mtype}^{trigger}", msh_ts)
    return out


def _allergies(out, base, ctx):
    for al in ctx["by"].get("AL1", []):
        if al.c(3, 1) == "NKA" or al.c(3, 2) == "No Known Allergies":
            continue
        out["allergy"].append({**base, "allergen": unescape(al.c(3, 2)), "code": al.c(3, 1), "system": al.c(3, 3),
                               "reaction": unescape(al[5]).title(), "severity": {"SV": "High", "MO": "Medium", "MI": "Low"}.get(al[4], al[4]),
                               "recorded_ts": parse_ts(al[6], ctx["msh_ts"])})


def _adt(out, base, ctx, trigger, content):
    by, pid, pv1, msh = ctx["by"], ctx["pid"], ctx["pv1"], ctx["msh"]
    evn = by.get("EVN", [None])[0]
    recorded = (parse_ts(evn[2], ctx["msh_ts"]) if evn else None) or ctx["msh_ts"]
    effective = (parse_ts(evn[6], ctx["msh_ts"]) if evn else None) or recorded
    reason = evn[4] if evn else ""
    cls = CLASS.get(pv1[2], pv1[2] or None)
    kind = TRIGGER_KIND.get(trigger)
    if trigger == "A08":
        kind = REASON_KIND.get(reason)
        if kind is None and reason == "ADMIT":
            kind = "class_change" if cls in ("I", "OBS") else "pending_admit"
        if kind is None:
            kind = "update"
    if kind not in ADT_KINDS:
        _quarantine(out, base, f"unsupported ADT trigger {trigger}", recorded)
        return
    loc = pv1[3].split("^")
    pend = pv1[42].split("^") if pv1[42] else []
    pv2 = by.get("PV2", [None])[0]
    obx = {o.c(3, 1): unescape(o[5]) for o in by.get("OBX", [])}
    mrg = by.get("MRG", [None])[0]
    pend_unit = pend[0] if pend and pend[0] else None
    rec = {**base, "content_hash": content_hash(content), "kind": kind, "trigger": trigger, "effective_ts": effective,
           "recorded_ts": recorded, "patient_class": cls, "unit": loc[0] or None, "room": (loc[1] if len(loc) > 1 else "") or None,
           "bed": (loc[2] if len(loc) > 2 else "") or None, "pending_unit": pend_unit,
           "pending_room": (pend[1] if len(pend) > 1 else "") or None, "pending_bed": (pend[2] if len(pend) > 2 else "") or None,
           "attending_id": ident(pv1[7]) or None, "admitting_id": ident(pv1[17]) or None, "service": SERVICE.get(pv1[10], pv1[10]),
           "level_of_care": None,                     # not on the wire; the unit alone is ambiguous (LKS 3S is TELE or MEDSURG)
           "esi": int(obx["ACUITY"]) if obx.get("ACUITY", "").isdigit() else None,
           "chief_complaint": unescape(pv2.c(3, 2)) if pv2 else "", "disposition": DISPOSITION.get(pv1[36], pv1[36]) if pv1[36] else "",
           "isolation": obx.get("ISOLATION", ""), "code_status": obx.get("CODESTATUS", ""),
           "patient_name": f"{unescape(pid.c(5, 1))}, {unescape(pid.c(5, 2))}", "birth_date": _date(pid[7]), "sex": pid[8],
           "surviving_mrn": base["mrn"] if kind == "merge" else None,
           "retired_mrn": (mrg.c(1) if mrg else None) if kind == "merge" else None}
    if kind == "merge" and mrg:
        for r in mrg.reps(1):
            parts = r.split("^")
            if len(parts) >= 5 and parts[4] == "MR":
                rec["retired_mrn"] = parts[0]
    out["adt"].append(rec)
    for fkind, key in (("isolation", "ISOLATION"), ("code_status", "CODESTATUS")):
        if obx.get(key):
            out["flag"].append({**base, "kind": fkind, "value": obx[key], "recorded_ts": recorded})
    _allergies(out, base, ctx)


def _date(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}" if len(yyyymmdd) >= 8 else None


def _oru(out, base, ctx):
    segs, msh = ctx["segs"], ctx["msh"]
    app = msh[3]
    obr = ctx["by"].get("OBR", [None])[0]
    orc = ctx["by"].get("ORC", [None])[0]
    if obr is None:
        _quarantine(out, base, "ORU without OBR", ctx["msh_ts"])
        return
    panel = obr.c(4, 1)
    if panel == "VITALS":
        category = "vital"
    elif app == "RADIANT" or obr[24] in ("RAD", "EC"):
        category = "ecg" if obr[24] == "EC" else "imaging"
    else:
        category = "lab"
    order_id = None if category == "vital" else (orc.c(2) if orc else "") or obr.c(2) or None
    resulted = parse_ts(obr[22], ctx["msh_ts"]) or ctx["msh_ts"]
    collected = parse_ts(obr[7], ctx["msh_ts"])
    rows = []
    for s in segs:
        if s.name == "OBX":
            rows.append([s, []])
        elif s.name == "NTE" and rows:
            rows[-1][1].append(unescape(s[3]))
    for s, notes in rows:
        code, name, loinc = s.c(3, 1), unescape(s.c(3, 2)), s.c(3, 4)
        if category == "vital":
            code = BP_KEY.get(loinc, "bp") if code == "5" else FLO_KEY.get(code, code)
        elif category in ("imaging", "ecg"):
            code = panel                              # report text rows carry &IMP, the exam code is on OBR-4
            loinc = obr.c(4, 4)
        lo, hi = None, None
        if s[7] and "-" in s[7]:
            lo, hi = (num(x) for x in s[7].split("-", 1))
        device = bool(s[18])
        status = s[11]
        rec = {**base, "category": category, "order_id": order_id, "code": code, "loinc": loinc, "name": name,
               "value": unescape(s[5]), "numeric": num(s[5]) if s[2] == "NM" else None, "units": s[6], "ref_low": lo, "ref_high": hi,
               "flag": s[8], "status": status, "observed_ts": collected if category == "vital" else (parse_ts(s[14], ctx["msh_ts"]) or collected),
               "resulted_ts": parse_ts(s[14], ctx["msh_ts"]) if category == "vital" else resulted, "comment": " ".join(notes),
               "validated": not device and not (category == "vital" and status == "P"),
               "line": None if category == "vital" else (int(s[1]) if s[1].isdigit() else None)}
        out["obs"].append(rec)


def _timing(orc, rxe=None):
    return orc.c(7, 2) if orc and orc[7] else (rxe.c(1, 2) if rxe else "")


def _med_base(base, ctx, orc, drug_field):
    parts = drug_field.split("^")
    return {**base, "order_id": orc.c(2) if orc else "", "rxcui": parts[0], "name": unescape(parts[1]) if len(parts) > 1 else ""}


def _rde(out, base, ctx):
    by = ctx["by"]
    orc, rxe, rxr = by.get("ORC", [None])[0], by.get("RXE", [None])[0], by.get("RXR", [None])[0]
    if rxe is None:
        _quarantine(out, base, "RDE without RXE", ctx["msh_ts"])
        return
    rate = f"{rxe[23]} {rxe[24]}".strip() if rxe[23] else ""
    route = rxr.c(1) if rxr else ""
    out["med"].append({**_med_base(base, ctx, orc, rxe[2]), "dose": rxe[3], "unit": rxe.c(5, 1), "route": ROUTE.get(route, route), "rate": rate,
                       "event": "order", "action": None, "line": None, "event_ts": (parse_ts(orc[9], ctx["msh_ts"]) if orc else None) or ctx["msh_ts"],
                       "frequency": _timing(orc, rxe)})
    _allergies(out, base, ctx)


def _ras(out, base, ctx):
    by = ctx["by"]
    orc, rxr = by.get("ORC", [None])[0], by.get("RXR", [None])[0]
    rate = ""
    for o in by.get("OBX", []):
        if o.c(3, 1) == "RATE":
            rate = f"{o[5]} {o[6]}".strip()
    route = rxr.c(1) if rxr else ""
    for rxa in by.get("RXA", []):
        out["med"].append({**_med_base(base, ctx, orc, rxa[5]), "dose": rxa[6], "unit": rxa.c(7, 1), "route": ROUTE.get(route, route),
                           "rate": rate, "event": "administration", "action": unescape(rxa[9]) or ("Held" if rxa[20] == "NA" else "Given"),
                           "line": int(rxa[2]) if rxa[2].isdigit() else None, "event_ts": parse_ts(rxa[3], ctx["msh_ts"]) or ctx["msh_ts"],
                           "frequency": _timing(orc)})


def _mdm(out, base, ctx):
    by = ctx["by"]
    txa = by.get("TXA", [None])[0]
    if txa is None:
        _quarantine(out, base, "MDM without TXA", ctx["msh_ts"])
        return
    status = NOTE_STATUS.get(txa[17], "In Progress")
    note_type = unescape(txa[2])
    activity = parse_ts(txa[4], ctx["msh_ts"]) or ctx["msh_ts"]
    out["note"].append({**base, "note_id": txa.c(12, 1), "note_type": note_type, "loinc": NOTE_LOINC.get(note_type, ""), "status": status,
                        "author_id": ident(txa[9]) or None, "created_ts": parse_ts(txa[6], ctx["msh_ts"]) or activity, "service_ts": None,
                        "signed_ts": activity if status == "Signed" else None,
                        "text": "\n".join(unescape(o[5]) for o in by.get("OBX", []))})

