# Build plan

A reference Databricks App that drafts emergency department (ED) to inpatient handoffs in near real time from synthetic data. Clinicians review, edit, sign, and acknowledge each handoff. Every claim the model writes cites its source.

## Scope

- **In scope:** synthetic data that mirrors real hospital feeds, a Databricks pipeline, handoff generation, a Databricks App, and docs that explain how a health system would connect real data.
- **Assumption:** the data is already in Databricks. The ingestion READMEs explain how a health system gets it there.
- **Out of scope for version 1:** writing back to the EHR. A separate README describes that path as future work.

## Architecture

```
SOURCES (synthetic; one encounter timeline rendered three ways)
  (A) FHIR R4 events in an integration-platform envelope -> Zerobus REST -> bronze.fhir_events
  (B) HL7 v2 message files -> UC volume -> Auto Loader + parser -> bronze.hl7v2_messages
  (C) Clarity-style nightly tables -> bronze.clarity_*

SILVER (serverless pipeline; both real-time inputs map to one schema)
  encounter_state (AUTO CDC SCD1), adt_history (SCD2), observations, med_admin,
  notes, allergies, code_status, isolation, pending_orders,
  handoff_events (deterministic ADT rules plus debounced meaningful events)

AI (serverless job)
  rules -> ai_classify / ai_extract -> LLM narrative (JSON per element with
  confidence and cited source) -> completeness rules from the template YAML

GOLD (Delta with change data feed)
  handoff_draft (all versions), boarding_list (current state)

LAKEBASE
  synced (read only): boarding_list, handoff_latest
  native: handoff_case, handoff_field_edit, handoff_signoff, handoff_ack, handoff_version

APP (FastAPI + React, on-behalf-of-user auth, polling)
  boarding list, nurse view (SBAR), physician view (I-PASS), custom template,
  edit / sign / acknowledge, "changed since signed" diff, per-unit access
```

## Data

- **Volume:** about 700 ED encounters over 7 days, about 120 admitted (about 17 percent), across 2 hospitals and 6 inpatient units.
- **Patient histories:** Synthea, about 3,000 adults with 10 years of history.
- **ED visits:** a custom generator builds them from 9 clinical scenarios: sepsis, CHF, COPD, chest pain, GI bleed, stroke, DKA, pneumonia, and hip fracture.
- **Edge cases:** 32 of them, all logged. Examples: A08 floods, retransmits, out-of-order messages, cancels, merges, late-signed notes, and a critical lab arriving after sign-off.
- **Realism checks:** a 25-item checklist runs as automated tests.

## Templates and rules

- **Templates:** SBAR for nurses, I-PASS for physicians, and a custom template that defaults to the Joint Commission minimum content.
- **Shared elements:** all three templates draw on the same 31 standard elements.
- **Rules:** each rule is tagged hard (blocks sign-off), stale (flags old data), or soft (warns), and each cites its evidence.

## Models

| Step | Model |
| --- | --- |
| Triggers and structured risk flags | Deterministic rules |
| Classifications from notes | `ai_classify` |
| Facts from notes | `ai_extract` |
| Handoff narrative | Hosted LLM through `ai_query`. Open models during development; Claude Sonnet when available in the workspace. |
| Regenerate with full chart (on demand) | Strongest available hosted model |

## Phases

Each phase ends at a checkpoint for review.

0. **Environment:** workspace, catalog, budgets, smoke tests, and repo.
1. **Synthetic data:** built locally, then loaded into the workspace.
2. **Pipeline:** bronze to gold, synced to Lakebase, with freshness measured.
3. **Handoff generation:** templates, rules, prompts, evaluation, and cost per handoff.
4. **App:** first in local mock mode, then deployed with Lakebase and on-behalf-of-user auth.
5. **Docs and launch:** ingestion READMEs, the feature status matrix, and a public mock demo.

## Cost controls

- Scale everything down by default: the smallest SQL warehouse with a 1-minute auto-stop, and Lakebase scaled to zero.
- Run pipelines on a trigger; run them continuously only during demos.
- Turn everything on and off with `demo-up` and `demo-down`, plus a nightly shutdown job.
- Set an account budget with alerts and a model-spend budget that blocks usage.
