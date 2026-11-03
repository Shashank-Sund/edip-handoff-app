"""Realism suite fixtures (spec section 12, items 6 to 25).

By default the world is generated with the built in stand in population, so CI
needs no Synthea output. Set EDIP_WORLD to a truth folder (the directory that
holds encounters.jsonl, or its parent) to run the same checks on a published
world, for example:

    EDIP_WORLD=synthetic/out/world/truth .venv/bin/python -m pytest -q tests/realism
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from realism_helpers import View  # noqa: E402
from edip_synth import synthea  # noqa: E402
from edip_synth.config import Config  # noqa: E402
from edip_synth.world import Simulator  # noqa: E402
from truth_io import load_truth  # noqa: E402


@pytest.fixture(scope="session")
def view() -> View:
    path = os.environ.get("EDIP_WORLD")
    if path:
        return View(load_truth(path), real=True)
    cfg = Config()
    return View(Simulator(cfg, synthea.stand_in(3000, cfg.seed)).run(), real=False)
