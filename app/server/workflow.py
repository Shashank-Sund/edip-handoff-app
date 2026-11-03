"""Clinician actions on a handoff: field edits, attestations, sign off and acknowledgement.

Append only: nothing is updated or deleted, so the record is its own audit trail. Every action carries `at`, the
time it applies to (the demo clock in mock mode, the current time live), and `recorded_ts`, when it was saved.
A view at time T counts only actions with at <= T.

MemoryStore keeps actions in process (mock mode). The Lakebase store (lakebase.py) writes the same rows to the
table app.handoff_action, one row per action with its kind and details.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime, timezone

KINDS = ("edit", "attest", "sign", "ack")


class MemoryStore:
    kind = "memory"

    def __init__(self):
        self._rows: dict[str, list[dict]] = defaultdict(list)
        self._lock = threading.Lock()

    def add(self, kind: str, row: dict) -> dict:
        assert kind in KINDS
        row = {**row, "kind": kind, "recorded_ts": datetime.now(timezone.utc)}
        with self._lock:
            self._rows[row["csn"]].append(row)
        return row

    def actions(self, csn: str, at: datetime | None = None) -> list[dict]:
        with self._lock:
            rows = list(self._rows.get(csn, []))
        return [r for r in rows if at is None or r["at"] <= at]

    def all_actions(self, at: datetime | None = None) -> list[dict]:
        with self._lock:
            rows = [r for v in self._rows.values() for r in v]
        return [r for r in rows if at is None or r["at"] <= at]

    def reset(self):
        with self._lock:
            self._rows.clear()
