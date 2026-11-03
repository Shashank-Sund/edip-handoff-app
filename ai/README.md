# Handoff generation

Turns the silver tables into draft handoffs for three templates: nurse SBAR, physician I-PASS, and a custom template that starts from the Joint Commission minimum. Clinicians review, edit, sign and acknowledge the drafts in the app.

## Design

1. **Chart packet** (`edip_ai/chart.py`). For one encounter at one point in time, collect identity, destination, code status, allergies, isolation, vitals, abnormal and critical labs, pending results, imaging, ED medications, notes, problems, home medications and language. **Every fact carries a source id**, such as `obs:<order>:<code>:<status>`, `med:<order>:<line>` or `note:<id>`.
2. **Deterministic elements come from data, never from the model.** Allergies, code status, isolation, vitals, labs, medications, pending results and destination are rendered straight from the packet, so the model cannot misstate them.
3. **One model call writes the narrative elements** that all three templates share: illness severity and why, working diagnosis, ED course, mental status and safety, action list with owners, contingency plan, and anticipated disposition. Every statement must cite packet source ids (`edip_ai/prompts.py`).
4. **Validation before anyone sees it** (`edip_ai/validate.py`):
   - unknown source ids are removed and counted;
   - numbers stated as facts must appear in the cited sources (thresholds and instructions such as "if SBP under 90" are exempt);
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

   A retract (canceled registration) marks the drafts retracted. It writes every version to `gold.handoff_draft` and the newest per encounter and template to `gold.handoff_latest` (change data feed on, synced to Lakebase for the app).

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

## Local runs

`edip_ai/local.py` builds silver-shaped records, encounter states and packets from a rendered world without Spark, so the whole chain runs on a laptop with the mock model (see `tests/test_handoff_core.py`). For one real call, set `DATABRICKS_CONFIG_PROFILE` and use `llm.ServingLLM("<endpoint>")`.
