"""Realism suite fixtures (spec section 12, items 6 to 25).

By default the world is generated with the built in stand in population, so CI
needs no Synthea output. Set EDIP_WORLD to a truth folder (the directory that
holds encounters.jsonl, or its parent) to run the same checks on a published
world, for example:

    EDIP_WORLD=synthetic/out/world/truth .venv/bin/python -m pytest -q tests/realism
"""
import functools
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from realism_helpers import View  # noqa: E402
from edip_synth import synthea  # noqa: E402
from edip_synth.config import Config  # noqa: E402
from edip_synth.world import Simulator  # noqa: E402
from truth_io import load_truth  # noqa: E402

# Every check runs on several seeds so a lucky draw cannot hide a generator bias. EDIP_SEEDS overrides the
# list; EDIP_SYNTHEA points at a Synthea CSV folder to use the real population instead of the stand in.
SEEDS = [int(x) for x in os.environ.get("EDIP_SEEDS", f"{Config().seed},1,2,3,4").split(",")]
WORLD = os.environ.get("EDIP_WORLD")


@functools.lru_cache(maxsize=None)
def _population():
    return synthea.load(os.environ["EDIP_SYNTHEA"]) if os.environ.get("EDIP_SYNTHEA") else None


@functools.lru_cache(maxsize=None)
def _view(seed):
    if WORLD:
        return View(load_truth(WORLD), real=True)
    cfg = replace(Config(), seed=seed)
    pop = _population() or synthea.stand_in(3000, seed)
    return View(Simulator(cfg, pop).run(), real=_population() is not None)


@pytest.fixture(scope="session", params=[None] if WORLD else SEEDS, ids=lambda s: "world" if s is None else f"seed{s}")
def view(request) -> View:
    return _view(request.param)


@pytest.fixture(scope="session")
def pooled() -> list:
    """All seeds' worlds, for metrics whose single sample of 120 admits is too noisy to judge alone."""
    return [_view(None)] if WORLD else [_view(s) for s in SEEDS]
