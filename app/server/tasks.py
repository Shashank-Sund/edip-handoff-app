"""Tasks: the action list of a signed handoff, and tasks added by hand, for whoever has the patient.

Tasks are rows that are only ever added (MemoryStore.add_tasks in mock mode; app.task_event in Lakebase):
  create      a new task: an action list item (E24) of a handoff when it is signed, or a task added by hand
  done        the task is done
  not_needed  the task is not needed, with a short reason
  reopen      Undo of done or not needed: the task is open again
  reassign    the task goes to a role (nurse or physician) or to a colleague
  due         a new due time, or none

A task at time T is its events with at <= T applied in order, as clinician actions and owners are read at T
(workflow.py, assignment.py). Signing a handoff turns each action list item into a task; signing again, or an
addendum, adds only the items whose text (case and spacing aside) is not a task of that template yet.

Who has the patient acts on their tasks: the ED nurse and physician while the patient boards; the receiving nurse
(the destination unit's) and the admitting physician after arrival. Anyone else but the viewer may still act; the
event records that it was outside their role.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta

from data import TZ, iso

KINDS = ("create", "done", "not_needed", "reopen", "reassign", "due")
ROLES = ("nurse", "physician")
CLOSED = ("done", "not_needed")
UNDOABLE = ("done", "not_needed", "reassign", "due")
# The discipline of each app role: the tasks of that role are theirs. The viewer has none.
DISCIPLINE = {"ed_nurse": "nurse", "unit_nurse": "nurse", "ed_physician": "physician", "admitting_physician": "physician"}
# Who has the patient: while boarding (or after a canceled admission, still in the ED), and once on the unit.
HAS_PATIENT = {"boarding": ("ed_nurse", "ed_physician"), "canceled": ("ed_nurse", "ed_physician"),
               "arrived": ("unit_nurse", "admitting_physician")}
# The words of an action list owner that name a role; the first one in the text decides, else the nurse. A medical
# service or specialty is the physician: "Hospital Medicine", "GI service", "Cardiology" (any word ending in -ology).
NURSE_WORDS = ("rn", "nurse", "nurses", "nursing")
PHYSICIAN_WORDS = ("md", "physician", "physicians", "team", "medicine", "service", "hospitalist", "attending", "resident",
                   "orthopedics", "psychiatry", "surgery")
# Due words that mean the time of signing, and a time of day, alone or after "by" or "at".
NOW_WORDS = ("now", "immediate", "immediately", "stat")
CLOCK = re.compile(r"(?:(?:by|at)[ \t]+)?([01]?[0-9]|2[0-3]):([0-5][0-9])", re.IGNORECASE)
SOON = timedelta(hours=2)                 # the Tasks page's "Due in the next 2 hours"
RECENT = timedelta(hours=12)              # and its recently completed tasks


def role_of(owner: str | None) -> str:
    """The role an action list item's owner names: "RN", "nurse" or "Nursing" is the nurse; "MD", "physician", a
    "team" or a medical service or specialty ("Hospital Medicine", "Cardiology") the physician; the first such word
    wins ("Nursing/MD" is the nurse); anything else ("Pharmacy", "Respiratory Therapy") is the nurse."""
    for w in re.findall(r"[a-z]+", (owner or "").lower()):
        if w in NURSE_WORDS:
            return "nurse"
        if w in PHYSICIAN_WORDS or w.endswith("ology"):
            return "physician"
    return "nurse"


def due_of(due: str | None, signed: datetime) -> datetime | None:
    """When an action list item is due, from its due text and the time the handoff was signed: a time of day such as
    "16:00" (or "by 16:00", "at 16:00") is the next 16:00 at or after signing (facility time, wall clock); "Now",
    "immediate", "immediately" and "stat" are the time of signing; anything else (empty, or repeating or vague such
    as "ongoing", "daily", "q4h") has no due time. The task keeps the words (due_text)."""
    text = (due or "").strip()
    if text.lower() in NOW_WORDS:
        return signed
    m = CLOCK.fullmatch(text)
    if not m:
        return None
    day = signed.astimezone(TZ).date()
    for d in range(2):
        t = datetime.combine(day + timedelta(days=d), time(int(m[1]), int(m[2]))).replace(tzinfo=TZ)
        if t >= signed:
            return t
    raise ValueError("no such time in the next two days")


def key(text: str) -> str:
    """An action list item's text as the duplicate check compares it: lowercase, single spaces."""
    return " ".join(text.lower().split())


def ordered(events: list[dict]) -> list[dict]:
    """Task events in the order they apply: by the time they apply to, then the order they were saved."""
    return sorted(events, key=lambda e: (e["at"], e["event_id"]))


def _assignee(e: dict) -> dict | None:
    return {"id": e["assignee_id"], "name": e.get("assignee_name") or e["assignee_id"]} if e.get("assignee_id") else None


def replay(events: list[dict], at: datetime | None = None) -> dict[int, dict]:
    """{task id: task} from one or more patients' task events with at <= `at` (every event when None), in the order
    the tasks were created. A task keeps every event as its history."""
    out: dict[int, dict] = {}
    for e in ordered([e for e in events if at is None or e["at"] <= at]):
        k, tid = e["kind"], e["task_id"]
        if k == "create":
            out[tid] = {"id": tid, "csn": e["csn"], "text": e["text"], "role": e["role"], "assignee": _assignee(e),
                        "due": e.get("due"), "due_text": e.get("due_text"), "status": "open", "reason": None,
                        "source": "handoff" if e.get("template") else "added", "template": e.get("template"),
                        "version": e.get("version"), "owner_text": e.get("owner_text"),
                        "created": {"by": e["by_name"], "at": e["at"]}, "closed": None, "history": []}
        t = out.get(tid)
        if t is None:
            continue                      # an event of a task created later (or never): nothing to apply it to
        if k in CLOSED:
            t |= {"status": k, "reason": e.get("reason") if k == "not_needed" else None,
                  "closed": {"by": e["by_name"], "at": e["at"]}}
        elif k == "reopen":
            t |= {"status": "open", "reason": None, "closed": None}
        elif k == "reassign":
            t |= {"role": e["role"], "assignee": _assignee(e)}
        elif k == "due":
            t["due"] = e.get("due")
        t["history"].append(e)
    return out


def undo_of(task: dict) -> str | None:
    """What Undo reverts on the task, the kind of its last change, or None: done and not needed reopen it, a reassign or
    a due time change goes back. An undo is not undone again, and a task with no change since it was created has none."""
    last = task["history"][-1]
    return last["kind"] if last["kind"] in UNDOABLE and not last.get("undo") else None


def overdue(task: dict, at: datetime) -> bool:
    return task["status"] == "open" and task["due"] is not None and task["due"] < at


def group(task: dict, at: datetime) -> str:
    """The Tasks page group of an open task: overdue, soon (due in the next 2 hours), later, or none (no due time)."""
    if task["due"] is None:
        return "none"
    if task["due"] < at:
        return "overdue"
    return "soon" if task["due"] <= at + SOON else "later"


def _step(e: dict) -> dict:
    """One event of a task's history, as the panel shows it."""
    out = {"kind": e["kind"], "by": e["by_name"], "by_role": e["by_role"], "at": iso(e["at"])}
    if e["kind"] == "not_needed":
        out["reason"] = e.get("reason")
    elif e["kind"] == "reassign":
        out |= {"role": e["role"], "to": e.get("assignee_name"), "from_role": e.get("from_role"),
                "from": e.get("from_assignee_name")}
    elif e["kind"] == "due":
        out |= {"due": iso(e.get("due")), "from_due": iso(e.get("from_due"))}
    if e.get("undo"):
        out["undo"] = True
    if e.get("outside_role"):
        out["outside_role"] = True
    return out


def shown(task: dict, at: datetime) -> dict:
    """A task as the API returns it at time `at`."""
    return {"id": task["id"], "csn": task["csn"], "text": task["text"], "role": task["role"], "assignee": task["assignee"],
            "due": iso(task["due"]), "due_text": task["due_text"], "overdue": overdue(task, at), "status": task["status"],
            "reason": task["reason"], "source": task["source"], "template": task["template"], "version": task["version"],
            "owner_text": task["owner_text"], "created": {"by": task["created"]["by"], "at": iso(task["created"]["at"])},
            "closed": {"by": task["closed"]["by"], "at": iso(task["closed"]["at"])} if task["closed"] else None,
            "undo": undo_of(task), "history": [_step(e) for e in task["history"]]}


def counts(tasks: list[dict], at: datetime) -> dict:
    """Open and overdue tasks, for the board row."""
    return {"open": sum(1 for t in tasks if t["status"] == "open"), "overdue": sum(1 for t in tasks if overdue(t, at))}
