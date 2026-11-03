# Epic Clarity (or Caboodle) nightly extracts

Clarity is Epic's relational reporting database, loaded overnight from the operational database (Chronicles). It runs on Microsoft SQL Server or Oracle, and data in it is usually about a day old. Caboodle is Epic's dimensional warehouse, built on top of Clarity. For this app, Clarity supplies history that does not change during an ED visit.

## What the pipeline uses Clarity for

The real time feeds describe the current visit. Clarity fills in the patient's background for the handoff:

| Silver table | Built from | Filter and join | Used in the handoff for |
| --- | --- | --- | --- |
| `silver.problem_list` | `bronze.clarity_problem_list` | joined to `clarity_patient` on `PAT_ID` to get `PAT_MRN_ID` as `mrn` | Problem list (`DESCRIPTION`, cited by `PROBLEM_LIST_ID`) |
| `silver.home_meds` | `bronze.clarity_order_med` | `ORDERING_MODE_C_NAME = 'Outpatient'`, same join | Home medications (`DESCRIPTION` or `DISPLAY_NAME`, cited by `ORDER_MED_ID`) |
| `silver.allergy_history` | `bronze.clarity_allergy` | same join | Allergy history; the handoff itself takes current allergies from the real time feeds |

The handoff job also reads `bronze.clarity_patient` directly (for example `ALLERGY_REVIEW_C_NAME`). These are materialized views, so they are recomputed from the bronze tables on each pipeline update.

**The join key is the MRN.** Clarity rows are joined to encounters through `PATIENT.PAT_MRN_ID`, which must match the MRN on the real time feeds (`PID-3` type `MR` in HL7, the `:MR` identifier in FHIR). If your site has several MRN types or facilities, decide which one the feeds carry and load the same one into `PAT_MRN_ID`. After a patient merge, the feeds carry the surviving MRN; Clarity carries `MERGED_INTO_PAT_ID`.

The pipeline does not filter the problem list by status. If your extract includes resolved or deleted problems, filter them in the extract (for example on `PROBLEM_STATUS_C_NAME`) or add the filter to `problem_list()` in `pipelines/edip_pipeline.py`.

## Table naming

The synthetic Clarity tables follow Epic's table and column names, using the Epic EHI export documentation as the reference (`PATIENT`, `PAT_ENC`, `PAT_ENC_HSP`, `CLARITY_ADT`, `PROBLEM_LIST`, `ORDER_MED`, `ALLERGY`, `HNO_INFO` and others, 65 tables in all, including `ZC_` category tables). `setup/load_bronze.py` lands each one as `bronze.clarity_<table name in lower case>`, typed from `synthetic/out/world/clarity/schema.json`.

The four tables the pipeline needs, and `CLARITY_ADT`, were checked against the EHI documentation; the other names were not each checked (to confirm before you rely on them). A real extract maps one to one: `PROBLEM_LIST` becomes `bronze.clarity_problem_list`. Column names should be kept exactly as in Clarity. Two caveats:

- Epic documents that table contents vary with each organization's Epic version, local build and custom development. Compare your columns with `schema.json` before the first load.
- The pipeline needs only four tables today: `PATIENT`, `PROBLEM_LIST`, `ORDER_MED` and `ALLERGY`. Start with these. The others exist for analytics and for checking the synthetic data, and you can add them later.

Caboodle uses different (dimensional) table names. If Caboodle is the only source your team can use, build views named like the Clarity tables above, with the columns the pipeline reads, rather than changing the pipeline.

## Getting the data out

| Option | How it works | Fits when | Tradeoffs |
| --- | --- | --- | --- |
| Lakeflow Connect SQL Server connector | Managed ingestion with change tracking or CDC into streaming tables (generally available as of September 2025) | Clarity on SQL Server, and the DBA allows change tracking or CDC | Incremental and low effort once running. An ingestion gateway runs on classic compute, continuously. Enabling change tracking on a reporting database is a decision for the Clarity DBA and Epic team (to confirm locally) |
| Lakeflow Connect Oracle connector | Integrated CDC with LogMiner (Beta), or query based ingestion with a cursor column | Clarity on Oracle | Beta status; needs archive log mode and supplemental logging, which a Clarity DBA may not allow |
| Lakehouse Federation | Query SQL Server or Oracle in place from Databricks, read only | A pilot, or very small tables | No copy to manage, but every query hits Clarity. Databricks recommends Lakeflow Connect when volume or latency matter |
| Scheduled JDBC extract | A job reads the four tables with Spark JDBC after the Clarity ETL finishes and overwrites bronze | Teams that want full control and a simple schedule | You own the schedule, credentials and retries. Full reads of `ORDER_MED` can be large; filter to patients with recent encounters if allowed |
| Existing vendor or in house tools | The health system's current ETL (SSIS, Informatica, Fivetran and similar) drops files or writes tables | A pipeline from Clarity to cloud storage already exists | Reuses approved tooling; adds a hop and a second schedule to coordinate |

Whatever the route, run it after the nightly Clarity ETL completes, and make a failed night leave the previous snapshot in place.

## Snapshot or incremental

The repo loads one full snapshot per night (`CREATE OR REPLACE TABLE ... AS SELECT`), and the silver views are full recomputes. This is the simplest correct choice for problem lists, home medications and allergies: they are small per patient, and a full snapshot handles deletes and resolved problems without extra logic.

Incremental loading (change tracking, CDC or a cursor column) pays off when tables are large or you want history. Keep three points in mind:

- **Deletes.** Clarity rows can be removed or marked deleted. Change tracking and CDC capture deletes; a cursor based query does not.
- **History.** Lakeflow Connect can keep SCD type 2 history. The handoff only needs current state.
- **Streaming tables versus views.** If bronze becomes a streaming table with updates and deletes, the materialized views in silver still read it correctly, because they recompute from the current table.

## Practical notes

- Clarity times are local and stored without an offset. The synthetic schema documents this (`encoding` in `schema.json`). The pipeline does not use Clarity times today, but convert them before you do.
- Clarity is often a shared reporting server. Agree the read window with the Clarity team, and use a dedicated, read only login scoped to the tables you need.
- Category columns (`_C`) come with `_C_NAME` text in this repo, as many Clarity views provide. If your extract has only codes, join the `ZC_` tables in the extract.

## Sources

- Epic EHI export tables: https://open.epic.com/EHITables
- Epic EHI export schema notes: https://open.epic.com/EHITables/TechSpec
- Example table page (PAT_ENC): https://open.epic.com/EHITables/GetTable/PAT_ENC.htm
- Lakeflow Connect SQL Server connector, GA announcement: https://www.databricks.com/blog/announcing-sql-server-connector-lakeflow-connect-now-generally-available
- Managed database connectors (change tracking and CDC): https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/cdc-overview
- Oracle integrated CDC connector: https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/oracle-integrated-overview
- Query based connectors: https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/query-based-overview
- Lakehouse Federation: https://docs.databricks.com/aws/en/query-federation/
