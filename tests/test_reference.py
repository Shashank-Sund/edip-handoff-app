"""The reference handoffs in ai/reference/ that setup loads instead of calling the model.

They must match what a deploy generates (every generation point of the reference world, the current templates),
and the job must refuse them on any mismatch so a draft never describes a different chart.
"""
import gzip
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "jobs"), str(ROOT / "ai"), str(ROOT / "pipelines"), str(ROOT / "synthetic")]
import handoff_job as H  # noqa: E402

REF = ROOT / "ai" / "reference"


def _ref():
    return [json.loads(line) for line in gzip.open(REF / "drafts.jsonl.gz", "rt")]


def _points(rows):
    """Generation points (csn, as_of, trigger, status) as the job builds them, from the reference rows."""
    seen = {}
    for r in rows:
        t = datetime.strptime(r["as_of"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        seen[(r["csn"], r["as_of"])] = (r["csn"], t, r["trigger"], r["status"])
    return list(seen.values())


def test_reference_is_current_for_the_templates():
    meta = json.loads((REF / "meta.json").read_text())
    assert meta["templates_sha"] == H.templates_sha(str(ROOT)), (
        "ai/templates changed: refresh the reference drafts (ai/reference/README.md) or setup will call the model")
    assert meta["drafts"] == len(_ref()) == 3 * meta["points"]


def test_reference_matches_the_generation_points_of_the_reference_world(tmp_path):
    from edip_ai import local
    from edip_ai.points import select_points, triggers
    py, syn = sys.executable, ROOT / "synthetic"
    subprocess.run([py, "-m", "edip_synth", "--out", str(tmp_path), "--synthea", "population/synthea_3000.json.gz"],
                   cwd=syn, check=True, capture_output=True)
    for feed in ("fhir", "hl7"):
        subprocess.run([py, "-m", f"edip_synth.render.{feed}", "--truth", str(tmp_path / "truth"), "--out", str(tmp_path)],
                       cwd=syn, check=True, capture_output=True)
    points, _ = select_points(triggers(local.records(tmp_path, ("fhir", "hl7"))), 60)
    want = {(p[0], p[1].strftime("%Y-%m-%dT%H:%M:%SZ")) for p in points}
    assert want == {(r["csn"], r["as_of"]) for r in _ref()}


def test_matching_points_load_with_the_job_schema():
    rows = _ref()
    points = _points(rows)
    out = H.load_reference(str(ROOT), points, points)
    assert out is not None and len(out) == len(rows)
    r = out[0]
    assert isinstance(r["as_of"], datetime) and isinstance(r["blocking"], list) and isinstance(r["score"], float)
    assert json.loads(r["draft"])["template"] == r["template"]
    # Only the points still to generate are loaded (a rerun after a partial run).
    assert len(H.load_reference(str(ROOT), points, points[:5])) == 15


def test_any_mismatch_falls_back_to_the_model(monkeypatch):
    points = _points(_ref())
    assert H.load_reference(str(ROOT), points[1:], points[1:]) is None          # a point missing
    extra = points + [("40099999999", points[0][1], "pending_admit", "draft")]
    assert H.load_reference(str(ROOT), extra, extra) is None                     # a point the reference lacks
    monkeypatch.setattr(H, "templates_sha", lambda _: "other")
    assert H.load_reference(str(ROOT), points, points) is None                   # other templates


def test_setup_defaults_to_reference_and_redrafts_use_the_model():
    import yaml
    b = yaml.safe_load((ROOT / "databricks.yml").read_text())
    assert b["variables"]["drafts"]["default"] == "reference"
    setup = {t["task_key"]: t for t in b["resources"]["jobs"]["edip_setup"]["tasks"]}
    assert setup["handoffs"]["run_job_task"]["job_parameters"]["drafts"] == "${var.drafts}"
    params = {p["name"]: p["default"] for p in b["resources"]["jobs"]["handoff_generator"]["parameters"]}
    assert params["drafts"] == "model"
