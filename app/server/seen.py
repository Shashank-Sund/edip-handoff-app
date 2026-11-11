"""What changed since you last looked: the looks each clinician takes at a patient, and the changes others made since.

Looks are rows that are only ever added (MemoryStore.look in mock mode; app.seen_event in Lakebase), one per:
  handoff     opening the patient's handoff page
  board       Mark as seen on the patient's board row
A look records who (the person's id, as owners and tasks name them: assignment.person_key), the patient, the time
viewed (at) and when it was saved.

Your last look at time T is your latest look at the patient with at <= T, as owners and tasks are read at T
(assignment.py, tasks.py): looking at an earlier time never moves your last look at a later time forward. A look is
not a clinical action, so it is recorded at an earlier time too, and live.

What changed between your last look and T, by anyone but you (a change from the pipeline has no author and always
counts):
  version     a newer draft version of a template: the items that differ from the version current at your look
              (clinician edits applied to both, so only the model's and the chart's changes show)
  action      a sign off, acknowledgement, edit or attestation (workflow.py)
  task        a task event (tasks.py)
  owner       a change of owner (assignment.py)
  phase       the patient arrived on the unit, or the admission was canceled
  severity    the illness severity changed
A clinician's change counts when it applies after the look, or at the same time and was saved after it: the replay
clock stands still while people act, so a colleague's sign off at the time you looked is still news. A pipeline
change counts when its version shows after the look.
"""
from __future__ import annotations

from datetime import datetime

from assignment import actor
from data import iso

SOURCES = ("handoff", "board")


def ordered(looks: list[dict]) -> list[dict]:
    """Looks in the order they apply: by the time viewed, then the order they were saved."""
    return sorted(looks, key=lambda r: (r["at"], r["seen_id"]))


def last_looks(looks: list[dict], who: str, at: datetime) -> dict[str, dict]:
    """{csn: the latest look by `who` with at <= `at`}, from looks at any patients."""
    out = {}
    for r in ordered([r for r in looks if r["by_id"] == who and r["at"] <= at]):
        out[r["csn"]] = r
    return out


def after(row: dict, look: dict) -> bool:
    """Whether a clinician's change (an action, task event or owner change) came after the look: a later time, or the
    same time and saved after it."""
    return (row["at"], row["recorded_ts"]) > (look["at"], look["recorded_ts"])


def by_other(row: dict, me: str) -> bool:
    """Whether someone else made the change (actor: by_id, else the signed in email)."""
    return actor(row) != me


def newest_first(items: list[tuple[datetime, dict]]) -> list[dict]:
    """The changes, newest first; changes at the same time keep the order they were listed in."""
    return [x for _, x in sorted(items, key=lambda x: x[0], reverse=True)]


def summary(look: dict | None, items: list[dict] | None) -> dict:
    """The board row's since: how many changes since your last look (none before a first look), when that was, and
    whether you never opened the patient."""
    return {"count": len(items or []), "last_look": iso(look["at"]) if look else None, "first_time": look is None}
