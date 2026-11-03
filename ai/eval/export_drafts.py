#!/usr/bin/env python3
"""Export gold.handoff_draft to JSONL for scoring with run_eval.py (Mode B).

  DATABRICKS_CONFIG_PROFILE=<profile> python ai/eval/export_drafts.py --warehouse <id> --out drafts.jsonl [--catalog edip]

Uses the SQL Statement Execution API with external links, so large results download in chunks.
"""
import argparse
import json
import time
import urllib.request

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import Disposition, Format, StatementState


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--catalog", default="edip")
    a = ap.parse_args()
    w = WorkspaceClient()
    cols = ["csn", "template", "version", "status", "trigger", "as_of", "model", "severity", "score", "blocking", "draft",
            "validation", "prompt_tokens", "completion_tokens", "seconds"]
    sql = (f"SELECT {', '.join(cols[:5])}, date_format(as_of, \"yyyy-MM-dd'T'HH:mm:ss'Z'\") AS as_of, "
           f"{', '.join(cols[6:])} FROM {a.catalog}.gold.handoff_draft ORDER BY csn, template, version")
    r = w.statement_execution.execute_statement(statement=sql, warehouse_id=a.warehouse, wait_timeout="50s",
                                                disposition=Disposition.EXTERNAL_LINKS, format=Format.JSON_ARRAY)
    while r.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(3)
        r = w.statement_execution.get_statement(r.statement_id)
    if r.status.state != StatementState.SUCCEEDED:
        raise SystemExit(f"{r.status.state}: {r.status.error.message if r.status.error else ''}")
    names = [c.name for c in r.manifest.schema.columns]
    n, chunk = 0, r.result
    with open(a.out, "w", encoding="utf-8") as f:
        while chunk is not None:
            for link in chunk.external_links or []:
                with urllib.request.urlopen(link.external_link, timeout=120) as resp:   # presigned, no auth header
                    for row in json.load(resp):
                        f.write(json.dumps(dict(zip(names, row)), ensure_ascii=False) + "\n")
                        n += 1
                nxt = link.next_chunk_index
                chunk = w.statement_execution.get_statement_result_chunk_n(r.statement_id, nxt) if nxt is not None else None
            if not chunk or not chunk.external_links:
                break
    print(f"exported {n} drafts to {a.out}")


if __name__ == "__main__":
    main()
