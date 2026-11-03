"""Read a truth folder (the JSONL files written by edip_synth.output) back into
the World dataclasses, so the realism tests and the review report can run on a
published world exactly as they run on a freshly generated one.

Standard library only. Timestamps written as "YYYY-MM-DDTHH:MM:SSZ" come back
as timezone aware UTC datetimes; everything else is left as JSON gave it.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edip_synth import model  # noqa: E402
from edip_synth.reference import Department, Facility, Provider  # noqa: E402

UTC = timezone.utc
_TS = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

TABLES = {
    "patients": model.Patient, "encounters": model.Encounter, "adt": model.AdtEvent, "ed_events": model.EdEvent,
    "bed_requests": model.BedRequest, "orders": model.Order, "results": model.Result, "med_orders": model.MedOrder,
    "mar": model.MarAdmin, "vitals": model.Vital, "notes": model.Note, "allergies": model.Allergy,
    "code_status": model.CodeStatus, "infections": model.Infection, "departments": Department,
    "providers": Provider, "facilities": Facility,
}


def _parse(v):
    if isinstance(v, str) and _TS.match(v):
        return datetime.strptime(v, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    if isinstance(v, dict):
        return {k: _parse(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_parse(x) for x in v]
    return v


def _rows(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield _parse(json.loads(line))


def load_truth(folder) -> model.World:
    """folder is the truth directory itself (contains encounters.jsonl) or its parent."""
    folder = Path(folder)
    if not (folder / "encounters.jsonl").exists() and (folder / "truth" / "encounters.jsonl").exists():
        folder = folder / "truth"
    config = json.loads((folder / "config.json").read_text())
    w = model.World(config, [], [], [])
    for name, cls in TABLES.items():
        rows = [cls(**r) for r in _rows(folder / f"{name}.jsonl")]
        setattr(w, name, rows)
    w.census = list(_rows(folder / "census.jsonl"))
    w.faults = list(_rows(folder / "fault_manifest.jsonl"))
    w.render_plan = json.loads((folder / "render_plan.json").read_text())
    return w


TZ = ZoneInfo("America/New_York")


def window(world) -> tuple:
    """(start, end) of the generated week as UTC datetimes, read from the world config."""
    cfg = world.config
    local_start = datetime.fromisoformat(cfg["window_start"]).astimezone(TZ)
    # Wall clock arithmetic in local time: DST ends inside the window, so the last day has 25 hours.
    return local_start.astimezone(UTC), (local_start + timedelta(days=int(cfg["days"]))).astimezone(UTC)
