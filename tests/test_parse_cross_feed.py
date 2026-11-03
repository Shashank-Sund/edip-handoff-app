"""Both real time feeds must parse to the same encounter state, because silver merges them into one
AUTO CDC table keyed by CSN: the same unit, room, bed and class codes, and the same latest event."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "synthetic"), str(ROOT / "pipelines")]

from edip_synth import synthea  # noqa: E402
from edip_synth.config import Config  # noqa: E402
from edip_synth.output import write  # noqa: E402
from edip_synth.render import fhir, hl7  # noqa: E402
from edip_synth.world import Simulator  # noqa: E402
from parse_fhir import parse_fhir  # noqa: E402
from parse_hl7 import parse_hl7  # noqa: E402

FIELDS = ("patient_class", "unit", "room", "bed")


@pytest.fixture(scope="module")
def feeds(tmp_path_factory):
    out = tmp_path_factory.mktemp("world")
    cfg = Config()
    write(Simulator(cfg, synthea.stand_in(3000, cfg.seed)).run(), out)
    hl7.render(out / "truth", out)
    fhir.render(out / "truth", out)
    h = []
    for line in open(out / "hl7" / "messages.jsonl"):
        m = json.loads(line)
        h += parse_hl7((out / "hl7" / m["file"]).read_text(encoding="utf-8"))["adt"]
    f = []
    for line in open(out / "fhir" / "events.jsonl"):
        f += parse_fhir(json.loads(line))["adt"]
    return h, f


def _state(rows):
    """Latest located event per CSN by (effective, recorded), as AUTO CDC SCD1 would keep it."""
    seen, state = set(), {}
    for r in sorted(rows, key=lambda r: (r["effective_ts"], r["recorded_ts"])):
        if r["msg_key"] in seen or not r["csn"] or r["unit"] is None:
            continue
        seen.add(r["msg_key"])
        state[r["csn"]] = {k: r[k] or None for k in FIELDS}     # the pipeline treats "" as absent
    return state


def test_feeds_agree_on_final_encounter_state(feeds):
    h, f = (_state(x) for x in feeds)
    assert set(h) == set(f)
    diff = {c: (h[c], f[c]) for c in h if h[c] != f[c]}
    assert not diff, list(diff.items())[:5]


def test_feeds_share_the_kind_and_code_vocabulary(feeds):
    h, f = feeds
    for field in ("kind", "patient_class", "unit"):
        assert {r[field] for r in h if r[field]} == {r[field] for r in f if r[field]}, field
