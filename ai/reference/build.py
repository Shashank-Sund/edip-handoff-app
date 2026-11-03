"""Write ai/reference/drafts.jsonl.gz and meta.json from drafts exported with ai/eval/export_drafts.py.

    python ai/reference/build.py <export.jsonl>
"""
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "jobs"))
from handoff_job import templates_sha  # noqa: E402


def main(src):
    rows = sorted((json.loads(line) for line in open(src)), key=lambda r: (r["csn"], r["as_of"], r["template"]))
    out = ROOT / "ai" / "reference"
    with open(out / "drafts.jsonl.gz", "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=9) as g:
        for r in rows:
            g.write((json.dumps(r, separators=(",", ":")) + "\n").encode())
    meta = {"drafts": len(rows), "points": len({(r["csn"], r["as_of"]) for r in rows}),
            "encounters": len({r["csn"] for r in rows}), "models": sorted({r["model"] for r in rows}),
            "templates_sha": templates_sha(str(ROOT)), "population": "synthea (synthetic/population/synthea_3000.json.gz)"}
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    print(meta)


if __name__ == "__main__":
    main(sys.argv[1])
