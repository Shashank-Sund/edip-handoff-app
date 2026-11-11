"""Clinician actions on a handoff: field edits, attestations, sign off and acknowledgement.

Append only: nothing is updated or deleted, so the record is its own audit trail. Every action carries `at`, the
time it applies to (the demo clock in mock mode, the current time live), and `recorded_ts`, when it was saved.
A view at time T counts only actions with at <= T.

MemoryStore keeps actions in process (mock mode). The Lakebase store (lakebase.py) writes the same rows to the
table app.handoff_action, one row per action with its kind and details. With the live clock, every view of an
earlier time is logged too (log_view; app.view_log in Lakebase). Who owns each patient is a separate append only
list (assign and assignments; assignment.py has the rules), and so are the tasks (add_tasks and task_events;
tasks.py has the rules) and each clinician's looks at a patient (look and last_looks; seen.py has the rules).
"""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone

import seen
from assignment import KINDS as ASSIGNMENT_KINDS
from tasks import KINDS as TASK_KINDS

KINDS = ("edit", "attest", "sign", "ack")       # a Restore is an edit with restore set: the model's text again


def last_names(actions: list[dict], assignments: list[dict], tasks: list[dict] = ()) -> dict[str, str]:
    """{lowercased email: the display name that person last used}, from clinician actions (by), ownership changes and
    task events (by_name), the latest saved winning. Live mode names colleagues with it after a restart
    (main._colleagues)."""
    seen = [(r["recorded_ts"], (r.get("user_email") or "").strip().lower(), r.get("by")) for r in actions]
    seen += [(r["recorded_ts"], (r.get("user_email") or "").strip().lower(), r.get("by_name")) for r in [*assignments, *tasks]]
    return {email: name for _, email, name in sorted(seen, key=lambda x: x[0]) if email and name}


class StoreUnavailable(RuntimeError):
    """The action store cannot be read or written now (live mode: Lakebase unreachable); the message is shown."""


class MemoryStore:
    kind = "memory"

    def __init__(self):
        self._rows: dict[str, list[dict]] = defaultdict(list)
        self.views: list[dict] = []
        self._assigned: dict[str, list[dict]] = defaultdict(list)
        self._tasks: dict[str, list[dict]] = defaultdict(list)
        self._looks: list[dict] = []
        self._n = 0
        self._events = 0
        self._task_ids = 0
        self._lock = threading.Lock()

    def add(self, kind: str, row: dict) -> dict:
        assert kind in KINDS
        row = {**row, "kind": kind, "recorded_ts": datetime.now(timezone.utc)}
        with self._lock:
            self._rows[row["csn"]].append(row)
        return row

    def log_view(self, row: dict) -> None:
        with self._lock:
            self.views.append({**row, "recorded_ts": datetime.now(timezone.utc)})

    def actions(self, csn: str, at: datetime | None = None, fresh: bool = False) -> list[dict]:
        with self._lock:
            rows = list(self._rows.get(csn, []))
        return [r for r in rows if at is None or r["at"] <= at]

    def all_actions(self, at: datetime | None = None) -> list[dict]:
        with self._lock:
            rows = [r for v in self._rows.values() for r in v]
        return [r for r in rows if at is None or r["at"] <= at]

    def assign(self, row: dict) -> dict:
        """One ownership change (claim, assign, release, handover); assignment_id gives the order it was saved."""
        assert row["kind"] in ASSIGNMENT_KINDS
        with self._lock:
            self._n += 1
            row = {**row, "assignment_id": self._n, "recorded_ts": datetime.now(timezone.utc)}
            self._assigned[row["csn"]].append(row)
        return row

    def assignments(self, csn: str, at: datetime | None = None, fresh: bool = False) -> list[dict]:
        with self._lock:
            rows = list(self._assigned.get(csn, []))
        return [r for r in rows if at is None or r["at"] <= at]

    def add_tasks(self, rows: list[dict]) -> list[dict]:
        """Task events, saved together; event_id gives the order they were saved, and a create gets a new task_id.
        A create from a handoff whose item is a task of that template already (csn, template, key) is skipped, as
        the unique index skips it in Lakebase. Returns the rows saved."""
        out = []
        with self._lock:
            for row in rows:
                assert row["kind"] in TASK_KINDS
                if row["kind"] == "create" and row.get("template") and any(
                        e["kind"] == "create" and e.get("template") == row["template"] and e.get("item_key") == row["item_key"]
                        for e in self._tasks[row["csn"]]):
                    continue
                self._events += 1
                if row["kind"] == "create":
                    self._task_ids += 1
                    row = {**row, "task_id": self._task_ids}
                row = {**row, "event_id": self._events, "recorded_ts": datetime.now(timezone.utc)}
                self._tasks[row["csn"]].append(row)
                out.append(row)
        return out

    def task_events(self, csn: str, at: datetime | None = None, fresh: bool = False) -> list[dict]:
        with self._lock:
            rows = list(self._tasks.get(csn, []))
        return [r for r in rows if at is None or r["at"] <= at]

    def all_task_events(self, at: datetime | None = None, fresh: bool = False) -> list[dict]:
        with self._lock:
            rows = [r for v in self._tasks.values() for r in v]
        return [r for r in rows if at is None or r["at"] <= at]

    def look(self, row: dict) -> dict:
        """One look at a patient (opening the handoff, or Mark as seen); seen_id gives the order it was saved."""
        assert row["source"] in seen.SOURCES
        with self._lock:
            row = {**row, "seen_id": len(self._looks) + 1, "recorded_ts": datetime.now(timezone.utc)}
            self._looks.append(row)
        return row

    def last_looks(self, who: str, at: datetime, csn: str | None = None, fresh: bool = False) -> dict[str, dict]:
        """{csn: the latest look by `who` with at <= `at`}, at one patient (csn) or all."""
        with self._lock:
            rows = [r for r in self._looks if csn is None or r["csn"] == csn]
        return seen.last_looks(rows, who, at)

    def names(self) -> dict[str, str]:
        with self._lock:
            acts = [r for v in self._rows.values() for r in v]
            assigned = [r for v in self._assigned.values() for r in v]
            tasks = [r for v in self._tasks.values() for r in v]
        return last_names(acts, assigned, tasks)

    def reset(self):
        with self._lock:
            self._rows.clear()
            self._assigned.clear()
            self._tasks.clear()
            self._looks.clear()
