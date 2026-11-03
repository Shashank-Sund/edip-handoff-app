# Synthetic data generator

Builds one coherent, fully synthetic week of emergency department (ED) activity at two hospitals. Each encounter is a single timeline, and later renderers turn that timeline into the three feeds a health system would actually have: FHIR events from an integration platform, raw HL7 v2 messages, and Clarity-style nightly tables. Because every feed comes from the same truth, cross-source joins reconcile exactly, and every injected fault is logged in a manifest.

No real patient data is used anywhere. Patient histories come from [Synthea](https://github.com/synthetichealth/synthea), and the ED visit is generated on top of them.

## Run

```bash
# with Synthea history (recommended)
python -m edip_synth --out ../data/world --synthea /path/to/synthea/output/csv

# without Synthea: uses a small built-in stand-in population (tests, quick runs)
python -m edip_synth --out ../data/world
```

The generator uses only the Python standard library and takes about 3 seconds. Output goes to `<out>/truth/*.jsonl`: one file per entity, plus `fault_manifest.jsonl`, `render_plan.json` and a `summary.json` of key distributions.

Synthea command used for the reference dataset (Java 17 or newer, or the `eclipse-temurin:21-jre` image):

```bash
java -jar synthea-with-dependencies.jar -p 3000 -s 20261102 -cs 20261102 -r 20261101 -a 18-95 Massachusetts Boston \
  --exporter.csv.export=true --exporter.fhir.export=false --exporter.years_of_history=10 \
  --generate.only_alive_patients=true --exporter.csv.include_quality_of_life_observations=false
```

## What it models

| Area | Reference dataset |
| --- | --- |
| Window | Monday 2026-10-26 to Sunday 2026-11-01, America/New_York. Daylight saving time ends inside the window, so 01:00 happens twice on the last day. |
| Volume | 700 ED encounters (420 and 280 across the two hospitals), 120 admitted (17 percent): 90 inpatient and 30 observation |
| Units | 2 EDs, 6 inpatient units and 2 observation units, with beds, private rooms and a background census, so boarding comes from real capacity |
| Clinical scenarios | Sepsis (urinary and pneumonia sources), heart failure, COPD, chest pain and NSTEMI, stroke, upper GI bleed, DKA, pneumonia and hip fracture in full detail, plus generic admit and discharge templates |
| Per encounter | ADT events, ED tracking events, bed requests, vitals that respond to treatment, lab panels with LOINC codes, imaging with preliminary and final reads, medication orders and administration records with RxNorm codes, notes, code status, allergies and isolation |
| End state | At the end of the window some patients are still in the ED and some are boarding, so the app opens on a live board |

The distributions (arrivals by hour, ESI mix, door to provider, boarding, length of stay, code status by age, allergy prevalence, isolation share) are anchored to public sources such as CDC NHAMCS 2022, HCUP and published hospital critical-value lists. Every tunable number lives in `edip_synth/config.py` and `edip_synth/scenarios.py`.

## Edge cases

Faults that change what happened (canceled registrations, a patient merge, a canceled transfer, a canceled discharge, observation to inpatient conversion, bed reassignment, late-signed notes, a critical lab after transfer, corrected results, missing code status, an allergy or isolation found mid-visit, vitals gaps, 72-hour returns, LWBS and AMA) are applied to the truth and logged in `fault_manifest.jsonl`.

Faults that only change how a feed looks (A08 floods, retransmits, out-of-order delivery, stale location replays, offset and Z-segment differences, encoding quirks, mixed-status panels) are chosen in `render_plan.json` and applied by the renderers, so every renderer agrees on which encounters are affected.

## Known limits

- The HL7 v2, FHIR and Clarity renderers are separate modules, built next.
- A few codes added beyond the research tables are listed in `scenarios.UNVERIFIED` and still need to be checked against RxNav and LOINC.
- With only 120 admissions, boarding medians move by about 30 percent between seeds. Tests use tolerances sized for that.
