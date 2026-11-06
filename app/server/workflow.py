"""Clinician actions on a handoff: field edits, attestations, sign off and acknowledgement.

Append only: nothing is updated or deleted, so the record is its own audit trail. Every action carries `at`, the
time it applies to (the demo clock in mock mode, the current time live), and `recorded_ts`, when it was saved.
A view at time T counts only actions with at <= T.

MemoryStore keeps actions in process (mock mode). The Lakebase store (lakebase.py) writes the same rows to the
table app.handoff_action, one row per action with its kind and details. With the live clock, every view of an
earlier time is logged too (log_view; app.view_log in Lakebase). Who owns each patient is a separate append only
list (assign and assignments; assignment.py has the rules).
"""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone

from assignment import KINDS as ASSIGNMENT_KINDS

KINDS = ("edit", "attest", "sign", "ack")       # a Restore is an edit with restore set: the model's text again


class StoreUnavailable(RuntimeError):
    """The action store cannot be read or written now (live mode: Lakebase unreachable); the message is shown."""


class MemoryStore:
    kind = "memory"

    def __init__(self):
        self._rows: dict[str, list[dict]] = defaultdict(list)
        self.views: list[dict] = []
        self._assigned: dict[str, list[dict]] = defaultdict(list)
        self._n = 0
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

    def reset(self):
        with self._lock:
            self._rows.clear()
            self._assigned.clear()
