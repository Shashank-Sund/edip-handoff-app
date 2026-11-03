# Streaming the FHIR feed through Zerobus (optional)

Setup loads the FHIR events in one batch by default, so a deploy needs nothing else. In a hospital, events from an integration engine (here, Redox) arrive one at a time; Zerobus is the Databricks service that writes them straight into a Delta table with low latency, without a message broker. This guide replays the synthetic events through Zerobus so the pipeline sees them as a stream.

## How it fits

```
Redox style events (synthetic/out/world/fhir/events.jsonl)
  -> synthetic/replay/zerobus_push.py  (REST ingest, OAuth as a service principal)
  -> <catalog>.bronze.redox_fhir_events (Delta, the same table the batch load fills)
  -> pipeline (silver, gold) -> handoffs -> Lakebase -> app
```

The rows are identical in both paths, so you can switch at any time.

## 1. Deploy without the batch FHIR load

```bash
scripts/deploy.sh dev --var fhir_mode=zerobus      # or add --var fhir_mode=zerobus to each step in setup.md
```

Setup creates the bronze table empty. To switch an existing deploy to Zerobus, run `TRUNCATE TABLE <catalog>.bronze.redox_fhir_events` first: the pipeline removes retransmits that arrive close together, not a second copy of the whole week.

## 2. Create the service principal and its secret

```bash
export DATABRICKS_CONFIG_PROFILE=<profile>
python scripts/setup_zerobus.py --catalog edip
```

The script:

1. creates the service principal `edip-zerobus` (or reuses it);
2. grants it `USE CATALOG`, `USE SCHEMA` on `bronze`, and `SELECT` and `MODIFY` on `bronze.redox_fhir_events` only;
3. creates an OAuth secret;
4. writes `~/.config/edip/zerobus.env` with the client id, secret, workspace URL and id, and the Zerobus endpoint (`https://<workspace-id>.zerobus.<region>.cloud.databricks.com`), readable only by you.

The secret never goes into the repo or the bundle. To rotate it, run the script again.

## 3. Generate the events locally and stream them

```bash
python -m pip install -e .                                   # stdlib only, plus pyyaml
cd synthetic && python -m edip_synth --out out/world --synthea population/synthea_3000.json.gz
python -m edip_synth.render.fhir --truth out/world/truth --out out/world && cd ..
set -a; . ~/.config/edip/zerobus.env; set +a
python synthetic/replay/zerobus_push.py --events synthetic/out/world/fhir/events.jsonl --table edip.bronze.redox_fhir_events
```

About 18,000 events go in batches of up to 4 MB, six requests at a time; a few hundred are retransmits on purpose (fault F02), as a real engine would send. Then run the pipeline and the handoff job (or the whole setup job again with `fhir_mode=zerobus`).

## 4. Measure freshness (optional)

```bash
python synthetic/replay/zerobus_push.py --events synthetic/out/world/fhir/events.jsonl --table edip.bronze.redox_fhir_events --probe 50 --rate 2
```

With the pipeline in continuous mode this sends 50 ADT events at 2 per second and lets you time them from send to `silver.adt_events`. The reference workspace measured a median of 34 seconds.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| HTTP 401 or 403 | The env file is not loaded, the secret was rotated, or a grant is missing; run `setup_zerobus.py` again. |
| HTTP 404 on the table | The table name must be the full `<catalog>.bronze.redox_fhir_events`, created by setup first. |
| Endpoint not reachable | Zerobus is regional; check the region in `ZEROBUS_ENDPOINT` matches your workspace. |
