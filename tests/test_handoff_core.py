"""Unit tests for the handoff generator core: templates, validator, rules."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ai"))

from edip_ai import chart, rules, templates, validate  # noqa: E402

NOW = datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc)


def test_templates_reference_known_elements_with_the_right_kind():
    el = templates.elements()
    for tid, t in templates.templates().items():
        used = {e for s in t["sections"] for e in s["elements"]}
        assert used <= set(el), (tid, used - set(el))
        assert {r["element"] for r in t["rules"]} <= used, tid
        for e in t["narrative"]:
            assert el[e]["kind"] == "narrative", (tid, e)
        for r in t["rules"]:
            assert r["level"] in ("hard", "stale", "soft"), (tid, r)
    # SEA 58 minimum content is covered by the custom template.
    custom = {e for s in templates.templates()["custom_sea58"]["sections"] for e in s["elements"]}
    assert {"E02", "E07", "E11", "E24", "E25", "E09", "E08", "E18", "E15", "E14"} <= custom


def _packet(**over):
    state = {"csn": "1", "patient_name": "Doe, Pat", "mrn": "40000001", "birth_date": "1950-01-01", "sex": "F",
             "chief_complaint": "Fever", "esi": 2, "status": "boarding", "unit": "ED", "room": "07", "bed": "A",
             "pending_unit": "5N", "service": "Hospital Medicine", "arrival_ts": NOW - timedelta(hours=5),
             "admit_decision_ts": NOW - timedelta(hours=2)}
    state.update(over.pop("state", {}))
    vit_t = over.pop("vitals_at", NOW - timedelta(minutes=20))
    obs = [{"category": "vital", "code": c, "numeric": v, "value": str(v), "observed_ts": vit_t, "resulted_ts": vit_t,
            "validated": True} for c, v in (("hr", 112), ("sbp", 96), ("pain", 4))]
    obs += [{"category": "lab", "order_id": "700", "code": "LACT", "name": "Lactate", "value": "4.6", "numeric": 4.6,
             "units": "mmol/L", "flag": "HH", "status": "F", "observed_ts": NOW - timedelta(hours=3),
             "resulted_ts": NOW - timedelta(hours=3), "comment": "Critical value called"}]
    flags = over.pop("flags", [{"kind": "code_status", "value": "Full Code", "recorded_ts": NOW - timedelta(hours=1)}])
    return chart.build_packet(state, obs, [], [], over.pop("allergies", []), flags, now=NOW)


def test_packet_sources_cover_every_fact():
    p = _packet()
    assert p["vitals"]["latest"]["hr"] == 112 and p["vitals"]["latest_age_min"] == 20
    assert p["labs"][0]["src"] in p["_sources"] and p["code_status"]["value"] == "Full Code"
    assert p["destination"]["minutes_boarding"] == 120


def test_validator_removes_unknown_sources_and_flags_numbers():
    p = _packet()
    lab = p["labs"][0]["src"]
    raw = "Here you go:\n```json\n" + json.dumps({
        "E07": {"value": "critical", "text": "Lactate 4.6 and HR 140", "sources": [lab, "obs:999:X:F"], "confidence": 3},
        "E25": {"text": "If SBP under 90 give 500 mL, recheck q2h", "sources": [p["vitals"]["latest"]["src"]], "confidence": 0.5},
        "E11": {"text": "Sepsis bundle " + chr(0x2014) + " cefTRIAXone given", "sources": [], "confidence": 0.4},
    }) + "\n```"
    out, report = validate.check(raw, p, ["E07", "E25", "E11", "E06"])
    assert out["E07"]["sources"] == [lab] and report["unknown_sources"] == 1
    assert out["E07"]["value"] == "watcher" and out["E07"]["confidence"] == 1.0 and report["issues"]
    assert "140" in out["E07"]["unsupported_numbers"] and "4.6" not in out["E07"]["unsupported_numbers"]
    assert out["E25"]["unsupported_numbers"] == []          # thresholds and schedules are advice, not facts
    assert chr(0x2014) not in out["E11"]["text"] and report["uncited_statements"] == 1
    assert out["E06"]["text"] == "" if "text" in out["E06"] else True


def test_validator_survives_truncated_output():
    out, report = validate.check('{"E07": {"value": "stable", "text": "ok"', _packet(), ["E07"])
    assert "parse_error" in report and out["E07"]["confidence"] == 0.0


def test_rules_attest_missing_and_stale():
    el = templates.elements()
    t = templates.templates()["sbar_nurse"]
    narrative = {"E07": {"value": "unstable", "text": "x", "sources": ["enc:visit"]}}
    res = {r["rule"]: r for r in rules.evaluate(t, _packet(flags=[]), narrative, el)}
    assert res["N05"]["status"] == "attest" and "Full code" in res["N05"]["attest_options"][0]      # no code status
    assert res["N08"]["status"] == "stale"                                    # 20 min old, unstable limit 15
    assert res["N02"]["status"] == "deferred"                                 # sender identity is entered at sign
    res = {r["rule"]: r for r in rules.evaluate(t, _packet(), {"E07": {"value": "stable"}}, el)}
    assert res["N08"]["status"] == "pass" and res["N05"]["status"] == "pass"
    s = rules.score(list(res.values()))
    assert 0 <= s["score"] <= 1 and "N08" not in s["blocking"]
