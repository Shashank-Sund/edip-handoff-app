"""Evaluation set runner for the handoff generator.

Mode A (local, CI): build a stand in world, render the feeds, generate drafts with MockLLM at the job's generation
points, score every draft against the truth, print a report and write JSON.

    .venv/bin/python ai/eval/run_eval.py [--limit N] [--seed S] [--backfill-interval-min 60] [--json out.json]
        [--report out.txt] [--keep-world DIR]

Mode B (real drafts): score exported gold.handoff_draft rows (JSONL, one row per draft with csn, template, as_of,
draft and validation; draft and validation may be JSON strings) against the world they were generated from.

    .venv/bin/python ai/eval/run_eval.py --drafts drafts.jsonl --world synthetic/out/world [--json out.json]

Standard library plus pyyaml only; no Spark, no network.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "ai", ROOT / "synthetic", ROOT / "pipelines"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from edip_ai import generate, llm, local  # noqa: E402
from edip_ai.points import select_points, triggers as make_triggers  # noqa: E402
from eval import expected as X  # noqa: E402
from eval import score as S  # noqa: E402


def build_world(out_dir, seed=None, population=3000):
    """Stand in world written to out_dir (truth, fhir, hl7, clarity). Returns the truth directory."""
    from edip_synth import synthea
    from edip_synth.config import Config
    from edip_synth.output import write
    from edip_synth.render import clarity, fhir, hl7
    from edip_synth.world import Simulator
    cfg = Config() if seed is None else _with_seed(Config(), seed)
    out = Path(out_dir)
    write(Simulator(cfg, synthea.stand_in(population, cfg.seed)).run(), out)
    hl7.render(out / "truth", out)
    fhir.render(out / "truth", out)
    clarity.render(out / "truth", out)
    return out / "truth"


def _with_seed(cfg, seed):
    from dataclasses import replace
    return replace(cfg, seed=seed)


def local_rows(world_dir, limit=0, backfill_interval_min=60, model=None, feeds=("fhir", "hl7")):
    """Draft rows shaped like gold.handoff_draft (draft as a dict) from a rendered world, at the job's points."""
    world = Path(world_dir)
    model = model or llm.MockLLM()
    recs = local.records(world, feeds)
    try:
        clarity = local.clarity_tables(world)
    except (FileNotFoundError, IndexError):
        clarity = None
    points, retracted = select_points(make_triggers(recs), backfill_interval_min)
    csns = sorted({p[0] for p in points})
    if limit:
        keep = set(csns[:limit])
        points = [p for p in points if p[0] in keep]
    adt = recs["adt"]
    rows = []
    for csn, as_of, trigger, status in points:
        state = local.encounter_states([r for r in adt if r["csn"] == csn and r["effective_ts"] <= as_of]).get(csn)
        if not state:
            continue
        packet = local.packet(recs, state, as_of, clarity)
        drafts, run = generate.draft(packet, model)
        for tid, d in drafts.items():
            rows.append({"csn": csn, "template": tid, "status": status, "trigger": trigger, "as_of": X.iso(as_of),
                         "model": run["model"], "severity": d["severity"], "score": d["completeness"]["score"],
                         "blocking": d["completeness"]["blocking"], "draft": d, "validation": run["validation"]})
    return rows, {"points": len(points), "encounters": len({p[0] for p in points}), "retracted": sorted(retracted)}


def read_rows(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if isinstance(r.get("draft"), str):
                r["draft"] = json.loads(r["draft"])
            if isinstance(r.get("validation"), str):
                r["validation"] = json.loads(r["validation"]) if r["validation"] else {}
            rows.append(r)
    return rows


def evaluate(rows, truth):
    """Score every draft row; rows whose encounter is not in the truth are skipped and listed."""
    lexicon = truth.world_lexicon()
    results, skipped, cache = [], [], {}
    for r in rows:
        if r.get("status") == "retracted" or r["csn"] not in truth.encounters:
            skipped.append({"csn": r["csn"], "template": r.get("template"), "why": "retracted" if r.get("status") == "retracted" else "not in truth"})
            continue
        key = (r["csn"], X.iso(X.ts(r["as_of"])))
        if key not in cache:
            cache[key] = X.expected(truth, r["csn"], r["as_of"])
        results.append(S.score_draft(r["draft"], cache[key], truth, lexicon, r.get("validation")))
    return results, skipped


def report(summary, meta):
    """Plain text report in a light markdown style. No em or en dashes anywhere."""
    lines = ["# Handoff evaluation", ""]
    lines += [f"* mode: {meta.get('mode')}", f"* world: {meta.get('world')}", f"* drafts scored: {meta.get('drafts')}",
              f"* encounters: {meta.get('encounters')}", f"* generation points: {meta.get('points')}",
              f"* skipped rows: {meta.get('skipped', 0)}", f"* seconds: {meta.get('seconds')}", ""]

    def block(title, a):
        lines.append(f"## {title}")
        lines.append(f"* drafts {a['drafts']}, encounters {a['encounters']}")
        lines.append(f"* deterministic accuracy {a['deterministic_accuracy']}")
        for k, v in a["element_accuracy"].items():
            lines.append(f"    * {k}: {v}")
        lines.append(f"* missed critical items {a['missed_critical_total']} "
                     + ", ".join(f"{k} {v}" for k, v in a["missed_critical_by_kind"].items()))
        lines.append(f"* narrative support share {a['narrative_support_share']}, unknown sources {a['unknown_sources']}, "
                     f"unsupported numbers {a['unsupported_numbers']}, contradictions {a['contradictions']}, "
                     f"hallucinated medications {a['hallucinated_meds']}, hallucinated labs {a['hallucinated_labs']}")
        lines.append(f"* severity agreement {a['severity_agreement']} " + ", ".join(f"{k} {v}" for k, v in a["severity_confusion"].items()))
        lines.append(f"* completeness mean {a['completeness_mean']}, blocking share {a['blocking_share']} "
                     + ", ".join(f"{k} {v}" for k, v in a["blocking_rules"].items()))
        lines.append("")

    if summary.get("overall"):
        block("Overall", summary["overall"])
    for t, a in summary.get("per_template", {}).items():
        block(f"Template {t}", a)
    lines.append("## Worst cases")
    if not summary.get("worst"):
        lines.append("* none: every draft matched the expected facts")
    for w in summary.get("worst", []):
        lines.append(f"* {w['csn']} {w['template']} at {w['as_of']} faults {','.join(w['faults']) or 'none'}: "
                     f"deterministic {w['deterministic_accuracy']}, missed {w['missed_critical']}, "
                     f"severity {w['severity']['draft']} vs {w['severity']['reference']}")
        for why in w["reasons"]:
            lines.append(f"    * {why}")
    text = "\n".join(lines) + "\n"
    assert chr(0x2014) not in text and chr(0x2013) not in text
    return text


def run(args):
    t0 = time.time()
    if args.drafts:
        truth = X.Truth(args.world)
        rows = read_rows(args.drafts)
        meta = {"mode": "B (exported drafts)", "world": str(args.world), "points": len({(r["csn"], str(r["as_of"])) for r in rows})}
        results, skipped = evaluate(rows, truth)
    else:
        tmp = None
        world_dir = Path(args.keep_world) if args.keep_world else None
        if world_dir is None:
            tmp = tempfile.TemporaryDirectory(prefix="edip_eval_")
            world_dir = Path(tmp.name)
        truth_dir = build_world(world_dir, args.seed)
        truth = X.Truth(truth_dir)
        rows, info = local_rows(world_dir, args.limit, args.backfill_interval_min)
        meta = {"mode": "A (stand in world, MockLLM)", "world": str(world_dir), **info}
        results, skipped = evaluate(rows, truth)
        if tmp:
            tmp.cleanup()
    summary = S.aggregate(results)
    meta.update(drafts=len(results), encounters=len({r["csn"] for r in results}), skipped=len(skipped),
                seconds=round(time.time() - t0, 1))
    text = report(summary, meta)
    out = {"meta": meta, "summary": summary, "results": results, "skipped": skipped}
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    return text, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drafts", help="JSONL of exported gold.handoff_draft rows (Mode B)")
    ap.add_argument("--world", default=str(ROOT / "synthetic" / "out" / "world"), help="world directory or its truth folder (Mode B)")
    ap.add_argument("--limit", type=int, default=0, help="Mode A: only the first N encounters")
    ap.add_argument("--seed", type=int, default=None, help="Mode A: world seed (default from edip_synth Config)")
    ap.add_argument("--backfill-interval-min", type=int, default=60)
    ap.add_argument("--keep-world", help="Mode A: write the stand in world here instead of a temp dir")
    ap.add_argument("--json", help="write the JSON summary and per draft results here")
    ap.add_argument("--report", help="write the text report here")
    args = ap.parse_args(argv)
    text, out = run(args)
    print(text)
    return 0 if out["summary"].get("overall", {}).get("missed_critical_total", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
