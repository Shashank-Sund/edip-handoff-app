"""Shared helpers for renderers. Renderers read the truth JSONL written by
edip_synth.output, never the in-memory world, so they stay decoupled."""
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
TABLES = ["patients", "encounters", "adt", "ed_events", "bed_requests", "orders", "results", "med_orders", "mar",
          "vitals", "notes", "allergies", "code_status", "infections", "census", "departments", "providers", "facilities"]


def ts(s):
    """Parse a truth timestamp ("2026-10-26T14:03:00Z") to an aware UTC datetime; None stays None."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) if s else None


def load_truth(truth_dir) -> dict:
    """Returns {table: [row dicts]} plus "fault_manifest", "render_plan" and "config"."""
    d = Path(truth_dir)
    out = {}
    for t in TABLES:
        with open(d / f"{t}.jsonl", encoding="utf-8") as f:
            out[t] = [json.loads(line) for line in f]
    with open(d / "fault_manifest.jsonl", encoding="utf-8") as f:
        out["fault_manifest"] = [json.loads(line) for line in f]
    out["render_plan"] = json.loads((d / "render_plan.json").read_text())
    out["config"] = json.loads((d / "config.json").read_text())
    return out


def rng_for(plan: dict, feed: str) -> random.Random:
    """Deterministic RNG per feed, from the render plan seed."""
    return random.Random(f"{plan['seed']}-{feed}")


def write_manifest(rows, path):
    """Feed level fault rows: fault_id, csn, stream, event_time, description, expected, level="render"."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({**r, "level": "render"}, ensure_ascii=False) + "\n")
