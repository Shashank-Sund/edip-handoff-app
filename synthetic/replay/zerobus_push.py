#!/usr/bin/env python3
"""Push Redox style FHIR events into a Delta table through Zerobus (REST ingest).

  python synthetic/replay/zerobus_push.py --events synthetic/out/world/fhir/events.jsonl \\
      --table edip.bronze.redox_fhir_events [--batch-mb 4] [--workers 6] [--limit N]

Credentials come from the environment (never the repo): DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET of
a service principal with USE CATALOG, USE SCHEMA and MODIFY plus SELECT on the table, WORKSPACE_URL,
WORKSPACE_ID and ZEROBUS_ENDPOINT. Load them with: set -a; . ~/.config/edip/zerobus.env; set +a

Default is the bulk backfill: events go in delivery order, in batches, several requests at a time. Zerobus
orders records only within a stream, so the pipeline sequences by event time, not arrival order.

Probe mode (--probe N --rate R) measures freshness: it re-sends the last N ADT events at R records per
second with a suffix on the message ids, so they land as new silver rows (with exact timing) while the
encounter state stays unchanged (same content).
Stdlib only.
"""
import argparse
import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone


def micros(s):
    if not s:
        return None
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp() * 1_000_000)


class Token:
    """OAuth token scoped to Zerobus writes on one table (authorization_details), refreshed before expiry."""

    def __init__(self, table):
        self.table, self.lock, self.value, self.expires = table, threading.Lock(), None, 0.0

    def get(self):
        with self.lock:
            if time.time() < self.expires - 120:
                return self.value
            cat, schema, _ = self.table.split(".")
            details = [
                {"type": "unity_catalog_privileges", "privileges": ["USE CATALOG"], "object_type": "CATALOG",
                 "object_full_path": cat},
                {"type": "unity_catalog_privileges", "privileges": ["USE SCHEMA"], "object_type": "SCHEMA",
                 "object_full_path": f"{cat}.{schema}"},
                {"type": "unity_catalog_privileges", "privileges": ["SELECT", "MODIFY"], "object_type": "TABLE",
                 "object_full_path": self.table, "operations": ["zerobuswrite"]}]
            body = urllib.parse.urlencode({
                "grant_type": "client_credentials", "scope": "all-apis",
                "resource": f"api://databricks/workspaces/{os.environ['WORKSPACE_ID']}/zerobusDirectWriteApi",
                "authorization_details": json.dumps(details)}).encode()
            req = urllib.request.Request(os.environ["WORKSPACE_URL"].rstrip("/") + "/oidc/v1/token", body)
            cred = f"{os.environ['DATABRICKS_CLIENT_ID']}:{os.environ['DATABRICKS_CLIENT_SECRET']}".encode()
            req.add_header("Authorization", "Basic " + base64.b64encode(cred).decode())
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r)
            self.value, self.expires = d["access_token"], time.time() + int(d.get("expires_in", 3600))
            return self.value


def record(e):
    """One bronze row; VARIANT columns take a JSON string, timestamps are epoch microseconds."""
    return {"event_id": e["event_id"], "attempt_id": e["attempt_id"], "data_model": e["data_model"],
            "event_type": e["event_type"], "event_datetime": micros(e["event_datetime"]), "source_id": e["source_id"],
            "source_name": e["source_name"], "facility_code": e["facility_code"], "is_test": e["is_test"],
            "hl7_event_code": e.get("hl7_event_code"), "hl7_control_id": e.get("hl7_control_id"),
            "bundle": json.dumps(e["bundle"], ensure_ascii=False, separators=(",", ":")),
            "ingest_ts": int(time.time() * 1_000_000), "landing_path": None, "delivery_ts": micros(e["delivery_ts"])}


def batches(path, limit, max_bytes):
    batch, size, n = [], 0, 0
    for line in open(path, encoding="utf-8"):
        if limit and n >= limit:
            break
        r = record(json.loads(line))
        b = len(json.dumps(r, ensure_ascii=False))
        if batch and size + b > max_bytes:
            yield batch
            batch, size = [], 0
        batch.append(r)
        size += b
        n += 1
    if batch:
        yield batch


def send(url, token, batch):
    data = json.dumps(batch, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    for attempt in range(6):
        req = urllib.request.Request(url, data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", "Bearer " + token.get())
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, len(batch)
        except urllib.error.HTTPError as err:
            if err.code == 401:
                token.expires = 0
            if err.code in (401, 429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"HTTP {err.code}: {err.read()[:400]!r}") from None
        except urllib.error.URLError:
            if attempt < 5:
                time.sleep(2 ** attempt)
                continue
            raise


def probe(a, url, token):
    rows = [json.loads(line) for line in open(a.events, encoding="utf-8")]
    adt = [r for r in rows if r["data_model"] == "FHIR.Event.PatientAdmin"][-a.probe:]
    suffix = time.strftime("-probe%H%M%S")
    t0 = time.time()
    for i, e in enumerate(adt):
        e = {**e, "event_id": e["event_id"] + suffix, "attempt_id": e["attempt_id"] + suffix}
        e["bundle"] = {**e["bundle"], "Meta": {**e["bundle"]["Meta"],
                                               "Logs": [{"ID": e["event_id"], "AttemptID": e["attempt_id"]}]}}
        send(url, token, [record(e)])
        time.sleep(max(0.0, t0 + (i + 1) / a.rate - time.time()))
    print(f"probe: sent {len(adt)} events with suffix {suffix} in {time.time() - t0:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--table", required=True)
    ap.add_argument("--batch-mb", type=float, default=4.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--probe", type=int, default=0, help="freshness probe: re-send the last N ADT events")
    ap.add_argument("--rate", type=float, default=2.0, help="probe records per second")
    a = ap.parse_args()
    url = f"{os.environ['ZEROBUS_ENDPOINT'].rstrip('/')}/zerobus/v1/tables/{a.table}/insert"
    token = Token(a.table)
    token.get()
    if a.probe:
        return probe(a, url, token)
    t0, sent = time.time(), 0
    with ThreadPoolExecutor(a.workers) as pool:
        futures = [pool.submit(send, url, token, b) for b in batches(a.events, a.limit, int(a.batch_mb * 1e6))]
        for f in futures:
            _, n = f.result()
            sent += n
            if sent % 2000 < n:
                print(f"  {sent} records, {time.time() - t0:.0f}s", flush=True)
    print(f"sent {sent} records in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
