# HL7 v2 from an interface engine

Most health systems already route HL7 v2 through an interface engine such as Rhapsody, Cloverleaf, NextGen Connect (formerly Mirth Connect) or Epic Bridges. This guide covers adding one more outbound route from that engine into Databricks, and what the repo's parser assumes about the messages.

## What bronze must look like

The pipeline reads `<catalog>.bronze.hl7_raw_messages` as a stream, one row per message. Only three columns are used:

| Column | Type | Used for |
| --- | --- | --- |
| `content` | STRING | The whole message, segments separated by CR (LF and CRLF are also accepted) |
| `engine_received_ts` | TIMESTAMP | When the engine received the message. The parser uses it to resolve the repeated DST hour (below) |
| `inserted` | TIMESTAMP | When the row landed; reported downstream as `landed_ts` for freshness |

The synthetic table also carries `path`, `md5Hash` and `messageId` (the landing columns of the Funke accelerator) and promoted MSH fields (`msh_control_id`, `msh_message_type`, `msh_trigger`, `msh_timestamp`, `msh_sending_facility`). Keep them for troubleshooting; the parser does not need them.

Two rules matter more than the column list:

1. **One message per row.** If the engine writes batch files (FHS/BHS wrappers or many messages per file), split them on `MSH` before or during landing.
2. **A real receipt time.** If the engine can stamp its own receive time (in the file name, a header, or a sidecar field), use it. File modification time is an acceptable fallback only if the engine writes each file as soon as it receives the message.

## Landing options

| Option | How it works | Latency | Tradeoffs |
| --- | --- | --- | --- |
| Engine file writer to a cloud bucket or UC volume, then Auto Loader | The engine writes one `.hl7` file per message; Auto Loader (`cloudFiles`, `binaryFile` format) streams new files into bronze. This is what Funke does | Seconds to a minute, plus pipeline time | Simplest and easiest to replay (the files are the archive). Many small files; set a retention policy. Ordering comes from event time, not file order |
| SFTP to Databricks | The engine pushes to an SFTP server; the Databricks SFTP connector (built on Auto Loader; Public Preview in the November 2025 release notes, current status to confirm) reads it | Minutes | Fits sites whose engines already do SFTP. Some Auto Loader options (file notifications, `cleanSource`) are not supported over SFTP |
| Engine pushes to cloud storage directly | Many engines have S3, Azure Blob or GCS writers or can call a cloud CLI | Seconds to a minute | Same as the first row, without a volume in between. Needs cloud credentials on the engine host |
| Zerobus | A small sender (or an engine plugin) posts each message as a row to Zerobus over REST or gRPC, or through its Kafka compatible API (Beta when checked; status to confirm) | Seconds | No files and no broker. You write and run the sender, and it must retry on errors. The default REST quota is 10,000 requests per second |
| Kafka, Azure Event Hubs or Amazon Kinesis | The engine publishes to a topic; a streaming table reads it with Structured Streaming | Seconds | Best if the health system already runs a broker for other consumers. Adds a system to operate |

Whatever the route, write to bronze unchanged. Do not let the engine "fix" messages for Databricks; transformation belongs in the parser, where it is tested and versioned.

## Parsing options

- **The repo's parser, `pipelines/parse_hl7.py`.** Standard library Python, wrapped as a UDF. It returns the contract records in `pipelines/edip_contract.py` (ADT, observations, medications, notes, allergies, flags, quarantine), so the FHIR feed and the HL7 feed land in the same silver tables. It handles ADT A02, A03, A04, A06, A08, A11 to A15, A26 and A40, ORU R01, RDE O11, RAS O17 and MDM T02. It is written for this dataset's Epic style feeds, so expect to change the mappings listed below.
- **Funke** (Databricks Industry Solutions, `databricks-industry-solutions/funke-hl7v2`). A declarative pipeline with a `raw_messages` table (Auto Loader from a landing volume) and a `parsed_messages` table that parses every message into nested Spark structures (segments, fields, components, repetitions). It does not map to clinical meaning; you build gold tables on top. A reasonable split is to use Funke for landing and generic parsing, and keep a site specific mapping step like `parse_hl7.py` for the handoff contract. The source is published under the Databricks License and is not covered by Databricks support SLAs.
- **Other libraries.** `hl7` or `hl7apy` (Python) or HAPI (Java) can replace the segment splitting; the mapping work is the same.

## Site profiles

Two hospitals in the synthetic data behave differently on purpose (`synthetic/edip_synth/config.py`, `site_profile`), because real sites do:

| Behavior | Site SGH | Site LKS | What the parser does |
| --- | --- | --- | --- |
| HL7 version | 2.5.1 | 2.3 | Reads both; fields used exist in both |
| UTC offset on timestamps | Yes (`-0400`, `-0500`) | No (local time) | With an offset, converts to UTC. Without one, assumes America/New_York |
| Z segments | ZPV, ZPD | None | Ignores unknown segments, including a stray ZZZ |
| Observation to inpatient | A06 | A08 with a class change in PV1-2 | Both become `class_change` |
| Pending admit and bed events | A14, A15, A26 | A08 with an EVN-4 reason (`ADMIT`, `BED_ASSIGN`, `BED_RELEASE`) | Both become the same ADT kinds |
| Observation class code | `OBS` | `O` | Both normalize to `OBS` |

For your site, check at least these mappings in `parse_hl7.py` against a few days of real traffic:

- the time zone (`TZ`), and whether each interface sends offsets;
- the A08 reason codes your engine or EHR uses (`REASON_KIND`), since A08 is the "everything else" event and many sites use it for bed and admit changes;
- where the MRN (`PID-3` with type `MR`) and the encounter id (`PV1-19`) live;
- ADT OBX codes for acuity, isolation and code status (`ACUITY`, `ISOLATION`, `CODESTATUS` here);
- service codes, discharge dispositions, note types and flowsheet row ids, which are local build.

A message without a usable MSH, PID or PV1 visit number is quarantined, not dropped. Empty A08s (a blank PV1) are a common real pattern and land in `silver.quarantine`.

## Daylight saving time

When DST ends, 01:00 to 01:59 happens twice. A feed without UTC offsets cannot say which one it means. The parser resolves it with a reference time that the value cannot come after:

- MSH-7 is checked against `engine_received_ts`;
- fields inside the message (EVN-2, EVN-6, OBR-22, RXA-3 and so on) are checked against MSH-7.

If the reference is at or within an hour after the later reading (with two minutes of clock skew allowed), the parser takes the later reading (standard time). Otherwise it takes the earlier reading (daylight time). Real time ADT, results and medication events resolve exactly. A value sent long after the repeated hour, such as a note created at 01:30 and signed the next morning, cannot be resolved from the message and takes the earlier reading. This is why `engine_received_ts` must be a real receipt time. If your engine can send offsets, turn them on; it removes the problem.

## Retransmits and duplicates

Engines resend a message when an ACK is lost. The repo handles three kinds of duplicate:

- **Retransmits** keep the same MSH-10. `silver.adt_events` drops repeats on `(source, msg_key)`, where `msg_key` is `MSH-3|MSH-4|MSH-10`, within a 7 day watermark. If your engine assigns a new control id on resend, or reuses ids after a restart, tell the parser: the key must be unique per original message.
- **Content duplicates** (the same content under a new control id, or an A08 flood with no material change) get the same `content_hash`, which ignores MSH-7 and MSH-10. They pass through `adt_events` as separate rows but do not change `encounter_state`, because they carry the same values.
- **The same clinical record on both feeds** collapses on content keys (encounter, order, code, status, times truncated to the second), because HL7 carries seconds and FHIR milliseconds.

Order comes from event time, not arrival: `encounter_state` sequences by effective time, then recorded time, and empty fields never overwrite values. Late and out of order messages land correctly.

## Sources

- Funke on GitHub: https://github.com/databricks-industry-solutions/funke-hl7v2
- Introducing Funke (Databricks blog): https://www.databricks.com/blog/introducing-funke-native-hl7v2-parsing-databricks
- Auto Loader: https://docs.databricks.com/aws/en/ingestion/cloud-object-storage/auto-loader/
- Ingest files from SFTP servers: https://docs.databricks.com/aws/en/ingestion/sftp
- Zerobus Ingest: https://docs.databricks.com/aws/en/ingestion/zerobus-ingest
- Zerobus quotas: https://docs.databricks.com/aws/en/ingestion/zerobus-quotas
- Zerobus Kafka compatible APIs: https://docs.databricks.com/aws/en/ingestion/zerobus-kafka
- Structured Streaming with Kafka: https://docs.databricks.com/aws/en/connect/streaming/kafka
- HL7 v2 event type table (0003): https://www.hl7.eu/HL7v2x/v231/hl7v231tab0003.htm
