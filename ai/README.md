# Handoff generation

Turns the silver tables into draft handoffs for three templates: nurse SBAR, physician I-PASS, and a custom template that starts from the Joint Commission minimum. Clinicians review, edit, sign and acknowledge the drafts in the app.

## Design

1. **Chart packet** (`edip_ai/chart.py`). For one encounter at one point in time, collect identity, destination, code status, allergies, isolation, vitals, abnormal and critical labs, pending results, imaging, ED medications, notes, problems, home medications and language. **Every fact carries a source id**, such as `obs:<order>:<code>:<status>`, `med:<order>:<line>` or `note:<id>`.
2. **Deterministic elements come from data, never from the model.** Allergies, code status, isolation, vitals, labs, medications, pending results and destination are rendered straight from the packet, so the model cannot misstate them.
3. **One model call writes the narrative elements** that all three templates share: illness severity and why, working diagnosis, ED course, mental status and safety, action list with owners, contingency plan, and anticipated disposition. Every statement must cite packet source ids (`edip_ai/prompts.py`).
4. **Validation before anyone sees it** (`edip_ai/validate.py`):
   - unknown source ids are removed and counted;
   - numbers stated as facts must appear in the cited sources; they compare by value (82 matches a charted 82.0), and a decimal may be a charted value rounded to the stated precision (lactate 2.6 for 2.62). Thresholds and instructions ("if SBP over 180/105", "q2h"), clock times and expected durations ("48-72 h") are exempt. What remains is flagged for the clinician, not removed: in practice these are totals the model computed, such as fluid volume summed across boluses;
   - severity must be stable, watcher or unstable;
   - a truncated answer is retried once with a larger budget.
5. **Completeness rules** from the template YAML (`edip_ai/rules.py`):
   - **hard** blocks sign off until the element has data or an attestation from a fixed list (for example "No known allergies confirmed");
   - **stale** blocks when the data is too old for the severity (vitals: 60, 30 or 15 minutes for stable, watcher, unstable);
   - **soft** warns and counts in the completeness score;
   - sender identity and receiver read back are checked in the app at sign and accept time.
6. **Job** (`jobs/handoff_job.py`). Reads `silver.handoff_events` and generates at:
   - every start (admit decision or pending admit);
   - every finalize (transfer to the unit);
   - regenerate triggers, debounced (5 minutes live; at most hourly when backfilling a past week).

   A retract (canceled registration) marks the drafts retracted. It writes every version to `gold.handoff_draft` (change data feed on, synced to Lakebase as `gold.handoff_draft_pg` for the app) and the newest per encounter and template to `gold.handoff_latest` (a convenience table for SQL; the app does not read it).

## Templates

`templates/elements.yaml` defines 31 canonical elements. Each template file (`sbar_nurse.yaml`, `ipass_physician.yaml`, `custom_sea58.yaml`) places elements into its sections and sets a rule per element. A health system adapts the custom template by copying it, renaming sections to local labels, and moving elements or changing rules. A site whose feeds cannot supply an element (for example lines, drains and airways) downgrades that rule from hard to soft, with a note.

Element and rule sources: Joint Commission Sentinel Event Alert 58 (2017); IHI SBAR; I-PASS (Starmer et al., NEJM 2014) and its ED adaptation (Heilman et al., WestJEM 2016); SBAR-DR (Smith et al., WestJEM 2018); PREP-4Cs (Lee et al., WestJEM 2017).

## Choosing the model

The model is configuration, not code. In `config.yaml`:

```yaml
model: databricks-gpt-oss-120b      # open model default
# model: databricks-claude-sonnet-5-5  # or any chat endpoint in your workspace
```

You can also pass the model for one run: `databricks bundle run handoff_generator -t dev --params model=databricks-claude-sonnet-5-5`. `client: mock` writes a deterministic narrative without a model, for CI and the app's mock mode.

## Rate limits and throughput

Pay-per-token model endpoints are rate limited per workspace. On the trial workspace used to build this app, `databricks-gpt-oss-120b` completed about 6 handoff calls a minute and answered extra concurrent requests with HTTP 429. The job therefore runs 4 concurrent calls by default (the `workers` job parameter; lower it if the log shows HTTP 429; the Databricks SDK retries rate limited requests with backoff), writes results every 20 generation points, and skips points already stored, so a rerun resumes. Setup normally skips the model entirely and loads the drafts shipped in `ai/reference/`. A production deployment that needs more throughput should use provisioned throughput for the model, or a hosted endpoint with higher limits.

## Verified on the reference week

`databricks-gpt-oss-120b`, all 440 generation points of the reference week (118 admitted encounters, 1,320 drafts; one point per admit decision or meaningful change before transfer, plus ED addenda), scored against the synthetic truth with `ai/eval/run_eval.py` (Mode B, drafts exported with `ai/eval/export_drafts.py`):

| Measure | Result |
| --- | --- |
| Deterministic elements correct (allergies, code status, isolation, vitals, critical labs, pending results, ED medications, destination) | 1.0 |
| Missed critical items (critical results, pending results, allergies, code status, isolation) | 0 |
| Contradictions, invented medications, invented labs | 0 |
| Narrative statements citing a real source | 99.8% |
| Unknown source ids per model call (removed by the validator) | 1.1 |
| Numbers flagged for the clinician, per template | 16, mostly fluid volumes summed across boluses |
| Illness severity agreement with the reference (rule floor plus model) | 0.977; no unstable patient called stable or watcher (the 30 disagreements all call a patient one level sicker) |
| Model calls failed | 0 |

Severity agreement was 0.33 before the rule floor, because the model tended to call patients one level lower than the reference. Completeness scores stay below 1 by design: sender identity, read back and some soft elements are completed in the app.

When the floor raises severity, E07 keeps the model's text after the rule's reasons (for example "Unstable by rule: spo2 89." followed by the model's "Stable: vitals stable..."). The app shows the rule reasons and the model's own call (`model_value`) as separate, labeled parts.

## Cost

Measured from `system.billing.usage` and `system.serving.endpoint_usage` on the trial workspace, at list prices:

- model tokens: about 0.15 USD per million input and 0.60 USD per million output tokens for `databricks-gpt-oss-120b`; one generation point (all three templates) uses about 2,650 input and 1,550 output tokens, about 0.0013 USD;
- job compute (serverless): about 0.35 USD per hour while the job runs;
- the full reference week backfill: about 0.57 USD of tokens plus 0.43 USD of compute, 73 minutes at 2 concurrent calls (measured before the default became 4; estimated from the per call figures above).

Live, at about 80 generation points a day for this volume, the model costs about 0.10 USD a day. A hosted model costs more per token; scale the token counts above by its price.

## Local runs

`edip_ai/local.py` builds silver-shaped records, encounter states and packets from a rendered world without Spark, so the whole chain runs on a laptop with the mock model (see `tests/test_handoff_core.py`). For one real call, set `DATABRICKS_CONFIG_PROFILE` and use `llm.ServingLLM("<endpoint>")`.
