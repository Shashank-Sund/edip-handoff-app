"""The app's read model: encounters and every handoff draft version, from fixtures (mock mode) or Lakebase (live).

Both sources produce the same shapes, so every view is computed the same way: a view takes a time `at` and
shows the world as it was then (mock mode replays the reference week; live mode uses the current time).
"""
from __future__ import annotations

import gzip
import json
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
TS_FIELDS = ("arrival_ts", "admit_decision_ts", "ed_departure_ts", "last_event_ts")


def ts(v) -> datetime | None:
    """Aware UTC datetime from an ISO string or datetime; naive values are UTC."""
    if v is None or v == "":
        return None
    if not isinstance(v, datetime):
        v = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def iso(t: datetime | None) -> str | None:
    """Facility local time with offset, as clinicians read it."""
    return t.astimezone(TZ).isoformat(timespec="seconds") if t else None


class Dataset:
    def __init__(self, encounters: list[dict], drafts: list[dict], meta: dict, source: str):
        self.meta, self.source = meta, source
        self.enc: dict[str, dict] = {}
        for e in encounters:
            e = dict(e)
            for k in TS_FIELDS:
                e[k] = ts(e.get(k))
            # Discharged patients carry no ED departure; their last event is the discharge.
            e["left_ts"] = e["ed_departure_ts"] or (e["last_event_ts"] if e.get("last_event_kind") == "discharge" else None)
            self.enc[e["csn"]] = e
        self.versions: dict[str, list[dict]] = defaultdict(list)
        for r in drafts:
            r = dict(r)
            r["at"] = ts(r["as_of"])
            if isinstance(r.get("draft"), str):
                r["draft"] = json.loads(r["draft"])
            if isinstance(r.get("blocking"), str):
                r["blocking"] = json.loads(r["blocking"])
            self.versions[r["csn"]].append(r)
        for rows in self.versions.values():
            rows.sort(key=lambda r: (r["at"], r["template"], r["version"]))
        self._keys = {c: [r["at"] for r in rows] for c, rows in self.versions.items()}
        w = meta.get("window") or {}
        if w.get("start"):
            self.start = ts(w["start"])
            local = self.start.astimezone(TZ)          # wall clock days, so the window ends at the same local hour
            self.end = (local.replace(tzinfo=None) + timedelta(days=w.get("days", 7))).replace(tzinfo=TZ).astimezone(timezone.utc)
        else:
            times = [r["at"] for rows in self.versions.values() for r in rows] or [datetime.now(timezone.utc)]
            self.start, self.end = min(times), max(times)

    def rows_at(self, csn: str, at: datetime) -> list[dict]:
        rows = self.versions.get(csn, [])
        return rows[: bisect_right(self._keys.get(csn, []), at)]

    def latest_at(self, csn: str, at: datetime) -> dict[str, dict]:
        out = {}
        for r in self.rows_at(csn, at):
            out[r["template"]] = r
        return out

    def busiest(self) -> datetime:
        """The hour of the window with the most patients boarding: a good default demo time."""
        best, best_n = self.start, -1
        t = self.start + timedelta(hours=6)
        while t < self.end:
            n = sum(1 for e in self.enc.values() if e["admit_decision_ts"] and e["admit_decision_ts"] <= t
                    and (e["ed_departure_ts"] is None or e["ed_departure_ts"] > t) and self.rows_at(e["csn"], t))
            if n > best_n:
                best, best_n = t, n
            t += timedelta(hours=1)
        return best


def load_fixtures(path: Path) -> Dataset:
    meta = json.loads((path / "meta.json").read_text())
    encounters = json.loads((path / "encounters.json").read_text())
    with gzip.open(path / "drafts.jsonl.gz", "rt", encoding="utf-8") as f:
        drafts = [json.loads(line) for line in f]
    return Dataset(encounters, drafts, meta, "fixtures")
