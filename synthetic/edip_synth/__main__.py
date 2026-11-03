"""python -m edip_synth --out DIR [--synthea DIR] [--seed N] [--encounters N]"""
import argparse
import time
from dataclasses import replace
from pathlib import Path

from . import synthea
from .config import Config
from .output import write
from .world import Simulator


def main():
    ap = argparse.ArgumentParser(description="Generate the synthetic ED to inpatient world (truth tables).")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--synthea", type=Path, help="Synthea CSV folder; omit to use the built in stand in population")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--encounters", type=int)
    a = ap.parse_args()
    cfg = Config()
    if a.seed is not None:
        cfg = replace(cfg, seed=a.seed)
    if a.encounters is not None:
        cfg = replace(cfg, encounters=a.encounters)
    t = time.time()
    pop = synthea.load(a.synthea) if a.synthea else synthea.stand_in(3000, cfg.seed)
    world = Simulator(cfg, pop).run()
    s = write(world, a.out)
    print(f"wrote {a.out} in {time.time() - t:.1f}s: {s['encounters']} encounters, {s['admitted']} admitted, "
          f"{s['counts']['adt']} ADT events, {s['counts']['results']} results")


if __name__ == "__main__":
    main()
