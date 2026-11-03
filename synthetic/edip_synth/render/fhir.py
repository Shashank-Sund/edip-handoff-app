"""Redox style FHIR R4 event renderer (spec sections 2.2, 6 and 11).

Reads the truth JSONL and writes out_dir/fhir/events.jsonl: one Redox cloud
delivery per line, in delivery order, shaped like a bronze.redox_fhir_events
row (event_id = Meta.Logs[0].ID, bundle = the whole Bundle with the Redox Meta
block) plus delivery_ts for the replayer.

Bundle shape: the spec's recommended primary shape is the cloud delivery, a
transaction Bundle whose entries carry request {POST, <ResourceType>} with the
Redox Meta block as a top level sibling of entry (spec 6.1, 6.8). The webhook
shape (Bundle.type "message", no request elements) is available behind
BUNDLE_TYPE / --bundle-type for sites that relay notifications instead (13.3).
The Meta block is kept in both so the bronze contract holds.

    python -m edip_synth.render.fhir --truth out/world/truth --out out/world
"""
import argparse
import json
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from . import fhir_resources as R
from .common import load_truth, rng_for, ts, write_manifest

BUNDLE_TYPE = "transaction"          # or "message" (spec 13.3)
A04_EVENT = "patient-arrived"        # site knob: some sites map A04 to "patient-registered" (spec 13.1)
EVENT_CODE_URL = "https://api.redoxengine.com/extensions/event-code"
CONTROL_ID_URL = "https://api.redoxengine.com/extensions/message-control-id"
EVENT_DEF = "https://fhir.redoxengine.com/EventDefinition/"
MSG_DEF = "https://fhir.redoxengine.com/MessageDefinition/"

# Redox FHIR event type -> (DataModel suffix, EventDefinition name). The PatientAdmin, Result and
# Document names are documented (spec 6.8); MedicationRequestCreated follows the Order category slug;
# MedicationAdministrationCreated is an extrapolation, Redox lists no administration notification (13.2).
EVENTS = {
    "patient-arrived": ("PatientAdmin", "PatientAdminArrived"), "patient-registered": ("PatientAdmin", "PatientAdminRegistered"),
    "patient-updated": ("PatientAdmin", "PatientAdminUpdated"), "patient-transferred": ("PatientAdmin", "PatientAdminTransferred"),
    "patient-discharged": ("PatientAdmin", "PatientAdminDischarged"), "patient-merged": ("PatientAdmin", "PatientAdminMerged"),
    "result-created": ("Result", "ResultCreated"),
    "document-created": ("Document", "DocumentReferenceCreated"), "document-replaced": ("Document", "DocumentReferenceReplaced"),
    "medication-request-created": ("Order", "MedicationRequestCreated"),
    "medication-administration-created": ("MedicationAdministration", "MedicationAdministrationCreated"),
}
ADT_EVENT = {"arrival": A04_EVENT, "transfer": "patient-transferred", "discharge": "patient-discharged",
             "merge": "patient-merged"}
ADT_CODE = {"arrival": "A04", "transfer": "A02", "discharge": "A03", "cancel_transfer": "A12", "cancel_discharge": "A13",
            "cancel_admit": "A11", "merge": "A40"}
PENDING_CODE = {"pending_admit": "A14", "bed_assigned": "A15", "bed_unassigned": "A26"}   # when the site sends them
CANCELED = {"cancel_transfer": "transfer", "cancel_discharge": "discharge", "cancel_admit": "arrival"}
# Interface counters for message-control-id (spec 2.3): ADT 4x, ORU 5x (labs) and 55x (flowsheets), RDE 6x, RAS 65x, MDM 7x.
IFACE_BASE = {"adt": 40000000, "oru": 50000000, "flo": 55000000, "rde": 60000000, "ras": 65000000, "mdm": 70000000}
CLINICAL_KINDS = ("pending_admit", "class_change", "transfer", "discharge", "isolation_update", "allergy_update")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_ms(dt, ms):
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


class Renderer:
    def __init__(self, truth, bundle_type=BUNDLE_TYPE):
        self.t = truth
        self.plan = truth["render_plan"]
        self.cfg = truth["config"]
        self.btype = bundle_type
        self.rng = rng_for(self.plan, "fhir")
        self.ids = R.Ids(self.plan["seed"])
        self.pats = {p["pat_id"]: p for p in truth["patients"]}
        self.encs = {e["csn"]: e for e in truth["encounters"]}
        self.provs = {p["prov_id"]: p for p in truth["providers"]}
        self.depts = {d["dept_id"]: d for d in truth["departments"]}
        self.dept_by_code = {(d["facility"], d["code"]): d for d in truth["departments"]}
        self.facs = {f["code"]: f for f in truth["facilities"]}
        self.orgs = {c: R.organization(self.ids, f) for c, f in self.facs.items()}
        self.adt = defaultdict(list)
        for a in truth["adt"]:
            self.adt[a["csn"]].append(a)
        self.allergies = defaultdict(list)
        for a in truth["allergies"]:
            self.allergies[a["pat_id"]].append(a)
        self.infections = defaultdict(list)
        for i in truth["infections"]:
            self.infections[i["pat_id"]].append(i)
        self.code_status = {c["csn"]: c for c in truth["code_status"]}
        self.orders = {o["order_id"]: o for o in truth["orders"]}
        self.med_orders = {m["order_med_id"]: m for m in truth["med_orders"]}
        self.snap = defaultdict(list)          # csn -> [(recorded_at, class, status, (dept_id, unit, room, bed))]
        self.events = []
        self.manifest = []
        self.class_variant = {}
        self.a08_flood = set(self.plan.get("F01_a08_flood", []))
        self.empty_a08 = set(self.plan.get("F04_empty_a08", []))
        self.html_notes = set(self.plan.get("F27_encoding_quirks", []))
        self.mixed_panels = set(self.plan.get("F28_mixed_status_panel", []))

    # ------------------------------------------------------------ helpers
    def src(self, fac):
        return fac.lower()

    def source(self, fac):
        return {"ID": self.ids.rid("source", fac), "Name": f"{self.facs[fac]['name']} EHR"}

    def prac(self, prov_id):
        p = self.provs.get(prov_id)
        return R.practitioner(self.ids, p, self.src(p["facility"])) if p else None

    def patient_for(self, enc, at):
        """The retired identity before a merge (fault F12), the surviving one after."""
        if enc["registered_pat_id"]:
            dup = self.pats[enc["registered_pat_id"]]
            if dup["merged_at"] and at < dup["merged_at"]:
                return dup
        return self.pats[enc["pat_id"]]

    def snapshot(self, csn, at):
        """Encounter class, status and location as of a recorded time (for non ADT bundles)."""
        enc = self.encs[csn]
        ed = self.dept_by_code[(enc["facility"], "ED")]
        best = ("E", "arrived", (ed["dept_id"], "ED", "WR", ""))
        for rec, cls, status, loc in self.snap[csn]:
            if rec <= at:
                best = (cls, status, loc)
        return best

    def fault(self, fid, csn, at, event_id, description, expected):
        self.manifest.append({"fault_id": fid, "csn": csn, "stream": "fhir", "event_time": at, "event_id": event_id,
                              "description": description, "expected": expected})

    def emit(self, key, csn, fac, etype, code, iface, at, resources, focus, canceled=None, variant=""):
        """Materialize one event: entries are serialized now so copies and replays stay cheap."""
        model, name = EVENTS[etype]
        header = {"resourceType": "MessageHeader", "id": self.ids.rid("msg", key), "eventUri": EVENT_DEF + name,
                  "definition": MSG_DEF + name + "Full",
                  "source": {"name": self.source(fac)["Name"], "endpoint": f"urn:uuid:{self.source(fac)['ID']}"},
                  "focus": [R.ref(r) for r in focus]}
        seen, entries = set(), []
        for r in [header] + [r for r in resources if r]:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            e = {"fullUrl": f"urn:uuid:{r['id']}", "resource": r}
            if self.btype == "transaction":
                e["request"] = {"method": "POST", "url": r["resourceType"]}
            entries.append(e)
        ev = {"key": key, "variant": variant, "attempt": 0, "csn": csn, "fac": fac, "model": model, "etype": etype,
              "code": code, "iface": iface, "dt": ts(at), "ms": self.ids.millis(key, variant), "canceled": canceled,
              "body": json.dumps(entries, ensure_ascii=False, separators=(",", ":")), "control": None, "delivery": None}
        self.events.append(ev)
        return ev

    def log_id(self, ev):
        return self.ids.rid("log", ev["key"], ev["variant"])

    # ------------------------------------------------------------ ADT (PatientAdmin)
    def render_adt(self):
        for csn in sorted(self.adt):
            self._encounter(csn)

    def _loc_entry(self, loc, status, start, end=None):
        return {"loc": loc, "status": status, "start": start, "end": end}

    def _encounter(self, csn):
        enc = self.encs[csn]
        fac, src = enc["facility"], self.src(enc["facility"])
        prof = self.cfg["site_profile"][fac]
        org = self.orgs[fac]
        rows = sorted(self.adt[csn], key=lambda a: a["seq"])
        # F30: patient class display strings vary by site, and 10 percent of visits use a third spelling.
        variant = 0 if fac == "SGH" else 1
        if self.rng.random() < 0.10:
            variant = self.rng.choice([2, 3])
        self.class_variant[csn] = variant
        first = None
        flood = sorted(ts(enc["arrival_at"]) + timedelta(minutes=self.rng.uniform(1, 30))
                       for _ in range(self.rng.randint(8, 12))) if csn in self.a08_flood else []
        empty_at = None
        st = {"status": "arrived", "class": "E", "locs": [], "planned": None, "service": "", "admitting": "",
              "priority": None, "reason": None, "dx": False, "period_end": None, "disposition": None,
              "admit_source": False, "cancelled": False, "attending": "", "level_of_care": "",
              "classes": [{"class": "E", "start": enc["arrival_at"], "end": None}]}
        for i, a in enumerate(rows):
            kind, d, eff, rec = a["kind"], a["details"], a["effective_at"], a["recorded_at"]
            loc = (a["dept_id"], a["unit"], a["room"], a["bed"])
            while flood and flood[0] < ts(rec):           # F01 copies carry the state as of their own time
                t = flood.pop(0)
                if i and rows[i - 1]["kind"] not in ("arrival", "cancel_admit"):
                    self._flood(enc, self.pats[rows[i - 1]["pat_id"]], st, rows[i - 1], org, t, len(flood))
            st["class"] = a["patient_class"]
            self._apply(st, enc, kind, d, eff, loc)
            self.snap[csn].append((rec, st["class"], st["status"], loc))
            code = ADT_CODE.get(kind) or (PENDING_CODE.get(kind) if prof["a14"] and kind in PENDING_CODE else None) \
                or (prof["class_change"] if kind == "class_change" else "A08")
            etype = ADT_EVENT.get(kind, "patient-updated")
            pat = self.pats[a["pat_id"]]
            resources, focus = self._adt_bundle(enc, pat, st, a, kind, org)
            ev = self.emit(f"adt/{a['event_id']}", csn, fac, etype, code, "adt", rec, resources, focus,
                           canceled=CANCELED.get(kind))
            first = first or ev
            if kind == "registration" and csn in self.empty_a08:
                empty_at = ts(rec) + timedelta(minutes=self.rng.uniform(2, 20))
            nxt = ts(rows[i + 1]["recorded_at"]) if i + 1 < len(rows) else None
            if empty_at and (nxt is None or empty_at < nxt):
                self._empty_a08(enc, pat, a, org, empty_at)
                empty_at = None
        while flood and rows[-1]["kind"] not in ("arrival", "cancel_admit"):
            self._flood(enc, self.pats[rows[-1]["pat_id"]], st, rows[-1], org, flood.pop(0), len(flood))
        if variant >= 2:
            self.fault("F30", csn, rows[0]["recorded_at"], self.log_id(first),
                       f"Patient class display spelled as variant {variant} on every event of the visit",
                       "Mapping table handles all class strings")

    def _apply(self, st, enc, kind, d, eff, loc):
        locs = st["locs"]
        current = [x for x in locs if x["status"] == "active"]
        if kind == "arrival":
            locs.append(self._loc_entry(loc, "active", eff))
        elif kind == "roomed":
            for x in current:
                x["status"], x["end"] = "completed", eff
            locs.append(self._loc_entry(loc, "active", eff))
            st["status"] = "in-progress"
        elif kind == "triage":
            st["priority"], st["reason"] = d.get("esi"), d.get("chief_complaint")
        elif kind == "provider":
            st["attending"] = d.get("provider", "")
        elif kind == "pending_admit":
            dept = self.dept_by_code.get((enc["facility"], d.get("pending_unit", "")))
            st["planned"] = self._loc_entry((dept["dept_id"], d["pending_unit"], "", ""), "planned", eff) if dept else None
            st.update(service=d.get("service", ""), admitting=d.get("admitting", ""), dx=True, admit_source=True,
                      level_of_care=d.get("level_of_care", ""))
        elif kind == "bed_assigned":
            dept = self.dept_by_code.get((enc["facility"], d["pending_unit"]))
            st["planned"] = self._loc_entry((dept["dept_id"], d["pending_unit"], d["pending_room"], d["pending_bed"]),
                                            "planned", eff) if dept else None
        elif kind == "bed_unassigned":
            if st["planned"]:
                st["planned"]["end"] = eff          # the plan ended: kept in the history, status planned with an end
                locs.append(st["planned"])
            st["planned"] = None
        elif kind == "class_change":
            st["status"] = "in-progress"
            st["classes"][-1]["end"] = eff
            st["classes"].append({"class": st["class"], "start": eff, "end": None})
            if d.get("admitting"):
                st["admitting"] = d["admitting"]
            if d.get("service"):
                st["service"] = d["service"]
        elif kind == "transfer":
            for x in current:
                x["status"], x["end"] = "completed", eff
            locs.append(self._loc_entry(loc, "active", eff))
            st["planned"] = None if st["planned"] and st["planned"]["loc"] == loc else st["planned"]
            st["status"] = "in-progress"
        elif kind == "cancel_transfer":
            if current:
                locs.remove(current[-1])          # the move never happened
            prior = [x for x in locs if x["loc"] == loc]
            if prior:
                prior[-1]["status"], prior[-1]["end"] = "active", None
            else:
                locs.append(self._loc_entry(loc, "active", eff))
        elif kind == "discharge":
            for x in current:
                x["status"], x["end"] = "completed", eff
            st.update(status="finished", period_end=eff, disposition=d.get("discharge_disposition_code"))
        elif kind == "cancel_discharge":
            last = [x for x in locs if x["status"] == "completed"]
            if last:
                last[-1]["status"], last[-1]["end"] = "active", None
            st.update(status="in-progress", period_end=None, disposition=None)
        elif kind == "cancel_admit":
            st.update(status="cancelled", cancelled=True)

    def _encounter_resource(self, enc, pat, st, a, org, resources):
        """Encounter snapshot plus the Location, Practitioner and Condition resources it references."""
        csn, src = enc["csn"], self.src(enc["facility"])
        pat_ref = R.ref({"id": self.ids.rid("pat", pat["pat_id"])})
        code, variants = R.CLASS[st["class"]]
        display = variants[self.class_variant[csn]]
        e = {"resourceType": "Encounter", "id": self.ids.rid("enc", csn),
             "identifier": [{"type": R.cc(R.TERM + "v2-0203", "VN", "Visit number"), "system": R.system_for(src, "VN"),
                             "value": csn}, {"system": R.system_for(src, "AN"), "value": enc["har"]}],
             "status": st["status"], "class": R.coding(R.TERM + "v3-ActCode", code, display),
             "type": [{"coding": [R.coding(R.system_for(src, "patient-class"), st["class"], variants[0])], "text": display}],
             "subject": pat_ref, "participant": [], "period": {"start": enc["arrival_at"]}, "location": [],
             "serviceProvider": R.ref(org, org["name"])}
        if st["service"]:
            e["serviceType"] = {"text": st["service"]}
        if st["priority"]:
            pr = R.ESI[int(st["priority"])]
            e["priority"] = R.cc(R.TERM + "v3-ActPriority", pr[0], pr[1], text=f"ESI {st['priority']}")
        if st["reason"]:
            e["reasonCode"] = [{"text": st["reason"]}]
        if st["period_end"]:
            e["period"]["end"] = st["period_end"]
        e["classHistory"] = [{"class": R.coding(R.TERM + "v3-ActCode", R.CLASS[c["class"]][0], R.CLASS[c["class"]][1][0]),
                              "period": {"start": c["start"], **({"end": c["end"]} if c["end"] else {})}}
                             for c in st["classes"]]
        if st["level_of_care"]:
            e["extension"] = [{"url": R.system_for(src, "level-of-care"), "valueString": st["level_of_care"]}]
        for role, prov_id in (("ATND", st["attending"]), ("ADM", st["admitting"])):
            p = self.prac(prov_id)
            if p:
                resources.append(p)
                e["participant"].append({"type": [R.cc(R.TERM + "v3-ParticipationType", role, R.PARTICIPANT[role])],
                                         "individual": R.ref(p, R.prov_display(self.provs[prov_id]))})
        if st["dx"] and enc["dx"]:
            e["diagnosis"] = []
            for dx in enc["dx"]:
                c = R.encounter_dx(self.ids, csn, dx, pat_ref, R.ref(e), enc["decision_at"] or a["recorded_at"])
                resources.append(c)
                e["diagnosis"].append({"condition": R.ref(c, dx[2]),
                                       "use": R.cc(R.TERM + "diagnosis-role", "AD", "Admission diagnosis")})
        hosp = {}
        if st["admit_source"]:
            hosp["admitSource"] = R.cc(R.TERM + "admit-source", "emd", "From accident/emergency department")
        if st["disposition"]:
            code_, disp = R.DISCHARGE[st["disposition"]]
            hosp["dischargeDisposition"] = {"coding": [R.coding(R.TERM + "discharge-disposition", code_, disp),
                                                       R.coding(R.system_for(src, "UB04FL17"), st["disposition"])],
                                            "text": a["details"].get("disposition") or disp}
        if hosp:
            e["hospitalization"] = hosp
        entries = list(st["locs"]) + ([st["planned"]] if st["planned"] else [])
        for x in entries:
            dept_id, unit, room, bed = x["loc"]
            chain = R.location_chain(self.ids, self.depts[dept_id], unit, room, bed, org)
            resources.extend(chain)
            le = {"location": R.ref(chain[-1], chain[-1]["name"]), "status": x["status"],
                  "physicalType": chain[-1]["physicalType"]}
            if x["start"]:
                le["period"] = {"start": x["start"]}
                if x["end"]:
                    le["period"]["end"] = x["end"]
            e["location"].append(le)
        return e

    def _clinical_context(self, enc, pat, enc_ref, at, with_problems=False):
        """Allergies, isolation flags and (on registration) problems and home meds: the detailed record."""
        src = self.src(enc["facility"])
        pat_ref = R.ref({"id": self.ids.rid("pat", pat["pat_id"])})
        out = []
        active = [x for x in self.allergies[pat["pat_id"]] if x["noted_at"] <= at]
        for x in active:
            out.append(R.allergy(self.ids, x, src, pat_ref, enc_ref if x["added_csn"] == enc["csn"] else None))
        if not active and pat["allergy_review"] == "No Known Allergies":
            out.append(R.no_known_allergy(self.ids, pat, pat_ref))
        for inf in self.infections[pat["pat_id"]]:
            if inf["added_at"] <= at:
                out.extend(R.infection(self.ids, inf, pat_ref, enc_ref))
        if with_problems:
            out.extend(R.problem(self.ids, pat, pr, pat_ref) for pr in pat["problems"])
            out.extend(R.home_med(self.ids, pat, m, pat_ref) for m in pat["home_meds"])
        return out

    def _adt_bundle(self, enc, pat, st, a, kind, org, mobile=None):
        resources = []
        src = self.src(enc["facility"])
        full = kind not in ("arrival", "cancel_admit")
        p = R.patient(self.ids, pat, src, org, full=full, mobile=mobile)
        resources.append(p)
        e = self._encounter_resource(enc, pat, st, a, org, resources)
        resources.insert(1, e)
        resources.append(org)
        focus = [p, e]
        at = a["recorded_at"]
        if kind == "registration":
            resources += self._clinical_context(enc, pat, R.ref(e), at, with_problems=True)
            resources.append(R.coverage(self.ids, pat, org))
        elif kind in CLINICAL_KINDS:
            resources += self._clinical_context(enc, pat, R.ref(e), at)
        elif kind == "code_status_update":
            cs = self.code_status.get(enc["csn"])
            if cs:
                author = self.prac(cs["entered_by"])
                resources.append(author)
                resources += R.code_status(self.ids, cs, R.ref(p), R.ref(e), R.ref(author))
        elif kind == "merge":
            retired = self.pats[a["details"]["retired"]]
            rp = R.patient(self.ids, retired, src, org, full=False)
            rp["active"] = False
            rp["link"] = [{"other": R.ref(p), "type": "replaced-by"}]
            p["link"] = [{"other": R.ref(rp), "type": "replaces"}]
            resources.append(rp)
            focus = [p, rp, e]
        return resources, focus

    def _flood(self, enc, pat, st, a, org, at, n):
        """F01: A08 churn after arrival. Even copies repeat the current state; odd ones add a mobile phone."""
        mobile = None
        if n % 2:
            home = R.e164(pat["phone"])
            mobile = home[:-3] + home[-1] + home[-2] + home[-3]
        resources, focus = self._adt_bundle(enc, pat, st, {**a, "recorded_at": iso(at)}, "a08_flood", org, mobile=mobile)
        ev = self.emit(f"adt/{a['event_id']}/flood/{n}", enc["csn"], enc["facility"], "patient-updated", "A08", "adt",
                       iso(at), resources, focus)
        self.fault("F01", enc["csn"], iso(at), self.log_id(ev),
                   "A08 flood: " + ("registration churn with a new mobile number" if mobile else "no material change"),
                   "Dedupe on content hash per CSN; encounter state unchanged")

    def _empty_a08(self, enc, pat, a, org, at):
        """F04: a PatientUpdate with no Visit, rendered as a Patient only bundle (no Encounter entry)."""
        p = R.patient(self.ids, pat, self.src(enc["facility"]), org, full=True)
        ev = self.emit(f"adt/{a['event_id']}/empty", enc["csn"], enc["facility"], "patient-updated", "A08", "adt",
                       iso(at), [p, org], [p])
        self.fault("F04", enc["csn"], iso(at), self.log_id(ev), "patient-updated with no Encounter (empty PV1)",
                   "Quarantine with a reason; do not null out location")

    # ------------------------------------------------------------ thin context for clinical feeds
    def _context(self, csn, at):
        """Sparse Patient, thin Encounter and its current Location, as a results or notes interface carries them."""
        enc = self.encs[csn]
        fac, src = enc["facility"], self.src(enc["facility"])
        org = self.orgs[fac]
        pat = self.patient_for(enc, at)
        p = R.patient(self.ids, pat, src, org, full=False)
        cls, status, loc = self.snapshot(csn, at)
        code, variants = R.CLASS[cls]
        e = {"resourceType": "Encounter", "id": self.ids.rid("enc", csn),
             "identifier": [{"system": R.system_for(src, "VN"), "value": csn}, {"system": R.system_for(src, "AN"),
                                                                                 "value": enc["har"]}],
             "status": status, "class": R.coding(R.TERM + "v3-ActCode", code, variants[self.class_variant.get(csn, 0)]),
             "subject": R.ref(p), "period": {"start": enc["arrival_at"]}, "serviceProvider": R.ref(org, org["name"])}
        chain = R.location_chain(self.ids, self.depts[loc[0]], loc[1], loc[2], loc[3], org)
        e["location"] = [{"location": R.ref(chain[-1], chain[-1]["name"]), "status": "active",
                          "physicalType": chain[-1]["physicalType"]}]
        return p, e, chain + [org], fac

    # ------------------------------------------------------------ results
    def render_results(self):
        groups = defaultdict(list)
        for r in self.t["results"]:
            groups[(r["order_id"], r["resulted_at"], r["status"])].append(r)
        for key in sorted(groups, key=lambda k: (k[1], k[0], k[2])):
            rows = sorted(groups[key], key=lambda r: r["line"])
            if key[2] == "F" and key[0] in self.mixed_panels:
                self._result_event(key, rows, mixed=True)
            self._result_event(key, rows)

    def _result_event(self, key, rows, mixed=False):
        order_id, resulted, status = key
        order = self.orders[order_id]
        csn = order["csn"]
        at = iso(ts(resulted) - timedelta(minutes=self.rng.uniform(5, 12))) if mixed else resulted
        p, e, extra, fac = self._context(csn, at)
        src = self.src(fac)
        pat_ref, enc_ref = R.ref(p), R.ref(e)
        lab = {"resourceType": "Organization", "id": self.ids.rid("org", fac, "lab"), "active": True,
               "name": f"{self.facs[fac]['name']} " + ("Imaging" if order["order_type"] == "Imaging" else "Core Laboratory"),
               "partOf": R.ref(self.orgs[fac])}
        requester = self.prac(order["provider"])
        imaging = order["order_type"] == "Imaging"
        code = {"coding": [R.coding(R.system_for(src, "EAP"), order["code"], order["name"])], "text": order["name"]}
        if order["loinc"]:
            code["coding"].insert(0, R.coding(R.LOINC, order["loinc"], order["name"]))
        spec = R.specimen(self.ids, order, src, pat_ref) if order["accession"] and order["collected_at"] else None
        obs = []
        for i, r in enumerate(rows):
            ostatus = R.RESULT_STATUS[r["status"]]
            if mixed:
                ostatus = ["final", "preliminary", "registered"][min(i, 2)]
            obs.append(R.result_observation(self.ids, {**r, "status": r["status"] + ("m" if mixed else "")}, src, ostatus,
                                            pat_ref, enc_ref, R.ref(lab), R.ref(spec) if spec else None,
                                            "imaging" if imaging else "laboratory"))
        done = status in ("F", "C")
        sr = R.service_request(self.ids, order, src, pat_ref, enc_ref, R.ref(requester), code, done)
        dr_status = "partial" if mixed else R.RESULT_STATUS[status]
        dr = R.diagnostic_report(self.ids, {**order, "src": src}, key + (("mixed",) if mixed else ()), dr_status, code,
                                 pat_ref, enc_ref, R.ref(lab), obs, at, rows[0]["observed_at"], based_on=R.ref(sr),
                                 specimen_ref=R.ref(spec) if spec else None,
                                 conclusion=rows[0]["value"] if imaging else None)
        resources = [dr, p, e, sr] + obs + ([spec] if spec else []) + [requester, lab] + extra
        ev_key = f"res/{order_id}/{resulted}/{status}" + ("/mixed" if mixed else "")
        ev = self.emit(ev_key, csn, fac, "result-created", "R01", "oru", at, resources, [dr])
        if mixed:
            self.fault("F28", csn, at, self.log_id(ev), f"Mixed status panel on order {order_id}: final, preliminary and "
                       "registered components in one partial DiagnosticReport", "Partial result handling")

    # ------------------------------------------------------------ vitals
    def render_vitals(self):
        for v in sorted(self.t["vitals"], key=lambda v: (v["entered_at"], v["csn"], v["line"])):
            p, e, extra, fac = self._context(v["csn"], v["entered_at"])
            nurse = self.prac(v["user_id"])
            unvalidated = not v["validated"]          # F24 lives in truth; rendered as preliminary device values
            obs = R.vital_observations(self.ids, v, R.ref(p), R.ref(e), R.ref(nurse), unvalidated)
            dr = R.diagnostic_report(self.ids, {}, ("vit", v["csn"], v["line"]), "preliminary" if unvalidated else "final",
                                     {"coding": [R.coding(R.system_for(self.src(fac), "FLO"), "VS", "Vital signs")],
                                      "text": "Vital signs"}, R.ref(p), R.ref(e), R.ref(nurse), obs, v["entered_at"],
                                     v["taken_at"], category=R.cc(R.TERM + "observation-category", "vital-signs", "Vital Signs"))
            dr["identifier"] = [{"system": R.system_for(self.src(fac), "FLO"), "value": f"{v['csn']}-{v['line']}"}]
            # Only sites with a flowsheet ORU interface carry the HL7 lineage extensions (site profile knob).
            hl7 = self.cfg["site_profile"][fac]["flowsheet_oru"]
            self.emit(f"vit/{v['csn']}/{v['line']}", v["csn"], fac, "result-created", "R01" if hl7 else None,
                      "flo" if hl7 else None, v["entered_at"], [dr, p, e] + obs + [nurse] + extra, [dr])

    # ------------------------------------------------------------ medications
    def render_medications(self):
        for mo in sorted(self.t["med_orders"], key=lambda m: (m["verified_at"], m["order_med_id"])):
            p, e, extra, fac = self._context(mo["csn"], mo["verified_at"])
            prescriber = self.prac(mo["prescriber"])
            mr = R.medication_request(self.ids, mo, self.src(fac), R.ref(p), R.ref(e), R.ref(prescriber))
            self.emit(f"mr/{mo['order_med_id']}", mo["csn"], fac, "medication-request-created", "O11", "rde",
                      mo["verified_at"], [mr, p, e, prescriber] + extra, [mr])
        for m in sorted(self.t["mar"], key=lambda m: (m["taken_at"], m["order_med_id"], m["line"])):
            mo = self.med_orders.get(m["order_med_id"])
            if not mo:
                continue
            p, e, extra, fac = self._context(m["csn"], m["taken_at"])
            nurse = self.prac(m["user_id"])
            mr_ref = R.ref({"id": self.ids.rid("mr", mo["order_med_id"])})
            ma = R.medication_administration(self.ids, m, mo, R.ref(p), R.ref(e), R.ref(nurse), mr_ref)
            ma["identifier"] = [{"system": R.system_for(self.src(fac), "MAR"), "value": f"{m['order_med_id']}-{m['line']}"}]
            mr = R.medication_request(self.ids, mo, self.src(fac), R.ref(p), R.ref(e),
                                      R.ref({"id": self.ids.rid("prac", mo["prescriber"])}))
            prescriber = self.prac(mo["prescriber"])
            self.emit(f"ma/{m['order_med_id']}/{m['line']}", m["csn"], fac, "medication-administration-created", "O17",
                      "ras", m["taken_at"], [ma, p, e, mr, nurse, prescriber] + extra, [ma])

    # ------------------------------------------------------------ notes
    def render_notes(self):
        items = [(v[1], n["note_id"], i, n, v) for n in self.t["notes"] for i, v in enumerate(n["versions"])]
        for at, note_id, i, n, v in sorted(items):
            p, e, extra, fac = self._context(n["csn"], at)
            author = self.prac(n["author"])
            html = note_id in self.html_notes
            doc = R.document_reference(self.ids, n, v, self.src(fac), R.ref(p), R.ref(e), R.ref(author), self.orgs[fac],
                                       html=html)
            etype = "document-replaced" if v[0] == "Signed" else "document-created"
            ev = self.emit(f"note/{note_id}/{i}", n["csn"], fac, etype, "T02", "mdm", at, [doc, p, e, author] + extra, [doc])
            if html:
                self.fault("F27", n["csn"], at, self.log_id(ev), f"Note {note_id} delivered as text/html with entities and "
                           "br tags", "Attachment decoding tolerates both text/plain and text/html")

    # ------------------------------------------------------------ feed faults and delivery
    def assign_control_ids(self):
        counters = Counter()
        for ev in sorted(self.events, key=lambda x: (x["dt"], x["key"])):
            if ev["iface"]:
                ev["control"] = str(IFACE_BASE[ev["iface"]] + counters[ev["iface"]])
                counters[ev["iface"]] += 1

    def inject(self):
        rng = self.rng
        by_csn = defaultdict(list)
        for ev in self.events:
            latency = rng.uniform(0.4, 3.0) if ev["model"] == "PatientAdmin" else rng.uniform(1.0, 8.0)
            ev["delivery"] = ev["dt"] + timedelta(milliseconds=ev["ms"]) + timedelta(seconds=latency)
            if ev["model"] == "PatientAdmin":
                by_csn[ev["csn"]].append(ev)
        base = list(self.events)
        # Delivery shifts first (F05, F06), on the originals only; copies made afterwards inherit them,
        # so no retry or duplicate of a delayed arrival can slip in ahead of the result.
        # F05: the class change (and anything between it and the move) reaches the destination after the transfer.
        for csn in self.plan.get("F05_out_of_order", []):
            evs = sorted(by_csn.get(csn, []), key=lambda x: x["dt"])
            tr = [x for x in evs if x["code"] == "A02"]
            cc_ = [x for x in evs if x["key"].startswith("adt/") and x["code"] in ("A06", "A08") and "class_change" in
                   self._kind_of(x) and x["dt"] < (tr[-1]["dt"] if tr else x["dt"])]
            if not tr or not cc_:
                continue
            late = [x for x in evs if cc_[-1]["dt"] <= x["dt"] < tr[-1]["dt"]]
            delay = rng.uniform(20, 120)
            for k, x in enumerate(late):
                x["delivery"] = tr[-1]["delivery"] + timedelta(seconds=delay + k)
            self.fault("F05", csn, iso(tr[-1]["dt"]), self.log_id(tr[-1]),
                       f"Transfer delivered {delay:.0f} s before the class change it follows",
                       "Sequence by EventDateTime, not arrival order")
        # F06: a lab result lands before the ADT interface has delivered the arrival (and the queue behind it).
        first_result = {}
        for ev in base:
            if ev["iface"] == "oru" and ev["csn"] not in first_result:
                first_result[ev["csn"]] = ev
        for csn, res in sorted(first_result.items()):
            if csn in self.plan.get("F05_out_of_order", []) or rng.random() >= self.plan["F06_oru_before_adt_rate"]:
                continue
            adt = [x for x in by_csn.get(csn, []) if x["delivery"] <= res["delivery"]]
            if not adt:
                continue
            delay = rng.uniform(60, 300)
            for k, x in enumerate(sorted(adt, key=lambda x: x["delivery"])):
                x["delivery"] = res["delivery"] + timedelta(seconds=delay + 0.7 * k)
            self.fault("F06", csn, iso(res["dt"]), self.log_id(res),
                       f"Result delivered {delay:.0f} s before the arrival event", "Hold or late join; the result must not be dropped")
        # F03: content duplicate with a new control id and a new log id (a second message with the same payload).
        n_adt = max((int(v["control"]) for v in base if v["iface"] == "adt"), default=IFACE_BASE["adt"] - 1) + 1
        for ev in base:
            if ev["model"] == "PatientAdmin" and not ev["variant"] and rng.random() < self.plan["F03_content_duplicate_rate"]:
                dup = {**ev, "variant": "dup", "control": str(n_adt), "dt": ev["dt"] + timedelta(seconds=rng.uniform(1, 30))}
                dup["ms"] = self.ids.millis(ev["key"], "dup")
                dup["delivery"] = ev["delivery"] + timedelta(seconds=rng.uniform(2, 60))
                n_adt += 1
                self.events.append(dup)
                self.fault("F03", ev["csn"], iso(dup["dt"]), self.log_id(dup),
                           f"Content duplicate of control id {ev['control']} under control id {dup['control']}",
                           "Content hash dedupe; keep the first")
        # F08: stale location replay, the last A08 before the unit transfer delivered again after it.
        for csn in self.plan.get("F08_stale_location_replay", []):
            evs = sorted(by_csn.get(csn, []), key=lambda x: x["dt"])
            tr = [x for x in evs if x["code"] == "A02"]
            if not tr:
                continue
            before = [x for x in evs if x["dt"] < tr[-1]["dt"] and not x["variant"]]
            stale = ([x for x in before if x["code"] == "A08"] or before)[-1]
            rep = {**stale, "variant": "replay", "delivery": tr[-1]["delivery"] + timedelta(seconds=rng.uniform(30, 300))}
            self.events.append(rep)
            self.fault("F08", csn, iso(rep["dt"]), self.log_id(rep),
                       f"Replay of control id {stale['control']} (ED bay) delivered after the transfer to the unit",
                       "Location must not revert; compare EventDateTime against the last applied event")
        # F02: Redox retry, same log ID with a new AttemptID, 5 to 90 s later.
        for ev in list(self.events):
            if ev["model"] in ("PatientAdmin", "Result") and ev["iface"] in ("adt", "oru") \
                    and rng.random() < self.plan["F02_retransmit_rate"]:
                retry = {**ev, "attempt": 1, "delivery": ev["delivery"] + timedelta(seconds=rng.uniform(5, 90))}
                self.events.append(retry)
                self.fault("F02", ev["csn"], iso(ev["dt"]), self.log_id(ev),
                           "Retry: same Meta.Logs[0].ID, new AttemptID", "Dedupe on Meta.Logs[0].ID; count in a dedupe metric")
        self.events.sort(key=lambda x: (x["delivery"], x["key"], x["variant"], x["attempt"]))

    def _kind_of(self, ev):
        eid = ev["key"].split("/")[1]
        for a in self.adt[ev["csn"]]:
            if a["event_id"] == eid:
                return a["kind"]
        return ""

    # ------------------------------------------------------------ output
    def row(self, ev):
        log, attempt = self.log_id(ev), self.ids.rid("attempt", ev["key"], ev["variant"], ev["attempt"])
        src = self.source(ev["fac"])
        dt = iso_ms(ev["dt"], ev["ms"])
        meta = {"DataModel": f"FHIR.Event.{ev['model']}", "EventType": ev["etype"], "EventDateTime": dt, "Test": True,
                "Source": src, "Destinations": [{"ID": self.ids.rid("destination", "databricks"), "Name": "Databricks Zerobus"}],
                "Logs": [{"ID": log, "AttemptID": attempt}], "FacilityCode": ev["fac"]}
        if ev["canceled"]:
            meta["CanceledEvent"] = ev["canceled"]
        if ev["code"]:
            meta["Extensions"] = {"event-code": {"url": EVENT_CODE_URL, "string": ev["code"]},
                                  "message-control-id": {"url": CONTROL_ID_URL, "string": ev["control"]}}
        bundle = ('{"resourceType":"Bundle","id":%s,"type":%s,"timestamp":%s,"Meta":%s,"entry":%s}'
                  % (json.dumps(log), json.dumps(self.btype), json.dumps(dt),
                     json.dumps(meta, ensure_ascii=False, separators=(",", ":")), ev["body"]))
        row = {"event_id": log, "attempt_id": attempt, "data_model": meta["DataModel"], "event_type": ev["etype"],
               "event_datetime": dt, "source_id": src["ID"], "source_name": src["Name"], "facility_code": ev["fac"],
               "is_test": True, "hl7_event_code": ev["code"], "hl7_control_id": ev["control"] if ev["code"] else None,
               "bundle": "__BUNDLE__", "landing_path": None, "delivery_ts": iso_ms(ev["delivery"], ev["delivery"].microsecond // 1000)}
        return json.dumps(row, ensure_ascii=False, separators=(",", ":")).replace('"__BUNDLE__"', bundle)

    def run(self, out_dir) -> dict:
        self.render_adt()
        self.render_results()
        self.render_vitals()
        self.render_medications()
        self.render_notes()
        self.assign_control_ids()
        self.inject()
        out = Path(out_dir) / "fhir"
        out.mkdir(parents=True, exist_ok=True)
        by_model, by_type = Counter(), Counter()
        with open(out / "events.jsonl", "w", encoding="utf-8") as f:
            for ev in self.events:
                f.write(self.row(ev) + "\n")
                by_model[f"FHIR.Event.{ev['model']}"] += 1
                by_type[ev["etype"]] += 1
        write_manifest(self.manifest, out / "fault_manifest.jsonl")
        faults = Counter(m["fault_id"] for m in self.manifest)
        return {"events": len(self.events), "by_data_model": dict(sorted(by_model.items())),
                "by_event_type": dict(sorted(by_type.items())), "faults": dict(sorted(faults.items())),
                "bytes": (out / "events.jsonl").stat().st_size, "bundle_type": self.btype, "path": str(out / "events.jsonl")}


def render(truth_dir, out_dir, bundle_type=BUNDLE_TYPE) -> dict:
    return Renderer(load_truth(truth_dir), bundle_type).run(out_dir)


def main():
    ap = argparse.ArgumentParser(description="Render the truth world as Redox FHIR R4 events.")
    ap.add_argument("--truth", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--bundle-type", choices=["transaction", "message"], default=BUNDLE_TYPE)
    a = ap.parse_args()
    s = render(a.truth, a.out, a.bundle_type)
    print(f"wrote {s['path']}: {s['events']} events, {s['bytes'] / 1e6:.1f} MB, {s['by_data_model']}, faults {s['faults']}")


if __name__ == "__main__":
    main()
