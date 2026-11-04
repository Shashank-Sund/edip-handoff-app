"""Views computed from the read model and the clinician actions at a time `at`.

  board(at, person)        patients with a handoff: boarding now, or arrived on the unit in the last 12 hours
  case(csn, at, person)    one patient: every template's effective draft (model text with clinician edits and
                           attestations applied), its rules after resolution, workflow state, versions, changes
  analytics(at)            census, boarding times, handoff timing and completeness across the week so far
  act(...)                 validates and records an edit, attestation, sign off or acknowledgement

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

from data import Dataset, iso
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


def _callback_ok(cb: str) -> bool:
    return len(re.findall(r"[0-9]", cb)) >= 3 or re.fullmatch(r"(?i)(x|ext\.?) ?[0-9]{2,}", cb) is not None


def _readback_ok(text: str) -> bool:
    """At least a sentence: three words, fifteen letters, and not one character repeated."""
    letters = [c for c in text if c.isalpha()]
    words = [w for w in text.split() if any(c.isalpha() for c in w)]
    return len(words) >= 3 and len(letters) >= 15 and len({c.lower() for c in letters}) >= 2


# ----------------------------------------------------------------------------------------- effective draft
def effective(row: dict, acts: list[dict], at: datetime | None = None, rules: dict | None = None) -> dict:
    """The draft as the clinician sees it at time `at`: edits applied over model text, rules resolved by attestations
    and edits. Edits and attestations carry forward to newer versions of the same template, flagged when the model's
    text changed underneath an edit; a Restore brings back the model's text. A stale vitals attestation counts only
    within the rule's age limit for the patient's severity (rules: the template's rule definitions)."""
    t = row["template"]
    rules = rules or {}
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
        if r["status"] in ("attest", "missing", "stale"):
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


def workflow(row: dict | None, acts: list[dict], versions: list[dict], departed: bool = False,
             rules: dict | None = None) -> dict:
    """draft, signed, changed (after signing: sign again), acknowledged, or transferred_unsigned (the patient left
    the ED with no sign off); with who and when. versions: the rows to find the signed version in."""
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
    changed = False
    if row["version"] != s["version"]:
        base = next((v for v in versions if v["template"] == t and v["version"] == s["version"]), None)
        changed = base is None or _clinical(effective(base, before, None, rules)) != _clinical(effective(row, before, None, rules))
    out["changed_since_signed"] = changed
    out["edited_since_signed"] = any(a["kind"] in ("edit", "attest") for a in after)
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
    def __init__(self, ds, store, templates: list[dict] | None = None, stale_boarding: timedelta | None = None):
        """ds: a Dataset, or a callable returning the current one (live mode reloads it from Lakebase).
        templates: the template definitions (rule limits and branches). stale_boarding: with the live clock, leave
        off a patient with an admit decision and no ED departure for longer than this."""
        self._ds, self.store = ds, store
        self.rules = rule_index(templates or [])
        self.stale_boarding = stale_boarding

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
        if any_row["status"] == "retracted":
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
        for tid, row in latest.items():
            eff = effective(row, acts, ref, self.rules.get(tid))
            templates[tid] = {"version": row["version"], "as_of": iso(row["at"]), "status": row["status"],
                              "trigger": row["trigger"], "score": eff["completeness"]["score"],
                              "blocking": eff["completeness"]["blocking"],
                              "workflow": workflow(row, acts, rows, phase == "arrived", self.rules.get(tid))}
        sev = (els.get("E07") or {}).get("narrative") or {}
        flags = {k: (els.get(k) or {}).get("data") for k in ("E08", "E09", "E10")}
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
            "code_status": (flags["E08"] or {}).get("value") if isinstance(flags["E08"], dict) else None,
            "isolation": (flags["E10"] or {}).get("value") if isinstance(flags["E10"], dict) else None,
            "allergies": [a.get("allergen") for a in (flags["E09"] or [])] if isinstance(flags["E09"], list) else [],
            "last_update": {"as_of": iso(any_row["at"]), "trigger": any_row["trigger"]},
            "templates": templates,
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
        }
        return {"at": iso(at), "template": tpl, "kpis": kpis, "rows": rows, "scoped": bool(mine)}

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
        for tid, versions in by_t.items():
            row, rules = versions[-1], self.rules.get(tid)
            wf = workflow(row, acts, versions, summary["phase"] == "arrived", rules)
            draft = effective(row, acts, ref, rules)
            # What changed: since the signature when the template changed after signing (the text the ED signed,
            # clinician edits included), else since the previous version; both as the clinician sees them.
            base, since = None, "previous"
            if wf["state"] == "changed":
                s, before, _ = _last_sign(acts, tid)
                signed = next((v for v in versions if v["version"] == s["version"]), None)
                if signed:
                    base, since = (s["version"], effective(signed, before, s["at"], rules)), "signed"
            elif len(versions) > 1:
                base = (versions[-2]["version"], effective(versions[-2], acts, ref, rules))
            templates[tid] = {
                "draft": draft, "row": {k: row[k] for k in ("version", "status", "trigger", "model")}
                | {"as_of": iso(row["at"])},
                "workflow": wf,
                "changes": {"since": since, "version": base[0] if base else None,
                            "elements": diff(base[1] if base else None, draft)},
                "versions": [{"version": v["version"], "as_of": iso(v["at"]), "trigger": v["trigger"], "status": v["status"],
                              "severity": v["severity"], "score": v["score"]} for v in versions],
            }
        log = sorted(({"kind": a["kind"], "template": a["template"], "by": a["by"], "at": iso(a["at"]),
                       "version": a["version"], "detail": a.get("element") or a.get("rule") or a.get("option")}
                      | ({"restore": True} if a.get("restore") else {})
                      for a in acts), key=lambda a: a["at"], reverse=True)
        return {"summary": summary, "templates": templates, "activity": log}

    # ------------------------------------------------------------------------------------------ analytics
    def analytics(self, at: datetime) -> dict:
        ds, encs = self.ds, list(self.ds.enc.values())
        start = ds.start
        series, t = [], start
        while t <= min(at, ds.end):
            ed = sum(1 for e in encs if e["arrival_ts"] and e["arrival_ts"] <= t and (e["left_ts"] is None or e["left_ts"] > t)
                     and e["status"] != "canceled")
            board = sum(1 for e in encs if e["admit_decision_ts"] and e["admit_decision_ts"] <= t
                        and (e["ed_departure_ts"] is None or e["ed_departure_ts"] > t) and ds.versions.get(e["csn"])
                        and (e["ed_departure_ts"] or self.stale_boarding is None or t - e["admit_decision_ts"] <= self.stale_boarding))
            series.append({"t": iso(t), "ed": ed, "boarding": board})
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
            timing.append(len({r["at"] for r in ds.versions[e["csn"]] if r["at"] <= at}))
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
        drafts = [r for rows in ds.versions.values() for r in rows if r["at"] <= at]
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
            "addenda": len({(r["csn"], r["at"]) for r in drafts if r["status"] == "addendum"}),
        }

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
        wf = workflow(latest, acts, [r for r in self.ds.rows_at(csn, at) if r["template"] == template], arrived, rules)
        row = {"csn": csn, "template": template, "version": latest["version"], "at": at, "by": person["name"],
               "role": person["role"], "user_email": person.get("user_email")}
        if kind in ("edit", "attest", "sign"):
            if template not in SIGNS.get(person["role"], ()):
                raise ActionError("Only the sending ED clinician for this template can change or sign it.")
            # After the patient leaves the ED the handoff is final, except for an addendum, or when the ED never
            # signed it or signed an older version: then the ED can still complete and sign it.
            unsigned = arrived and (not wf.get("signed") or wf["signed"]["version"] < latest["version"])
            if summary["phase"] != "boarding" and latest["status"] != "addendum" and not unsigned:
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
        eff = effective(latest, acts, min(at, dep) if dep else at, rules)
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
            row |= {"rule": r["rule"], "option": body["option"]}
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
        self.store.add(kind, row)
        return self.case(csn, at, person)


def _histogram(values: list[float]) -> list[dict]:
    bins = [(0.0, 0.5, "under 50%"), (0.5, 0.6, "50 to 59%"), (0.6, 0.7, "60 to 69%"), (0.7, 0.8, "70 to 79%"),
            (0.8, 0.9, "80 to 89%"), (0.9, 1.01, "90% or more")]
    return [{"bin": label, "n": sum(1 for v in values if lo <= v < hi)} for lo, hi, label in bins]
