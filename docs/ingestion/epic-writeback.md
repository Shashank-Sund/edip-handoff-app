# Future work: writing signed handoffs back to Epic

Version 1 does not write to the EHR. A signed and acknowledged handoff lives in the app: drafts are synced to Lakebase, and every clinician action (edits, attestations, sign offs, read backs, with persona, role and signed in user) is appended to `app.handoff_action`. This guide describes how a health system could file the signed handoff in Epic later, and what must be reviewed before it does.

Nothing here is implemented in the repo. Treat it as a starting point for a design review, not a recipe.

## What would be written

The natural unit is one note per signed handoff: the final text in the template the clinician signed (SBAR, I-PASS or the custom template), the signer, the receiving clinician and the time of read back, linked to the encounter (CSN) and patient (MRN). Structured items in the handoff already come from the chart, so they should not be written back as new discrete data. Writing a note avoids creating a second source of truth for problems, medications or allergies.

## Options

### A. FHIR DocumentReference create, directly to Epic

US Core defines how a client writes a clinical note: a `POST` of a FHIR R4 `DocumentReference`, with the note text as an attachment, a LOINC `type`, `author`, and `context.encounter`. US Core asks servers to support conditional create (`If-None-Exist`) so a retried request does not create a second note, and a corrected note is a new `DocumentReference` that `replaces` the earlier one.

Epic supports creating clinical notes through its FHIR API in R4; the exact API name, required fields, allowed note types and whether it is enabled for a given organization are to confirm on fhir.epic.com and with the health system's Epic team.

Tradeoffs: standards based and near real time, with an immediate success or error response. It needs an Epic app registration, a backend service credential approved by the health system, and the note types and encounter rules Epic enforces.

### B. Through Redox

If Redox already delivers the inbound feed, the same connection may be able to send notes to Epic, so the health system does not register a second integration. Which Redox data model or FHIR operation applies, and whether the connection is configured to write notes, is to confirm with Redox. Redox's public FHIR documentation for `DocumentReference` covers read and search.

Tradeoffs: one vendor relationship for both directions and Redox handles Epic specifics. Adds a dependency on Redox for a clinical write path, and the queue pause behavior described in [fhir-redox.md](fhir-redox.md) applies in the other direction too.

### C. HL7 v2 MDM through the interface engine

An outbound MDM^T02 (original document with content) from Databricks to the interface engine, which routes it into Epic as an inbound note. This is how many transcription and device systems file documents today.

Tradeoffs: uses an existing, well understood path and the engine team's monitoring. Asynchronous (an ACK confirms receipt by the engine, not filing in the chart), and corrections need T08 or T10 style replacement messages, which the receiving interface must support (to confirm with the engine team).

## How it would fit the repo

A minimal design, all inside the existing bundle:

1. A gold table of signed handoffs, built from `app.handoff_action` (copied from Lakebase by a job, or read in place through a Lakehouse Federation connection to Postgres), one row per sign off and acknowledgment, with a stable handoff version id.
2. An outbound job that reads new rows, builds the note (option A, B or C), and records the result (external id, status, error) in an outbound log table.
3. Idempotency on the handoff version id: conditional create in FHIR, or a fixed MSH-10 per version in HL7, so a retry never files twice.
4. Retraction: a handoff that is retracted after filing (a canceled admission) needs a defined action in Epic, such as marking the note entered in error. This must be designed before go live, not after.

## Clinical review

- **Is the handoff part of the legal medical record?** If it is filed, it is. Decide the note type, who can see it, and how long it is retained.
- **Model drafted text.** The narrative is drafted by a model and edited and signed by a clinician. Decide whether the filed note says so, and in what words.
- **Signer identity.** The app knows the signed in user's email. Epic needs a provider id. Define the mapping, and what happens for a user without one.
- **Duplicates with existing documentation.** Many units already document handoff in a flowsheet or note. Agree which one is the record, so clinicians do not chart twice.
- **Amendments.** If a newer draft arrives after sign off, the app flags it until it is signed again. Decide whether a re-signed handoff replaces the filed note or adds one.
- **Downtime.** Define what clinicians see when the write fails, and who is alerted.

## Security and privacy review

- **Credentials.** A dedicated service principal or backend service account, scoped to write notes only, with secrets in a secret scope, never in the bundle. Rotation and revocation owned by a named team.
- **Data leaving the platform.** The outbound job sends PHI to Epic or Redox. Document the network path, encryption in transit, and the business associate agreements that cover it.
- **Least privilege in Databricks.** Only the outbound job reads the signed handoff table with write intent; clinicians and analysts read it through views.
- **Audit.** Keep the outbound log with request ids and responses, and link it to `app.handoff_action`, so any filed note can be traced to the clinician action that produced it.
- **Testing.** Use the Epic and Redox non production environments with synthetic patients first. The synthetic data in this repo is suitable for that.

## Sources

- US Core, writing clinical notes: https://hl7.org/fhir/us/core/writing-clinical-notes.html
- FHIR R4 DocumentReference: https://hl7.org/fhir/R4/documentreference.html
- Epic FHIR API specifications: https://fhir.epic.com/Specifications
- Redox FHIR DocumentReference read: https://docs.redoxengine.com/api-reference/fhir-api-reference/fhir-resources/DocumentReference/read/
- HL7 v2 event types, including MDM T02: https://www.hl7.eu/HL7v2x/v231/hl7v231tab0003.htm
- Repo: `app/README.md` (sign off, read back and `app.handoff_action`), `PLAN.md` (writeback out of scope for version 1)
