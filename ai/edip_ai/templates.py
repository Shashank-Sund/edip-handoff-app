"""Load the handoff templates (ai/templates/*.yaml)."""
from functools import lru_cache
from pathlib import Path

import yaml

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates"


@lru_cache(maxsize=None)
def elements(path: str = str(TEMPLATE_DIR)) -> dict:
    return yaml.safe_load((Path(path) / "elements.yaml").read_text())["elements"]


@lru_cache(maxsize=None)
def templates(path: str = str(TEMPLATE_DIR)) -> dict:
    out = {}
    for f in sorted(Path(path).glob("*.yaml")):
        if f.name == "elements.yaml":
            continue
        t = yaml.safe_load(f.read_text())
        out[t["id"]] = t
    return out


def narrative_elements(path: str = str(TEMPLATE_DIR)) -> list:
    """Union of narrative elements across templates: one model call serves every template."""
    out = []
    for t in templates(path).values():
        for e in t.get("narrative", []):
            if e not in out:
                out.append(e)
    return sorted(out)
