# Pipeline

A serverless Lakeflow Spark Declarative Pipeline turns the three bronze feeds into one encounter state that the app and the handoff generator read.

```
bronze.redox_fhir_events  (Zerobus)   ─┐
bronze.hl7_raw_messages   (files)     ─┼─ parse_fhir.py / parse_hl7.py ─▶ one contract (edip_contract.py)
                                        │
silver.adt_events ─▶ silver.encounter_state (AUTO CDC SCD1, change data feed) ─▶ Lakebase (synced table)
                 └─▶ silver.adt_history    (AUTO CDC SCD2)
silver.observations, med_events, notes, allergy_events, flag_events (deduplicated across feeds)
silver.handoff_events  (start, regenerate, finalize, retract)
silver.problem_list, home_meds, allergy_history  (Clarity, nightly)
gold.boarding_list
```

## How it works

- **One contract for both feeds.** `parse_hl7.py` and `parse_fhir.py` are plain Python (standard library) and return the same records. CI reconciles each parser with the generator's truth and with each other, so the two feeds always agree on unit, room, bed, class and event kind.
- **Order comes from event time, not arrival.** `encounter_state` sequences by (effective time, recorded time). Late, backdated and out-of-order messages (F05, F07, F08) land correctly, and empty fields never overwrite values.
- **Duplicates.** Retransmits and Redox retries collapse on the message key. Clinical records that arrive on both feeds collapse on content, with timestamps truncated to the second, because HL7 carries seconds and FHIR milliseconds. A handoff starts and retracts once per encounter.
- **Daylight saving time.** Some HL7 feeds send local times without a UTC offset, and when DST ends 01:00 to 01:59 happens twice. The parser takes the later reading (standard time) when the message arrived within an hour of it: MSH-7 is checked against the engine receipt time, and fields inside the message against MSH-7. Otherwise it takes the earlier reading. Real time ADT, results and medication events resolve exactly. A value sent more than an hour after the repeated hour (a note created at 01:30 and signed the next morning) cannot be resolved from the message and takes the earlier reading.
- **Nothing is dropped silently.** Messages that cannot be applied, such as the empty visit updates (F04), go to `silver.quarantine` with a reason.

## Run

```bash
export DATABRICKS_CONFIG_PROFILE=<your profile>
python setup/load_bronze.py --world synthetic/out/world --warehouse <warehouse id>
set -a; . ~/.config/edip/zerobus.env; set +a
python synthetic/replay/zerobus_push.py --events synthetic/out/world/fhir/events.jsonl --table edip.bronze.redox_fhir_events
databricks bundle deploy -t dev && databricks bundle run edip_pipeline -t dev
```

The pipeline is triggered by default. For demos it is switched to continuous through the API and then switched back by redeploying the bundle. Bundle variables do not render into the boolean `continuous` field, so a variable cannot be used for this.

## Verified on the reference week

| Check | Result |
| --- | --- |
| Encounter state | 702 of 702 encounters. Status matches truth for every encounter except one transfer that is documented after the window ends (F07), which neither feed had sent, so "boarding" is correct |
| Medication administrations, orders, note versions | 2,046 / 861 / 4,051, exact |
| Results | Every truth result is present; the 87 extras are the planted F28 partial reports, which each feed sends separately |
| Handoff triggers | 118 starts (118 truth pending admits), 2 retracts (F09), 117 finalizes (114 encounters plus 3 wrong bed corrections, F10) |
| Quarantine | Exactly the 3 planted empty visit messages per feed |
| Lakebase | `silver.encounter_state` (a streaming table) syncs in Triggered mode; online in about 40 s, 702 rows |
| Freshness | Zerobus landing to silver: median 34 s, p90 58 s, max 64 s (continuous mode, 118 probe events at 2 per second) |

Freshness was measured with `zerobus_push.py --probe`, which re-sends recent ADT events under new message IDs. They land as new silver rows with exact timing and leave the encounter state unchanged.
