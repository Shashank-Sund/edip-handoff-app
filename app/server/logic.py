"""Views computed from the read model and the clinician actions at a time `at`.

  board(at, person)        patients with a handoff: boarding now, or arrived on the unit in the last 12 hours
  case(csn, at, person)    one patient: every template's effective draft (model text with clinician edits and
                           attestations applied), its rules after resolution, workflow state, versions, changes
  analytics(at)            census, boarding times, handoff timing and completeness across the week so far
  act(...)                 validates and records an edit, attestation, sign off or acknowledgement
  own(...)                 Take, Assign to a colleague, Release: who owns the patient for the ED role
  takeover(...), take_over(...)   a colleague's patients at a shift change, and taking some or all of them
  patient_tasks(...), add_task(...), task_act(...), my_tasks(...)   tasks (tasks.py): one patient's, a new one, a
                           change to one, and the caller's across patients for the Tasks page

Every patient has an owner per ED role (assignment.py), shown on the board and the handoff. Acting on a patient
someone else owns is allowed and recorded as outside the assignment (off_assignment). Signing a handoff turns its
action list into tasks; acting on a task while someone else has the patient is allowed and recorded as outside
the role (outside_role).

Sign off follows the content: any edit or attestation after a signature, or a newer version whose clinical content
differs from the signed one, puts the template in state "changed" (sign again); the receiver cannot accept it until
the ED signs again. A stale vitals attestation counts only within that rule's age limit for the patient's severity.
"""
from __future__ import annotations

import copy
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from assignment import HANDOVER_BUCKETS, ROLES, counts, handover_bucket, next_shift, owner_at, person_key
import tasks
from data import Dataset, iso, status_at, ts
from personas import ACKS, SIGNS, sees

ARRIVED_WINDOW = timedelta(hours=12)
STALE_OPTION = "Rechecked at bedside, unchanged"
STALE_ATTEST = [STALE_OPTION]
SEVERITY_RANK = {"stable": 0, "watcher": 1, "unstable": 2}
# Where the patient is and how they get there: the transfer itself changes these, which is no reason to sign again.
NOT_CLINICAL = ("E04", "E28")
MAX_TEXT = 2000
MAX_CALLBACK = 40
EMPTY_EDIT = "Write the text, or use Restore to bring back the model's text."
TOO_LONG = "Keep each text under 2,000 characters."
EARLIER = "You are viewing an earlier time; move the clock to now to act."
READBACK = "Write a short read back: the patient, the key risk and what you will do first."
CALLBACK = "Add a callback number the receiver can dial: at least 3 digits, or an extension like x4471."
UNSIGNED = ("draft", "transferred_unsigned")
# Attestation choices that need words from the clinician. The template lists the choice (ai/templates); the app
# asks for the text. The first choice of a rule confirms there is nothing to add ("No isolation needed"); any other
# choice is a value the clinician entered, shown on the item until the chart has one.
TYPED = {
    ("E10", "Other"): {"label": "Type of isolation", "note": None, "required": "Write the type of isolation."},
    ("E09", "Add an allergy"): {"label": "Allergen", "note": "Reaction (optional)", "required": "Write the allergen."},
}
# Code status, allergies and isolation belong to the patient, not to one template: a value entered on any template
# shows on every template, and a template cannot confirm "none" while another holds an entered value (or the
# reverse). Allergies are a list; the others hold one value, the latest entered.
PATIENT = ("E08", "E09", "E10")
ALLERGY = "Add an allergy"
PATIENT_LABEL = {"E08": "Code status", "E09": "An allergy", "E10": "Isolation"}
TEMPLATE_NAME = {"sbar_nurse": "SBAR", "ipass_physician": "I-PASS", "custom_sea58": "Joint Commission minimum"}
# The template each ED role signs and owns the patient for, in Analytics.
OWN_TEMPLATE = {"ed_nurse": "sbar_nurse", "ed_physician": "ipass_physician"}
# The patient level items Take over lists when their attestation is not confirmed yet.
CONFIRM_LABEL = {"E08": "Code status", "E09": "Allergies", "E10": "Isolation"}
NOT_ED = "Only ED nurses and physicians take patients."
# Tasks: what the panel and the Tasks page say, and the refusals.
MAX_REASON = 200
TASK_READ_ONLY = "The viewer role can read tasks but not change them."
NO_TASK = "There is no such task."
TASK_TEXT = "Write what needs to be done."
TASK_ROLE = "Choose nurse or physician."
TASK_COLLEAGUE = "Choose a colleague from the list."
TASK_ROLE_MISMATCH = "Choose a colleague in that role, or only the role."
TASK_REASON = "Write a short reason the task is not needed."
TASK_REASON_LONG = "Keep the reason under 200 characters."
TASK_DUE = "The due time must be a date and time."
TASK_CLOSED = "This task is closed. Press Undo to open it again first."
TASK_UNDO = "There is nothing to undo on this task."
TASK_KIND = "Choose Done, Not needed, Reassign, Change due time or Undo."
OUTSIDE_ROLE = {
    "boarding": "The ED nurse and physician have this patient while they board. You can still act on a task; it is "
                "recorded as outside your role.",
    "canceled": "The ED nurse and physician have this patient. You can still act on a task; it is recorded as outside "
                "your role.",
    "arrived": "The receiving nurse and the admitting physician have this patient now. You can still act on a task; it "
               "is recorded as outside your role.",
}


class ActionError(ValueError):
    """A request the rules do not allow; the message is shown to the user as is."""


# ---------------------------------------------------------------------------------------------------- helpers
def _age(birth: str | None, at: datetime) -> int | None:
    if not birth:
        return None
    y, m, d = (int(x) for x in str(birth)[:10].split("-"))
    return at.year - y - ((at.month, at.day) < (m, d))


def _elements(draft: dict) -> dict[str, dict]:
    return {e["id"]: e for s in draft.get("sections", []) for e in s["elements"]}


def _strip(v):
    if isinstance(v, dict):
        return {k: _strip(x) for k, x in v.items() if k not in ("src", "sources", "confidence", "unsupported_numbers",
                                                              "weakly_supported_numbers", "minutes_in_ed",
                                                              "minutes_boarding", "latest_age_min")}
    if isinstance(v, list):
        return [_strip(x) for x in v]
    return v


def _content(e: dict):
    if e["kind"] == "narrative":
        n = e.get("narrative") or {}
        if isinstance(n.get("items"), list):
            return [{k: i.get(k) for k in ("text", "owner", "due")} for i in n["items"]]
        return {"value": n.get("value"), "text": n.get("text")} if e["id"] == "E07" else n.get("text")
    if e.get("entered"):                            # a value the clinician entered is content like any other
        return {"chart": _strip(e.get("data")), "entered": [{"value": x["value"], "note": x["note"]} for x in e["entered"]]}
    return _strip(e.get("data"))


def _ordered(acts: list[dict]) -> list[dict]:
    """Actions in the order they apply: by the time they apply to, then the order they were saved."""
    return sorted(acts, key=lambda a: (a["at"], a["recorded_ts"]))


def _latest_by(acts: list[dict], kind: str, key) -> dict:
    out = {}
    for a in _ordered([a for a in acts if a["kind"] == kind]):
        out[key(a)] = a
    return out


def _drop_recorded(v):
    if isinstance(v, dict):
        return {k: _drop_recorded(x) for k, x in v.items() if k != "recorded_ts"}
    if isinstance(v, list):
        return [_drop_recorded(x) for x in v]
    return v


def _clinical(draft: dict) -> dict:
    """The content sign off is about: every item but where the patient is, and a value recorded again unchanged
    (the admitting team charting the same code status) is no change."""
    return {eid: _drop_recorded(_content(e)) for eid, e in _elements(draft).items() if eid not in NOT_CLINICAL}


def _last_sign(acts: list[dict], template: str):
    """The latest sign off of a template and every action up to and including it, or (None, [])."""
    mine = _ordered([a for a in acts if a["template"] == template])
    signs = [i for i, a in enumerate(mine) if a["kind"] == "sign"]
    if not signs:
        return None, [], mine
    return mine[signs[-1]], mine[: signs[-1] + 1], mine[signs[-1] + 1:]


def rule_index(templates: list[dict]) -> dict[str, dict[str, dict]]:
    """{template id: {rule id: rule}} from the template definitions (ai/templates)."""
    return {t["id"]: {r["id"]: r for r in t.get("rules") or []} for t in templates}


def _supersede(d: dict, rules: dict) -> None:
    """A rule that applies only in the high risk branch (it has `when`) replaces the template's other rules on the
    same item: a watcher or unstable patient cannot be attested "Stable, nothing anticipated", and the item shows
    one rule, not two."""
    present = {r["rule"] for r in d.get("rules", [])}
    drop = {rid for w, wd in rules.items() if wd.get("when") and w in present
            for rid, rd in rules.items() if rid != w and rd.get("element") == wd.get("element") and not rd.get("when")}
    if not drop & present:
        return
    d["rules"] = [r for r in d["rules"] if r["rule"] not in drop]
    for e in _elements(d).values():
        if e.get("rules"):
            e["rules"] = [r for r in e["rules"] if r["rule"] not in drop]


def _entered(a: dict, options: list | None) -> bool:
    """An attestation that enters a value: any choice but the rule's first, which confirms there is nothing to add."""
    return bool(options) and a["option"] != options[0] or bool(a.get("entry"))


def _options(index: dict, template: str, rule: str) -> list:
    return ((index.get(template) or {}).get(rule) or {}).get("attest") or []


def _entry_of(a: dict) -> dict:
    return {"rule": a["rule"], "option": a["option"], "value": a.get("entry") or a["option"], "note": a.get("entry_note"),
            "by": a["by"], "at": iso(a["at"]), "version": a["version"], "template": a["template"]}


def patient_entries(acts: list[dict], index: dict) -> dict:
    """The patient level values entered on any template (index: the rules of every template, rule_index()):
    {"E08": entry or None, "E10": entry or None, "E09": [entries], "none": {(template, element): action}}, where
    "none" holds each template's current "nothing to add" choice (No known allergies confirmed and the like)."""
    current, allergies = {}, {}
    for a in _ordered([a for a in acts if a["kind"] == "attest"]):
        el = ((index.get(a["template"]) or {}).get(a["rule"]) or {}).get("element")
        if el not in PATIENT:
            continue
        current[(a["template"], el)] = a
        if el == "E09" and a["option"] == ALLERGY:
            key = (a.get("entry") or "").strip().lower()
            if a.get("remove"):
                allergies.pop(key, None)
            elif key:
                allergies[key] = _entry_of(a)
    out = {"E09": list(allergies.values()), "none": {}}
    for el in ("E08", "E10"):
        entered = [a for (t, e), a in current.items() if e == el and not a.get("remove")
                   and _entered(a, _options(index, t, a["rule"]))]
        out[el] = _entry_of(max(entered, key=lambda a: (a["at"], a["recorded_ts"]))) if entered else None
    for (t, el), a in current.items():
        if not a.get("remove") and not _entered(a, _options(index, t, a["rule"])):
            out["none"][(t, el)] = a
    return out


def _callback_ok(cb: str) -> bool:
    return len(re.findall(r"[0-9]", cb)) >= 3 or re.fullmatch(r"(?i)(x|ext\.?) ?[0-9]{2,}", cb) is not None


def _readback_ok(text: str) -> bool:
    """At least a sentence: three words, fifteen letters, and not one character repeated."""
    letters = [c for c in text if c.isalpha()]
    words = [w for w in text.split() if any(c.isalpha() for c in w)]
    return len(words) >= 3 and len(letters) >= 15 and len({c.lower() for c in letters}) >= 2


# ----------------------------------------------------------------------------------------- effective draft
def effective(row: dict, acts: list[dict], at: datetime | None = None, rules: dict | None = None,
              patient: dict | None = None) -> dict:
    """The draft as the clinician sees it at time `at`: edits applied over model text, rules resolved by attestations
    and edits. Edits and attestations carry forward to newer versions of the same template, flagged when the model's
    text changed underneath an edit; a Restore brings back the model's text. A stale vitals attestation counts only
    within the rule's age limit for the patient's severity (rules: the template's rule definitions). patient: the
    patient level entries (patient_entries()); without it, those of this template's actions."""
    t = row["template"]
    rules = rules or {}
    if patient is None:
        patient = patient_entries(acts, {t: rules})
    mine = [a for a in acts if a["template"] == t]
    edits = _latest_by(mine, "edit", lambda a: a["element"])
    attests = _latest_by(mine, "attest", lambda a: a["rule"])
    d = copy.deepcopy(row["draft"])
    _supersede(d, rules)
    els = _elements(d)
    for eid, a in edits.items():
        e = els.get(eid)
        if not e or e["kind"] != "narrative" or a.get("restore"):
            continue
        n = e.setdefault("narrative", {}) or {}
        e["narrative"] = n
        model = {"text": n.get("text"), "items": n.get("items")}
        if a.get("items") is not None:
            n["items"] = [{"text": i.get("text", ""), "owner": i.get("owner"), "due": i.get("due"), "sources": [],
                           "edited": True} for i in a["items"]]
        else:
            n["text"] = a.get("text") or ""
        n["edited"] = {"by": a["by"], "at": iso(a["at"]), "version": a["version"],
                       "model_changed": a["version"] != row["version"] and model != a.get("model_before")}
        n["model_text"] = model
    sev = ((els.get("E07") or {}).get("narrative") or {}).get("value") or row.get("severity") or "watcher"
    for r in d.get("rules", []):
        e = els.get(r["element"])
        if r["status"] == "stale":
            r["attest_options"] = STALE_ATTEST
        elif r["status"] == "attest" and (rules.get(r["rule"]) or {}).get("attest"):
            # The choices of the current templates, not the ones the draft was written with: no redraft needed.
            r["attest_options"] = list(rules[r["rule"]]["attest"])
        typed = {o: {"label": TYPED[(r["element"], o)]["label"], "note": TYPED[(r["element"], o)]["note"]}
                 for o in r.get("attest_options") or [] if (r["element"], o) in TYPED}
        if typed:
            r["attest_typed"] = typed
        if r["status"] == "attest" and r["element"] in PATIENT:
            # Patient level: an entered value from any template resolves it; otherwise this template's "none".
            a = attests.get(r["rule"])
            found = patient["E09"] if r["element"] == "E09" else [x for x in (patient[r["element"]],) if x]
            if found and e:
                last = found[-1]
                r["resolution"] = {"kind": "attested", "option": last["option"], "by": last["by"], "at": last["at"],
                                   "version": last["version"], "entered": True}
                e["entered"] = found
            elif a and not a.get("remove") and not _entered(a, r.get("attest_options")):
                r["resolution"] = {"kind": "attested", "option": a["option"], "by": a["by"], "at": iso(a["at"]),
                                   "version": a["version"]}
        elif r["status"] in ("attest", "missing", "stale"):
            a = attests.get(r["rule"])
            if a and r["status"] != "missing" and r["level"] == "stale" and a["option"] == STALE_OPTION and at is not None:
                limit = ((rules.get(r["rule"]) or {}).get("max_age_min") or {}).get(sev, 60)
                until = a["at"] + timedelta(minutes=limit)
                if at > until:            # the recheck is as old as the vitals limit: recheck again
                    r["attest_expired"] = {"by": a["by"], "at": iso(a["at"]), "until": iso(until), "limit_min": limit,
                                           "severity": sev}
                    a = None
            if a and r["status"] != "missing":
                r["resolution"] = {"kind": "attested", "option": a["option"], "by": a["by"], "at": iso(a["at"]),
                                   "version": a["version"]}
            elif e and e["kind"] == "narrative" and (e.get("narrative") or {}).get("edited") and \
                    ((e["narrative"].get("text") or "").strip() or e["narrative"].get("items")):
                r["resolution"] = {"kind": "edited", "by": e["narrative"]["edited"]["by"]}
    for e in els.values():                          # element rules mirror the template level results
        for r in e.get("rules", []):
            match = next((x for x in d["rules"] if x["rule"] == r["rule"]), None)
            if match:
                r.update(match)
    judged = [r for r in d["rules"] if r["status"] != "deferred"]
    ok = [r for r in judged if r["status"] == "pass" or r.get("resolution")]
    blocking = [r["rule"] for r in judged if r["level"] in ("hard", "stale") and r["status"] != "pass"
                and not r.get("resolution")]
    d["completeness"] = {"score": round(len(ok) / max(1, len(judged)), 3), "blocking": blocking,
                         "model_score": row["draft"]["completeness"]["score"]}
    return d


def _until(acts: list[dict], s: dict) -> list[dict]:
    """Every action, of any template, saved up to and including the sign off s."""
    return [a for a in acts if (a["at"], a["recorded_ts"]) <= (s["at"], s["recorded_ts"])]


def workflow(row: dict | None, acts: list[dict], versions: list[dict], departed: bool = False,
             rules: dict | None = None, index: dict | None = None) -> dict:
    """draft, signed, changed (after signing: sign again), acknowledged, or transferred_unsigned (the patient left
    the ED with no sign off); with who and when. versions: the rows to find the signed version in. index: the rules
    of every template, for the patient level values entered on another template."""
    if row is None:
        return {"state": "drafting"}
    t = row["template"]
    s, before, after = _last_sign(acts, t)
    out = {"state": "draft", "version": row["version"]}
    if s is None:
        if departed:
            out["state"] = "transferred_unsigned"
        return out
    out.update(state="signed", signed={"by": s["by"], "role": s["role"], "callback": s.get("callback"),
                                       "at": iso(s["at"]), "version": s["version"]})
    index = index or {t: rules or {}}
    signed_pat, now_pat = patient_entries(_until(acts, s), index), patient_entries(acts, index)
    changed = False
    if row["version"] != s["version"]:
        base = next((v for v in versions if v["template"] == t and v["version"] == s["version"]), None)
        changed = base is None or (_clinical(effective(base, before, None, rules, signed_pat))
                                   != _clinical(effective(row, before, None, rules, signed_pat)))
    out["changed_since_signed"] = changed
    # An edit or attestation after signing, here or (for code status, allergies, isolation) on another template.
    out["edited_since_signed"] = any(a["kind"] in ("edit", "attest") for a in after) or (
        _clinical(effective(row, before, None, rules, signed_pat)) != _clinical(effective(row, before, None, rules, now_pat)))
    if changed or out["edited_since_signed"]:
        out["state"] = "changed"
        return out
    acks = [a for a in after if a["kind"] == "ack"]
    out["changed_since_acknowledged"] = not acks and any(a["kind"] == "ack" for a in before)
    if acks:
        k = acks[-1]
        out.update(state="acknowledged", acknowledged={"by": k["by"], "at": iso(k["at"]), "version": k["version"],
                                                       "readback": k.get("readback"), "questions": k.get("questions")})
    return out


def diff(old: dict | None, new: dict) -> list[dict]:
    if old is None:
        return []
    a, b = _elements(old), _elements(new)
    out = []
    for eid, e in b.items():
        before, after = _content(a[eid]) if eid in a else None, _content(e)
        if before != after:
            out.append({"id": eid, "label": e["label"], "kind": e["kind"], "before": before, "after": after})
    return out


# ---------------------------------------------------------------------------------------------------- views
class Views:
    def __init__(self, ds, store, templates: list[dict] | None = None, stale_boarding: timedelta | None = None,
                 stale_owner: timedelta = timedelta(hours=14), shifts: list[tuple[int, int]] | None = None,
                 shift_warning: timedelta = timedelta(minutes=30)):
        """ds: a Dataset, or a callable returning the current one (live mode reloads it from Lakebase).
        templates: the template definitions (rule limits and branches). stale_boarding: with the live clock, leave
        off a patient with an admit decision and no ED departure for longer than this. stale_owner: an owner who has
        not acted on the patient for longer than this no longer counts. shifts: the local times of day the ED shift
        changes, (hour, minute); shift_warning: how long before one the board warns."""
        self._ds, self.store = ds, store
        self.rules = rule_index(templates or [])
        self.stale_boarding = stale_boarding
        self.stale_owner, self.shifts, self.shift_warning = stale_owner, shifts or [(7, 0), (19, 0)], shift_warning

    @property
    def ds(self) -> Dataset:
        return self._ds() if callable(self._ds) else self._ds

    def _departure(self, csn: str, at: datetime) -> datetime | None:
        e = self.ds.enc[csn]
        return e["ed_departure_ts"] if e["ed_departure_ts"] and e["ed_departure_ts"] <= at else None

    def _summary(self, csn: str, at: datetime, person: dict) -> dict | None:
        e = self.ds.enc.get(csn)
        if not e or not e["admit_decision_ts"] or e["admit_decision_ts"] > at:
            return None
        latest = self.ds.latest_at(csn, at)
        dep = self._departure(csn, at)
        if dep and at - dep > ARRIVED_WINDOW:
            return None
        if not dep and self.stale_boarding is not None and at - e["admit_decision_ts"] > self.stale_boarding:
            return None                           # live clock: a departure the feeds never sent
        if not latest:
            return None                           # the first draft is not written yet
        any_row = next(iter(latest.values()))
        if status_at(any_row, at) == "retracted":
            phase = "canceled"
        else:
            phase = "arrived" if dep else "boarding"
        els = _elements(any_row["draft"])
        dest = (els.get("E04") or {}).get("data") or {}
        if not sees(person, e["facility"], dest.get("pending_unit") or (dest.get("unit") if dep else None)):
            return None
        acts = self.store.actions(csn, at)
        rows = self.ds.rows_at(csn, at)
        ref = min(at, dep) if dep else at
        templates = {}
        pat = patient_entries(acts, self.rules)
        for tid, row in latest.items():
            eff = effective(row, acts, ref, self.rules.get(tid), pat)
            templates[tid] = {"version": row["version"], "as_of": iso(row["at"]), "status": status_at(row, at),
                              "trigger": row["trigger"], "score": eff["completeness"]["score"],
                              "blocking": eff["completeness"]["blocking"],
                              "workflow": workflow(row, acts, rows, phase == "arrived", self.rules.get(tid), self.rules)}
        sev = (els.get("E07") or {}).get("narrative") or {}
        flags = {k: (els.get(k) or {}).get("data") for k in ("E08", "E09", "E10")}
        code = (flags["E08"] or {}).get("value") if isinstance(flags["E08"], dict) else None
        isolation = (flags["E10"] or {}).get("value") if isinstance(flags["E10"], dict) else None
        allergies = [a.get("allergen") for a in (flags["E09"] or [])] if isinstance(flags["E09"], list) else []

        def shown(x):                             # an entered value, for the patient box and the board flags
            return {k: x[k] for k in ("value", "note", "by", "template")}
        assigned, me = self.store.assignments(csn, at), person_key(person)
        return {
            "csn": csn, "facility": e["facility"], "phase": phase,
            "patient": {"name": e["patient_name"], "mrn": e["mrn"], "sex": e["sex"], "age": _age(e["birth_date"], at)},
            "chief_complaint": e["chief_complaint"], "esi": e["esi"], "service": dest.get("service") or e["service"],
            "arrival_ts": iso(e["arrival_ts"]), "admit_decision_ts": iso(e["admit_decision_ts"]),
            "ed_departure_ts": iso(dep),
            "minutes_boarding": round(((dep or at) - e["admit_decision_ts"]).total_seconds() / 60),
            "location": {k: dest.get(k) for k in ("unit", "room", "bed")},
            "destination": {k: dest.get(k) for k in ("pending_unit", "pending_room", "pending_bed", "level_of_care")}
            if not dep else {"pending_unit": dest.get("unit") or dest.get("pending_unit"), "pending_room": dest.get("room"),
                             "pending_bed": dest.get("bed"), "level_of_care": dest.get("level_of_care")},
            "severity": sev.get("value"), "severity_floor": (sev.get("floor") or {}).get("value"),
            "code_status": code, "isolation": isolation, "allergies": allergies,
            # Entered in the sign off while the chart has none; the chart value replaces them when it arrives.
            "entered": {"code_status": shown(pat["E08"]) if pat["E08"] and not code else None,
                        "isolation": shown(pat["E10"]) if pat["E10"] and not isolation else None,
                        "allergies": [shown(x) for x in pat["E09"]] if not allergies else []},
            "last_update": {"as_of": iso(any_row["at"]), "trigger": any_row["trigger"]},
            "templates": templates,
            "owners": {role: owner_at(assigned, role, acts, at, self.stale_owner, me) for role in ROLES},
            # Open and overdue tasks, for the board row.
            "tasks": tasks.counts(list(tasks.replay(self.store.task_events(csn, at), at).values()), at),
        }

    def board(self, at: datetime, person: dict) -> dict:
        rows = [s for s in (self._summary(c, at, person) for c in self.ds.enc) if s]
        rows.sort(key=lambda r: (r["phase"] != "boarding", -SEVERITY_RANK.get(r["severity"] or "", -1),
                                 -r["minutes_boarding"]))
        boarding = [r for r in rows if r["phase"] == "boarding"]
        mine = person["can_sign"] or person["can_ack"]
        tpl = person["default_template"]

        def state(r):
            return (r["templates"].get(tpl) or {}).get("workflow", {}).get("state")
        to_sign = [r for r in boarding if state(r) in ("draft", "changed")]
        kpis = {
            "boarding": len(boarding),
            "median_boarding_min": round(statistics.median([r["minutes_boarding"] for r in boarding])) if boarding else None,
            "unstable": sum(1 for r in boarding if r["severity"] == "unstable"),
            "to_sign": len(to_sign) if person["can_sign"] else None,
            "to_acknowledge": sum(1 for r in rows if state(r) == "signed") if person["can_ack"] else None,
            "blocking": sum(1 for r in to_sign if (r["templates"].get(tpl) or {}).get("blocking")) if person["can_sign"] else None,
            "arrived": sum(1 for r in rows if r["phase"] == "arrived"),
            # ED roles: patients who went up before anyone signed their handoff for this role.
            "left_unsigned": sum(1 for r in rows if r["phase"] == "arrived" and state(r) == "transferred_unsigned")
            if person["can_sign"] else None,
        }
        shift = None
        if person["role"] in ROLES:
            role = person["role"]
            owned = [r for r in boarding if counts(r["owners"][role]) and r["owners"][role]["you"]]
            kpis["mine"] = len(owned)
            kpis["unassigned"] = sum(1 for r in boarding if not counts(r["owners"][role]))
            nxt = next_shift(at, self.shifts)
            # Shift change: the board warns the owner of the patients whose handoff is not signed (or changed since).
            shift = {"at": iso(nxt), "minutes": round((nxt - at).total_seconds() / 60),
                     "warning": nxt - at <= self.shift_warning, "mine": len(owned),
                     "unsigned": sum(1 for r in owned if state(r) in ("draft", "changed"))}
        return {"at": iso(at), "template": tpl, "kpis": kpis, "rows": rows, "scoped": bool(mine), "shift": shift}

    def case(self, csn: str, at: datetime, person: dict) -> dict | None:
        summary = self._summary(csn, at, person)
        if summary is None:
            return None
        acts = self.store.actions(csn, at)
        rows = self.ds.rows_at(csn, at)
        dep = self._departure(csn, at)
        ref = min(at, dep) if dep else at
        by_t = defaultdict(list)
        for r in rows:
            by_t[r["template"]].append(r)
        templates = {}
        pat = patient_entries(acts, self.rules)
        for tid, versions in by_t.items():
            versions.sort(key=lambda v: v["version"])     # an older version the job wrote late (live clock) goes first
            row, rules = versions[-1], self.rules.get(tid)
            wf = workflow(row, acts, versions, summary["phase"] == "arrived", rules, self.rules)
            draft = effective(row, acts, ref, rules, pat)
            # What changed: since the signature when the template changed after signing (the text the ED signed,
            # clinician edits included), else since the previous version; both as the clinician sees them.
            base, since = None, "previous"
            if wf["state"] == "changed":
                s, before, _ = _last_sign(acts, tid)
                signed = next((v for v in versions if v["version"] == s["version"]), None)
                if signed:
                    then = patient_entries(_until(acts, s), self.rules)
                    base, since = (s["version"], effective(signed, before, s["at"], rules, then)), "signed"
            elif len(versions) > 1:
                base = (versions[-2]["version"], effective(versions[-2], acts, ref, rules, pat))
            templates[tid] = {
                "draft": draft, "row": {k: row[k] for k in ("version", "trigger", "model")}
                | {"status": status_at(row, at), "as_of": iso(row["at"])},
                "workflow": wf,
                "changes": {"since": since, "version": base[0] if base else None,
                            "elements": diff(base[1] if base else None, draft)},
                "versions": [{"version": v["version"], "as_of": iso(v["at"]), "trigger": v["trigger"], "status": status_at(v, at),
                              "severity": v["severity"], "score": v["score"]} for v in versions],
            }
        # Task events are in the activity too (kind "task", the event in "task", the task's text in detail).
        events = self.store.task_events(csn, at)
        text = {t["id"]: t["text"] for t in tasks.replay(events, at).values()}
        log = sorted([{"kind": a["kind"], "template": a["template"], "by": a["by"], "at": iso(a["at"]),
                       "version": a["version"], "detail": a.get("element") or a.get("rule") or a.get("option")}
                      | ({"restore": True} if a.get("restore") else {})
                      | ({"off_assignment": True, "assigned_to": a.get("assigned_to")} if a.get("off_assignment") else {})
                      | (self._entry(a) if a["kind"] == "attest" else {})
                      for a in acts]
                     + [{"kind": "task", "template": None, "by": e["by_name"], "at": iso(e["at"]), "version": None,
                         "detail": text.get(e["task_id"]), "task": e["kind"], "task_id": e["task_id"]}
                        | ({"outside_role": True} if e.get("outside_role") else {})
                        for e in tasks.ordered(events)], key=lambda a: a["at"], reverse=True)
        return {"summary": summary, "templates": templates, "activity": log}

    def _entry(self, a: dict) -> dict:
        """History keeps a value the clinician entered, also once the chart has one and the item shows the chart."""
        options = ((self.rules.get(a["template"]) or {}).get(a["rule"]) or {}).get("attest")
        if a.get("remove"):
            return {"removed": a.get("entry")}
        if not _entered(a, options):
            return {}
        return {"entered": a.get("entry") or a["option"]} | ({"note": a["entry_note"]} if a.get("entry_note") else {})

    @staticmethod
    def _patient_check(r: dict, body: dict, template: str, pat: dict) -> None:
        """Code status, allergies and isolation are one value for the patient: a template cannot confirm "none"
        while a value is entered, or enter one while another template confirmed "none"."""
        el, options = r["element"], r.get("attest_options") or []
        if body.get("remove") and el == "E09" and body["option"] == ALLERGY:
            return
        if options and body["option"] == options[0]:
            found = pat["E09"][-1] if el == "E09" and pat["E09"] else pat.get(el) if el != "E09" else None
            if found and (el == "E09" or found["template"] != template):
                where = TEMPLATE_NAME.get(found["template"], found["template"])
                change = "Remove it there first." if el == "E09" else "Change it there first."
                raise ActionError(f"{PATIENT_LABEL[el]} was entered as {found['value']} by {found['by']} on the {where}. {change}")
            return
        other = next((a for (t, e), a in pat["none"].items() if e == el and t != template), None)
        if other:
            where = TEMPLATE_NAME.get(other["template"], other["template"])
            raise ActionError(f"{other['by']} confirmed \"{other['option']}\" on the {where}. Change it there first.")

    # ------------------------------------------------------------------------------------------ analytics
    def analytics(self, at: datetime) -> dict:
        ds, encs = self.ds, list(self.ds.enc.values())
        start = ds.start
        series, t = [], start
        while t <= min(at, ds.end):
            ed = sum(1 for e in encs if e["arrival_ts"] and e["arrival_ts"] <= t and (e["left_ts"] is None or e["left_ts"] > t)
                     and not ds.canceled_at(e, t))
            board = [e for e in encs if e["admit_decision_ts"] and e["admit_decision_ts"] <= t
                     and (e["ed_departure_ts"] is None or e["ed_departure_ts"] > t) and ds.versions.get(e["csn"])
                     and not ds.canceled_at(e, t)
                     and (e["ed_departure_ts"] or self.stale_boarding is None or t - e["admit_decision_ts"] <= self.stale_boarding)]
            # Boarding patients no one owns (or whose owner went stale) for each ED role, at this hour.
            free = dict.fromkeys(ROLES, 0)
            for e in board:
                assigned = self.store.assignments(e["csn"], t)
                acts_e = self.store.actions(e["csn"], t) if assigned else []
                for role in ROLES:
                    free[role] += not (assigned and counts(owner_at(assigned, role, acts_e, t, self.stale_owner, None)))
            series.append({"t": iso(t), "ed": ed, "boarding": len(board), "unassigned_rn": free["ed_nurse"],
                           "unassigned_md": free["ed_physician"]})
            t += timedelta(hours=1)
        done = [e for e in encs if e["ed_departure_ts"] and e["ed_departure_ts"] <= at and e["admit_decision_ts"]
                and ds.versions.get(e["csn"])]
        by_unit = defaultdict(list)
        timing, completeness, blockers, sev_mix = [], [], Counter(), Counter()
        for e in done:
            latest = ds.latest_at(e["csn"], e["ed_departure_ts"])
            row = latest.get("sbar_nurse") or next(iter(latest.values()), None)
            dest = ((_elements(row["draft"]).get("E04") or {}).get("data") or {}) if row else {}
            unit = f"{e['facility']} {dest.get('pending_unit') or e['unit']}"
            by_unit[unit].append((e["ed_departure_ts"] - e["admit_decision_ts"]).total_seconds() / 60)
            timing.append(len({r["at"] for r in ds.versions[e["csn"]] if r["shown"] <= at}))
            if row:
                completeness.append(row["draft"]["completeness"]["score"])
                sev_mix[row["severity"] or "unknown"] += 1
                for r in row["draft"]["rules"]:
                    if r["level"] in ("hard", "stale") and r["status"] not in ("pass", "deferred"):
                        blockers[r["label"]] += 1

        def q(v, p):
            v = sorted(v)
            return round(v[min(len(v) - 1, int(p * len(v)))]) if v else None
        units = [{"unit": u, "n": len(v), "median_min": q(v, 0.5), "p90_min": q(v, 0.9)} for u, v in sorted(by_unit.items())]
        acts = self.store.all_actions(at)
        signed_before = 0
        for e in done:
            if any(a["kind"] == "sign" and a["csn"] == e["csn"] and a["at"] <= e["ed_departure_ts"] for a in acts):
                signed_before += 1
        drafts = [r for rows in ds.versions.values() for r in rows if r["shown"] <= at]
        return {
            "at": iso(at), "series": series, "units": units,
            "admitted": len(done),
            "drafts_per_patient": {"median": q(timing, 0.5), "max": max(timing) if timing else None},
            "completeness_at_transfer": {"median": statistics.median(completeness) if completeness else None,
                                         "histogram": _histogram(completeness)},
            "blocking_at_transfer": [{"label": k, "n": v} for k, v in blockers.most_common(8)],
            "severity_at_transfer": dict(sev_mix),
            "signed_before_transfer": signed_before,
            "transferred_without_sign_off": len(done) - signed_before,
            "drafts": len(drafts), "model_calls": len({(r["csn"], r["at"]) for r in drafts}),
            "addenda": len({(r["csn"], r["at"]) for r in drafts if status_at(r, at) == "addendum"}),
            "owners": self._owner_analytics(at, acts),
        }

    def _owner_analytics(self, at: datetime, acts: list[dict]) -> dict:
        """Owners in Analytics: when the first owner was recorded (since; null when none yet: before it, every patient
        is unassigned, which says nothing), ED actions taken outside an assignment, handovers by their distance from
        the nearest shift change, and minutes from the admit decision to the first signature of each role's template,
        by who owned the patient for that role when it was signed (owner null: no one)."""
        recorded = [r["at"] for csn in self.ds.enc for r in self.store.assignments(csn, at)]
        ed = [a for a in acts if a["role"] in ROLES and a["kind"] in ("edit", "attest", "sign")]
        outside = [{"role": role, "n": sum(1 for a in ed if a["role"] == role and a.get("off_assignment")),
                    "of": sum(1 for a in ed if a["role"] == role)} for role in ROLES]
        handovers = Counter(handover_bucket(r["at"], self.shifts) for csn in self.ds.enc
                            for r in self.store.assignments(csn, at) if r["kind"] == "handover")
        by_csn = defaultdict(list)
        for a in acts:
            by_csn[a["csn"]].append(a)
        waits = defaultdict(list)
        for csn, mine in by_csn.items():
            e = self.ds.enc.get(csn)
            if not e or not e["admit_decision_ts"]:
                continue
            for role, tpl in OWN_TEMPLATE.items():
                signs = _ordered([a for a in mine if a["kind"] == "sign" and a["template"] == tpl])
                if not signs:
                    continue
                s = signs[0]
                o = owner_at(self.store.assignments(csn, s["at"]), role, [a for a in mine if a["at"] <= s["at"]], s["at"],
                             self.stale_owner, None)
                waits[(role, o["name"] if counts(o) else None)].append((s["at"] - e["admit_decision_ts"]).total_seconds() / 60)
        to_sign = [{"role": role, "owner": name, "n": len(v), "median_min": round(statistics.median(v))}
                   for (role, name), v in sorted(waits.items(), key=lambda kv: (ROLES.index(kv[0][0]), kv[0][1] is None,
                                                                                 kv[0][1] or ""))]
        return {"since": iso(min(recorded)) if recorded else None, "outside": outside,
                "handovers": [{"bucket": b, "n": handovers.get(b, 0)} for b in HANDOVER_BUCKETS], "to_sign": to_sign}

    # -------------------------------------------------------------------------------------------- actions
    def act(self, kind: str, csn: str, template: str, at: datetime, person: dict, body: dict) -> dict:
        summary = self._summary(csn, at, person)
        if summary is None:
            raise ActionError("This patient is not on your board at this time.")
        latest = self.ds.latest_at(csn, at).get(template)
        if latest is None:
            raise ActionError("There is no draft for this template yet.")
        acts = self.store.actions(csn, at, fresh=True)    # live mode: what other app processes saved, too
        rules = self.rules.get(template)
        arrived = summary["phase"] == "arrived"
        wf = workflow(latest, acts, [r for r in self.ds.rows_at(csn, at) if r["template"] == template], arrived, rules,
                      self.rules)
        row = {"csn": csn, "template": template, "version": latest["version"], "at": at, "by": person["name"],
               "role": person["role"], "user_email": person.get("user_email"), "by_id": person_key(person)}
        if kind in ("edit", "attest", "sign"):
            if template not in SIGNS.get(person["role"], ()):
                raise ActionError("Only the sending ED clinician for this template can change or sign it.")
            # After the patient leaves the ED the handoff is final, except for an addendum, or when the ED never
            # signed it or signed an older version: then the ED can still complete and sign it.
            unsigned = arrived and (not wf.get("signed") or wf["signed"]["version"] < latest["version"])
            if summary["phase"] != "boarding" and status_at(latest, at) != "addendum" and not unsigned:
                raise ActionError("The patient has left the ED; the handoff is final.")
        elif kind == "ack":
            if template not in ACKS.get(person["role"], ()):
                raise ActionError("Only the receiving clinician can acknowledge this handoff.")
        else:
            raise ActionError(f"Unknown action {kind}.")
        # The replay clock: an action cannot go before one already recorded for this handoff.
        if any(a["template"] == template and a["at"] > at for a in self.store.actions(csn)):
            raise ActionError(EARLIER)
        dep = self._departure(csn, at)
        pat = patient_entries(acts, self.rules)
        eff = effective(latest, acts, min(at, dep) if dep else at, rules, pat)
        if kind == "edit":
            el = _elements(eff).get(body.get("element"))
            if not el or el["kind"] != "narrative":
                raise ActionError("Only narrative items can be edited; structured data comes from the chart.")
            if el["id"] == "E07":
                raise ActionError("Illness severity comes from the rule floor and the model; it cannot be edited.")
            model = _elements(latest["draft"])[el["id"]].get("narrative") or {}
            row |= {"element": el["id"], "model_before": {"text": model.get("text"), "items": model.get("items")}}
            if body.get("restore"):
                if not (el.get("narrative") or {}).get("edited"):
                    raise ActionError("This item shows the model's text already; there is no edit to restore.")
                row |= {"restore": True, "text": None, "items": None}
            elif body.get("items") is not None:
                items = [{"text": (i.get("text") or "").strip(), "owner": (i.get("owner") or "").strip() or None,
                          "due": (i.get("due") or "").strip() or None} for i in body["items"]]
                if not items or any(not i["text"] for i in items):
                    raise ActionError(EMPTY_EDIT)
                if any(len(v) > MAX_TEXT for i in items for v in i.values() if v):
                    raise ActionError(TOO_LONG)
                row |= {"text": None, "items": items}
            else:
                text = (body.get("text") or "").strip()
                if not text:
                    raise ActionError(EMPTY_EDIT)
                if len(text) > MAX_TEXT:
                    raise ActionError(TOO_LONG)
                row |= {"text": text, "items": None}
        elif kind == "attest":
            r = next((x for x in eff["rules"] if x["rule"] == body.get("rule")), None)
            if not r or body.get("option") not in (r.get("attest_options") or []):
                raise ActionError("Choose one of the listed attestations for this rule.")
            row |= {"rule": r["rule"], "option": body["option"], "entry": None, "entry_note": None}
            typed = TYPED.get((r["element"], body["option"]))
            if r["element"] in PATIENT:
                self._patient_check(r, body, template, pat)
            if typed and body.get("remove"):
                wanted = (body.get("entry") or "").strip().lower()
                listed = pat["E09"] if r["element"] == "E09" else []
                gone = next((x for x in listed if x["value"].strip().lower() == wanted), None)
                if not gone:
                    raise ActionError("That allergy is not on the list.")
                row |= {"entry": gone["value"], "remove": True}
            elif typed:
                entry = (body.get("entry") or "").strip()
                note = (body.get("entry_note") or "").strip() if typed["note"] else ""
                if not entry:
                    raise ActionError(typed["required"])
                if len(entry) > MAX_TEXT or len(note) > MAX_TEXT:
                    raise ActionError(TOO_LONG)
                row |= {"entry": entry, "entry_note": note or None}
        elif kind == "sign":
            if eff["completeness"]["blocking"]:
                labels = [x["label"] for x in eff["rules"] if x["rule"] in eff["completeness"]["blocking"]]
                raise ActionError("Resolve before signing: " + ", ".join(labels) + ".")
            callback = (body.get("callback") or "").strip()
            if not callback:
                raise ActionError("Add a callback number so the receiver can reach you.")
            if len(callback) > MAX_CALLBACK or not _callback_ok(callback):
                raise ActionError(CALLBACK)
            row |= {"callback": callback}
        else:
            if wf["state"] in UNSIGNED:
                raise ActionError("The handoff has not been signed yet.")
            if wf["state"] == "changed":
                raise ActionError("Waiting for the ED to sign again: the handoff changed after it was signed.")
            readback, questions = (body.get("readback") or "").strip(), (body.get("questions") or "").strip()
            if not _readback_ok(readback):
                raise ActionError(READBACK)
            if len(readback) > MAX_TEXT or len(questions) > MAX_TEXT:
                raise ActionError(TOO_LONG)
            row |= {"readback": readback, "questions": questions or None}
        # Allowed, never blocked: an ED clinician acting on a patient a colleague owns, recorded for Analytics.
        owner = summary["owners"].get(person["role"])
        if counts(owner) and not owner["you"]:
            row |= {"off_assignment": True, "assigned_to": owner["name"]}
        self.store.add(kind, row)
        if kind == "sign":
            # The action list becomes tasks; a sign again or an addendum adds only the new items.
            self._handoff_tasks(csn, template, latest["version"], at, row, eff)
        return self.case(csn, at, person)

    # ------------------------------------------------------------------------------------------ ownership
    def _assignment(self, csn: str, at: datetime, person: dict, kind: str, to: dict | None, owner: dict | None) -> dict:
        """An assignment row for the caller's role; like actions, it cannot go before one already recorded."""
        role = person["role"]
        if any(r["role"] == role and r["at"] > at for r in self.store.assignments(csn, fresh=True)):
            raise ActionError(EARLIER)
        return {"csn": csn, "role": role, "kind": kind, "assignee_id": to["id"] if to else None,
                "assignee_name": to["name"] if to else None, "from_id": owner["id"] if owner else None,
                "from_name": owner["name"] if owner else None, "at": at, "by_name": person["name"], "by_role": role,
                "user_email": person.get("user_email"), "by_id": person_key(person), "detail": {}}

    def seed_owners(self, at: datetime, nurse: dict, physician: dict) -> list[dict]:
        """The demo's starting owners (mock mode, and again after Reset), so My patients is not empty and a shift change
        takes a few clicks: `nurse` owns up to three patients on the board half an hour before the next shift change
        after `at` who board through it, and `physician` the first two of them. Each is taken five minutes
        after its first draft, but no earlier than three hours before `at` (so no owner is stale at `at`): a patient
        admitted after `at` is already owned when it reaches the board. nurse and physician: {"id", "name"}."""
        ds, nxt = self.ds, next_shift(at, self.shifts)
        warn = nxt - timedelta(minutes=30)
        taken = {}
        for e in sorted(ds.enc.values(), key=lambda e: (e["admit_decision_ts"] or nxt, e["csn"])):
            first = self.first_shown(e["csn"], warn) if e["admit_decision_ts"] else None
            if first is None or (e["ed_departure_ts"] is not None and e["ed_departure_ts"] <= nxt) or ds.canceled_at(e, nxt):
                continue
            t = max(first + timedelta(minutes=5), at - timedelta(hours=3))
            if t <= warn:
                taken[e["csn"]] = t
            if len(taken) == 3:
                break
        rows = []
        for i, (csn, t) in enumerate(taken.items()):
            for role, who in (("ed_nurse", nurse), ("ed_physician", physician)):
                if role == "ed_nurse" or i < 2:
                    rows.append({"csn": csn, "role": role, "kind": "claim", "assignee_id": who["id"],
                                 "assignee_name": who["name"], "from_id": None, "from_name": None, "at": t,
                                 "by_name": who["name"], "by_role": role, "user_email": None, "by_id": who["id"],
                                 "detail": {"seed": True}})
        return rows

    def first_shown(self, csn: str, at: datetime) -> datetime | None:
        """When the patient's first draft shows, if it shows by `at`."""
        rows = self.ds.rows_at(csn, at)
        return rows[0]["shown"] if rows else None

    def own(self, kind: str, csn: str, at: datetime, person: dict, to: str | None, colleagues: list[dict]) -> dict:
        """Take (claim), Assign to a colleague or Release, for the caller's ED role. colleagues: the people of that
        role (main.py: demo personas, or the role map live)."""
        if person["role"] not in ROLES:
            raise ActionError(NOT_ED)
        summary = self._summary(csn, at, person)
        if summary is None:
            raise ActionError("This patient is not on your board at this time.")
        if summary["phase"] != "boarding":
            raise ActionError("The patient has left the ED, so they no longer need an ED owner.")
        owner, me = summary["owners"][person["role"]], person_key(person)
        current = owner if counts(owner) else None
        if kind == "claim":
            if current and current["you"]:
                raise ActionError("This patient is already yours.")
            row = self._assignment(csn, at, person, kind, {"id": me, "name": person["name"]}, owner)
        elif kind == "assign":
            target = next((c for c in colleagues if c["id"] == to), None)
            if target is None:
                raise ActionError("Choose a colleague from the list.")
            if target["id"] == me:
                raise ActionError("To take this patient yourself, press Take.")
            if current and current["id"] == target["id"]:
                raise ActionError(f"{target['name']} already owns this patient.")
            row = self._assignment(csn, at, person, kind, target, owner)
        elif kind == "release":
            if owner is None:
                raise ActionError("No one owns this patient, so there is nothing to release.")
            if owner["id"] != me:
                raise ActionError(f"Only {owner['name']} can release this patient. Use Take or Assign instead.")
            row = self._assignment(csn, at, person, kind, None, owner)
        else:
            raise ActionError("Choose Take, Assign or Release.")
        self.store.assign(row)
        return self.case(csn, at, person)

    def takeover(self, at: datetime, person: dict, colleague: str | None, colleagues: list[dict]) -> dict:
        """Take over at a shift change: the caller's colleagues with how many patients each owns now, and, for the
        chosen colleague, each of their boarding patients with where the handoff for the caller's role stands, the
        code status, allergies and isolation not yet confirmed, and what changed since it was signed."""
        role = person["role"]
        if role not in ROLES:
            raise ActionError(NOT_ED)
        owned = defaultdict(list)
        for s in (self._summary(c, at, person) for c in self.ds.enc):
            if s and s["phase"] == "boarding" and counts(s["owners"][role]):
                owned[s["owners"][role]["id"]].append(s)
        me = person_key(person)
        others = [c for c in colleagues if c["id"] != me]
        chosen = next((c for c in others if c["id"] == colleague), None)
        if colleague and chosen is None:
            raise ActionError("Choose a colleague from the list.")
        tpl, rows = person["default_template"], []
        theirs = sorted(owned.get(colleague, []) if chosen else [],
                        key=lambda r: (-SEVERITY_RANK.get(r["severity"] or "", -1), -r["minutes_boarding"], r["csn"]))
        for s in theirs:
            t = self.case(s["csn"], at, person)["templates"].get(tpl)
            wf = t["workflow"] if t else {"state": "drafting"}
            confirm = {r["element"] for r in (t["draft"]["rules"] if t else [])
                       if r["element"] in CONFIRM_LABEL and r["status"] == "attest" and not r.get("resolution")}
            rows.append({"csn": s["csn"], "patient": s["patient"], "severity": s["severity"],
                         "chief_complaint": s["chief_complaint"], "facility": s["facility"], "destination": s["destination"],
                         "minutes_boarding": s["minutes_boarding"], "owner": s["owners"][role], "state": wf["state"],
                         "signed": wf.get("signed"), "acknowledged": wf.get("acknowledged"),
                         "blocking": len(t["draft"]["completeness"]["blocking"]) if t else 0,
                         "to_confirm": [CONFIRM_LABEL[e] for e in CONFIRM_LABEL if e in confirm],
                         "changed": [c["label"] for c in t["changes"]["elements"]] if t and wf["state"] == "changed" else []})
        return {"at": iso(at), "role": role, "template": tpl,
                "colleagues": [c | {"patients": len(owned.get(c["id"], []))} for c in others],
                "colleague": chosen, "rows": rows}

    def take_over(self, at: datetime, person: dict, colleague: str, csns: list[str], colleagues: list[dict]) -> dict:
        """Take some or all of a colleague's patients in one go: one handover row per patient, all or none."""
        view = self.takeover(at, person, colleague, colleagues)
        if view["colleague"] is None:
            raise ActionError("Choose a colleague from the list.")
        if not csns:
            raise ActionError("Choose at least one patient to take over.")
        theirs = {r["csn"]: r for r in view["rows"]}
        rows = []
        for csn in dict.fromkeys(csns):
            r = theirs.get(csn)
            if r is None:
                e = self.ds.enc.get(csn)
                who = e["patient_name"] if e else "that patient"
                raise ActionError(f"{view['colleague']['name']} no longer owns {who}. Reload the list and try again.")
            rows.append(self._assignment(csn, at, person, "handover", {"id": person_key(person), "name": person["name"]},
                                         r["owner"]))
        for row in rows:
            self.store.assign(row)
        return self.takeover(at, person, colleague, colleagues) | {"taken": [r["csn"] for r in rows]}

    # ---------------------------------------------------------------------------------------------- tasks
    def _handoff_tasks(self, csn: str, template: str, version: int, at: datetime, who: dict, draft: dict) -> list[dict]:
        """The action list (E24) of a handoff signed at `at` as tasks: one create per item whose text is not a task
        of this template yet, by the clinician who signed (who: the sign off row). Returns the rows saved."""
        el = _elements(draft).get("E24") or {}
        items = (el.get("narrative") or {}).get("items") or []
        have = {e["item_key"] for e in self.store.task_events(csn, fresh=True)
                if e["kind"] == "create" and e.get("template") == template}
        rows = []
        for i in items:
            text = (i.get("text") or "").strip()
            k = tasks.key(text)
            if not k or k in have:
                continue
            have.add(k)
            rows.append({"csn": csn, "kind": "create", "template": template, "version": version, "item_key": k,
                         "text": text, "role": tasks.role_of(i.get("owner")), "assignee_id": None, "assignee_name": None,
                         "due": tasks.due_of(i.get("due"), at), "due_text": (i.get("due") or "").strip() or None,
                         "owner_text": (i.get("owner") or "").strip() or None, "at": at, "by_name": who["by"],
                         "by_role": who["role"], "user_email": who.get("user_email"), "by_id": who.get("by_id")})
        return self.store.add_tasks(rows) if rows else []

    def backfill_tasks(self) -> int:
        """Every handoff signed before tasks existed (a deployment from before them) gets its action list as tasks, as
        signing does now: at the time of each signature, by its signer. Safe to run again and from several app
        processes at once (the duplicate check, and the unique index in Lakebase). Returns how many tasks it added."""
        added = 0
        for a in _ordered([a for a in self.store.all_actions() if a["kind"] == "sign"]):
            row = next((v for v in self.ds.versions.get(a["csn"], []) if v["template"] == a["template"]
                        and v["version"] == a["version"]), None)
            if row is None:
                continue
            before = _until(self.store.actions(a["csn"]), a)
            draft = effective(row, before, a["at"], self.rules.get(a["template"]), patient_entries(before, self.rules))
            added += len(self._handoff_tasks(a["csn"], a["template"], a["version"], a["at"], a, draft))
        return added

    @staticmethod
    def _in_role(person: dict, summary: dict) -> bool:
        """Whether the person has the patient: ED roles while boarding, the receiving unit's nurse and the admitting
        physician after arrival."""
        return person["role"] in tasks.HAS_PATIENT.get(summary["phase"], ())

    @staticmethod
    def task_people(summary: dict, people: list[dict]) -> list[dict]:
        """Who a task of this patient can go to: the nurses (ED nurses and the destination unit's) and the physicians
        (ED and admitting). people: everyone with a clinical role (main.py: demo personas, or the role map live)."""
        unit = summary["destination"]["pending_unit"]
        return [{"id": p["id"], "name": p["name"], "role": tasks.DISCIPLINE[p["role"]], "role_label": p.get("role_label")}
                for p in people if p["role"] in tasks.DISCIPLINE and sees(p, summary["facility"], unit)]

    def _mine(self, t: dict, summary: dict, person: dict) -> bool:
        """A task is the caller's when it was given to them, or when it is for their role (and no one in particular)
        on a patient they have: for ED roles, a patient they own."""
        if t["assignee"]:
            return t["assignee"]["id"] == person_key(person)
        role = person["role"]
        if t["role"] != tasks.DISCIPLINE.get(role) or not self._in_role(person, summary):
            return False
        if role in ROLES:
            owner = summary["owners"][role]
            return counts(owner) and owner["you"]
        return True

    def patient_tasks(self, csn: str, at: datetime, person: dict, people: list[dict]) -> dict | None:
        """One patient's tasks at `at` (open and closed, in the order they were created), their open and overdue
        counts, whether the caller can act (and the note when it would be outside their role), and who a task can go
        to. None when the patient is not on the caller's board."""
        summary = self._summary(csn, at, person)
        if summary is None:
            return None
        found = list(tasks.replay(self.store.task_events(csn, at), at).values())
        viewer, in_role = person["role"] == "viewer", self._in_role(person, summary)
        return {"at": iso(at), "csn": csn, "phase": summary["phase"], "tasks": [tasks.shown(t, at) for t in found],
                "counts": tasks.counts(found, at),
                "access": {"can_act": not viewer, "in_role": in_role,
                           "note": TASK_READ_ONLY if viewer else None if in_role else OUTSIDE_ROLE[summary["phase"]]},
                "people": self.task_people(summary, people)}

    def _task_check(self, csn: str, at: datetime, person: dict) -> dict:
        """The patient's summary for a task change: on the caller's board, not the viewer, and not before a task event
        already recorded for the patient (the replay clock)."""
        summary = self._summary(csn, at, person)
        if summary is None:
            raise ActionError("This patient is not on your board at this time.")
        if person["role"] == "viewer":
            raise ActionError(TASK_READ_ONLY)
        if any(e["at"] > at for e in self.store.task_events(csn, fresh=True)):
            raise ActionError(EARLIER)
        return summary

    def _event(self, csn: str, kind: str, at: datetime, person: dict, summary: dict, **fields) -> dict:
        row = {"csn": csn, "kind": kind, "at": at, "by_name": person["name"], "by_role": person["role"],
               "user_email": person.get("user_email"), "by_id": person_key(person)} | fields
        if not self._in_role(person, summary):
            row["outside_role"] = True          # allowed, never blocked; recorded
        return row

    @staticmethod
    def _due(raw) -> datetime | None:
        if raw is None:
            return None
        try:
            return ts(raw)
        except (ValueError, OverflowError):
            raise ActionError(TASK_DUE)

    def _target(self, body: dict, summary: dict, people: list[dict]) -> tuple[str, dict | None]:
        """(role, colleague or None) from a body with role and to (a colleague's id)."""
        role, to = body.get("role"), body.get("to")
        if to:
            p = next((x for x in self.task_people(summary, people) if x["id"] == to), None)
            if p is None:
                raise ActionError(TASK_COLLEAGUE)
            if role and role != p["role"]:
                raise ActionError(TASK_ROLE_MISMATCH)
            return p["role"], p
        if role not in tasks.ROLES:
            raise ActionError(TASK_ROLE)
        return role, None

    def add_task(self, csn: str, at: datetime, person: dict, body: dict, people: list[dict]) -> dict:
        """A task added by hand: text, role (nurse or physician), optionally a colleague and a due time."""
        summary = self._task_check(csn, at, person)
        text = (body.get("text") or "").strip()
        if not text:
            raise ActionError(TASK_TEXT)
        if len(text) > MAX_TEXT:
            raise ActionError(TOO_LONG)
        role, to = self._target(body, summary, people)
        due = self._due(body.get("due"))
        self.store.add_tasks([self._event(csn, "create", at, person, summary, template=None, item_key=None, text=text,
                                          role=role, assignee_id=to["id"] if to else None,
                                          assignee_name=to["name"] if to else None, due=due)])
        return self.patient_tasks(csn, at, person, people)

    def task_act(self, task_id: int, at: datetime, person: dict, body: dict, people: list[dict]) -> dict:
        """A change to one task: done, not_needed (with a reason), reassign (role or to), due (a time, or none), or
        undo (reopens a closed task, or puts back the last reassign or due time)."""
        create = next((e for e in self.store.all_task_events(fresh=True) if e["kind"] == "create" and e["task_id"] == task_id),
                      None)
        if create is None:
            raise ActionError(NO_TASK)
        csn = create["csn"]
        summary = self._task_check(csn, at, person)       # also refuses a task created after `at` (the replay clock)
        t = tasks.replay(self.store.task_events(csn, at), at)[task_id]
        kind = body.get("kind")
        ev = lambda k, **f: self._event(csn, k, at, person, summary, task_id=task_id, **f)  # noqa: E731
        if kind in tasks.CLOSED:
            if t["status"] != "open":
                raise ActionError(TASK_CLOSED)
            reason = (body.get("reason") or "").strip()
            if kind == "not_needed" and not reason:
                raise ActionError(TASK_REASON)
            if len(reason) > MAX_REASON:
                raise ActionError(TASK_REASON_LONG)
            row = ev(kind, reason=reason if kind == "not_needed" else None)
        elif kind in ("reassign", "due"):
            if t["status"] != "open":
                raise ActionError(TASK_CLOSED)
            was = {"from_role": t["role"], "from_assignee_id": (t["assignee"] or {}).get("id"),
                   "from_assignee_name": (t["assignee"] or {}).get("name")}
            if kind == "reassign":
                role, to = self._target(body, summary, people)
                if (role, to["id"] if to else None) == (t["role"], was["from_assignee_id"]):
                    who = to["name"] if to else f"the {role}"
                    raise ActionError(f"The task is with {who} already.")
                row = ev("reassign", role=role, assignee_id=to["id"] if to else None,
                         assignee_name=to["name"] if to else None, **was)
            else:
                due = self._due(body.get("due"))
                if due == t["due"]:
                    raise ActionError("The task has that due time already.")
                row = ev("due", due=due, from_due=t["due"])
        elif kind == "undo":
            last = tasks.undo_of(t)
            if last is None:
                raise ActionError(TASK_UNDO)
            e = t["history"][-1]
            if last in tasks.CLOSED:
                row = ev("reopen", undo=True)
            elif last == "reassign":
                row = ev("reassign", role=e["from_role"], assignee_id=e.get("from_assignee_id"),
                         assignee_name=e.get("from_assignee_name"), from_role=t["role"],
                         from_assignee_id=(t["assignee"] or {}).get("id"),
                         from_assignee_name=(t["assignee"] or {}).get("name"), undo=True)
            else:
                row = ev("due", due=e.get("from_due"), from_due=t["due"], undo=True)
        else:
            raise ActionError(TASK_KIND)
        self.store.add_tasks([row])
        return self.patient_tasks(csn, at, person, people)

    def my_tasks(self, at: datetime, person: dict, scope: str = "mine") -> dict:
        """The Tasks page: the caller's open tasks across patients (scope "mine"; "all": every task on their board),
        grouped Overdue, Due in the next 2 hours, Later and No time, and the tasks closed in the last 12 hours."""
        groups = {g: [] for g in ("overdue", "soon", "later", "none")}
        done = []
        by_csn = defaultdict(list)
        for e in self.store.all_task_events(at):
            by_csn[e["csn"]].append(e)
        me = person_key(person)
        for csn, events in by_csn.items():
            summary = self._summary(csn, at, person)
            if summary is None:
                continue
            patient = {"csn": csn, "patient": summary["patient"], "phase": summary["phase"], "facility": summary["facility"],
                       "unit": summary["destination"]["pending_unit"], "severity": summary["severity"],
                       "in_role": self._in_role(person, summary)}
            for t in tasks.replay(events, at).values():
                mine = scope == "all" or self._mine(t, summary, person)
                if t["status"] == "open" and mine:
                    groups[tasks.group(t, at)].append((t, patient))
                elif t["status"] != "open" and at - t["closed"]["at"] <= tasks.RECENT and (
                        mine or t["history"][-1].get("by_id") == me):
                    done.append((t, patient))
        far = datetime.max.replace(tzinfo=at.tzinfo)
        out = {g: [tasks.shown(t, at) | p for t, p in sorted(v, key=lambda x: (x[0]["due"] or far, x[0]["created"]["at"],
                                                                                x[0]["id"]))]
               for g, v in groups.items()}
        return {"at": iso(at), "scope": scope, "role": tasks.DISCIPLINE.get(person["role"]),
                "groups": [{"id": g, "tasks": out[g]} for g in groups],
                "done": [tasks.shown(t, at) | p for t, p in sorted(done, key=lambda x: (x[0]["closed"]["at"], x[0]["id"]),
                                                                    reverse=True)],
                "counts": {"open": sum(len(v) for v in groups.values()), "overdue": len(groups["overdue"])}}


def _histogram(values: list[float]) -> list[dict]:
    bins = [(0.0, 0.5, "under 50%"), (0.5, 0.6, "50 to 59%"), (0.6, 0.7, "60 to 69%"), (0.7, 0.8, "70 to 79%"),
            (0.8, 0.9, "80 to 89%"), (0.9, 1.01, "90% or more")]
    return [{"bin": label, "n": sum(1 for v in values if lo <= v < hi)} for lo, hi, label in bins]
