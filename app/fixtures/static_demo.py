#!/usr/bin/env python3
"""Data for the static demo build: copies the mock mode fixtures and writes templates.json, the answer of the
server's /api/templates (so the browser needs no YAML parser).

    .venv/bin/python app/fixtures/static_demo.py [--fixtures app/fixtures/data] [--out app/client/dist/demo/data]

Run after `npm run build:demo` in app/client (the build empties its output folder).
"""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ("encounters.json", "drafts.jsonl.gz", "meta.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixtures", default=str(ROOT / "app/fixtures/data"))
    ap.add_argument("--out", default=str(ROOT / "app/client/dist/demo/data"))
    a = ap.parse_args()
    src, out = Path(a.fixtures), Path(a.out)
    missing = [f for f in FILES if not (src / f).exists()]
    if missing:
        raise SystemExit(f"missing {', '.join(missing)} in {src}: run app/fixtures/build.py first")
    out.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        shutil.copyfile(src / f, out / f)
    from parity import load_app  # the same mock mode app the parity test drives

    client, _ = load_app(src)
    (out / "templates.json").write_text(json.dumps(client.get("/api/templates").json()))
    print(f"demo data in {out}: {', '.join(sorted(p.name for p in out.iterdir()))}")


if __name__ == "__main__":
    main()
