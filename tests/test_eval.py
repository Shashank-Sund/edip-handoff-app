"""Evaluation set (ai/eval) on a small stand in world: Mode A end to end with MockLLM.

Every deterministic element the templates show must equal the truth at each generation point, no safety item
may be missed, the scorer must catch injected errors, and the job and the eval must share one definition of
the generation points.
"""
import ast
import copy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "ai"), str(ROOT / "synthetic"), str(ROOT / "pipelines")]

from edip_ai import points  # noqa: E402
from eval import expected as X  # noqa: E402
from eval import run_eval  # noqa: E402
from eval import score as S  # noqa: E402

LIMIT = 25      # encounters (enough that the sample includes critical results); the module runs in under 30 s


@pytest.fixture(scope="module")
def evaluated(tmp_path_factory):
    world = tmp_path_factory.mktemp("world")
    truth = X.Truth(run_eval.build_world(world))
    rows, info = run_eval.local_rows(world, limit=LIMIT)
    results, skipped = run_eval.evaluate(rows, truth)
    assert not skipped
    expected = {(r["csn"], r["as_of"]): X.expected(truth, r["csn"], r["as_of"]) for r in rows}
    return {"truth": truth, "rows": rows, "results": results, "info": info, "expected": expected,
            "lexicon": truth.world_lexicon()}


def test_mode_a_covers_every_template_and_several_points(evaluated):
    res = evaluated["results"]
    assert len({r["csn"] for r in res}) == LIMIT
    assert {r["template"] for r in res} == {"sbar_nurse", "ipass_physician", "custom_sea58"}
    assert evaluated["info"]["points"] > LIMIT          # starts plus finalizes at least
    assert {r["as_of"] for r in res} == {r["as_of"] for r in evaluated["rows"]}


def test_deterministic_elements_equal_the_truth(evaluated):
    bad = [(r["csn"], r["template"], r["as_of"], r["reasons"]) for r in evaluated["results"]
           if not all(r["deterministic"].values())]
    assert not bad, bad[:5]
    # The data must actually be exercised: for each fact some point in the sample has a non empty expectation.
    exp = evaluated["expected"].values()
    assert any(e["allergies"] for e in exp)
    assert any(e["code_status"] for e in exp) and any(not e["code_status"] for e in exp)
    assert any(e["isolation"] for e in exp)
    assert any(e["vitals"] for e in exp)
    assert any(e["critical"] for e in exp)
    assert any(e["pending"]["preliminary"] for e in exp)
    assert any(e["ed_meds"] for e in exp)
    assert any(X.visible_location(e["destination"])[2] for e in exp)     # a bed, pending or occupied


def test_no_missed_critical_items(evaluated):
    missed = [(r["csn"], r["template"], r["as_of"], r["missed_critical"]) for r in evaluated["results"]
              if r["missed_critical"]["total"]]
    assert not missed, missed[:5]


def test_narrative_fully_cited_with_mock(evaluated):
    for r in evaluated["results"]:
        n = r["narrative"]
        assert n["unknown_sources"] == 0 and not n["hallucinated_meds"] and not n["hallucinated_labs"], r["reasons"]
        assert not n["contradictions"], (r["csn"], n["contradictions"])
        assert n["support_share"] == 1.0


def _pick(evaluated, want):
    for row, r in zip(evaluated["rows"], evaluated["results"]):
        e = evaluated["expected"][(row["csn"], row["as_of"])]
        if row["template"] == "sbar_nurse" and want(e, S.elements(row["draft"])):
            return row, e
    pytest.skip("no suitable draft in the sample")


def _rescore(evaluated, row, draft, e):
    return S.score_draft(draft, e, evaluated["truth"], evaluated["lexicon"], row["validation"])


def test_scorer_catches_a_dropped_allergy(evaluated):
    row, e = _pick(evaluated, lambda e, els: e["allergies"] and els["E09"].get("data"))
    draft = copy.deepcopy(row["draft"])
    S.elements(draft)["E09"]["data"].pop(0)
    r = _rescore(evaluated, row, draft, e)
    assert r["deterministic"]["allergies"] is False
    assert r["missed_critical"]["allergies"] == 1
    assert any(x.startswith("E09") for x in r["reasons"])


def test_scorer_catches_a_changed_lab_value(evaluated):
    row, e = _pick(evaluated, lambda e, els: els["E15"].get("data"))
    draft = copy.deepcopy(row["draft"])
    S.elements(draft)["E15"]["data"][0]["value"] = "99.9"
    r = _rescore(evaluated, row, draft, e)
    assert r["deterministic"]["critical_labs"] is False
    assert any("labs not in truth" in x for x in r["reasons"])
    # The same error written into the narrative is a lexical contradiction.
    name = next(iter(evaluated["truth"].lab_values(row["csn"])))
    draft2 = copy.deepcopy(row["draft"])
    S.elements(draft2)["E11"]["narrative"]["text"] = f"Repeat {name} 987.5 noted."
    r2 = _rescore(evaluated, row, draft2, e)
    assert r2["narrative"]["contradictions"], r2["narrative"]


def test_scorer_catches_an_invented_drug(evaluated):
    row, e = _pick(evaluated, lambda e, els: "warfarin" not in evaluated["truth"].drug_names(e["csn"]))
    draft = copy.deepcopy(row["draft"])
    n = S.elements(draft)["E11"]["narrative"]
    n["text"] = (n.get("text") or "") + " Started warfarin 5 mg."
    r = _rescore(evaluated, row, draft, e)
    assert r["narrative"]["hallucinated_meds"] == ["warfarin"]
    assert any("hallucinated_meds" in x for x in r["reasons"])


def test_scorer_catches_an_unknown_source(evaluated):
    row, e = _pick(evaluated, lambda e, els: True)
    draft = copy.deepcopy(row["draft"])
    n = S.elements(draft)["E11"]["narrative"]
    n["sources"] = list(n.get("sources") or []) + ["obs:999999999:K:F", "bogus:1"]
    r = _rescore(evaluated, row, draft, e)
    assert r["narrative"]["unknown_sources"] >= 2
    assert any("unknown sources" in x for x in r["reasons"])


def test_report_has_no_dashes_and_lists_metrics(evaluated):
    text = run_eval.report(S.aggregate(evaluated["results"]), {"mode": "test", "world": "tmp"})
    assert chr(0x2014) not in text and chr(0x2013) not in text
    assert "deterministic accuracy" in text and "Template sbar_nurse" in text


def test_job_and_eval_share_the_generation_points():
    tree = ast.parse((ROOT / "jobs" / "handoff_job.py").read_text(encoding="utf-8"))
    imported = {(n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert ("edip_ai.points", "select_points") in imported
    src = (ROOT / "jobs" / "handoff_job.py").read_text(encoding="utf-8")
    assert "select_points(triggers, a.backfill_interval_min)" in src
    assert "gap = timedelta" not in src          # the inline loop is gone
    assert run_eval.select_points is points.select_points


def test_select_points_semantics():
    t0 = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    m = lambda n: t0 + timedelta(minutes=n)  # noqa: E731
    trig = [{"csn": "A", "action": "start", "kind": "pending_admit", "effective_ts": m(0)},
            {"csn": "A", "action": "regenerate", "kind": "bed_assigned", "effective_ts": m(10)},     # inside the gap
            {"csn": "A", "action": "regenerate", "kind": "critical_result", "effective_ts": m(70)},
            {"csn": "A", "action": "regenerate", "kind": "note_signed", "effective_ts": m(80)},      # inside the gap
            {"csn": "A", "action": "finalize", "kind": "transfer", "effective_ts": m(90)},
            {"csn": "B", "action": "regenerate", "kind": "bed_assigned", "effective_ts": m(5)},      # no start
            {"csn": "C", "action": "start", "kind": "pending_admit", "effective_ts": m(0)},
            {"csn": "C", "action": "retract", "kind": "cancel_admit", "effective_ts": m(30)}]
    pts, retracted = points.select_points(trig, 60)
    assert pts == [("A", m(0), "pending_admit", "draft"), ("A", m(70), "critical_result", "draft"), ("A", m(90), "transfer", "final")]
    assert retracted == {"C"}


def test_after_transfer_only_ed_addenda_regenerate():
    t0 = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    m = lambda n: t0 + timedelta(minutes=n)  # noqa: E731
    trig = [{"csn": "A", "action": "start", "kind": "pending_admit", "effective_ts": m(0)},
            {"csn": "A", "action": "finalize", "kind": "transfer", "effective_ts": m(90)},
            {"csn": "A", "action": "regenerate", "kind": "note_signed", "reason": "signed H&P", "effective_ts": m(100)},
            {"csn": "A", "action": "regenerate", "kind": "note_signed", "reason": points.ED_NOTE, "effective_ts": m(110)},
            {"csn": "A", "action": "regenerate", "kind": "critical_result", "effective_ts": m(115), "observed_ts": m(60)},
            {"csn": "A", "action": "regenerate", "kind": "critical_result", "effective_ts": m(200), "observed_ts": m(150)},
            {"csn": "A", "action": "regenerate", "kind": "class_change", "effective_ts": m(220)},
            {"csn": "A", "action": "finalize", "kind": "transfer", "effective_ts": m(300)}]   # unit to unit
    pts, _ = points.select_points(trig, 60)
    assert pts == [("A", m(0), "pending_admit", "draft"), ("A", m(90), "transfer", "final"),
                   ("A", m(110), "note_signed", "addendum"), ("A", m(115), "critical_result", "addendum")]
    late = [{"csn": "A", "kind": "critical_result", "effective_ts": m(115)}]
    obs = [{"csn": "A", "flag": "HH", "resulted_ts": m(115), "observed_ts": m(60)}]
    assert points.collected_times(late, obs)[0]["observed_ts"] == m(60)


def test_reference_severity_rule():
    base = {"critical_unaddressed": [], "critical": [], "destination": {"level_of_care": "MEDSURG"}, "vitals": None,
            "esi": 3, "pending": {"preliminary": []}}
    assert X.reference_severity(base)[0] == "stable"
    assert X.reference_severity({**base, "esi": 2})[0] == "watcher"
    assert X.reference_severity({**base, "vitals": {"hr": 118}})[0] == "watcher"
    assert X.reference_severity({**base, "destination": {"level_of_care": "ICU"}})[0] == "unstable"
    assert X.reference_severity({**base, "vitals": {"sbp": 82}})[0] == "unstable"
    crit = {"name": "Potassium", "value": "6.8"}
    assert X.reference_severity({**base, "critical": [crit], "critical_unaddressed": [crit]})[0] == "unstable"
    assert X.reference_severity({**base, "critical": [crit]})[0] == "watcher"     # addressed by a later normal value
