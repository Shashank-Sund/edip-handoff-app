# FHIR events from Redox

Redox receives events from the EHR, converts them to FHIR R4 bundles, and posts each one as a notification to a destination you configure. This guide covers how those notifications can reach `<catalog>.bronze.redox_fhir_events`, and what the repo assumes about them.

## What bronze must look like

The table is created by `setup/load_bronze.py`:

| Column | Type | Source in the Redox payload | Used by the pipeline |
| --- | --- | --- | --- |
| `event_id` | STRING, not null | `Meta.Logs[0].ID` | Fallback message key |
| `attempt_id` | STRING | `Meta.Logs[0].AttemptID` | Troubleshooting only |
| `data_model` | STRING | `Meta.DataModel` (for example `FHIR.Event.PatientAdmin`) | Fallback |
| `event_type` | STRING | `Meta.EventType` (for example `patient-arrived`) | Fallback |
| `event_datetime` | TIMESTAMP | `Meta.EventDateTime` | Fallback recorded time |
| `source_id`, `source_name` | STRING | `Meta.Source` | No |
| `facility_code` | STRING | `Meta.FacilityCode` | Fallback |
| `is_test` | BOOLEAN | `Meta.Test` | No (filter on it in production) |
| `hl7_event_code` | STRING | `Meta.Extensions["event-code"]` (A04, A02 ...) | Fallback |
| `hl7_control_id` | STRING | `Meta.Extensions["message-control-id"]` | No |
| `bundle` | VARIANT, not null | The whole payload: the FHIR Bundle plus its top level `Meta` | Yes |
| `ingest_ts` | TIMESTAMP | Set by the writer | Yes, as `landed_ts` |
| `landing_path` | STRING | File path, when the event came from a file | No |
| `delivery_ts` | TIMESTAMP | When Redox delivered it | No |

The parser (`pipelines/parse_fhir.py`) reads `Meta` from inside `bundle` first and falls back to the columns. So the one hard requirement is: **store the complete Redox payload, with `Meta`, in `bundle`.** The other columns are promoted for filtering and auditing.

**Why VARIANT.** Bundles differ by data model and by connection, and Redox adds fields over time. A VARIANT column stores each payload as sent, without a schema migration when a new field appears, and can still be queried with path expressions (`bundle:Meta.EventType`). The pipeline converts it back to JSON text for the parser.

## Landing options

### A. Redox destination, a small relay, Zerobus REST (what the repo models)

```
Redox destination (HTTPS POST) -> relay -> Zerobus REST insert -> bronze.redox_fhir_events
```

Zerobus writes rows straight into a Unity Catalog Delta table, without a message broker. Its REST interface takes JSON rows that match the table schema, authenticated with a short lived OAuth token for a service principal that has `USE CATALOG`, `USE SCHEMA`, and `SELECT` and `MODIFY` on the one table.

A relay sits in between because the two sides do not match directly. Redox posts its own payload and authenticates with a verification token it sends in a request header. Zerobus expects rows in the table's shape and a Databricks OAuth token. The relay:

1. checks the Redox verification token and rejects anything else;
2. builds one row (the same mapping as `record()` in `synthetic/replay/zerobus_push.py`: envelope columns, `bundle` as a JSON string, timestamps as epoch microseconds);
3. gets and caches the OAuth token (the repo's `Token` class shows the request, scoped to one table);
4. posts to `https://<workspace-id>.zerobus.<region>.cloud.databricks.com/zerobus/v1/tables/<catalog>.bronze.redox_fhir_events/insert`;
5. returns HTTP 200 to Redox only after Zerobus accepts the row, and an error status otherwise, so Redox retries.

The relay can be a cloud function or a small web service. `zerobus_push.py` is the reference for steps 2 to 4; `scripts/setup_zerobus.py` creates the service principal and grants. Whether a Redox destination can itself perform the OAuth client credentials exchange, which would remove the relay, is to confirm with Redox.

Tradeoffs: lowest latency (median 34 seconds from landing to silver in the reference workspace, with the pipeline in continuous mode), no files to manage, but you run and monitor the relay. Zerobus records are limited to 10 MB each, which is well above a typical ADT bundle but worth checking for document notifications with attachments.

### B. Redox destination, a small service, files in a volume

```
Redox destination -> service writes one JSON file per event -> UC volume -> Auto Loader -> bronze.redox_fhir_events
```

The service validates the token, writes the payload unchanged to a volume (or a cloud bucket), and returns 200. An Auto Loader stream reads new files and fills the same columns, with `landing_path` set. Latency is a little higher, and you manage file retention, but the raw files are a simple replay archive and the service needs no Databricks OAuth logic.

### C. Redox managed delivery to a data platform

Redox works with Databricks as a partner, and Databricks describes ingesting Redox data as batch or streaming. Whether Redox offers a managed delivery into Databricks or cloud storage for your contract, and in what format, is to confirm with Redox. If it does, check that the output preserves the `Meta` block and the log ID, since the pipeline's deduplication depends on them.

## Retries, ordering and duplicates

- **Response.** Redox expects HTTP 200 for a successful delivery and a 4XX or 5XX status for a failure.
- **Retries.** In production, Redox retries a failed delivery automatically after a short wait and pauses the queue for that destination until delivery succeeds. In staging and development it does not retry or pause. A user with a support or engineer role can also retry a log manually from the dashboard. A retry is a new attempt within the same log, so `Meta.Logs[0].ID` stays the same while `AttemptID` changes. The exact retry schedule is to confirm with Redox.
- **Deduplication.** The parser uses `Meta.Logs[0].ID` as the message key, and `silver.adt_events` drops repeats on it within a 7 day watermark. A retry therefore lands once. Content duplicates (a new log with the same clinical content) share a `content_hash` that excludes `Meta` and the MessageHeader.
- **Ordering.** Redox delivers in first in, first out order per subscription, unless child queues (Beta) are enabled for parallel delivery. Redox advises against sequencing on the log ID. Zerobus orders records only within one stream, and the repo sends several requests at a time. The pipeline does not rely on either: `encounter_state` sequences by effective time, then recorded time, so late and out of order events still land correctly.
- **Pauses.** Because a failure pauses the Redox queue in production, a relay outage delays every later event. Alert on relay errors, and return 200 only for events you have durably written.

## Data model notes

- The parser handles `FHIR.Event.PatientAdmin`, `Result`, `Order` (medication requests), `MedicationAdministration` and `Document`, for a fixed list of event types (`EVENT_TYPES` in `parse_fhir.py`). Anything else is quarantined with the reason `unsupported event`, so a new subscription shows up in `silver.quarantine` before it is mapped. It detects the ADT kind from `Meta.Extensions["event-code"]` when present, and otherwise from the Encounter content (class history, planned locations, Coverage, `Meta.CanceledEvent`).
- Identifiers are matched by system suffix: encounter `:VN`, MRN `:MR`, provider `:PROVID`. These are connection specific; check them on your first real bundles.
- The synthetic feed includes medication administration notifications. Redox does not document such a notification, so this part is an extrapolation (see `synthetic/README.md`). Confirm what your connection sends.

## Sources

- Redox, responding to asynchronous events (FIFO, retries, queue pause): https://docs.redoxengine.com/permalink/1vFPDeTOMENuIP4mzkONZ9
- Redox, retry a log: https://docs.redoxengine.com/how-to-use-redox/retry-a-log
- Redox, verify a destination (verification token): https://docs.redoxengine.com/permalink/035df9d9-f4d9-52bf-9e7a-e7851ecf5866-verify-destination
- Redox FHIR data model, Meta fields: https://developer.redoxengine.com/data-models/FHIR.Event.Result.html
- Databricks and Redox partnership (2023): https://www.databricks.com/blog/new-partnership-redox-and-how-we-unlock-healthcare-data-drive-advanced-analytics
- Zerobus Ingest: https://docs.databricks.com/aws/en/ingestion/zerobus-ingest
- Zerobus concepts (ordering per stream): https://docs.databricks.com/aws/en/ingestion/zerobus-concepts
- Zerobus quotas (record size, REST rate): https://docs.databricks.com/aws/en/ingestion/zerobus-quotas
- Zerobus message types (VARIANT as JSON string): https://docs.databricks.com/aws/en/ingestion/zerobus-message-types
- Repo: `synthetic/replay/zerobus_push.py`, [docs/zerobus.md](../zerobus.md)
