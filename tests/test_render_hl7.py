"""The raw HL7 v2 rendering reconciles with the truth world and every feed fault is
rendered. Builds a world from the stand in population, writes it, renders it."""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "synthetic"))

from edip_synth import synthea  # noqa: E402
from edip_synth.config import Config  # noqa: E402
from edip_synth.output import write  # noqa: E402
from edip_synth.render import hl7  # noqa: E402
from edip_synth.render.common import load_truth  # noqa: E402
from edip_synth.world import Simulator  # noqa: E402

INJECTED = {"F01", "F03", "F04", "F08"}      # extra ADT messages that have no truth event


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    cfg = Config()
    world = Simulator(cfg, synthea.stand_in(3000, cfg.seed)).run()
    out = tmp_path_factory.mktemp("world")
    write(world, out)
    summary = hl7.render(out / "truth", out)
    root = out / "hl7"
    index = [json.loads(l) for l in open(root / "messages.jsonl", encoding="utf-8")]
    manifest = [json.loads(l) for l in open(root / "fault_manifest.jsonl", encoding="utf-8")]
    return {"root": root, "truth": load_truth(out / "truth"), "index": index, "manifest": manifest, "summary": summary, "out": out}


def read(root, rel):
    text = (root / rel).read_bytes().decode("utf-8")
    assert text.endswith("\r") and "\n" not in text
    return [s.split("|") for s in text[:-1].split("\r")]


@pytest.fixture(scope="module")
def parsed(rendered):
    return {r["file"]: read(rendered["root"], r["file"]) for r in rendered["index"]}


def test_every_file_parses_and_matches_the_site_profile(rendered, parsed):
    profile = rendered["truth"]["config"]["site_profile"]
    files = {p.relative_to(rendered["root"]).as_posix() for p in rendered["root"].rglob("*.hl7")}
    assert files == {r["file"] for r in rendered["index"]} and len(files) == len(rendered["index"])
    for r in rendered["index"]:
        segs = parsed[r["file"]]
        msh = segs[0]
        assert msh[0] == "MSH" and msh[1] == "^~\\&"
        assert msh[3] == r["facility"] and msh[9] == r["msh10"] and msh[10] == "T" and msh[17] == "UNICODE UTF-8"
        prof = profile[r["facility"]]
        assert msh[11] == prof["hl7_version"]
        parts = msh[8].split("^")
        assert parts[:2] == [r["type"], r["trigger"]] and len(parts) == (2 if prof["hl7_version"] == "2.3" else 3)
        assert prof["msh7_offset"] == (msh[6][-5] in "+-")
        for s in segs[1:]:
            assert len(s[0]) == 3 and s[0].isalnum() and s[0].isupper(), (r["file"], s[0])
        text = "\r".join("|".join(s) for s in segs)
        assert chr(0x2014) not in text and chr(0x2013) not in text
        if prof["z_segments"] and r["type"] == "ADT" and r["trigger"] in ("A04", "A06", "A14") and not r["faults"]:
            assert any(s[0] == "ZPV" for s in segs)
        if not prof["z_segments"]:
            assert not any(s[0].startswith("Z") for s in segs)


def test_delivery_order_is_monotonic_and_lexical(rendered):
    files = [r["file"].split("/")[-1] for r in rendered["index"]]
    assert files == sorted(files)
    stamps = [r["delivery_ts"] for r in rendered["index"]]
    assert stamps == sorted(stamps)
    counters = defaultdict(list)
    for r in rendered["index"]:
        if not set(r["faults"]) & {"F02", "F03"}:
            counters[(r["facility"], r["interface"])].append(int(r["msh10"]))
    for key, ids in counters.items():
        assert len(ids) == len(set(ids)), key


def test_adt_reconciles_with_truth_after_dedupe(rendered, parsed):
    truth = rendered["truth"]
    profile = truth["config"]["site_profile"]
    seen, refs = set(), []
    for r in rendered["index"]:
        if r["type"] != "ADT" or set(r["faults"]) & INJECTED:
            continue
        msh = parsed[r["file"]][0]
        key = (msh[2], msh[3], msh[9])
        if key in seen:
            assert "F02" in r["faults"]
            continue
        seen.add(key)
        refs.append((r["csn"], r["ref"], r["trigger"]))
    assert len(refs) == len({(c, e) for c, e, _ in refs}) == len(truth["adt"])
    assert {(c, e) for c, e, _ in refs} == {(a["csn"], a["event_id"]) for a in truth["adt"]}

    def trig(a):
        prof = profile[a["facility"]]
        k = a["kind"]
        if k == "pending_admit":
            return "A14" if prof["a14"] else "A08"
        if k == "class_change":
            return prof["class_change"]
        if k in ("bed_assigned", "bed_unassigned"):
            return {"bed_assigned": "A15", "bed_unassigned": "A26"}[k] if prof["a14"] else "A08"
        return hl7.TRIGGER[k]
    assert Counter((c, t) for c, _, t in refs) == Counter((a["csn"], trig(a)) for a in truth["adt"])
    retrans = sum(1 for r in rendered["index"] if "F02" in r["faults"])
    assert retrans > 0


def test_adt_content(rendered, parsed):
    truth = rendered["truth"]
    encs = {e["csn"]: e for e in truth["encounters"]}
    pats = {p["pat_id"]: p for p in truth["patients"]}
    by_event = {r["ref"]: r for r in rendered["index"] if r["type"] == "ADT" and r["ref"] and "F02" not in r["faults"]}
    for a in truth["adt"]:
        segs = parsed[by_event[a["event_id"]]["file"]]
        by = defaultdict(list)
        for s in segs:
            by[s[0]].append(s)
        pid, pv1, evn = by["PID"][0], by["PV1"][0], by["EVN"][0]
        pat = pats[a["pat_id"]]
        assert pid[3].startswith(f"{pat['mrn']}^^^MRN^MR~{pat['epi']}^^^EPI^EPI")
        assert pv1[19] == f"{a['csn']}^^^EPI^CSN"
        assert pv1[3].split("^")[:3] == [a["unit"], a["room"], a["bed"]]
        if a["kind"] == "transfer":
            assert pv1[6].split("^")[:3] == [a["prior"]["unit"], a["prior"]["room"], a["prior"]["bed"]]
        if a["kind"] == "merge":
            assert by["MRG"] and by["MRG"][0][1].startswith(pats[a["details"]["retired"]]["mrn"])
        if a["kind"] == "discharge":
            assert pv1[36] == a["details"]["discharge_disposition_code"] and pv1[45]
        assert (evn[2] != evn[6]) == (a["recorded_at"] != a["effective_at"])
    # Before a merge every stream uses the retired MRN.
    for e in truth["encounters"]:
        if not e["registered_pat_id"]:
            continue
        merge_at = pats[e["registered_pat_id"]]["merged_at"]
        before = [r for r in rendered["index"] if r["csn"] == e["csn"] and r["trigger"] != "A40" and "F08" not in r["faults"]
                  and any(a["event_id"] == r["ref"] and a["recorded_at"] < merge_at for a in truth["adt"])]
        assert before
        for r in before:
            pid = [s for s in parsed[r["file"]] if s[0] == "PID"][0]
            assert pid[3].startswith(pats[e["registered_pat_id"]]["mrn"])


def test_every_result_has_an_oru_obx(rendered, parsed):
    truth = rendered["truth"]
    seen = set()
    for r in rendered["index"]:
        if r["type"] != "ORU" or r["ref"].startswith("vitals") or set(r["faults"]) & {"F28", "F02"}:
            continue
        for s in parsed[r["file"]]:
            if s[0] == "OBX":
                seen.add((r["ref"], s[3].split("^")[0], s[11], s[5]))
    for x in truth["results"]:
        lab = "" if x["loinc"] and x["lab"] in ("CXR", "CTH", "CTA", "CTAP", "CTPE", "XRHIP", "ECG", "RUQUS") else x["lab"]
        assert (x["order_id"], lab or "&IMP", x["status"], hl7.esc(x["value"])) in seen, x
    mixed = [r for r in rendered["index"] if "F28" in r["faults"]]
    assert mixed
    statuses = {s[11] for s in parsed[mixed[0]["file"]] if s[0] == "OBX"}
    assert statuses == {"F", "P", "I"}


def test_vitals_oru_follow_the_flowsheet_knob(rendered):
    truth = rendered["truth"]
    profile = truth["config"]["site_profile"]
    encs = {e["csn"]: e for e in truth["encounters"]}
    device = {(v["csn"], v["line"]) for v in truth["vitals"] if not v["validated"]}
    assert device == {tuple(x) for x in truth["render_plan"]["F24_unvalidated_device_values"]}
    rows = [r for r in rendered["index"] if r["ref"].startswith("vitals") and "F02" not in r["faults"]]
    refs = {r["ref"]: r for r in rows}
    for v in truth["vitals"]:
        key = f"vitals:{v['csn']}:{v['line']}"
        expect = profile[encs[v["csn"]]["facility"]]["flowsheet_oru"] or (v["csn"], v["line"]) in device
        assert (key in refs) == expect, key
    assert sum(1 for r in rows if "F24" in r["faults"]) == len(device)


def test_every_mar_row_has_a_ras_and_every_order_an_rde(rendered):
    truth = rendered["truth"]
    ras = {r["ref"] for r in rendered["index"] if r["type"] == "RAS"}
    assert ras == {f"{m['order_med_id']}:{m['line']}" for m in truth["mar"]}
    rde = {r["ref"] for r in rendered["index"] if r["type"] == "RDE"}
    assert rde == {m["order_med_id"] for m in truth["med_orders"]}


def test_every_signed_note_has_an_authenticated_mdm(rendered, parsed):
    truth = rendered["truth"]
    status = {}
    for r in rendered["index"]:
        if r["type"] != "MDM":
            continue
        txa = [s for s in parsed[r["file"]] if s[0] == "TXA"][0]
        status.setdefault(txa[12].split("^")[0], set()).add(txa[17])
    for n in truth["notes"]:
        assert "IP" in status[n["note_id"]]
        assert ("AU" in status[n["note_id"]]) == bool(n["signed_at"]), n["note_id"]
    big = max(len((rendered["root"] / r["file"]).read_bytes()) for r in rendered["index"] if "F27" in r["faults"])
    assert 55_000 < big < 70_000
    quirk = [r for r in rendered["index"] if "F27" in r["faults"]][0]
    text = (rendered["root"] / quirk["file"]).read_text(encoding="utf-8")
    assert "\\T\\" in text and "\\E\\" in text
    assert any("Nuñez" in (rendered["root"] / r["file"]).read_text(encoding="utf-8") for r in rendered["index"] if r["type"] == "ADT")


def test_every_manifest_fault_has_a_rendered_message(rendered):
    files = {r["file"]: r for r in rendered["index"]}
    ids = {m["fault_id"] for m in rendered["manifest"]}
    assert {"F01", "F02", "F03", "F04", "F05", "F06", "F08", "F13", "F24", "F25", "F26", "F27", "F28", "F30"} <= ids
    for m in rendered["manifest"]:
        assert m["level"] == "render" and m["stream"] == "hl7"
        if m["csn"] == "":
            continue                                   # site wide rows (offset style, Z segments, class strings)
        assert m["file"] in files, m
        assert m["fault_id"] in files[m["file"]]["faults"], m
        assert files[m["file"]]["msh10"] == m["msh10"]
    plan = rendered["truth"]["render_plan"]
    flood = Counter(m["csn"] for m in rendered["manifest"] if m["fault_id"] == "F01")
    assert set(flood) <= set(plan["F01_a08_flood"]) and all(8 <= n <= 12 for n in flood.values())
    assert sum(1 for m in rendered["manifest"] if m["fault_id"] == "F04") == len(plan["F04_empty_a08"])


def test_fault_mechanics(rendered, parsed):
    index = rendered["index"]
    by_file = {r["file"]: r for r in index}
    # F02: same MSH-10, byte identical content. F03: new MSH-10 and MSH-7, same body.
    originals = defaultdict(list)
    for r in index:
        originals[(r["facility"], r["interface"], r["msh10"])].append(r)
    for key, rows in originals.items():
        if len(rows) > 1:
            assert all("F02" in r["faults"] for r in rows[1:])
            texts = {(rendered["root"] / r["file"]).read_bytes() for r in rows}
            assert len(texts) == 1
    for r in index:
        if "F03" in r["faults"]:
            body = [s for s in parsed[r["file"]][1:]]
            twin = [o for o in index if o["csn"] == r["csn"] and o["type"] == "ADT" and o["msh10"] != r["msh10"]
                    and parsed[o["file"]][1:] == body]
            assert twin
    # F04: blank PV1. F05: the A02 lands before its class change. F06: an ORU lands before the A04.
    for r in index:
        if "F04" in r["faults"]:
            assert [s[0] for s in parsed[r["file"]]] == ["MSH", "EVN", "PID", "PV1"] and parsed[r["file"]][3] == ["PV1", "1"]
        if "F05" in r["faults"]:
            a02 = [o for o in index if o["csn"] == r["csn"] and o["trigger"] == "A02"]
            assert a02 and a02[-1]["delivery_ts"] < r["delivery_ts"] and a02[-1]["msh7"] > r["msh7"]
        if "F06" in r["faults"]:
            # The ADT outage holds every ADT message until after the first lab (BEAKER) ORU; any earlier
            # ORU (flowsheet vitals) therefore precedes the A04 as well.
            orus = [o for o in index if o["csn"] == r["csn"] and o["type"] == "ORU"]
            first_lab = min(o["delivery_ts"] for o in orus if parsed[o["file"]][0][2] == "BEAKER")
            assert first_lab < r["delivery_ts"] and min(o["delivery_ts"] for o in orus) < r["delivery_ts"]
            assert all(a["delivery_ts"] > first_lab for a in index if a["csn"] == r["csn"] and a["type"] == "ADT"
                       and a["ref"] and a["msh7"] <= r["msh7"])
        if "F08" in r["faults"]:
            pv1 = [s for s in parsed[r["file"]] if s[0] == "PV1"][0]
            a02 = [o for o in index if o["csn"] == r["csn"] and o["trigger"] == "A02"][-1]
            assert pv1[3].startswith("ED^") and a02["delivery_ts"] < r["delivery_ts"]
        if "F25" in r["faults"]:
            msh, evn = parsed[r["file"]][0], parsed[r["file"]][1]
            assert msh[6].endswith("-0500") and evn[2].endswith("-0400") and msh[6][:14] == evn[2][:14]
        if "F26" in r["faults"] and r["csn"]:
            assert parsed[r["file"]][-1][0] == "ZZZ"
    assert any("F08" in r["faults"] for r in index) and any("F05" in r["faults"] for r in index)
    assert len(by_file) == len(index)


def test_render_is_deterministic(rendered):
    again = rendered["out"].parent / "again"
    hl7.render(rendered["out"] / "truth", again)
    assert (again / "hl7" / "messages.jsonl").read_bytes() == (rendered["root"] / "messages.jsonl").read_bytes()
    assert (again / "hl7" / "fault_manifest.jsonl").read_bytes() == (rendered["root"] / "fault_manifest.jsonl").read_bytes()
