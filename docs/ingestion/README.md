# Connecting real feeds

The reference app runs on one synthetic week. Every feed is shaped like what a health system already has, so a data team can replace the synthetic source with a real one without changing the pipeline. This folder explains how, for each feed, and what each choice costs.

The pipeline (`pipelines/edip_pipeline.py`) reads three kinds of bronze tables in the catalog you deploy to (default `edip`). If you land real data in the same tables with the same columns, silver, gold, the handoff generator and the app work unchanged.

```
Interface engine (HL7 v2)  -> files or stream -> bronze.hl7_raw_messages  -+
Redox (FHIR events)        -> Zerobus or files -> bronze.redox_fhir_events -+-> parse_hl7.py / parse_fhir.py
                                                                              -> silver (one contract) -> gold -> Lakebase -> app
Clarity (nightly)          -> connector or extract -> bronze.clarity_*  ------> silver.problem_list, home_meds, allergy_history
```

## The three feeds

**HL7 v2 from an interface engine.** ADT, results (ORU), pharmacy orders and administrations (RDE, RAS) and notes (MDM), one row per message. This is the feed most hospitals already route to downstream systems. It is near real time, but every site sends it a little differently. See [hl7v2.md](hl7v2.md).

**FHIR events from Redox.** Redox converts EHR events into FHIR R4 bundles with a Redox `Meta` envelope and posts them to a destination. The repo streams these into Delta with Zerobus, which measured a median of 34 seconds from landing to silver. See [fhir-redox.md](fhir-redox.md).

**Clarity nightly tables.** History that does not change during an ED visit: problem list, home medications and allergy history. The repo mirrors Epic EHI table names, so a real extract maps one to one. See [clarity.md](clarity.md).

You do not need both real time feeds. HL7 v2 and FHIR are parsed into the same records (`pipelines/edip_contract.py`), and the pipeline deduplicates records that arrive on both. If you have only one, leave the other bronze table empty; setup already does this for FHIR when `fhir_mode=zerobus`.

Writing signed handoffs back into the EHR is not part of version 1. [epic-writeback.md](epic-writeback.md) describes the options and the reviews they need.

## Comparison

| | HL7 v2 (interface engine) | FHIR events (Redox) | Clarity (nightly) |
| --- | --- | --- | --- |
| Bronze table | `bronze.hl7_raw_messages` | `bronze.redox_fhir_events` | `bronze.clarity_<table>`, one per Epic table |
| Latency | Seconds to a few minutes, depending on how files or events are landed and whether the pipeline runs continuous or triggered | Seconds; median 34 s landing to silver in the reference workspace (continuous mode) | About a day; Clarity itself loads overnight from the operational database |
| Integration effort | Highest. A new outbound route on the engine, plus mapping your site profile (offsets, Z segments, event reason codes, A08 versus A06) into `parse_hl7.py` | Medium. A Redox destination and a small relay; the bundle shape is consistent, but Redox configuration varies by connection | Medium, mostly approvals. Read access to Clarity, a connector or extract job, and a table list |
| Main cost drivers | Engine channel build and license terms; cloud storage; pipeline compute (continuous mode costs more than triggered) | Redox subscription for the connection; Zerobus ingest (pricing to confirm with your account team); a small relay service; pipeline compute | Connector or extract compute (the Lakeflow Connect SQL Server gateway runs on classic compute); storage; one nightly refresh |
| What the pipeline reads | `content` (one message, CR or LF separated), `engine_received_ts`, `inserted` | `bundle` (VARIANT, the full Redox payload including `Meta`), the envelope columns, `ingest_ts` | `clarity_patient` (`PAT_ID`, `PAT_MRN_ID`), `clarity_problem_list`, `clarity_order_med` (`ORDERING_MODE_C_NAME`), `clarity_allergy` |
| Deduplication key | `MSH-3\|MSH-4\|MSH-10` | Redox `Meta.Logs[0].ID` | Full snapshot replace, no key needed |
| Where it fails | Quarantine table `silver.quarantine`, with a reason | Same | Design the load so a failed night leaves the previous snapshot in place |

## Before you start

- **Use a non production workspace and a separate catalog.** Pass `--var catalog=<name>` so real data never shares a catalog with the synthetic truth. Do not run `setup/load_bronze.py` against a catalog that holds real data: it replaces the bronze tables.
- **Treat every bronze table as PHI.** Grant access through Unity Catalog groups, keep the landing volume private, and decide retention before the first message lands.
- **Turn off the demo features.** Set `EDIP_SYNTHETIC_DATA=false` in `databricks.yml`: the app then follows the current time and takes each user's role from `EDIP_ROLE_MAP`. Setting only `EDIP_CLOCK_MODE=live` leaves the demo personas on, so anyone could act as any clinician. The full checklist: [Moving to real data](../setup.md#moving-to-real-data).
- **Run the parser tests on your own samples first.** `parse_hl7.py` and `parse_fhir.py` are plain Python with no Spark dependency, so an analyst can feed them de-identified sample messages on a laptop and check the records before anything is deployed.
- **Watch `silver.quarantine`.** Every message the parsers cannot apply goes there with a reason. On a new feed, the quarantine reasons are the fastest way to find a mapping gap.

## Guides

- [hl7v2.md](hl7v2.md): HL7 v2 from Rhapsody, Cloverleaf, NextGen Connect (Mirth) or Epic Bridges
- [fhir-redox.md](fhir-redox.md): Redox FHIR events through Zerobus, files or other routes
- [clarity.md](clarity.md): Epic Clarity or Caboodle nightly extracts
- [epic-writeback.md](epic-writeback.md): future work, writing signed handoffs back to Epic
