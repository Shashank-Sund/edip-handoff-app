"""Write the world as JSONL truth tables plus a summary of key distributions."""
import json
import statistics
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path

ENTITIES = ["patients", "encounters", "adt", "ed_events", "bed_requests", "orders", "results", "med_orders", "mar",
            "vitals", "notes", "allergies", "code_status", "infections", "census", "departments", "providers", "facilities"]


def _default(o):
    if isinstance(o, datetime):
        return o.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(o, set):
        return sorted(o)
    raise TypeError(type(o))


def _row(x):
    return asdict(x) if is_dataclass(x) else x


def write(world, out_dir: Path) -> dict:
    out = Path(out_dir) / "truth"
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name in ENTITIES:
        rows = getattr(world, name)
        with open(out / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(_row(r), default=_default, ensure_ascii=False) + "\n")
        counts[name] = len(rows)
    with open(out / "fault_manifest.jsonl", "w", encoding="utf-8") as f:
        for r in world.faults:
            f.write(json.dumps(r, default=_default, ensure_ascii=False) + "\n")
    (out / "render_plan.json").write_text(json.dumps(world.render_plan, indent=1, default=_default))
    (out / "config.json").write_text(json.dumps(world.config, indent=1, default=_default))
    s = summary(world)
    s["counts"] = counts
    (Path(out_dir) / "summary.json").write_text(json.dumps(s, indent=1, default=_default))
    return s


def _q(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def summary(w) -> dict:
    encs = [e for e in w.encounters if e.disposition != "CANCELED"]
    mins = lambda a, b: (b - a) / timedelta(minutes=1)  # noqa: E731
    adm = [e for e in encs if e.disposition.startswith("ADMIT")]
    board = [mins(e.decision_at, e.departure_at) for e in adm if e.decision_at and e.departure_at]
    d2p = [mins(e.arrival_at, e.provider_at) for e in encs if e.provider_at]
    a2d = [mins(e.arrival_at, e.decision_at) for e in adm if e.decision_at]
    dlos = [mins(e.arrival_at, e.departure_at) for e in encs if e.disposition == "DISCHARGE" and e.departure_at]
    by = lambda f: {k: sum(1 for e in encs if f(e) == k) for k in sorted({f(e) for e in encs}, key=str)}  # noqa: E731
    pats = {p.pat_id: p for p in w.patients}
    age = lambda e: (e.arrival_at.year - int(pats[e.pat_id].birth_date[:4]))  # noqa: E731
    flags = {}
    for f in w.faults:
        flags[f["fault_id"]] = flags.get(f["fault_id"], 0) + 1
    crit = [r for r in w.results if r.flag in ("HH", "LL")]
    return {
        "encounters": len(encs), "admitted": len(adm),
        "admit_rate": round(len(adm) / len(encs), 3),
        "disposition": by(lambda e: e.disposition), "facility": by(lambda e: e.facility), "esi": by(lambda e: e.esi),
        "state_at_end": by(lambda e: e.state_at_end), "arrival_mode": by(lambda e: e.arrival_mode),
        "admitted_65_plus": round(sum(1 for e in adm if age(e) >= 65) / max(1, len(adm)), 3),
        "admitted_ems": round(sum(1 for e in adm if e.arrival_mode == "EMS") / max(1, len(adm)), 3),
        "female": round(sum(1 for e in encs if pats[e.pat_id].sex == "F") / len(encs), 3),
        "isolation_admits": sum(1 for e in adm if e.isolation),
        "minutes": {"door_to_provider": {"median": _q(d2p, .5), "p90": _q(d2p, .9)},
                    "arrival_to_decision": {"median": _q(a2d, .5), "p90": _q(a2d, .9)},
                    "boarding": {"median": _q(board, .5), "p90": _q(board, .9), "p99": _q(board, .99)},
                    "discharged_los": {"median": _q(dlos, .5), "p90": _q(dlos, .9)}},
        "critical_results": len(crit), "results": len(w.results),
        "allergy_any_share": round(len({a.pat_id for a in w.allergies}) / max(1, len(w.patients)), 3),
        "faults": dict(sorted(flags.items())),
    }
