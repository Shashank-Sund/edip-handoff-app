"""ED to inpatient handoff pipeline (Lakeflow Spark Declarative Pipelines, serverless).

bronze (Zerobus FHIR events, HL7 v2 raw messages, Clarity snapshots)
  -> silver: one normalized schema for both real time feeds (edip_contract), encounter state (AUTO CDC SCD1),
     ADT history (SCD2), handoff trigger events, Clarity history
  -> gold: boarding list

Parsing lives in parse_hl7.py and parse_fhir.py (pure Python, unit tested locally); this file only wires
them into streaming tables. Configuration keys: edip.catalog, edip.code_path (folder holding the parsers).
"""
import json
import sys

from pyspark import pipelines as dp
from pyspark.sql import functions as F
from pyspark.sql import types as T

sys.path.append(spark.conf.get("edip.code_path"))  # noqa: F821 (spark is provided by the pipeline runtime)
from edip_contract import KINDS  # noqa: E402
from parse_fhir import parse_fhir  # noqa: E402
from parse_hl7 import parse_hl7  # noqa: E402

CAT = spark.conf.get("edip.catalog", "edip")  # noqa: F821
BRONZE, SILVER, GOLD = f"{CAT}.bronze", f"{CAT}.silver", f"{CAT}.gold"

INTS, FLOATS, BOOLS = {"esi", "line"}, {"numeric", "ref_low", "ref_high"}, {"validated"}


def _type(field):
    if field.endswith("_ts"):
        return T.TimestampType()
    if field in INTS:
        return T.IntegerType()
    if field in FLOATS:
        return T.DoubleType()
    if field in BOOLS:
        return T.BooleanType()
    return T.StringType()


RECORD = {k: T.StructType([T.StructField(f, _type(f)) for f in fields]) for k, fields in KINDS.items()}
PARSED = T.StructType([T.StructField(k, T.ArrayType(RECORD[k])) for k in KINDS])


def _coerce(out):
    """Parser dicts to UDF rows: exact contract keys, typed values."""
    res = {}
    for kind, fields in KINDS.items():
        rows = []
        for r in out.get(kind, []):
            row = {}
            for f in fields:
                v = r.get(f)
                if v == "":
                    v = None          # empty means absent, so AUTO CDC (ignore nulls) never overwrites a value
                if v is not None and f in INTS:
                    v = int(v)
                elif v is not None and f in FLOATS:
                    v = float(v)
                elif v is not None and f in BOOLS:
                    v = bool(v)
                elif v is not None and not f.endswith("_ts") and not isinstance(v, str):
                    v = str(v)
                row[f] = v
            rows.append(row)
        res[kind] = rows
    return res


def _safe(fn, *args):
    try:
        return _coerce(fn(*args))
    except Exception as exc:  # a message that cannot be parsed is quarantined, never dropped silently
        return {**{k: [] for k in KINDS}, "quarantine": [{"source": None, "msg_key": None, "facility": None,
                                                          "csn": None, "reason": f"parse error: {exc}"[:500],
                                                          "recorded_ts": None}]}


@F.udf(PARSED)
def fhir_udf(meta_json, bundle_json):
    row = json.loads(meta_json)
    row["bundle"] = bundle_json
    return _safe(parse_fhir, row)


@F.udf(PARSED)
def hl7_udf(content, received_ts):
    return _safe(parse_hl7, content, received_ts)


# ------------------------------------------------------------------ parsed feeds
@dp.temporary_view(name="fhir_parsed")
def fhir_parsed():
    src = spark.readStream.table(f"{BRONZE}.redox_fhir_events")  # noqa: F821
    meta = F.to_json(F.struct(*[c for c in src.columns if c != "bundle"]))
    return src.select(fhir_udf(meta, F.expr("to_json(bundle)")).alias("p"), F.col("ingest_ts").alias("landed_ts"))


@dp.temporary_view(name="hl7_parsed")
def hl7_parsed():
    src = spark.readStream.table(f"{BRONZE}.hl7_raw_messages")  # noqa: F821
    return src.select(hl7_udf("content", "engine_received_ts").alias("p"), F.col("inserted").alias("landed_ts"))


def _exploded(kind):
    """Both feeds' records of one kind, with landing and processing times for freshness."""
    def one(view):
        return (spark.readStream.table(view)  # noqa: F821
                .select(F.explode(f"p.{kind}").alias("r"), "landed_ts")
                .select("r.*", "landed_ts", F.current_timestamp().alias("processed_ts")))
    return one("fhir_parsed").unionByName(one("hl7_parsed"))


# ------------------------------------------------------------------ silver: normalized events
@dp.table(name=f"{SILVER}.adt_events", comment="ADT events from both real time feeds, one schema; per source")
def adt_events():
    return (_exploded("adt").withWatermark("recorded_ts", "7 days")
            .dropDuplicatesWithinWatermark(["source", "msg_key"]))          # F02 retransmits and retries


def _second_keys(df, keys):
    """Dedupe keys with timestamps truncated to the second: HL7 carries seconds, FHIR milliseconds."""
    out = []
    for k in keys:
        if k.endswith("_ts"):
            df = df.withColumn(f"_k_{k}", F.date_trunc("second", k))
            out.append(f"_k_{k}")
        else:
            out.append(k)
    return df, out


def _cross_feed(kind, keys, ts):
    """Clinical records arrive on both feeds; keep whichever copy lands first."""
    df, k = _second_keys(_exploded(kind), keys)
    return df.withWatermark(ts, "7 days").dropDuplicatesWithinWatermark(k).drop(*[c for c in k if c.startswith("_k_")])


@dp.table(name=f"{SILVER}.observations", comment="Labs, imaging, ECG and vitals from both feeds")
def observations():
    # Keyed on the component code, not the line: the feeds number lines differently.
    return _cross_feed("obs", ["csn", "category", "order_id", "code", "status", "observed_ts", "resulted_ts"],
                       "resulted_ts")


@dp.table(name=f"{SILVER}.med_events", comment="Medication orders and administrations from both feeds")
def med_events():
    return _cross_feed("med", ["csn", "order_id", "event", "line", "action"], "event_ts")


@dp.table(name=f"{SILVER}.notes", comment="Clinical notes (each version) from both feeds")
def notes():
    return _cross_feed("note", ["csn", "note_id", "status"], "created_ts")


@dp.table(name=f"{SILVER}.allergy_events", comment="Allergies reported on the real time feeds")
def allergy_events():
    return _cross_feed("allergy", ["mrn", "allergen", "recorded_ts"], "recorded_ts")


@dp.table(name=f"{SILVER}.flag_events", comment="Isolation and code status from the real time feeds")
def flag_events():
    return _cross_feed("flag", ["csn", "kind", "value", "recorded_ts"], "recorded_ts")


@dp.table(name=f"{SILVER}.quarantine", comment="Messages that could not be applied, with a reason")
def quarantine():
    return _exploded("quarantine")


# ------------------------------------------------------------------ silver: encounter state
STATUS = F.expr("""CASE kind
    WHEN 'arrival' THEN 'in_ed' WHEN 'pending_admit' THEN 'boarding' WHEN 'bed_assigned' THEN 'boarding'
    WHEN 'cancel_transfer' THEN 'boarding' WHEN 'cancel_discharge' THEN 'in_ed' WHEN 'cancel_admit' THEN 'canceled'
    WHEN 'discharge' THEN 'discharged'
    WHEN 'transfer' THEN CASE WHEN unit = 'ED' THEN NULL ELSE 'inpatient' END END""")


@dp.temporary_view(name="encounter_changes")
def encounter_changes():
    e = spark.readStream.table(f"{SILVER}.adt_events")  # noqa: F821
    return (e.where("csn IS NOT NULL AND effective_ts IS NOT NULL")
            .withColumn("status", STATUS)
            .withColumn("mrn", F.coalesce("surviving_mrn", "mrn"))
            .withColumn("arrival_ts", F.when(F.col("kind") == "arrival", F.col("effective_ts")))
            .withColumn("admit_decision_ts", F.when(F.col("kind") == "pending_admit", F.col("effective_ts")))
            .withColumn("ed_departure_ts", F.when((F.col("kind") == "transfer") & (F.col("unit") != "ED"), F.col("effective_ts")))
            .withColumn("last_event_kind", F.col("kind"))
            .withColumn("last_event_ts", F.col("effective_ts"))
            .select("csn", "facility", "mrn", "patient_name", "birth_date", "sex", "status", "patient_class", "unit", "room",
                    "bed", "pending_unit", "pending_room", "pending_bed", "attending_id", "admitting_id", "service",
                    "level_of_care", "esi", "chief_complaint", "isolation", "code_status", "arrival_ts",
                    "admit_decision_ts", "ed_departure_ts", "last_event_kind", "last_event_ts", "recorded_ts",
                    "landed_ts", "processed_ts"))


dp.create_streaming_table(name=f"{SILVER}.encounter_state", comment="Current state per encounter (SCD1)",
                          table_properties={"delta.enableChangeDataFeed": "true"})
dp.create_auto_cdc_flow(target=f"{SILVER}.encounter_state", source="encounter_changes", keys=["csn"],
                        sequence_by=F.struct("last_event_ts", "recorded_ts"), ignore_null_updates=True,
                        stored_as_scd_type=1)

dp.create_streaming_table(name=f"{SILVER}.adt_history", comment="Location and class history per encounter (SCD2)")
dp.create_auto_cdc_flow(target=f"{SILVER}.adt_history", source="encounter_changes", keys=["csn"],
                        sequence_by=F.struct("last_event_ts", "recorded_ts"), ignore_null_updates=True,
                        stored_as_scd_type=2,
                        track_history_column_list=["status", "patient_class", "unit", "room", "bed", "pending_unit"])


# ------------------------------------------------------------------ silver: handoff triggers
@dp.table(name=f"{SILVER}.handoff_events",
          comment="Deterministic triggers for the handoff generator: start, regenerate, finalize, retract")
def handoff_events():
    adt = (spark.readStream.table(f"{SILVER}.adt_events")  # noqa: F821
           .select("csn", "kind", "effective_ts", "recorded_ts", "processed_ts")
           .withColumn("action", F.expr("""CASE kind
                WHEN 'pending_admit' THEN 'start' WHEN 'class_change' THEN 'regenerate'
                WHEN 'bed_assigned' THEN 'regenerate' WHEN 'bed_unassigned' THEN 'regenerate'
                WHEN 'cancel_transfer' THEN 'regenerate' WHEN 'transfer' THEN 'finalize'
                WHEN 'cancel_admit' THEN 'retract' END"""))
           .where("action IS NOT NULL")
           .withColumn("reason", F.col("kind")))
    crit = (spark.readStream.table(f"{SILVER}.observations")  # noqa: F821
            .where("flag IN ('HH', 'LL')")
            .select("csn", F.lit("critical_result").alias("kind"), F.col("resulted_ts").alias("effective_ts"),
                    F.col("resulted_ts").alias("recorded_ts"), "processed_ts", F.lit("regenerate").alias("action"),
                    F.concat_ws(" ", F.lit("critical"), "name", "value").alias("reason")))
    signed = (spark.readStream.table(f"{SILVER}.notes")  # noqa: F821
              .where("status = 'Signed'")
              .select("csn", F.lit("note_signed").alias("kind"), F.col("signed_ts").alias("effective_ts"),
                      F.col("signed_ts").alias("recorded_ts"), "processed_ts", F.lit("regenerate").alias("action"),
                      F.concat_ws(" ", F.lit("signed"), "note_type").alias("reason")))
    events, keys = _second_keys(adt.unionByName(crit).unionByName(signed), ["csn", "kind", "effective_ts"])
    # A handoff starts and is retracted once per encounter; keying those on the encounter alone also absorbs the
    # feeds disagreeing on the time (seconds apart, or an hour apart when an HL7 feed without UTC offsets sends a
    # time inside the repeated DST hour). Finalize and regenerate stay time keyed (F10 finalizes twice).
    events = events.withColumn("_k_effective_ts", F.when(F.col("action").isin("start", "retract"), F.lit(None))
                               .otherwise(F.col("_k_effective_ts")))
    return (events.withWatermark("effective_ts", "7 days")
            .dropDuplicatesWithinWatermark(keys)                                # the same event on both feeds
            .drop(*[c for c in keys if c.startswith("_k_")]))


# ------------------------------------------------------------------ silver: Clarity history (nightly batch)
@dp.materialized_view(name=f"{SILVER}.problem_list", comment="Active problems from the nightly Clarity extract")
def problem_list():
    p = spark.read.table(f"{BRONZE}.clarity_patient").select("PAT_ID", F.col("PAT_MRN_ID").alias("mrn"))  # noqa: F821
    pl = spark.read.table(f"{BRONZE}.clarity_problem_list")  # noqa: F821
    return pl.join(p, "PAT_ID")


@dp.materialized_view(name=f"{SILVER}.home_meds", comment="Outpatient medications from the nightly Clarity extract")
def home_meds():
    p = spark.read.table(f"{BRONZE}.clarity_patient").select("PAT_ID", F.col("PAT_MRN_ID").alias("mrn"))  # noqa: F821
    om = spark.read.table(f"{BRONZE}.clarity_order_med").where("ORDERING_MODE_C_NAME = 'Outpatient'")  # noqa: F821
    return om.join(p, "PAT_ID")


@dp.materialized_view(name=f"{SILVER}.allergy_history", comment="Allergies from the nightly Clarity extract")
def allergy_history():
    p = spark.read.table(f"{BRONZE}.clarity_patient").select("PAT_ID", F.col("PAT_MRN_ID").alias("mrn"))  # noqa: F821
    return spark.read.table(f"{BRONZE}.clarity_allergy").join(p, "PAT_ID")  # noqa: F821


# ------------------------------------------------------------------ gold
@dp.materialized_view(name=f"{GOLD}.boarding_list",
                      comment="Encounters in the ED or boarding, for the app's board (time based fields at read time)")
def boarding_list():
    return (spark.read.table(f"{SILVER}.encounter_state")  # noqa: F821
            .where("status IN ('in_ed', 'boarding')")
            .select("csn", "facility", "mrn", "patient_name", "birth_date", "sex", "status", "patient_class", "unit",
                    "room", "bed", "pending_unit", "pending_room", "pending_bed", "level_of_care", "service", "esi",
                    "chief_complaint", "isolation", "code_status", "arrival_ts", "admit_decision_ts", "last_event_kind",
                    "last_event_ts"))
