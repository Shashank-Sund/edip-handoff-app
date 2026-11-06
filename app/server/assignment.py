"""Who owns each ED patient: one nurse owner and one physician owner per patient, and the ED's shift changes.

Owners are set by rows that are only ever added (MemoryStore.assign in mock mode; app.assignment in Lakebase):
  claim      Take: the clinician takes the patient for themselves
  assign     a clinician gives the patient to a colleague in the same role
  release    the owner gives the patient up; it is Unassigned again
  handover   Take over at a shift change: the clinician takes a colleague's patient

The owner for a role at time T is the latest row for (csn, role) with at <= T, as clinician actions are read at T
(workflow.py). An owner who has not acted on the patient, taken it or been given it for longer than the stale limit
(EDIP_STALE_OWNER_HOURS) no longer counts: the patient shows as Unassigned, with the stale owner named.

A person is their persona id with demo personas, and their lowercased sign in email otherwise (person_key). Acting
on a patient someone else owns is allowed; the action records that it was outside the assignment.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from data import TZ, iso

ROLES = ("ed_nurse", "ed_physician")
KINDS = ("claim", "assign", "release", "handover")
OWNS = ("claim", "assign", "handover")          # the kinds that give the patient an owner


def person_key(person: dict) -> str:
    """Who a person is for ownership: set by main._person (persona id, or the sign in email live)."""
    return person.get("key") or person["id"]


def actor(a: dict) -> str | None:
    """Who did a clinician action: by_id when the app recorded it, else the signed in email (live rows)."""
    return a.get("by_id") or (a.get("user_email") or "").strip().lower() or None


def ordered(rows: list[dict]) -> list[dict]:
    """Assignment rows in the order they apply: by the time they apply to, then the order they were saved."""
    return sorted(rows, key=lambda r: (r["at"], r["assignment_id"]))


def owner_at(rows: list[dict], role: str, acts: list[dict], at: datetime, stale: timedelta, me: str | None) -> dict | None:
    """The owner for `role` at `at`, from one patient's assignment rows and clinician actions; None when no one owns
    the patient (never taken, or released). stale: the owner has not acted, taken it or been given it within the
    stale limit, so the patient counts as Unassigned. you: the owner is `me`."""
    mine = ordered([r for r in rows if r["role"] == role and r["at"] <= at])
    if not mine or mine[-1]["kind"] not in OWNS:
        return None
    last = mine[-1]
    who = last["assignee_id"]
    active = max([last["at"]] + [a["at"] for a in acts if a["at"] <= at and actor(a) == who])
    return {"id": who, "name": last["assignee_name"], "since": iso(last["at"]), "last_active": iso(active),
            "stale": at - active > stale, "you": who == me}


def counts(owner: dict | None) -> bool:
    """Whether an owner counts: someone owns the patient and has not gone stale."""
    return bool(owner) and not owner["stale"]


def next_shift(at: datetime, changes: list[tuple[int, int]]) -> datetime:
    """The first shift change after `at`, at the facility's local time of day (wall clock, so a daylight saving
    change keeps 07:00 at 07:00)."""
    day = at.astimezone(TZ).date()
    for d in range(3):
        for h, m in changes:
            t = datetime.combine(day + timedelta(days=d), time(h, m)).replace(tzinfo=TZ)
            if t > at:
                return t
    raise ValueError("no shift change in the next two days")


def last_shift(at: datetime, changes: list[tuple[int, int]]) -> datetime:
    """The latest shift change at or before `at`, at the facility's local time of day."""
    day = at.astimezone(TZ).date()
    for d in range(0, 3):
        for h, m in reversed(changes):
            t = datetime.combine(day - timedelta(days=d), time(h, m)).replace(tzinfo=TZ)
            if t <= at:
                return t
    raise ValueError("no shift change in the last two days")


# How far a handover was from the nearest shift change, for Analytics.
HANDOVER_BUCKETS = ("More than 30 min before", "Up to 30 min before", "Up to 30 min after", "More than 30 min after")


def handover_bucket(at: datetime, changes: list[tuple[int, int]]) -> str:
    """Which of HANDOVER_BUCKETS a handover at `at` falls in, measured from the nearest shift change."""
    before, after = next_shift(at, changes), last_shift(at, changes)
    near = after if at - after <= before - at else before
    m = (at - near).total_seconds() / 60
    return HANDOVER_BUCKETS[0 if m < -30 else 1 if m < 0 else 2 if m <= 30 else 3]
