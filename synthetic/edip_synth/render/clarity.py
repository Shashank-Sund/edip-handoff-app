"""Clarity style nightly batch tables (spec section 7, fault F32).

One snapshot per local midnight in the window. A snapshot holds only what was
documented (recorded, resulted, entered, saved) before that midnight, so day
seven exists only in the streams. Column names follow Epic's EHI pages where
the spec verified them; `_C` codes and the rest are site defined and marked so
in schema.json. Timestamps are written as naive local (America/New_York) or
UTC strings, exactly as Clarity DATETIME columns behave.

    python -m edip_synth.render.clarity --truth DIR --out DIR
"""
import argparse
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .common import TZ, load_truth, ts, write_manifest

UTC = timezone.utc
EPOCH = date(1840, 12, 31)                 # Epic DATE_REAL origin
OUT_OFFSET = 400_000_000                   # Transfer Out companion EVENT_ID = truth EVENT_ID + OUT_OFFSET
HOME_MED_BASE = 600_000_000
PROBLEM_BASE = 300_000_000
NOTE_CSN_BASE = 100_000_000
TYPES = {"bigint": "BIGINT", "int": "INT", "double": "DOUBLE", "string": "STRING", "yn": "STRING",
         "date": "DATE", "ts": "TIMESTAMP", "utc": "TIMESTAMP"}
SOURCE = {"a": "ehi", "b": "clarity_conventional", "c": "site"}

# ----------------------------------------------------------------------------- site code sets (ZC_*)
CODES = {
    "PAT_CLASS": {"Inpatient": 101, "Outpatient": 102, "Emergency": 103, "Observation": 104},
    "BASE_CLASS": {"Direct": 1, "Inpatient": 2, "Outpatient": 3, "Emergency": 4, "Observation": 5},
    "EVENT_TYPE": {"Admission": 1, "Discharge": 2, "Transfer In": 3, "Transfer Out": 4, "Patient Update": 5, "Census": 6,
                   "Hospital Outpatient": 7, "Leave of Absence Out": 8, "Leave of Absence Return": 9,
                   "Leave of Absence Census": 10},
    "EVENT_SUBTYPE": {"Original": 1, "Canceled": 2, "Update": 3},
    "ED_DISPOSITION": {"Admit": 1, "Discharge": 2, "Observation": 3, "Transfer to Another Facility": 4, "AMA": 5,
                       "LWBS": 6, "Eloped": 7, "Expired": 8, "Left Before Treatment Complete": 9},
    "ACUITY_LEVEL": {f"ESI {i}": i for i in range(1, 6)},
    "MAR_RSLT": {"Given": 1, "Missed": 2, "Refused": 3, "Canceled Entry": 4, "Held": 5, "New Bag": 6, "Restarted": 7,
                 "Stopped": 8, "Rate Change": 9},
    "ORDER_STATUS": {"Pending": 1, "Sent": 2, "Resulted": 3, "Canceled": 4, "Completed": 5, "Holding for Referral": 6,
                     "Denied Approval": 7, "Suspend": 8, "Discontinued": 9},
    "LAB_STATUS": {"In process": 1, "Preliminary result": 2, "Final result": 3, "Edited": 4},
    "RESULT_STATUS": {"Preliminary": 2, "Final": 3, "Corrected": 4, "Incomplete": 5},
    "RESULT_FLAG": {"(NONE)": 1, "Abnormal": 2, "Panic": 3, "Low": 4, "High": 5, "Low Panic": 6, "High Panic": 7},
    "NOTE_STATUS": {"Incomplete": 1, "Signed": 2, "Addendum": 3, "Deleted": 4, "Revised": 5, "Cosigned": 6},
    "CODE_STATUS": {"Full Code": 1, "DNR": 2, "Partial Code": 3, "DNR/DNI": 4, "Comfort Care": 5},
    "ISOLATION": {"Contact": 1, "Droplet": 2, "Airborne": 3, "Contact Plus (enteric)": 4},
    "SEX": {"Female": 1, "Male": 2, "Unknown": 3},
    "MARITAL_STATUS": {"Single": 1, "Married": 2, "Divorced": 3, "Widowed": 4},
    "ETHNIC_GROUP": {"Hispanic or Latino": 1, "Not Hispanic or Latino": 2},
    "PATIENT_RACE": {"White": 1, "Black or African American": 2, "Asian": 3,
                     "Native Hawaiian or Other Pacific Islander": 4, "American Indian or Alaska Native": 5, "Other": 6},
    "LANGUAGE": {"English": 1, "Spanish": 2, "Portuguese": 3, "Haitian Creole": 4, "Vietnamese": 5, "Mandarin": 6},
    "PAT_STATUS": {"Alive": 1, "Deceased": 2},
    "STATE": {"Massachusetts": 22},
    "APPT_STATUS": {"Scheduled": 1, "Completed": 2, "Canceled": 3, "No Show": 4, "Left without seen": 5, "Arrived": 6},
    "ENC_TYPE": {"Hospital Encounter": 3, "Emergency": 4, "Office Visit": 101},
    "FIN_CLASS": {"Commercial": 1, "Medicare": 2, "Medicaid": 3, "Self-Pay": 4, "Medicare Advantage": 5, "Other": 6},
    "ADT_PATIENT_STAT": {"Preadmission": 1, "Admission": 2, "Discharged": 3, "Leave of Absence": 4,
                         "Hospital Outpatient Visit": 5},
    "ADMIT_CONF_STAT": {"Confirmed": 1, "Pending": 2, "Canceled": 3, "Completed": 4},
    "MEANS_OF_ARRV": {"Ambulance": 1, "Walk-in": 2, "Private Vehicle": 3, "Police": 4, "Helicopter": 5},
    "LEVEL_OF_CARE": {"Med Surg": 1, "Telemetry": 2, "Stepdown": 3, "ICU": 4, "Observation": 5},
    "HOSP_SERV": {"Hospital Medicine": 1, "Critical Care": 2, "Observation Medicine": 3, "Orthopedics": 4,
                  "Emergency Medicine": 5},
    "ORDER_TYPE": {"Lab": 1, "Imaging": 2, "Consult": 3, "Diet": 4, "Nursing": 5, "ADT": 6, "Code Status": 7},
    "ORDER_CLASS": {"Normal": 1, "Hospital Performed": 2, "Historical Med": 3},
    "ORDER_PRIORITY": {"Routine": 1, "STAT": 2, "Urgent": 3, "Timed": 4, "AM Draw": 5},
    "MED_ROUTE": {"Oral": 1, "Intravenous": 2, "Subcutaneous": 3, "Intramuscular": 4, "Inhalation": 5, "Sublingual": 6,
                  "Topical": 7, "Rectal": 8, "Nasal": 9},
    "ORDERING_MODE": {"Outpatient": 1, "Inpatient": 2},
    "PEND_REQ_STATUS": {"Requested": 1, "Assigned": 2, "Rejected": 3, "Approved": 4},
    "PEND_EVENT_TYPE": {"Admission": 1, "Discharge": 2, "Transfer": 3},
    "ED_PAT_STATUS": {s: i for i, s in enumerate(["Expected", "Waiting Room", "Waiting for Triage", "In Triage",
                                                 "Waiting for Room", "Waiting for Nurse", "Waiting for Physician",
                                                 "With Provider", "Test Results", "Ready for Disposition", "Bed Requested",
                                                 "Ready for Admit", "Waiting for Transport", "In Transport",
                                                 "Ready for Discharge", "Patient Left"], 1)},
    "PROBLEM_STATUS": {"Active": 1, "Resolved": 2, "Deleted": 3},
    "ALLERGY_SEVERITY": {"High": 1, "Medium": 2, "Low": 3},
    "ALRGY_STATUS": {"Active": 1, "Deleted": 2},
    "REACTION": {"Hives": 1, "Rash": 2, "Anaphylaxis": 3, "Nausea and Vomiting": 4, "Swelling": 5, "Shortness of Breath": 6,
                 "Itching": 7, "Other": 8},
    "IP_NOTE_TYPE": {"Progress Notes": 1, "Consults": 2, "H&P": 3, "Discharge Summary": 4, "ED Notes": 5,
                     "ED Triage Notes": 6, "ED Provider Notes": 7, "Nursing Note": 8,
                     "ED to Hosp-Admission Nursing Transfer": 9},
    "TR_TEAM_REL": {"Attending Physician": 1, "Admitting Physician": 2, "Consulting Physician": 3, "Primary Nurse": 4,
                    "Hospitalist": 5, "Resident": 6, "Advanced Practice Provider": 7},
    "INFECTION_TYPE": {"MRSA": 1, "VRE": 2, "C. difficile": 3, "COVID-19": 4, "Influenza": 5, "ESBL": 6, "CRE": 7,
                       "Rule out TB": 8},
    "DISCH_DISP": {"Home": 1, "Transfer to another facility": 2, "Against medical advice": 7, "Left without being seen": 7,
                   "Left before treatment complete": 7},
}
ZC_TABLES = ["PAT_CLASS", "EVENT_TYPE", "EVENT_SUBTYPE", "ED_DISPOSITION", "ACUITY_LEVEL", "MAR_RSLT", "ORDER_STATUS",
             "LAB_STATUS", "RESULT_FLAG", "RESULT_STATUS", "NOTE_STATUS", "CODE_STATUS", "ISOLATION", "ED_PAT_STATUS",
             "LEVEL_OF_CARE", "ADT_PATIENT_STAT", "PEND_REQ_STATUS"]

CLASS_NAME = {"E": "Emergency", "I": "Inpatient", "OBS": "Observation"}
BASE_NAME = {"E": "Emergency", "I": "Inpatient", "OBS": "Outpatient"}
LOC_NAME = {"MEDSURG": "Med Surg", "TELE": "Telemetry", "STEPDOWN": "Stepdown", "ICU": "ICU", "OBS": "Observation"}
ARRIVAL = {"EMS": "Ambulance", "Walk-in": "Walk-in", "Private vehicle": "Private Vehicle", "Police": "Police",
           "Helicopter": "Helicopter"}
ROUTE = {"IV": "Intravenous", "PO": "Oral", "SL": "Sublingual", "INH": "Inhalation", "SC": "Subcutaneous",
         "IM": "Intramuscular", "PR": "Rectal"}
FREQ = {"ONCE": (1, "Once"), "Q4H": (2, "Q4H"), "Q6H": (3, "Q6H"), "Q8H": (4, "Q8H"), "Q12H": (5, "Q12H"),
        "BID": (6, "BID"), "TID": (7, "TID"), "Q24H": (8, "Daily"), "CONTINUOUS": (9, "Continuous"), "PRN": (10, "PRN")}
FLAG = {"": "(NONE)", "H": "High", "L": "Low", "HH": "High Panic", "LL": "Low Panic", "A": "Abnormal"}
RSTAT = {"P": "Preliminary", "F": "Final", "C": "Corrected"}
LABST = {"P": "Preliminary result", "F": "Final result", "C": "Edited"}
DISPO = {"Admit": "Admit", "Observation": "Observation", "Discharge": "Discharge", "Transfer": "Transfer to Another Facility",
         "Home": "Discharge", "Left without being seen": "LWBS", "Against medical advice": "AMA",
         "Left before treatment complete": "Left Before Treatment Complete",
         "Transfer to another facility": "Transfer to Another Facility"}
ED_STATUS = {"Patient Arrived in ED": "Waiting Room", "Triage Started": "In Triage", "Triage Completed": "Waiting for Room",
             "Patient Roomed in ED": "Waiting for Physician", "Provider Assigned": "With Provider",
             "Disposition Selected: Discharge": "Ready for Discharge", "Disposition Selected: Admit": "Ready for Admit",
             "Disposition Selected: Observation": "Ready for Admit", "Disposition Selected: Transfer": "Ready for Disposition",
             "Bed Requested": "Bed Requested", "Bed Assigned": "Waiting for Transport", "Ready for Transport": "In Transport",
             "Patient Departed from ED": "Patient Left", "Patient Discharged": "Patient Left", "Patient Transferred": "Patient Left",
             "Left Without Being Seen": "Patient Left", "Left Against Medical Advice": "Patient Left",
             "Left Before Treatment Complete": "Patient Left"}
RACE = {"white": "White", "black": "Black or African American", "asian": "Asian",
        "hawaiian": "Native Hawaiian or Other Pacific Islander", "native": "American Indian or Alaska Native", "other": "Other"}
MARITAL = {"M": "Married", "S": "Single", "D": "Divorced", "W": "Widowed"}
PAYOR = {"Medicare": 1001, "Medicare Advantage": 1002, "Medicaid": 1003, "Commercial": 1004, "Self-pay": 1005, "Other": 1006}
FIN_CLASS = {"Self-pay": "Self-Pay"}
# Flowsheet rows: (FLO_MEAS_ID, display, Epic style name, value type, units). 5 to 14 are Foundation convention (c).
FLO = [("5", "BP", "R BP", "Blood Pressure", "mmHg"), ("6", "Temp", "R TEMPERATURE", "Temperature", "F"),
       ("8", "Pulse", "R PULSE", "Numeric Type", "/min"), ("9", "Resp", "R RESPIRATIONS", "Numeric Type", "/min"),
       ("10", "SpO2", "R OXYGEN SATURATION", "Numeric Type", "%"), ("11", "Height", "R HEIGHT", "Patient Height", "in"),
       ("14", "Weight", "R WEIGHT", "Patient Weight", "oz"),
       ("3040100003", "MAP", "R MEAN ARTERIAL PRESSURE", "Numeric Type", "mmHg"),
       ("3040100001", "O2 Device", "R O2 DEVICE", "Category Type", ""),
       ("3040100002", "O2 Flow Rate", "R O2 FLOW RATE", "Numeric Type", "L/min"),
       ("3040102607", "Pain Score", "R PAIN SCORE 0-10", "Numeric Type", "{score}"),
       ("3040104328", "GCS Total", "R GLASGOW COMA SCALE TOTAL", "Numeric Type", "{score}")]
EVENT_TYPE = {"arrival": "Admission", "discharge": "Discharge", "roomed": "Transfer In", "transfer": "Transfer In"}
CANCELS = {"cancel_transfer": "transfer", "cancel_discharge": "discharge", "cancel_admit": "arrival"}
LOC_KINDS = {"arrival", "roomed", "transfer", "cancel_transfer", "class_change", "discharge", "cancel_discharge", "cancel_admit"}

# ----------------------------------------------------------------------------- schema
# "COLUMN type source": source a = verified EHI column name, b = conventional production Clarity, c = site defined.
SCHEMA = {
    "PATIENT": ("PAT_ID", """
        PAT_ID string a | PAT_NAME string a | PAT_FIRST_NAME string a | PAT_MIDDLE_NAME string a | PAT_LAST_NAME string a
        PAT_MRN_ID string a | BIRTH_DATE date a | DEATH_DATE date a | SEX_C int c | SEX_C_NAME string a
        ETHNIC_GROUP_C int c | ETHNIC_GROUP_C_NAME string a | LANGUAGE_C int c | LANGUAGE_C_NAME string a
        RELIGION_C_NAME string a | INTRPTR_NEEDED_YN yn a | ADD_LINE_1 string b | CITY string a | STATE_C int c
        STATE_C_NAME string a | ZIP string a | HOME_PHONE string a | EMAIL_ADDRESS string a | SSN string a
        CUR_PCP_PROV_ID string a | PAT_STATUS_C int c | PAT_STATUS_C_NAME string a | MARITAL_STATUS_C int c
        MARITAL_STATUS_C_NAME string b | CREATE_USER_ID string a | ALLERGY_REVIEW_C_NAME string c
        MERGED_INTO_PAT_ID string c | MERGE_INSTANT_DTTM ts c"""),
    "PATIENT_RACE": ("PAT_ID,LINE", "PAT_ID string a | LINE int a | PATIENT_RACE_C int c | PATIENT_RACE_C_NAME string a"),
    "IDENTITY_ID": ("PAT_ID,LINE", "PAT_ID string b | LINE int b | IDENTITY_TYPE_ID int c | IDENTITY_ID string b"),
    "PROBLEM_LIST": ("PROBLEM_LIST_ID", """
        PROBLEM_LIST_ID bigint a | PAT_ID string b | DX_ID bigint a | DESCRIPTION string a | NOTED_DATE date a
        RESOLVED_DATE date a | DATE_OF_ENTRY date a | ENTRY_USER_ID string a | PROBLEM_STATUS_C int c
        PROBLEM_STATUS_C_NAME string a | CHRONIC_YN yn a | CLASS_OF_PROBLEM_C_NAME string a | PRIORITY_C_NAME string a
        PROBLEM_TYPE_C_NAME string a | PROBLEM_CMT string a | OVERVIEW_NOTE_ID string a"""),
    "ALLERGY": ("ALLERGY_ID", """
        ALLERGY_ID bigint a | PAT_ID string b | ALLERGEN_ID bigint a | ALLERGEN_ID_ALLERGEN_NAME string a | REACTION string a
        DATE_NOTED date a | ALRGY_ENTERED_DTTM ts a | ENTRY_USER_ID string a | ALLERGY_SEVERITY_C int c
        ALLERGY_SEVERITY_C_NAME string a | SEVERITY_C_NAME string a | ALRGY_STATUS_C int c | ALRGY_STATUS_C_NAME string a
        ALLERGY_CERTAINTY_C_NAME string a | ALLERGY_SOURCE_C_NAME string a | ALLERGY_PAT_CSN bigint a
        ALLERGEN_CODE string c | ALLERGEN_CODE_SYSTEM string c"""),
    "PAT_ALLERGIES": ("PAT_ID,LINE", "PAT_ID string a | LINE int a | ALLERGY_RECORD_ID bigint a"),
    "ALLERGY_REACTIONS": ("ALLERGY_ID,LINE", "ALLERGY_ID bigint a | LINE int a | REACTION_C int c | REACTION_C_NAME string a"),
    "INFECTIONS": ("INFECTION_ID", """
        INFECTION_ID bigint a | PAT_ID string a | PAT_ENC_CSN_ID bigint a | INFECTION_RECORD_TYPE_C_NAME string a
        INFECTION_TYPE_C int c | INFECTION_TYPE_C_NAME string a | INF_STATUS_C_NAME string a | HOW_ADDED_C_NAME string a
        ONSET_DATE date a | ADD_LOCAL_DTTM ts a | ADD_UTC_DTTM utc a | RESOLVE_UTC_DTTM utc a | EXPIRATION_DATE date a
        DOESNT_EXPIRE_YN yn a | REQUIRED_ISOLATION_OPTION_C_NAME string a | SPECIMEN_TYPE_C_NAME string a
        SPECIMEN_SOURCE_C_NAME string a | COMMENTS string a | ISOLATION_TYPE_C int c | ISOLATION_TYPE_C_NAME string c"""),
    "OCS_CODE_STATUS": ("OCS_ID", """
        OCS_ID bigint a | PATIENT_ID string a | PATIENT_CSN bigint a | CODE_STATUS_C int c | CODE_STATUS_C_NAME string a
        ACTIVATED_INST ts a | INACTIVATED_INST ts a | ACTV_UTC_DTTM utc a | INACTV_UTC_DTTM utc a | CONTEXT_C_NAME string a
        ORDER_ID bigint a | USER_ID string a | COMMENTS string a | OCS_STATUS_C_NAME string a"""),
    "PAT_ENC": ("PAT_ENC_CSN_ID", """
        PAT_ID string a | PAT_ENC_DATE_REAL double a | PAT_ENC_CSN_ID bigint a | CONTACT_DATE date a | HOSP_ADMSN_TIME ts a
        HOSP_DISCHRG_TIME ts a | HOSP_ADMSN_TYPE_C_NAME string a | DEPARTMENT_ID bigint b | VISIT_PROV_ID string a
        PCP_PROV_ID string a | APPT_STATUS_C int c | APPT_STATUS_C_NAME string a | ENC_CLOSED_YN yn a | HSP_ACCOUNT_ID bigint a
        INPATIENT_DATA_ID string a | IP_EPISODE_ID bigint a | FIN_CLASS_C int c | FIN_CLASS_C_NAME string a | BMI double a
        BSA double a | EFFECTIVE_DATE_DTTM ts a | DISCHARGE_DATE_DT date a | ENC_INSTANT ts a | ENC_CREATE_USER_ID string a
        UPDATE_DATE ts a | ENC_TYPE_C int c | ENC_TYPE_C_NAME string b"""),
    "PAT_ENC_HSP": ("PAT_ENC_CSN_ID", """
        PAT_ENC_CSN_ID bigint a | PAT_ID string a | ADT_PAT_CLASS_C int c | ADT_PAT_CLASS_C_NAME string a
        ADT_PATIENT_STAT_C int c | ADT_PATIENT_STAT_C_NAME string a | ADT_ARRIVAL_TIME ts a | ADT_ARRIVAL_STS_C_NAME string a
        HOSP_ADMSN_TIME ts a | HOSP_DISCH_TIME ts a | ADMIT_CONF_STAT_C int c | ADMIT_CONF_STAT_C_NAME string a
        ADMIT_SOURCE_C_NAME string a | ADMIT_CATEGORY_C_NAME string a | HOSP_ADMSN_TYPE_C_NAME string a
        MEANS_OF_ARRV_C int c | MEANS_OF_ARRV_C_NAME string a | ACUITY_LEVEL_C int c | ACUITY_LEVEL_C_NAME string a
        ED_EPISODE_ID bigint a | ED_DISPOSITION_C int c | ED_DISPOSITION_C_NAME string a | ED_DISP_TIME ts a
        ED_DEPARTURE_TIME ts a | TRIAGE_DATETIME ts a | TRIAGE_STATUS_C_NAME string a | MSE_DATE ts a | EMER_ADM_DATE ts a
        EMER_ADM_EVENT_ID bigint a | INP_ADM_DATE ts a | INP_ADM_EVENT_ID bigint a | INP_ADM_EVENT_DATE ts a
        OP_ADM_DATE ts a | OP_ADM_EVENT_ID bigint a | INP_DWNGRD_EVNT_ID bigint a | INP_DWNGRD_DATE ts a
        LEVEL_OF_CARE_C int c | LEVEL_OF_CARE_C_NAME string a | HOSP_SERV_C int c | HOSP_SERV_C_NAME string a
        ACCOMMODATION_C_NAME string a | DEPARTMENT_ID bigint b | ROOM_ID bigint b | BED_ID bigint b | INPATIENT_DATA_ID string a
        DISCH_DISP_C int c | DISCH_DISP_C_NAME string a | DISCH_DEST_C_NAME string a | EXP_ADMISSION_TIME ts a
        EXP_DISCHARGE_DATE date a | EXP_LEN_OF_STAY double a | PENDING_DISCH_TIME ts a | CHIEF_COMPLAINT_C_NAME string a
        ER_INJURY string a | ADMIT_PROV_TEXT string a | ATTEND_PROV_TEXT string a | BILL_ATTEND_PROV_ID string a"""),
    "PAT_ENC_HSP_2": ("PAT_ENC_CSN_ID", """
        PAT_ENC_CSN_ID bigint a | ED_DISPO_PAT_COND_C_NAME string a | ED_ENC_SRC_C_NAME string a | PRI_PROBLEM_ID bigint a
        PREDICTED_LOS double a | EXPECTED_DISCH_DISP_C_NAME string a | MED_READINESS_YN yn a | ADT_ARRIVAL_UTC_DTTM utc a
        ED_DEPART_UTC_DTTM utc a | HOSP_ADMSN_UTC_DTTM utc a | HOSP_DISCH_UTC_DTTM utc a | INP_ADMSN_UTC_DTTM utc a"""),
    "HSP_ACCOUNT": ("HSP_ACCOUNT_ID", """
        HSP_ACCOUNT_ID bigint a | PAT_ID string a | ACCT_CLASS_HA_C_NAME string a | ACCT_FIN_CLASS_C_NAME string a
        ACCT_BILLSTS_HA_C_NAME string a | ADM_DATE_TIME ts a | DISCH_DATE_TIME ts a | ER_ADMIT_DATE_TIME ts a
        ER_DSCHG_DATE_TIME ts a | ATTENDING_PROV_ID string a | ADMISSION_SOURCE_C_NAME string a
        ADMISSION_TYPE_C_NAME string a | PATIENT_STATUS_C_NAME string a | DISCH_DEPT_ID bigint a | PRIM_SVC_HA_C_NAME string a
        FINAL_DRG_ID bigint a | PRIMARY_PAYOR_ID bigint a | CODING_STATUS_C_NAME string a"""),
    "PAT_ENC_DX": ("PAT_ENC_CSN_ID,LINE", """
        PAT_ENC_CSN_ID bigint a | LINE int a | CONTACT_DATE date a | DX_ID bigint a | PRIMARY_DX_YN yn a | DX_ED_YN yn a
        DX_CHRONIC_YN yn a | DX_QUALIFIER_C_NAME string a | ANNOTATION string a"""),
    "PAT_ENC_RSN_VISIT": ("PAT_ENC_CSN_ID,LINE", """
        PAT_ENC_CSN_ID bigint a | LINE int a | ENC_REASON_ID bigint b | ENC_REASON_ID_REASON_VISIT_NAME string a
        COMMENTS string a | RFV_ONSET_DT date a | BODY_LOC_ID bigint a"""),
    "HSP_ADMIT_DIAG": ("PAT_ENC_CSN_ID,LINE", "PAT_ENC_CSN_ID bigint a | LINE int a | DX_ID bigint a | ADMIT_DIAG_TEXT string a"),
    "HSP_ATND_PROV": ("PAT_ENC_CSN_ID,LINE", """
        PAT_ENC_CSN_ID bigint a | LINE int a | ATTEND_FROM_DATE ts a | ATTEND_TO_DATE ts a | PROV_ID string a | ED_ATTEND_YN yn a"""),
    "TREATMENT_TEAM": ("PAT_ENC_CSN_ID,LINE", """
        PAT_ENC_CSN_ID bigint a | LINE int a | TR_TEAM_ID string a | TR_TEAM_REL_C int c | TR_TEAM_REL_C_NAME string a
        TR_TEAM_SPEC_C_NAME string a | TR_TEAM_BEG_DTTM ts a | TR_TEAM_END_DTTM ts a | TR_TEAM_ED_YN yn a | TR_TEAM_ISDE_YN yn a"""),
    "CLARITY_ADT": ("EVENT_ID", """
        EVENT_ID bigint a | EVENT_TYPE_C int c | EVENT_TYPE_C_NAME string a | EVENT_SUBTYPE_C int c
        EVENT_SUBTYPE_C_NAME string a | PAT_ID string a | PAT_ENC_CSN_ID bigint a | EFFECTIVE_TIME ts a | EVENT_TIME ts a
        EFFECTIVE_UTC_DTTM utc a | DELETE_TIME ts a | USER_ID string a | DEPARTMENT_ID bigint b | ROOM_ID bigint b
        BED_ID bigint a | BED_CSN_ID bigint a | ROOM_CSN_ID bigint a | BED_STATUS_C_NAME string a | STATUS_OF_BED_C_NAME string a
        PAT_CLASS_C int c | PAT_CLASS_C_NAME string a | BASE_PAT_CLASS_C int c | BASE_PAT_CLASS_C_NAME string a
        FROM_BASE_CLASS_C int c | FROM_BASE_CLASS_C_NAME string a | TO_BASE_CLASS_C int c | TO_BASE_CLASS_C_NAME string a
        PAT_SERVICE_C int c | PAT_SERVICE_C_NAME string a | PAT_LVL_OF_CARE_C int c | PAT_LVL_OF_CARE_C_NAME string a
        ACCOMMODATION_C_NAME string a | XFER_EVENT_ID bigint a | XFER_IN_EVENT_ID bigint a | NEXT_OUT_EVENT_ID bigint a
        LAST_IN_EVENT_ID bigint a | CANC_EVENT_ID bigint a | ORIGINAL_EVENT_ID bigint a | FIRST_IP_IN_IP_YN yn a
        ORDER_ID bigint a | ACTION_SOURCE_C_NAME string a"""),
    "PEND_ACTION": ("PEND_ID", """
        PEND_ID string a | PEND_EVENT_TYPE_C int c | PEND_EVENT_TYPE_C_NAME string a | PEND_RECRD_TYPE_C_NAME string a
        PAT_ID string a | PAT_ENC_CSN_ID bigint a | START_TIME ts a | END_TIME ts a | UNIT_ID bigint a | ROOM_ID bigint a
        BED_ID bigint a | PAT_CLASS_C int c | PAT_CLASS_C_NAME string a | PAT_SERVICE_C_NAME string a | LVL_OF_CARE_C int c
        LVL_OF_CARE_C_NAME string a | ACCOMMODATION_C_NAME string a | ACUITY_C_NAME string a | DX_ID bigint a
        ADM_COND_C_NAME string a | EXPECT_LEN_OF_STAY double a | REQUEST_TIME ts a | REQ_BY_USR_ID string a
        ASSIGNED_TIME ts a | APPROVED_TIME ts a | REJECTED_TIME ts a | READY_TO_PLAN_DTTM ts a | PEND_REQ_STATUS_C int c
        PEND_REQ_STATUS_C_NAME string a | REQUEST_URGENCY_C_NAME string a | IS_DELETED_YN yn a | DELETE_TIME ts a
        DELETE_CAUSE_C_NAME string a | LINKED_EVENT_ID bigint a | COMPLETED_YN yn a | CANCEL_REASON_C_NAME string a
        ADT_ORDER_ID bigint a | PEND_ATND_PROV_ID string a | ADT_PND_ADMITNG_ID string a | ISOLATION_C_NAME string c"""),
    "ED_IEV_PAT_INFO": ("EVENT_ID", """
        EVENT_ID string a | PAT_ID string a | PAT_ENC_CSN_ID bigint a | CONTACT_DATE date a | DEPT_EVENT_DEP_ID bigint a
        REG_WAIT_ARRV_DTTM ts a | REG_WAIT_STATE_C_NAME string a | TRANSFER_STATUS_C_NAME string a | CREATE_DTTM ts a
        UPDATE_DATE ts a"""),
    "ED_IEV_EVENT_INFO": ("EVENT_ID,LINE", """
        EVENT_ID string a | LINE int a | EVENT_DISPLAY_NAME string a | EVENT_TIME ts a | EVENT_RECORD_TIME ts a
        EVENT_USER_ID string a | EVENT_PROV_ID string a | EVENT_DEPT_ID bigint a | EVENT_CMT string a | ADT_EVENT_ID bigint a
        EVENT_STATUS_C_NAME string a | TRANSFER_DEST_DEPT_ID bigint a | ED_C_CLIENT_SRC_C_NAME string a"""),
    "ED_PAT_STATUS": ("INPATIENT_DATA_ID,LINE", """
        INPATIENT_DATA_ID string a | LINE int a | ED_PAT_STATUS_C int c | ED_PAT_STATUS_C_NAME string a | PAT_STATUS_TIME ts a
        PAT_STATUS_USER_ID string a | PAT_ENC_CSN_ID bigint a"""),
    "ED_DISPO_AUDIT": ("PAT_ENC_CSN_ID,LINE", """
        PAT_ENC_CSN_ID bigint a | LINE int a | ED_DISPOSITION_C int c | ED_DISPOSITION_C_NAME string a | ED_DISP_INST ts a
        USER_ID string a"""),
    "ORDER_PROC": ("ORDER_PROC_ID", """
        ORDER_PROC_ID bigint a | PAT_ID string a | PAT_ENC_CSN_ID bigint a | PAT_ENC_DATE_REAL double a | ORDERING_DATE date a
        ORDER_TIME ts a | ORDER_INST ts a | RESULT_TIME ts a | PROC_START_TIME ts a | PROC_ENDING_TIME ts a | PROC_ID bigint b
        PROC_CODE string b | DESCRIPTION string a | DISPLAY_NAME string a | ORDER_TYPE_C int c | ORDER_TYPE_C_NAME string a
        ORDER_CLASS_C int c | ORDER_CLASS_C_NAME string a | ORDER_STATUS_C int c | ORDER_STATUS_C_NAME string a
        LAB_STATUS_C int c | LAB_STATUS_C_NAME string a | RADIOLOGY_STATUS_C_NAME string a | ORDER_PRIORITY_C int c
        ORDER_PRIORITY_C_NAME string a | ABNORMAL_YN yn a | IS_PENDING_ORD_YN yn a | AUTHRZING_PROV_ID string a
        BILLING_PROV_ID string a | ORD_CREATR_USER_ID string a | REASON_FOR_CANC_C_NAME string a | FUTURE_OR_STAND string a
        SPECIMEN_TYPE_C_NAME string a | SPECIMEN_SOURCE_C_NAME string a | SPECIMN_TAKEN_TIME ts b | RESULT_LAB_ID bigint a
        HV_HOSPITALIST_YN yn a | CHNG_ORDER_PROC_ID bigint a | SESSION_KEY string a | ACCESSION_NUM string c"""),
    "ORDER_RESULTS": ("ORDER_PROC_ID,ORD_DATE_REAL,LINE", """
        ORDER_PROC_ID bigint a | ORD_DATE_REAL double a | LINE int a | PAT_ENC_CSN_ID bigint a | RESULT_DATE ts a
        COMPONENT_ID bigint a | COMPONENT_ID_NAME string a | ORD_VALUE string a | ORD_NUM_VALUE double a
        RESULT_FLAG_C int c | RESULT_FLAG_C_NAME string a | REFERENCE_LOW string a | REFERENCE_HIGH string a
        REFERENCE_UNIT string a | REF_NORMAL_VALS string a | RESULT_STATUS_C int a | RESULT_STATUS_C_NAME string a
        LAB_STATUS_C_NAME string a | COMP_OBS_INST_TM ts a | COMP_ANL_INST_TM ts a | RESULT_IN_RANGE_YN yn a
        INTERFACE_YN yn a | COMPONENT_COMMENT string a | COMPON_LNC_ID string a | RESULTING_LAB_ID bigint a
        ORGANISM_QUANTITY string a | LRR_BASED_ORGAN_ID bigint a"""),
    "ORDER_MED": ("ORDER_MED_ID", """
        ORDER_MED_ID bigint a | PAT_ID string a | PAT_ENC_CSN_ID bigint a | ORDERING_DATE date a | ORDER_INST ts a
        ORDER_START_TIME ts a | ORDER_END_TIME ts a | START_DATE date a | END_DATE date a | DESCRIPTION string a
        DISPLAY_NAME string a | MEDICATION_ID bigint b | SIG string b | HV_DISCRETE_DOSE string a | MIN_DISCRETE_DOSE double a
        MAX_DISCRETE_DOSE double a | DOSE_UNIT_C_NAME string a | HV_DOSE_UNIT_C_NAME string a | HV_DISCR_FREQ_ID bigint a
        HV_DISCR_FREQ_ID_FREQ_NAME string a | MED_ROUTE_C int c | MED_ROUTE_C_NAME string a | DOSAGE string a
        QUANTITY string a | REFILLS string a | ORDER_CLASS_C int c | ORDER_CLASS_C_NAME string a | ORDER_STATUS_C int c
        ORDER_STATUS_C_NAME string a | ACT_ORDER_C_NAME string a | ORDERING_MODE_C int c | ORDERING_MODE_C_NAME string a
        ORDER_PRIORITY_C_NAME string a | AUTHRZING_PROV_ID string a | ORD_PROV_ID string a | MED_PRESC_PROV_ID string a
        DISCON_TIME ts a | RSN_FOR_DISCON_C_NAME string a | CHNG_ORDER_MED_ID bigint a | IS_PENDING_ORD_YN yn a
        NON_FORMULARY_YN yn a | WEIGHT_BASED_YN yn a | ORD_TM_WEIGHT double a | MED_COMMENTS string a | LASTDOSE ts a
        INFORMANT_C_NAME string a | RESUME_STATUS_C_NAME string a | PHARMACY_ID bigint a"""),
    "ORDER_MEDINFO": ("ORDER_MED_ID", """
        ORDER_MED_ID bigint a | LAST_ADMIN_INST ts a | ADMIN_MIN_DOSE double a | ADMIN_MAX_DOSE double a
        ADMIN_DOSE_UNIT_C_NAME string a"""),
    "MAR_ADMIN_INFO": ("ORDER_MED_ID,LINE", """
        ORDER_MED_ID bigint a | LINE int a | TAKEN_TIME ts a | SCHEDULED_TIME ts a | MAR_ORIG_DUE_TM ts a | SAVED_TIME ts a
        MAR_ACTION_C int c | MAR_ACTION_C_NAME string a | SIG string a | DOSE_UNIT_C_NAME string a | ROUTE_C int c
        ROUTE_C_NAME string a | SITE_C_NAME string a | INFUSION_RATE double a | MAR_INF_RATE_UNIT_C_NAME string a
        MAR_DURATION double a | MAR_DURATION_UNIT_C_NAME string a | CONCENTRATION string a | USER_ID string a
        SEC_USER_ID string a | MAR_DOC_USER_ID string a | REASON_C_NAME string a | COMMENTS string a
        MAR_ADMIN_DEPT_ID bigint a | SCAN_MODE_YN yn a | MORPHINE_EQUIV_MG_DOSE double a | DEV_RECV_TIME ts a"""),
    "IP_FLWSHT_REC": ("FSD_ID", "FSD_ID string a | INPATIENT_DATA_ID string a | RECORD_DATE date a | PAT_ID string a"),
    "IP_FLWSHT_MEAS": ("FSD_ID,LINE", """
        FSD_ID string a | LINE int a | FLO_MEAS_ID string a | FLO_MEAS_ID_DISP_NAME string a | OCCURANCE int a
        RECORDED_TIME ts a | ENTRY_TIME ts a | TAKEN_USER_ID string a | ENTRY_USER_ID string a | MEAS_VALUE string b
        MEAS_COMMENT string a | EDITED_LINE int a | ISACCEPTED_YN yn a | ABNORMAL_C_NAME string a
        ABNORMAL_TYPE_C_NAME string a | CAPTURE_DEVICE_ID string a | RECEIVED_INSTANT ts a | FLT_ID string a
        DOC_METHOD_C_NAME string a | NEEDS_COSIGN_C_NAME string a"""),
    "IP_FLO_GP_DATA": ("FLO_MEAS_ID", """
        FLO_MEAS_ID string a | DISP_NAME string a | FLO_MEAS_NAME string b | VAL_TYPE_C_NAME string b | UNITS string b"""),
    "HNO_INFO": ("NOTE_ID", """
        NOTE_ID string a | PAT_ID string b | PAT_ENC_CSN_ID bigint a | IP_NOTE_TYPE_C int c | IP_NOTE_TYPE_C_NAME string a
        ENTRY_USER_ID string a | CURRENT_AUTHOR_ID string a | CREATE_INSTANT_DTTM utc a | CRT_INST_LOCAL_DTTM ts a
        DATE_OF_SERVIC_DTTM ts a | LST_FILED_INST_DTTM ts a | UNSIGNED_YN yn a | SIGNED_NOTE_ID string a
        COSIGNED_NOTE_LINK string a | DELETE_INSTANT_DTTM utc a | NOTE_DESC string a | PRIORITY_YN yn a"""),
    "NOTE_ENC_INFO": ("CONTACT_SERIAL_NUM", """
        CONTACT_SERIAL_NUM bigint a | NOTE_ID string a | CONTACT_DATE_REAL double a | CONTACT_DATE date a
        PAT_ENC_CSN_ID bigint a | AUTHOR_USER_ID string a | AUTH_LNKED_PROV_ID string a | AUTHOR_SERVICE_C_NAME string a
        AUTHOR_PRVD_TYPE_C_NAME string a | NOTE_STATUS_C int c | NOTE_STATUS_C_NAME string a | NOTE_TYPE_C_NAME string a
        NOTE_FORMAT_C_NAME string a | ENTRY_INSTANT_DTTM ts a | NOTE_FILE_TIME_DTTM utc a | NOT_FILETM_LOC_DTTM ts a
        SPEC_NOTE_TIME_DTTM ts a | UPD_AUTHOR_INS_DTTM ts a | COSIGN_INSTANT_DTTM ts a | COSIGN_REQUIRED_C_NAME string a
        SENSITIVE_STAT_C_NAME string a"""),
    "HNO_NOTE_TEXT": ("NOTE_CSN_ID,LINE", """
        NOTE_ID string b | LINE int b | CONTACT_DATE_REAL double b | NOTE_CSN_ID bigint b | NOTE_TEXT string b
        IS_ARCHIVED_YN yn b | CONTACT_DATE date b | CHRON_ITEM_NUM int b"""),
    "CLARITY_DEP": ("DEPARTMENT_ID", """
        DEPARTMENT_ID bigint a | DEPARTMENT_NAME string a | EXTERNAL_NAME string a | SPECIALTY string a | REV_LOC_ID bigint a
        SERV_AREA_ID bigint a | DEPT_ABBREVIATION string a"""),
    "CLARITY_LOC": ("LOC_ID", "LOC_ID bigint a | LOC_NAME string a | HOSP_PARENT_LOC_ID bigint a"),
    "CLARITY_ROM": ("ROOM_ID", """
        ROOM_ID bigint a | ROOM_NAME string a | DEPARTMENT_ID bigint a | ROOM_CSN_ID bigint a | PRIVATE_YN yn c"""),
    "CLARITY_BED": ("BED_ID", """
        BED_ID bigint a | BED_LABEL string a | ROOM_ID bigint a | BED_STATUS_C_NAME string a | CENSUS_INCLUSN_YN yn a"""),
    "CLARITY_SER": ("PROV_ID", """
        PROV_ID string a | PROV_NAME string a | PROV_TYPE string a | ACTIVE_STATUS string a | NPI string b | USER_ID string b"""),
    "CLARITY_SER_SPEC": ("PROV_ID,LINE", "PROV_ID string b | LINE int b | SPECIALTY_C_NAME string b"),
    "CLARITY_EMP": ("USER_ID", "USER_ID string b | NAME string b | PROV_ID string b"),
    "CLARITY_EDG": ("DX_ID", """
        DX_ID bigint a | DX_NAME string a | CURRENT_ICD10_LIST string b | REF_BILL_CODE string b | SNOMED_CONCEPT_ID string c"""),
    "CLARITY_EAP": ("PROC_ID", "PROC_ID bigint b | PROC_NAME string b | PROC_CODE string b"),
    "CLARITY_COMPONENT": ("COMPONENT_ID", "COMPONENT_ID bigint a | NAME string a | BASE_NAME string b | DFLT_LNC_ID string b"),
    "CLARITY_MEDICATION": ("MEDICATION_ID", "MEDICATION_ID bigint a | NAME string a | GENERIC_NAME string a"),
    "RXNORM_CODES": ("MEDICATION_ID,LINE", """
        MEDICATION_ID bigint b | LINE int b | RXNORM_CODE string b | RXNORM_TERM_TYPE_C_NAME string b"""),
    "CLARITY_EPM": ("PAYOR_ID", "PAYOR_ID bigint b | PAYOR_NAME string b"),
}
for _z in ZC_TABLES:
    SCHEMA[f"ZC_{_z}"] = (f"{_z}_C", f"{_z}_C int c | NAME string c | ABBR string c")

# The column that decides snapshot membership (documentation time), per table.
AS_OF = {"CLARITY_ADT": "EVENT_TIME", "ED_IEV_EVENT_INFO": "EVENT_RECORD_TIME", "ED_PAT_STATUS": "PAT_STATUS_TIME",
         "ED_DISPO_AUDIT": "ED_DISP_INST", "ORDER_PROC": "ORDER_INST", "ORDER_RESULTS": "COMP_ANL_INST_TM",
         "ORDER_MED": "ORDER_INST", "MAR_ADMIN_INFO": "SAVED_TIME", "IP_FLWSHT_MEAS": "ENTRY_TIME",
         "HNO_INFO": "CRT_INST_LOCAL_DTTM", "NOTE_ENC_INFO": "ENTRY_INSTANT_DTTM", "ALLERGY": "ALRGY_ENTERED_DTTM",
         "OCS_CODE_STATUS": "ACTIVATED_INST", "INFECTIONS": "ADD_LOCAL_DTTM", "PEND_ACTION": "REQUEST_TIME",
         "PAT_ENC": "ENC_INSTANT", "PAT_ENC_HSP": "ADT_ARRIVAL_TIME", "ED_IEV_PAT_INFO": "CREATE_DTTM"}
# Columns allowed to hold a time after the snapshot midnight (schedules, expectations).
FUTURE_OK = {"SCHEDULED_TIME", "MAR_ORIG_DUE_TM", "ORDER_END_TIME", "END_DATE", "EXP_ADMISSION_TIME", "EXP_DISCHARGE_DATE",
             "PENDING_DISCH_TIME", "EXPIRATION_DATE", "RESOLVED_DATE"}


def columns(table):
    """[(name, type token, source letter)] for a table."""
    out = []
    for part in SCHEMA[table][1].replace("\n", "|").split("|"):
        if part.strip():
            name, typ, src = part.split()
            out.append((name, typ, src))
    return out


COLS = {t: [c[0] for c in columns(t)] for t in SCHEMA}


def row(table, **kw):
    bad = set(kw) - set(COLS[table])
    if bad:
        raise KeyError(f"{table}: unknown columns {sorted(bad)}")
    return {c: kw.get(c) for c in COLS[table]}


def C(prefix, cat, name):
    """The _C plus _C_NAME column pair for a site code set."""
    return {f"{prefix}_C": CODES[cat].get(name) if name is not None else None, f"{prefix}_C_NAME": name}


# ----------------------------------------------------------------------------- time helpers
def loc(dt):
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M:%S") if dt else None


def utc(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") if dt else None


def day(dt):
    return dt.astimezone(TZ).strftime("%Y-%m-%d") if dt else None


def dreal(dt):
    return float((dt.astimezone(TZ).date() - EPOCH).days) if dt else None


def parse(rows, *fields):
    for r in rows:
        for f in fields:
            r[f] = ts(r[f])
    return rows


def num(csn, base):
    return int(csn) % 10 ** 9 + base


# ----------------------------------------------------------------------------- truth index
class Truth:
    def __init__(self, t):
        self.t = t
        self.cfg = t["config"]
        start = datetime.fromisoformat(self.cfg["window_start"]).astimezone(TZ)
        end = start + timedelta(days=self.cfg["days"])          # local wall clock, across the DST change
        # Nightly extracts: every local midnight from the start date up to the window end.
        first = datetime(start.year, start.month, start.day, tzinfo=TZ)
        self.midnights = [m for m in (first + timedelta(days=i) for i in range(self.cfg["days"] + 1)) if m <= end]
        self.end = end.astimezone(UTC)
        self.pats = {p["pat_id"]: p for p in parse(t["patients"], "merged_at")}
        self.encs = {e["csn"]: e for e in parse(t["encounters"], "arrival_at", "triage_at", "roomed_at", "provider_at",
                                                "decision_at", "bed_request_at", "bed_assigned_at", "departure_at",
                                                "class_change_at")}
        self.by_csn = {}
        for name, fields in (("adt", ("effective_at", "recorded_at")), ("ed_events", ("event_at", "recorded_at")),
                             ("bed_requests", ("requested_at", "assigned_at", "completed_at")),
                             ("orders", ("ordered_at", "collected_at")), ("med_orders", ("ordered_at", "verified_at")),
                             ("vitals", ("taken_at", "entered_at")), ("notes", ("created_at", "service_at", "signed_at")),
                             ("code_status", ("activated_at",)), ("infections", ("added_at",))):
            d = defaultdict(list)
            for r in parse(t[name], *fields):
                d[r["csn"]].append(r)
            self.by_csn[name] = d
        self.results = defaultdict(list)
        for r in parse(t["results"], "observed_at", "resulted_at"):
            self.results[r["order_id"]].append(r)
        self.mar = defaultdict(list)
        for r in parse(t["mar"], "taken_at", "scheduled_at"):
            self.mar[r["order_med_id"]].append(r)
        self.allergies = defaultdict(list)
        for r in parse(t["allergies"], "noted_at"):
            self.allergies[r["pat_id"]].append(r)
        self.infections_by_pat = defaultdict(list)
        for r in t["infections"]:
            self.infections_by_pat[r["pat_id"]].append(r)
        self.prov = {p["prov_id"]: p for p in t["providers"]}
        self.dept = {d["dept_id"]: d for d in t["departments"]}
        self.dept_by_code = {(d["facility"], d["code"]): d for d in t["departments"]}
        self.loc_id = {f["code"]: 101 + i for i, f in enumerate(t["facilities"])}
        # Patients created by a quick registration inside the window (merged duplicates, F12).
        self.pat_created = {e["registered_pat_id"]: e["arrival_at"] for e in self.encs.values() if e["registered_pat_id"]}
        self._dims()

    # ---- dimensions shared by every snapshot
    def _dims(self):
        self.rooms, self.beds, self.room_private = {}, {}, {}
        rid, bid = 200000, 300000
        for d in self.t["departments"]:
            seen = []
            for room, bed, private in d["rooms"]:
                key = (d["facility"], d["code"], room)
                if key not in self.rooms:
                    rid += 1
                    self.rooms[key] = rid
                    self.room_private[rid] = private
                    seen.append(room)
                bid += 1
                self.beds[key + (bed,)] = bid
            if d["level"] == "ED":
                for room in ["WR"] + [f"H{i}" for i in range(1, 9)]:
                    rid += 1
                    self.rooms[(d["facility"], d["code"], room)] = rid
                    self.room_private[rid] = False
        dx = set()
        for e in self.encs.values():
            dx.update((icd, sno, disp) for icd, sno, disp in e["dx"])
        for p in self.pats.values():
            dx.update((q["icd10"] or "", q["snomed"] or "", q["display"]) for q in p["problems"])
        self.dx_id = {k: 400000 + i for i, k in enumerate(sorted(dx))}
        procs = sorted({(o["code"], o["name"]) for o in self.t["orders"]})
        self.proc_id = {k: 500000 + i for i, k in enumerate(procs)}
        comps = {}
        for r in self.t["results"]:
            comps.setdefault(r["lab"], (r["name"], r["loinc"]))
        self.comp_id = {k: 1230000000 + i for i, k in enumerate(sorted(comps))}
        self.comp = comps
        meds = {(m["rxcui"], m["name"]) for m in self.t["med_orders"]}
        meds |= {(m["rxcui"], m["display"]) for p in self.pats.values() for m in p["home_meds"]}
        self.med_id = {k: 60000 + i for i, k in enumerate(sorted(meds))}
        allergens = sorted({(a["allergen"], a["code"], a["system"]) for a in self.t["allergies"]})
        self.allergen_id = {k: 20000 + i for i, k in enumerate(allergens)}
        reasons = sorted({e["chief_complaint"] for e in self.encs.values()})
        self.reason_id = {k: 70000 + i for i, k in enumerate(reasons)}

    def dxid(self, icd, sno, disp):
        return self.dx_id.get((icd or "", sno or "", disp))

    def room(self, fac, unit, room):
        return self.rooms.get((fac, unit, room)) if room else None

    def bed(self, fac, unit, room, bed):
        return self.beds.get((fac, unit, room, bed)) if room and bed else None

    def user(self, prov_id):
        p = self.prov.get(prov_id)
        return p["user_id"] if p else (prov_id or None)

    def prov_name(self, prov_id):
        p = self.prov.get(prov_id)
        return f"{p['last'].upper()}, {p['first'].upper()}" if p else None

    def dept_of(self, fac, unit):
        d = self.dept_by_code.get((fac, unit))
        return d["dept_id"] if d else None


# ----------------------------------------------------------------------------- one snapshot
class Snapshot:
    def __init__(self, tr: Truth, cut: datetime):
        self.tr = tr
        self.cut = cut
        self.rows = defaultdict(list)

    def b4(self, dt):
        return dt is not None and dt < self.cut

    def pat(self, pat_id):
        """PAT_ID as Clarity shows it at the cutoff: a merged duplicate points to the survivor."""
        p = self.tr.pats.get(pat_id)
        if p and p["merged_into"] and self.b4(p["merged_at"]):
            return p["merged_into"]
        return pat_id

    def enc_pat(self, e):
        if e["registered_pat_id"] and not self.b4(self.tr.pats[e["registered_pat_id"]]["merged_at"]):
            return e["registered_pat_id"]
        return e["pat_id"]

    def add(self, table, **kw):
        self.rows[table].append(row(table, **kw))

    def build(self):
        tr = self.tr
        self.dimensions()
        for p in tr.t["patients"]:
            created = tr.pat_created.get(p["pat_id"])
            if created is None or self.b4(created):
                self.patient(p)
        for e in tr.t["encounters"]:
            self.encounter(e)
        return self.rows

    # ---- dimensions
    def dimensions(self):
        tr = self.tr
        for f in tr.t["facilities"]:
            self.add("CLARITY_LOC", LOC_ID=tr.loc_id[f["code"]], LOC_NAME=f["name"].upper(), HOSP_PARENT_LOC_ID=tr.loc_id[f["code"]])
        for d in tr.t["departments"]:
            spec = {"ED": "Emergency Medicine", "ICU": "Critical Care", "OBS": "Observation Medicine"}.get(d["level"], "Internal Medicine")
            self.add("CLARITY_DEP", DEPARTMENT_ID=int(d["dept_id"]), DEPARTMENT_NAME=d["name"], EXTERNAL_NAME=d["name"][4:].title(),
                     SPECIALTY=spec, REV_LOC_ID=tr.loc_id[d["facility"]], SERV_AREA_ID=10, DEPT_ABBREVIATION=d["code"])
        for (fac, unit, room), rid in tr.rooms.items():
            self.add("CLARITY_ROM", ROOM_ID=rid, ROOM_NAME=room, DEPARTMENT_ID=int(tr.dept_of(fac, unit)), ROOM_CSN_ID=rid * 10 + 1,
                     PRIVATE_YN="Y" if tr.room_private[rid] else "N")
        for (fac, unit, room, bed), bid in tr.beds.items():
            self.add("CLARITY_BED", BED_ID=bid, BED_LABEL=f"{room}-{bed}", ROOM_ID=tr.rooms[(fac, unit, room)],
                     BED_STATUS_C_NAME="Active", CENSUS_INCLUSN_YN="N" if unit == "ED" else "Y")
        for p in tr.t["providers"]:
            self.add("CLARITY_SER", PROV_ID=p["prov_id"], PROV_NAME=tr.prov_name(p["prov_id"]), PROV_TYPE=p["prov_type"],
                     ACTIVE_STATUS="Active", NPI=p["npi"], USER_ID=p["user_id"])
            self.add("CLARITY_SER_SPEC", PROV_ID=p["prov_id"], LINE=1, SPECIALTY_C_NAME=p["specialty"])
            self.add("CLARITY_EMP", USER_ID=p["user_id"], NAME=tr.prov_name(p["prov_id"]), PROV_ID=p["prov_id"])
        for (icd, sno, disp), did in sorted(tr.dx_id.items(), key=lambda x: x[1]):
            self.add("CLARITY_EDG", DX_ID=did, DX_NAME=disp, CURRENT_ICD10_LIST=icd or None, REF_BILL_CODE=icd or None,
                     SNOMED_CONCEPT_ID=sno or None)
        for (code, name), pid in sorted(tr.proc_id.items(), key=lambda x: x[1]):
            self.add("CLARITY_EAP", PROC_ID=pid, PROC_NAME=name, PROC_CODE=code)
        for lab, cid in sorted(tr.comp_id.items(), key=lambda x: x[1]):
            name, loinc = tr.comp[lab]
            self.add("CLARITY_COMPONENT", COMPONENT_ID=cid, NAME=name, BASE_NAME=lab, DFLT_LNC_ID=loinc or None)
        for (rxcui, name), mid in sorted(tr.med_id.items(), key=lambda x: x[1]):
            self.add("CLARITY_MEDICATION", MEDICATION_ID=mid, NAME=name, GENERIC_NAME=name.split(" ")[0].lower())
            self.add("RXNORM_CODES", MEDICATION_ID=mid, LINE=1, RXNORM_CODE=rxcui or None, RXNORM_TERM_TYPE_C_NAME="SCD" if rxcui else None)
        for name, pid in PAYOR.items():
            self.add("CLARITY_EPM", PAYOR_ID=pid, PAYOR_NAME=name)
        for fid, disp, name, vtype, units in FLO:
            self.add("IP_FLO_GP_DATA", FLO_MEAS_ID=fid, DISP_NAME=disp, FLO_MEAS_NAME=name, VAL_TYPE_C_NAME=vtype, UNITS=units or None)
        for z in ZC_TABLES:
            for name, code in sorted(CODES[z].items(), key=lambda x: x[1]):
                self.rows[f"ZC_{z}"].append({f"{z}_C": code, "NAME": name, "ABBR": name[:12].upper()})

    # ---- patient level history
    def patient(self, p):
        tr = self.tr
        merged = p["merged_into"] and self.b4(p["merged_at"])
        self.add("PATIENT", PAT_ID=p["pat_id"], PAT_NAME=f"{p['last']}, {p['first']} {p['middle'][:1]}".strip(),
                 PAT_FIRST_NAME=p["first"], PAT_MIDDLE_NAME=p["middle"] or None, PAT_LAST_NAME=p["last"], PAT_MRN_ID=p["mrn"],
                 BIRTH_DATE=p["birth_date"], **C("SEX", "SEX", {"F": "Female", "M": "Male"}.get(p["sex"], "Unknown")),
                 **C("ETHNIC_GROUP", "ETHNIC_GROUP", "Hispanic or Latino" if p["ethnicity"] == "hispanic" else "Not Hispanic or Latino"),
                 **C("LANGUAGE", "LANGUAGE", p["language"]), INTRPTR_NEEDED_YN="Y" if p["interpreter"] else "N",
                 ADD_LINE_1=p["address"], CITY=p["city"], **C("STATE", "STATE", "Massachusetts"), ZIP=p["zip"], HOME_PHONE=p["phone"],
                 SSN=p["ssn"], **C("PAT_STATUS", "PAT_STATUS", "Alive"), **C("MARITAL_STATUS", "MARITAL_STATUS", MARITAL.get(p["marital"])),
                 CREATE_USER_ID=None, ALLERGY_REVIEW_C_NAME=p["allergy_review"],
                 MERGED_INTO_PAT_ID=p["merged_into"] if merged else None, MERGE_INSTANT_DTTM=loc(p["merged_at"]) if merged else None)
        self.add("PATIENT_RACE", PAT_ID=p["pat_id"], LINE=1, **C("PATIENT_RACE", "PATIENT_RACE", RACE.get(p["race"], "Other")))
        self.add("IDENTITY_ID", PAT_ID=p["pat_id"], LINE=1, IDENTITY_TYPE_ID=100, IDENTITY_ID=p["mrn"])
        self.add("IDENTITY_ID", PAT_ID=p["pat_id"], LINE=2, IDENTITY_TYPE_ID=101, IDENTITY_ID=p["epi"])
        base = PROBLEM_BASE + p["_index"] * 50
        for i, q in enumerate(p["problems"]):
            self.add("PROBLEM_LIST", PROBLEM_LIST_ID=base + i, PAT_ID=p["pat_id"], DX_ID=tr.dxid(q["icd10"], q["snomed"], q["display"]),
                     DESCRIPTION=q["display"], NOTED_DATE=q["onset"], DATE_OF_ENTRY=q["onset"],
                     **C("PROBLEM_STATUS", "PROBLEM_STATUS", "Active"), CHRONIC_YN="Y", CLASS_OF_PROBLEM_C_NAME="Chronic",
                     PROBLEM_TYPE_C_NAME="MEDICAL")
        base = HOME_MED_BASE + p["_index"] * 50
        for i, m in enumerate(p["home_meds"]):
            mid = tr.med_id[(m["rxcui"], m["display"])]
            self.add("ORDER_MED", ORDER_MED_ID=base + i, PAT_ID=p["pat_id"], ORDERING_DATE=m["start"], START_DATE=m["start"],
                     DESCRIPTION=m["display"], DISPLAY_NAME=m["display"], MEDICATION_ID=mid,
                     **C("ORDER_CLASS", "ORDER_CLASS", "Historical Med"), **C("ORDER_STATUS", "ORDER_STATUS", "Sent"),
                     ACT_ORDER_C_NAME="Active Medication", **C("ORDERING_MODE", "ORDERING_MODE", "Outpatient"),
                     IS_PENDING_ORD_YN="N", NON_FORMULARY_YN="N", WEIGHT_BASED_YN="N", INFORMANT_C_NAME="Patient")
        line = 0
        for a in sorted(tr.allergies[p["pat_id"]], key=lambda a: (a["noted_at"], a["allergy_id"])):
            if not self.b4(a["noted_at"]):
                continue
            line += 1
            sev = {"Mild": "Low", "Moderate": "Medium"}.get(a["severity"], a["severity"])
            react = {"Nausea": "Nausea and Vomiting"}.get(a["reaction"], a["reaction"])
            react = react if react in CODES["REACTION"] else "Other"
            self.add("ALLERGY", ALLERGY_ID=int(a["allergy_id"]), PAT_ID=p["pat_id"],
                     ALLERGEN_ID=tr.allergen_id[(a["allergen"], a["code"], a["system"])], ALLERGEN_ID_ALLERGEN_NAME=a["allergen"],
                     REACTION=a["reaction"], DATE_NOTED=day(a["noted_at"]), ALRGY_ENTERED_DTTM=loc(a["noted_at"]),
                     ENTRY_USER_ID=tr.user(tr.encs[a["added_csn"]]["ed_nurse"]) if a["added_csn"] else None,
                     **C("ALLERGY_SEVERITY", "ALLERGY_SEVERITY", sev), SEVERITY_C_NAME="Allergy",
                     **C("ALRGY_STATUS", "ALRGY_STATUS", a["status"]), ALLERGY_CERTAINTY_C_NAME="Confirmed",
                     ALLERGY_SOURCE_C_NAME="Patient", ALLERGY_PAT_CSN=int(a["added_csn"]) if a["added_csn"] else None,
                     ALLERGEN_CODE=a["code"] or None, ALLERGEN_CODE_SYSTEM=a["system"] if a["code"] else None)
            self.add("PAT_ALLERGIES", PAT_ID=p["pat_id"], LINE=line, ALLERGY_RECORD_ID=int(a["allergy_id"]))
            self.add("ALLERGY_REACTIONS", ALLERGY_ID=int(a["allergy_id"]), LINE=1, **C("REACTION", "REACTION", react))
        for inf in tr.infections_by_pat[p["pat_id"]]:
            if not self.b4(inf["added_at"]):
                continue
            hist = inf["source"] == "history"
            chronic = inf["infection"] in ("MRSA", "VRE", "ESBL", "CRE")
            self.add("INFECTIONS", INFECTION_ID=int(inf["infection_id"]), PAT_ID=p["pat_id"],
                     PAT_ENC_CSN_ID=None if hist else int(inf["csn"]),
                     INFECTION_RECORD_TYPE_C_NAME="Patient Level" if hist else "Encounter Level",
                     **C("INFECTION_TYPE", "INFECTION_TYPE", inf["infection"]), INF_STATUS_C_NAME="Active",
                     HOW_ADDED_C_NAME="Import" if hist else "User", ONSET_DATE=day(inf["added_at"]),
                     ADD_LOCAL_DTTM=loc(inf["added_at"]), ADD_UTC_DTTM=utc(inf["added_at"]), DOESNT_EXPIRE_YN="Y" if chronic else "N",
                     REQUIRED_ISOLATION_OPTION_C_NAME="Default isolation", **C("ISOLATION_TYPE", "ISOLATION", inf["isolation"]))

    # ---- encounter
    def encounter(self, e):
        tr, csn = self.tr, e["csn"]
        adt = [a for a in tr.by_csn["adt"][csn] if self.b4(a["recorded_at"])]
        if not adt:
            return
        pat_id = self.enc_pat(e)
        p = tr.pats[e["pat_id"]]
        fac = e["facility"]
        ed_dept = int(adt[0]["dept_id"])
        canceled = {}                                  # original event_id -> cancel event_id
        for a in adt:
            if a["kind"] in CANCELS:
                orig = [x for x in adt if x["kind"] == CANCELS[a["kind"]] and x["seq"] < a["seq"]]
                if orig:
                    canceled[orig[-1]["event_id"]] = a["event_id"]
        live = [a for a in adt if a["event_id"] not in canceled and a["kind"] not in CANCELS]
        first = lambda kind: next((a for a in live if a["kind"] == kind), None)  # noqa: E731
        arrival = first("arrival")
        triage, roomed, provider = first("triage"), first("roomed"), first("provider")
        pending = first("pending_admit")
        class_i = next((a for a in live if a["kind"] == "class_change" and a["patient_class"] == "I"), None)
        class_obs = next((a for a in live if a["kind"] == "class_change" and a["patient_class"] == "OBS"), None)
        transfers = [a for a in live if a["kind"] == "transfer"]
        discharge = first("discharge")
        is_canceled = any(a["kind"] == "cancel_admit" for a in adt)
        state = [a for a in adt if a["kind"] in LOC_KINDS][-1]
        cls = state["patient_class"]
        unit_dept = tr.dept_of(fac, state["unit"])
        room_id = tr.room(fac, state["unit"], state["room"])
        bed_id = tr.bed(fac, state["unit"], state["room"], state["bed"])
        # A tracking event linked to an ADT event is filed with it, so a backdated transfer (F07) delays both.
        rec_of = {a["event_id"]: a["recorded_at"] for a in tr.by_csn["adt"][csn]}
        ed_events = []
        for x in tr.by_csn["ed_events"][csn]:
            filed = max(x["recorded_at"], rec_of.get(x["adt_event_id"], x["recorded_at"]))
            if self.b4(filed):
                ed_events.append(dict(x, recorded_at=filed))
        dispos = [x for x in ed_events if x["name"].startswith("Disposition Selected: ")]
        if discharge and discharge["details"].get("disposition") and discharge["details"]["disposition"] != "Home":
            dispos.append({"name": "Disposition Selected: " + discharge["details"]["disposition"], "event_at": discharge["effective_at"],
                           "user_id": discharge["user_id"]})
        dispo = DISPO.get(dispos[-1]["name"].split(": ", 1)[1]) if dispos else None
        dispo_at = dispos[-1]["event_at"] if dispos else None
        departed = (transfers[-1]["effective_at"] if transfers else None) or (discharge["effective_at"] if discharge else None)
        reqs = [b for b in tr.by_csn["bed_requests"][csn] if self.b4(b["requested_at"])]
        level = LOC_NAME.get(reqs[-1]["level_of_care"]) if reqs else (LOC_NAME.get(pending["details"]["level_of_care"]) if pending else None)
        service = (pending or {}).get("details", {}).get("service") or (e["admit_service"] or None if pending else None)
        admitting = (pending or {}).get("details", {}).get("admitting") or (e["admitting_prov"] or None if pending else None)
        attending = state["attending"]
        inp_id = str(num(csn, 50_000_000))
        har = int(e["har"])
        payer = FIN_CLASS.get(p["payer"], p["payer"])
        disch = discharge["details"] if discharge else {}
        if is_canceled:
            appt, closed, stat, conf = "Canceled", "Y", "Preadmission", "Canceled"
        elif discharge:
            appt, closed, stat = ("Left without seen" if e["disposition"] == "LWBS" else "Completed"), "Y", "Discharged"
            conf = "Confirmed"
        else:
            appt, closed, stat = "Arrived", "N", "Admission"
            conf = "Pending" if pending and not (class_i or class_obs) else "Confirmed"
        bmi = round(p["weight_kg"] / (p["height_cm"] / 100) ** 2, 1)
        self.add("PAT_ENC", PAT_ID=pat_id, PAT_ENC_DATE_REAL=dreal(e["arrival_at"]), PAT_ENC_CSN_ID=int(csn), CONTACT_DATE=day(e["arrival_at"]),
                 HOSP_ADMSN_TIME=loc(roomed["effective_at"]) if roomed else None, HOSP_DISCHRG_TIME=loc(discharge["effective_at"]) if discharge else None,
                 HOSP_ADMSN_TYPE_C_NAME="Emergency", DEPARTMENT_ID=ed_dept, VISIT_PROV_ID=e["ed_attending"] or None,
                 **C("APPT_STATUS", "APPT_STATUS", appt), ENC_CLOSED_YN=closed, HSP_ACCOUNT_ID=har, INPATIENT_DATA_ID=inp_id,
                 IP_EPISODE_ID=num(csn, 80_000_000), **C("FIN_CLASS", "FIN_CLASS", payer), BMI=bmi,
                 BSA=round((p["weight_kg"] * p["height_cm"] / 3600) ** 0.5, 2), EFFECTIVE_DATE_DTTM=loc(e["arrival_at"]),
                 DISCHARGE_DATE_DT=day(discharge["effective_at"]) if discharge else None, ENC_INSTANT=loc(adt[0]["recorded_at"]),
                 ENC_CREATE_USER_ID=tr.user(adt[0]["user_id"]), UPDATE_DATE=loc(max(a["recorded_at"] for a in adt)),
                 **C("ENC_TYPE", "ENC_TYPE", "Hospital Encounter"))
        pend_unit = reqs[-1] if reqs else None
        self.add("PAT_ENC_HSP", PAT_ENC_CSN_ID=int(csn), PAT_ID=pat_id, **C("ADT_PAT_CLASS", "PAT_CLASS", CLASS_NAME[cls]),
                 **C("ADT_PATIENT_STAT", "ADT_PATIENT_STAT", stat), ADT_ARRIVAL_TIME=loc(e["arrival_at"]),
                 ADT_ARRIVAL_STS_C_NAME=None if is_canceled else "Departed" if departed else "Roomed" if roomed else "Waiting",
                 HOSP_ADMSN_TIME=loc(roomed["effective_at"]) if roomed else None,
                 HOSP_DISCH_TIME=loc(discharge["effective_at"]) if discharge else None, **C("ADMIT_CONF_STAT", "ADMIT_CONF_STAT", conf),
                 ADMIT_SOURCE_C_NAME="Emergency Room" if (class_i or class_obs) else None,
                 ADMIT_CATEGORY_C_NAME="Emergency" if (class_i or class_obs) else None, HOSP_ADMSN_TYPE_C_NAME="Emergency",
                 **C("MEANS_OF_ARRV", "MEANS_OF_ARRV", ARRIVAL.get(e["arrival_mode"])),
                 **C("ACUITY_LEVEL", "ACUITY_LEVEL", f"ESI {e['esi']}" if triage else None), ED_EPISODE_ID=num(csn, 70_000_000),
                 **C("ED_DISPOSITION", "ED_DISPOSITION", dispo), ED_DISP_TIME=loc(dispo_at), ED_DEPARTURE_TIME=loc(departed),
                 TRIAGE_DATETIME=loc(triage["effective_at"]) if triage else None, TRIAGE_STATUS_C_NAME="Complete" if triage else None,
                 MSE_DATE=loc(provider["effective_at"]) if provider else None,
                 EMER_ADM_DATE=loc(arrival["effective_at"]) if arrival else None, EMER_ADM_EVENT_ID=int(arrival["event_id"]) if arrival else None,
                 INP_ADM_DATE=loc(class_i["effective_at"]) if class_i else None, INP_ADM_EVENT_ID=int(class_i["event_id"]) if class_i else None,
                 INP_ADM_EVENT_DATE=loc(class_i["recorded_at"]) if class_i else None,
                 OP_ADM_DATE=loc(class_obs["effective_at"]) if class_obs else None, OP_ADM_EVENT_ID=int(class_obs["event_id"]) if class_obs else None,
                 **C("LEVEL_OF_CARE", "LEVEL_OF_CARE", level), **C("HOSP_SERV", "HOSP_SERV", service),
                 ACCOMMODATION_C_NAME=self.accommodation(room_id, state["unit"]), DEPARTMENT_ID=int(unit_dept), ROOM_ID=room_id, BED_ID=bed_id,
                 INPATIENT_DATA_ID=inp_id, **C("DISCH_DISP", "DISCH_DISP", disch.get("disposition")),
                 DISCH_DEST_C_NAME=disch.get("disposition"), CHIEF_COMPLAINT_C_NAME=e["chief_complaint"] if triage else None,
                 ADMIT_PROV_TEXT=tr.prov_name(admitting) if (class_i or class_obs) else None, ATTEND_PROV_TEXT=tr.prov_name(attending),
                 BILL_ATTEND_PROV_ID=attending or None)
        self.add("PAT_ENC_HSP_2", PAT_ENC_CSN_ID=int(csn), ED_ENC_SRC_C_NAME="Standard",
                 PRI_PROBLEM_ID=tr.dxid(*e["dx"][0]) if (e["dx"] and (class_i or class_obs)) else None,
                 ADT_ARRIVAL_UTC_DTTM=utc(e["arrival_at"]), ED_DEPART_UTC_DTTM=utc(departed),
                 HOSP_ADMSN_UTC_DTTM=utc(roomed["effective_at"]) if roomed else None,
                 HOSP_DISCH_UTC_DTTM=utc(discharge["effective_at"]) if discharge else None,
                 INP_ADMSN_UTC_DTTM=utc(class_i["effective_at"]) if class_i else None)
        self.add("HSP_ACCOUNT", HSP_ACCOUNT_ID=har, PAT_ID=pat_id, ACCT_CLASS_HA_C_NAME=CLASS_NAME[cls], ACCT_FIN_CLASS_C_NAME=payer,
                 ACCT_BILLSTS_HA_C_NAME="Discharged/Not Billed" if discharge else "Open",
                 ADM_DATE_TIME=loc(roomed["effective_at"]) if roomed else None, DISCH_DATE_TIME=loc(discharge["effective_at"]) if discharge else None,
                 ER_ADMIT_DATE_TIME=loc(e["arrival_at"]), ER_DSCHG_DATE_TIME=loc(departed), ATTENDING_PROV_ID=attending or None,
                 ADMISSION_SOURCE_C_NAME="Emergency Room" if (class_i or class_obs) else None, ADMISSION_TYPE_C_NAME="Emergency",
                 PATIENT_STATUS_C_NAME=disch.get("disposition"), DISCH_DEPT_ID=ed_dept if discharge else None, PRIM_SVC_HA_C_NAME=service,
                 PRIMARY_PAYOR_ID=PAYOR.get(p["payer"]), CODING_STATUS_C_NAME="Not Coded")
        if triage:
            self.add("PAT_ENC_RSN_VISIT", PAT_ENC_CSN_ID=int(csn), LINE=1, ENC_REASON_ID=tr.reason_id[e["chief_complaint"]],
                     ENC_REASON_ID_REASON_VISIT_NAME=e["chief_complaint"])
        if e["dx"] and (dispos or (discharge and e["disposition"] in ("AMA", "LBTC"))):
            for i, (icd, sno, disp) in enumerate(e["dx"], 1):
                self.add("PAT_ENC_DX", PAT_ENC_CSN_ID=int(csn), LINE=i, CONTACT_DATE=day(e["arrival_at"]), DX_ID=tr.dxid(icd, sno, disp),
                         PRIMARY_DX_YN="Y" if i == 1 else "N", DX_ED_YN="Y", DX_CHRONIC_YN="N", DX_QUALIFIER_C_NAME="Confirmed")
        if pending and e["dx"]:
            for i, (icd, sno, disp) in enumerate(e["dx"], 1):
                self.add("HSP_ADMIT_DIAG", PAT_ENC_CSN_ID=int(csn), LINE=i, DX_ID=tr.dxid(icd, sno, disp), ADMIT_DIAG_TEXT=disp)
        self.providers(e, provider, class_i or class_obs, transfers, discharge, admitting)
        self.adt_rows(e, adt, canceled, ed_dept)
        self.pend_rows(e, reqs, adt, live, admitting)
        self.ed_rows(e, ed_events, inp_id, ed_dept, pat_id)
        self.orders(e, pat_id, fac)
        self.meds(e, pat_id)
        self.vitals(e, inp_id, pat_id, p, ed_dept)
        self.notes(e, pat_id)
        for c in tr.by_csn["code_status"][csn]:
            if self.b4(c["activated_at"]):
                order = next((o for o in tr.by_csn["orders"][csn] if o["order_type"] == "Code Status" and self.b4(o["ordered_at"])), None)
                in_ed = departed is None or c["activated_at"] < departed
                self.add("OCS_CODE_STATUS", OCS_ID=int(c["ocs_id"]), PATIENT_ID=pat_id, PATIENT_CSN=int(csn),
                         **C("CODE_STATUS", "CODE_STATUS", c["status"]), ACTIVATED_INST=loc(c["activated_at"]), ACTV_UTC_DTTM=utc(c["activated_at"]),
                         CONTEXT_C_NAME="ED" if in_ed else "Inpatient", ORDER_ID=int(order["order_id"]) if order else None,
                         USER_ID=tr.user(c["entered_by"]), OCS_STATUS_C_NAME="Active")

    def accommodation(self, room_id, unit):
        if unit == "ED" or room_id is None:
            return None
        return "Private" if self.tr.room_private.get(room_id) else "Semi-Private"

    def providers(self, e, provider, class_change, transfers, discharge, admitting):
        tr, csn = self.tr, e["csn"]
        line = 0
        if provider:
            line += 1
            end = class_change["effective_at"] if class_change else (discharge["effective_at"] if discharge else None)
            self.add("HSP_ATND_PROV", PAT_ENC_CSN_ID=int(csn), LINE=line, ATTEND_FROM_DATE=loc(provider["effective_at"]),
                     ATTEND_TO_DATE=loc(end), PROV_ID=e["ed_attending"], ED_ATTEND_YN="Y")
        if class_change and admitting:
            line += 1
            self.add("HSP_ATND_PROV", PAT_ENC_CSN_ID=int(csn), LINE=line, ATTEND_FROM_DATE=loc(class_change["effective_at"]),
                     PROV_ID=admitting, ED_ATTEND_YN="N")
        team = []
        if provider:
            team.append((e["ed_attending"], "Attending Physician", provider["effective_at"], class_change["effective_at"] if class_change else (discharge["effective_at"] if discharge else None), "Y"))
            if e["ed_resident"]:
                ptype = tr.prov[e["ed_resident"]]["prov_type"]
                team.append((e["ed_resident"], "Resident" if ptype == "Resident" else "Advanced Practice Provider", provider["effective_at"], None, "Y"))
        if e["ed_nurse"]:
            team.append((e["ed_nurse"], "Primary Nurse", e["arrival_at"], transfers[-1]["effective_at"] if transfers else None, "Y"))
        if class_change and admitting:
            team.append((admitting, "Admitting Physician", class_change["effective_at"], None, "N"))
        if transfers and e["unit_nurse"]:
            team.append((e["unit_nurse"], "Primary Nurse", transfers[-1]["effective_at"], None, "N"))
        for n in tr.by_csn["notes"][csn]:
            if n["note_type"] == "Consults" and self.b4(n["created_at"]):
                team.append((n["author"], "Consulting Physician", n["created_at"], None, "N"))
        for i, (prov, rel, beg, end, ed) in enumerate(team, 1):
            self.add("TREATMENT_TEAM", PAT_ENC_CSN_ID=int(csn), LINE=i, TR_TEAM_ID=prov, **C("TR_TEAM_REL", "TR_TEAM_REL", rel),
                     TR_TEAM_SPEC_C_NAME=tr.prov[prov]["specialty"] if prov in tr.prov else None, TR_TEAM_BEG_DTTM=loc(beg),
                     TR_TEAM_END_DTTM=loc(end), TR_TEAM_ED_YN=ed, TR_TEAM_ISDE_YN="N")

    # ---- CLARITY_ADT
    def adt_rows(self, e, adt, canceled, ed_dept):
        tr, csn, fac = self.tr, e["csn"], e["facility"]
        adt_order = next((o for o in tr.by_csn["orders"][csn] if o["order_type"] == "ADT" and self.b4(o["ordered_at"])), None)
        by_id = {a["event_id"]: a for a in adt}
        out = []
        first_ip = False
        for a in adt:
            eid = int(a["event_id"])
            cls = a["patient_class"]
            kind = a["kind"]
            if kind in CANCELS:
                orig = next((k for k, v in canceled.items() if v == a["event_id"]), None)
                etype = EVENT_TYPE[CANCELS[kind]]
                sub, original = "Canceled", int(orig) if orig else eid
            else:
                etype = EVENT_TYPE.get(kind, "Patient Update")
                sub, original = "Original", eid
            unit_dept = int(tr.dept_of(fac, a["unit"]))
            room_id = tr.room(fac, a["unit"], a["room"])
            bed_id = tr.bed(fac, a["unit"], a["room"], a["bed"])
            admitted = cls != "E"
            from_cls = to_cls = None
            if kind == "class_change":
                from_cls, to_cls = BASE_NAME[a["prior"]["class"]], BASE_NAME[cls]
            fip = "N"
            if etype == "Transfer In" and cls == "I" and a["unit"] != "ED" and not first_ip:
                fip, first_ip = "Y", True
            if kind == "class_change" and a["details"].get("conversion") and a["unit"] != "ED" and not first_ip:
                fip, first_ip = "Y", True
            canc = canceled.get(a["event_id"])
            r = row("CLARITY_ADT", EVENT_ID=eid, **C("EVENT_TYPE", "EVENT_TYPE", etype), **C("EVENT_SUBTYPE", "EVENT_SUBTYPE", "Canceled" if canc else sub),
                    PAT_ID=self.pat(a["pat_id"]), PAT_ENC_CSN_ID=int(csn), EFFECTIVE_TIME=loc(a["effective_at"]), EVENT_TIME=loc(a["recorded_at"]),
                    EFFECTIVE_UTC_DTTM=utc(a["effective_at"]), USER_ID=tr.user(a["user_id"]), DEPARTMENT_ID=unit_dept, ROOM_ID=room_id, BED_ID=bed_id,
                    BED_CSN_ID=bed_id * 10 + 1 if bed_id else None, ROOM_CSN_ID=room_id * 10 + 1 if room_id else None,
                    BED_STATUS_C_NAME="Active" if bed_id else None, STATUS_OF_BED_C_NAME="Ready" if bed_id else None,
                    **C("PAT_CLASS", "PAT_CLASS", CLASS_NAME[cls]), **C("BASE_PAT_CLASS", "BASE_CLASS", BASE_NAME[cls]),
                    **C("FROM_BASE_CLASS", "BASE_CLASS", from_cls), **C("TO_BASE_CLASS", "BASE_CLASS", to_cls),
                    **C("PAT_SERVICE", "HOSP_SERV", e["admit_service"] or None if admitted else None),
                    **C("PAT_LVL_OF_CARE", "LEVEL_OF_CARE", LOC_NAME.get(e["level_of_care"]) if admitted else None),
                    ACCOMMODATION_C_NAME=self.accommodation(room_id, a["unit"]), CANC_EVENT_ID=int(canc) if canc else None,
                    ORIGINAL_EVENT_ID=original, FIRST_IP_IN_IP_YN=fip,
                    ORDER_ID=int(adt_order["order_id"]) if (kind == "class_change" and adt_order) else None,
                    ACTION_SOURCE_C_NAME="Attending Provider Added" if kind == "provider" else None)
            if kind in ("roomed", "transfer"):
                # Companion Transfer Out at the prior location, same instant, derived EVENT_ID.
                pr = a["prior"]
                oid = eid + OUT_OFFSET
                p_room = tr.room(fac, pr["unit"], pr["room"])
                p_bed = tr.bed(fac, pr["unit"], pr["room"], pr["bed"])
                o = dict(r, EVENT_ID=oid, **C("EVENT_TYPE", "EVENT_TYPE", "Transfer Out"), DEPARTMENT_ID=int(tr.dept_of(fac, pr["unit"])),
                         ROOM_ID=p_room, BED_ID=p_bed, BED_CSN_ID=p_bed * 10 + 1 if p_bed else None, ROOM_CSN_ID=p_room * 10 + 1 if p_room else None,
                         BED_STATUS_C_NAME="Active" if p_bed else None, STATUS_OF_BED_C_NAME="Ready" if p_bed else None,
                         ACCOMMODATION_C_NAME=self.accommodation(p_room, pr["unit"]), XFER_EVENT_ID=eid, XFER_IN_EVENT_ID=eid,
                         ORIGINAL_EVENT_ID=oid if sub == "Original" else original, FIRST_IP_IN_IP_YN="N", ORDER_ID=None)
                r["XFER_EVENT_ID"], r["XFER_IN_EVENT_ID"] = oid, eid
                out.append(o)
            out.append(r)
        # Bed chain among live rows: LAST_IN_EVENT_ID points back to the Admission or Transfer In in effect,
        # NEXT_OUT_EVENT_ID forward to the Transfer Out or Discharge that ended it.
        live = [r for r in out if r["EVENT_SUBTYPE_C_NAME"] != "Canceled"]
        last_in = None
        for r in live:
            if r["EVENT_TYPE_C_NAME"] in ("Admission", "Transfer In"):
                last_in = r["EVENT_ID"]
            r["LAST_IN_EVENT_ID"] = last_in
        nxt = None
        for r in reversed(live):
            if r["EVENT_TYPE_C_NAME"] in ("Transfer Out", "Discharge"):
                nxt = r["EVENT_ID"]
            elif r["EVENT_TYPE_C_NAME"] in ("Admission", "Transfer In"):
                r["NEXT_OUT_EVENT_ID"] = nxt
        self.rows["CLARITY_ADT"].extend(out)

    # ---- PEND_ACTION
    def pend_rows(self, e, reqs, adt, live, admitting):
        tr, csn, fac = self.tr, e["csn"], e["facility"]
        adt_order = next((o for o in tr.by_csn["orders"][csn] if o["order_type"] == "ADT" and self.b4(o["ordered_at"])), None)
        unassigned = [a for a in adt if a["kind"] == "bed_unassigned"]
        transfers = [a for a in live if a["kind"] == "transfer"]
        for b in reqs:
            assigned = b["assigned_at"] if self.b4(b["assigned_at"]) else None
            completed = b["completed_at"] if self.b4(b["completed_at"]) else None
            linked = next((a for a in transfers if completed and abs((a["effective_at"] - completed).total_seconds()) < 90), None)
            deleted_at = None
            if b["deleted"]:
                deleted_at = next((a["effective_at"] for a in unassigned if a["effective_at"] >= b["requested_at"]), None)
                if deleted_at and not self.b4(deleted_at):
                    deleted_at = None
            dept = int(b["dept_id"])
            unit = tr.dept[b["dept_id"]]["code"]
            room_id = tr.room(fac, unit, b["room"]) if assigned else None
            bed_id = tr.bed(fac, unit, b["room"], b["bed"]) if assigned else None
            self.add("PEND_ACTION", PEND_ID=b["pend_id"], **C("PEND_EVENT_TYPE", "PEND_EVENT_TYPE", "Admission"), PEND_RECRD_TYPE_C_NAME="ED Arrival",
                     PAT_ID=self.enc_pat(e), PAT_ENC_CSN_ID=int(csn), START_TIME=loc(b["requested_at"]), END_TIME=loc(completed), UNIT_ID=dept,
                     ROOM_ID=room_id, BED_ID=bed_id, **C("PAT_CLASS", "PAT_CLASS", "Observation" if b["level_of_care"] == "OBS" else "Inpatient"),
                     PAT_SERVICE_C_NAME=e["admit_service"] or None, **C("LVL_OF_CARE", "LEVEL_OF_CARE", LOC_NAME.get(b["level_of_care"])),
                     ACCOMMODATION_C_NAME="Private" if b["isolation"] else None, ACUITY_C_NAME=f"ESI {e['esi']}",
                     DX_ID=tr.dxid(*e["dx"][0]) if e["dx"] else None, ADM_COND_C_NAME="Stable", REQUEST_TIME=loc(b["requested_at"]),
                     REQ_BY_USR_ID=tr.user(e["ed_nurse"]), ASSIGNED_TIME=loc(assigned),
                     **C("PEND_REQ_STATUS", "PEND_REQ_STATUS", "Assigned" if assigned else "Requested"),
                     REQUEST_URGENCY_C_NAME="A-High" if b["level_of_care"] in ("ICU", "STEPDOWN") else "B-Medium",
                     IS_DELETED_YN="Y" if deleted_at else "N", DELETE_TIME=loc(deleted_at), DELETE_CAUSE_C_NAME=b["delete_cause"] if deleted_at else None,
                     LINKED_EVENT_ID=int(linked["event_id"]) if linked else None, COMPLETED_YN="Y" if completed else "N",
                     ADT_ORDER_ID=int(adt_order["order_id"]) if adt_order else None, PEND_ATND_PROV_ID=admitting, ADT_PND_ADMITNG_ID=admitting,
                     ISOLATION_C_NAME=b["isolation"] or None)

    # ---- ED tracking
    def ed_rows(self, e, events, inp_id, ed_dept, pat_id):
        tr, csn = self.tr, e["csn"]
        if not events:
            return
        iev = f"IEV{csn}"
        self.add("ED_IEV_PAT_INFO", EVENT_ID=iev, PAT_ID=pat_id, PAT_ENC_CSN_ID=int(csn), CONTACT_DATE=day(e["arrival_at"]), DEPT_EVENT_DEP_ID=ed_dept,
                 REG_WAIT_ARRV_DTTM=loc(e["arrival_at"]), REG_WAIT_STATE_C_NAME="Identified Encounter",
                 TRANSFER_STATUS_C_NAME="Completed" if any(x["name"] == "Patient Departed from ED" for x in events) else "Active",
                 CREATE_DTTM=loc(events[0]["recorded_at"]), UPDATE_DATE=loc(max(x["recorded_at"] for x in events)))
        status_line = dispo_line = 0
        for x in events:
            self.add("ED_IEV_EVENT_INFO", EVENT_ID=iev, LINE=x["line"], EVENT_DISPLAY_NAME=x["name"], EVENT_TIME=loc(x["event_at"]),
                     EVENT_RECORD_TIME=loc(x["recorded_at"]), EVENT_USER_ID=tr.user(x["user_id"]),
                     EVENT_PROV_ID=x["user_id"] if tr.prov.get(x["user_id"], {}).get("prov_type") != "Registered Nurse" else None,
                     EVENT_DEPT_ID=ed_dept, ADT_EVENT_ID=int(x["adt_event_id"]) if x["adt_event_id"] else None, EVENT_STATUS_C_NAME="Active",
                     TRANSFER_DEST_DEPT_ID=int(e["unit_dept_id"]) if (x["name"] == "Patient Departed from ED" and e["unit_dept_id"]) else None,
                     ED_C_CLIENT_SRC_C_NAME="Hyperspace")
            st = ED_STATUS.get(x["name"])
            if st:
                status_line += 1
                self.add("ED_PAT_STATUS", INPATIENT_DATA_ID=inp_id, LINE=status_line, **C("ED_PAT_STATUS", "ED_PAT_STATUS", st),
                         PAT_STATUS_TIME=loc(x["recorded_at"]), PAT_STATUS_USER_ID=tr.user(x["user_id"]), PAT_ENC_CSN_ID=int(csn))
            if x["name"].startswith("Disposition Selected: "):
                dispo_line += 1
                self.add("ED_DISPO_AUDIT", PAT_ENC_CSN_ID=int(csn), LINE=dispo_line,
                         **C("ED_DISPOSITION", "ED_DISPOSITION", DISPO[x["name"].split(": ", 1)[1]]), ED_DISP_INST=loc(x["recorded_at"]),
                         USER_ID=tr.user(x["user_id"]))

    # ---- orders and results
    def orders(self, e, pat_id, fac):
        tr, csn = self.tr, e["csn"]
        lab_id = 100 if fac == "SGH" else 200
        for o in tr.by_csn["orders"][csn]:
            if not self.b4(o["ordered_at"]):
                continue
            res = [r for r in tr.results[o["order_id"]] if self.b4(r["resulted_at"])]
            statuses = {r["status"] for r in res}
            collected = o["collected_at"] if self.b4(o["collected_at"]) else None
            if o["order_type"] in ("ADT", "Consult", "Code Status"):
                ostat, lstat = "Completed", None
            elif statuses & {"F", "C"}:
                ostat, lstat = "Resulted", "Edited" if "C" in statuses else "Final result"
            elif "P" in statuses:
                ostat, lstat = "Sent", "Preliminary result"
            else:
                ostat, lstat = "Sent", "In process" if collected else None
            rad = None
            if o["order_type"] == "Imaging":
                rad = "Exam Begun" if collected else "Ordered"
            self.add("ORDER_PROC", ORDER_PROC_ID=int(o["order_id"]), PAT_ID=pat_id, PAT_ENC_CSN_ID=int(csn), PAT_ENC_DATE_REAL=dreal(e["arrival_at"]),
                     ORDERING_DATE=day(o["ordered_at"]), ORDER_TIME=loc(o["ordered_at"]), ORDER_INST=loc(o["ordered_at"]),
                     RESULT_TIME=loc(max(r["resulted_at"] for r in res)) if res else None, PROC_START_TIME=loc(o["ordered_at"]),
                     PROC_ID=tr.proc_id[(o["code"], o["name"])], PROC_CODE=o["code"], DESCRIPTION=o["name"], DISPLAY_NAME=o["name"],
                     **C("ORDER_TYPE", "ORDER_TYPE", o["order_type"]), **C("ORDER_CLASS", "ORDER_CLASS", "Normal"),
                     **C("ORDER_STATUS", "ORDER_STATUS", ostat), **C("LAB_STATUS", "LAB_STATUS", lstat), RADIOLOGY_STATUS_C_NAME=rad,
                     **C("ORDER_PRIORITY", "ORDER_PRIORITY", o["priority"]),
                     ABNORMAL_YN=("Y" if any(r["flag"] for r in res) else "N") if res else None, IS_PENDING_ORD_YN="N",
                     AUTHRZING_PROV_ID=o["provider"], BILLING_PROV_ID=o["provider"], ORD_CREATR_USER_ID=tr.user(o["provider"]),
                     FUTURE_OR_STAND="N", SPECIMEN_TYPE_C_NAME="Blood" if (o["order_type"] == "Lab" and not o["name"].startswith("Urin")) else None,
                     SPECIMN_TAKEN_TIME=loc(collected), RESULT_LAB_ID=lab_id if o["order_type"] == "Lab" else None,
                     HV_HOSPITALIST_YN="Y" if o["provider"] == e["admitting_prov"] and e["admitting_prov"] else "N",
                     SESSION_KEY=f"{csn}-{o['ordered_at'].strftime('%Y%m%d%H%M')[:-1]}", ACCESSION_NUM=o["accession"] or None)
            latest = {}
            for r in sorted(res, key=lambda r: (r["resulted_at"], "PFC".index(r["status"]))):
                latest[r["line"]] = r          # Clarity keeps the current value per component line
            for line, r in sorted(latest.items()):
                numeric = r["numeric"]
                in_range = None
                if numeric is not None and (r["ref_low"] is not None or r["ref_high"] is not None):
                    in_range = "N" if r["flag"] else "Y"
                self.add("ORDER_RESULTS", ORDER_PROC_ID=int(o["order_id"]), ORD_DATE_REAL=dreal(o["ordered_at"]), LINE=line, PAT_ENC_CSN_ID=int(csn),
                         RESULT_DATE=loc(r["resulted_at"]), COMPONENT_ID=tr.comp_id[r["lab"]], COMPONENT_ID_NAME=r["name"], ORD_VALUE=r["value"],
                         ORD_NUM_VALUE=numeric if numeric is not None else 9999999.0, **C("RESULT_FLAG", "RESULT_FLAG", FLAG[r["flag"]]),
                         REFERENCE_LOW=str(r["ref_low"]) if r["ref_low"] is not None else None,
                         REFERENCE_HIGH=str(r["ref_high"]) if r["ref_high"] is not None else None, REFERENCE_UNIT=r["units"] or None,
                         REF_NORMAL_VALS=f"{r['ref_low']} - {r['ref_high']}" if r["ref_low"] is not None else None,
                         **C("RESULT_STATUS", "RESULT_STATUS", RSTAT[r["status"]]), LAB_STATUS_C_NAME=LABST[r["status"]],
                         COMP_OBS_INST_TM=loc(r["observed_at"]), COMP_ANL_INST_TM=loc(r["resulted_at"]), RESULT_IN_RANGE_YN=in_range, INTERFACE_YN="Y",
                         COMPONENT_COMMENT=r["comment"] or None, COMPON_LNC_ID=r["loinc"] or None, RESULTING_LAB_ID=lab_id if o["order_type"] == "Lab" else None)

    # ---- medications
    def meds(self, e, pat_id):
        tr, csn = self.tr, e["csn"]
        for m in tr.by_csn["med_orders"][csn]:
            if not self.b4(m["ordered_at"]):
                continue
            mar = [x for x in tr.mar[m["order_med_id"]] if self.b4(x["taken_at"])]
            done = m["freq"] == "ONCE" and any(x["action"] == "Given" for x in mar)
            fid, fname = FREQ.get(m["freq"], (None, m["freq"]))
            weight_based = m["comment"].startswith("Weight based")
            dose = float(m["dose"]) if m["dose"].replace(".", "", 1).isdigit() else None
            self.add("ORDER_MED", ORDER_MED_ID=int(m["order_med_id"]), PAT_ID=pat_id, PAT_ENC_CSN_ID=int(csn), ORDERING_DATE=day(m["ordered_at"]),
                     ORDER_INST=loc(m["ordered_at"]), ORDER_START_TIME=loc(m["ordered_at"]), START_DATE=day(m["ordered_at"]),
                     ORDER_END_TIME=loc(mar[0]["taken_at"]) if done else None, DESCRIPTION=m["name"], DISPLAY_NAME=m["name"],
                     MEDICATION_ID=tr.med_id[(m["rxcui"], m["name"])], SIG=f"{m['dose']} {m['unit']} {ROUTE.get(m['route'], m['route'])} {fname}",
                     HV_DISCRETE_DOSE=m["dose"], MIN_DISCRETE_DOSE=dose, MAX_DISCRETE_DOSE=dose, DOSE_UNIT_C_NAME=m["unit"], HV_DOSE_UNIT_C_NAME=m["unit"],
                     HV_DISCR_FREQ_ID=fid, HV_DISCR_FREQ_ID_FREQ_NAME=fname, **C("MED_ROUTE", "MED_ROUTE", ROUTE.get(m["route"], m["route"])),
                     DOSAGE=f"{m['dose']} {m['unit']}", **C("ORDER_CLASS", "ORDER_CLASS", "Normal"),
                     **C("ORDER_STATUS", "ORDER_STATUS", "Completed" if done else "Sent"),
                     ACT_ORDER_C_NAME="Completed Medication" if done else "Active Medication", **C("ORDERING_MODE", "ORDERING_MODE", "Inpatient"),
                     ORDER_PRIORITY_C_NAME="STAT" if m["kind"] in ("bolus", "infusion") else "Routine", AUTHRZING_PROV_ID=m["prescriber"],
                     ORD_PROV_ID=m["prescriber"], MED_PRESC_PROV_ID=m["prescriber"], IS_PENDING_ORD_YN="N", NON_FORMULARY_YN="N",
                     WEIGHT_BASED_YN="Y" if weight_based else "N", ORD_TM_WEIGHT=round(e["weight_kg"] * 35.274) if weight_based else None,
                     MED_COMMENTS=m["comment"] or None, LASTDOSE=loc(mar[-1]["taken_at"]) if mar else None)
            if mar:
                self.add("ORDER_MEDINFO", ORDER_MED_ID=int(m["order_med_id"]), LAST_ADMIN_INST=loc(max(x["taken_at"] for x in mar)),
                         ADMIN_MIN_DOSE=dose, ADMIN_MAX_DOSE=dose, ADMIN_DOSE_UNIT_C_NAME=m["unit"])
            for x in mar:
                rate, rate_unit = None, None
                if x["rate"]:
                    parts = x["rate"].split(" ", 1)
                    rate, rate_unit = float(parts[0]), parts[1] if len(parts) > 1 else None
                self.add("MAR_ADMIN_INFO", ORDER_MED_ID=int(m["order_med_id"]), LINE=x["line"], TAKEN_TIME=loc(x["taken_at"]),
                         SCHEDULED_TIME=loc(x["scheduled_at"]), MAR_ORIG_DUE_TM=loc(x["scheduled_at"]), SAVED_TIME=loc(x["taken_at"]),
                         **C("MAR_ACTION", "MAR_RSLT", x["action"]), SIG=x["dose"], DOSE_UNIT_C_NAME=x["unit"],
                         **C("ROUTE", "MED_ROUTE", ROUTE.get(x["route"], x["route"])), INFUSION_RATE=rate, MAR_INF_RATE_UNIT_C_NAME=rate_unit,
                         USER_ID=tr.user(x["user_id"]), MAR_DOC_USER_ID=tr.user(x["user_id"]),
                         REASON_C_NAME="Patient Condition" if x["action"] == "Held" else None, MAR_ADMIN_DEPT_ID=int(x["dept_id"]), SCAN_MODE_YN="Y")

    # ---- flowsheets
    def vitals(self, e, inp_id, pat_id, p, ed_dept):
        tr, csn = self.tr, e["csn"]
        vit = [v for v in tr.by_csn["vitals"][csn] if self.b4(v["entered_at"])]
        if not vit:
            return
        recs, lines = {}, defaultdict(int)
        day0 = tr.midnights[0].date()
        disp = {f[0]: f[1] for f in FLO}
        for v in vit:
            d = v["taken_at"].astimezone(TZ).date()
            fsd = f"{inp_id}{(d - day0).days:02d}"
            if fsd not in recs:
                recs[fsd] = d
                self.add("IP_FLWSHT_REC", FSD_ID=fsd, INPATIENT_DATA_ID=inp_id, RECORD_DATE=d.isoformat(), PAT_ID=pat_id)
            vals = [("5", f"{v['sbp']}/{v['dbp']}", "High" if v["sbp"] > 160 else "Low" if v["sbp"] < 90 else None),
                    ("8", str(v["hr"]), "High" if v["hr"] > 100 else "Low" if v["hr"] < 50 else None),
                    ("9", str(v["rr"]), "High" if v["rr"] > 24 else "Low" if v["rr"] < 10 else None),
                    ("10", str(v["spo2"]), "Low" if v["spo2"] < 90 else None),
                    ("3040100003", str(v["map"]), None), ("3040100001", v["o2_device"], None)]
            if v["temp"] is not None:
                f = round(v["temp"] * 9 / 5 + 32, 1)
                vals.append(("6", f"{f}", "High" if v["temp"] >= 38.0 else "Low" if v["temp"] < 35.5 else None))
            if v["o2_flow"] is not None:
                vals.append(("3040100002", f"{v['o2_flow']:g}", None))
            if v["pain"] is not None:
                vals.append(("3040102607", str(v["pain"]), None))
            if v["gcs"] is not None:
                vals.append(("3040104328", str(v["gcs"]), "Low" if v["gcs"] < 15 else None))
            if v["line"] == 1:
                vals += [("14", str(round(e["weight_kg"] * 35.274)), None), ("11", str(round(p["height_cm"] / 2.54)), None)]
            for fid, val, abn in vals:
                lines[fsd] += 1
                self.add("IP_FLWSHT_MEAS", FSD_ID=fsd, LINE=lines[fsd], FLO_MEAS_ID=fid, FLO_MEAS_ID_DISP_NAME=disp[fid], OCCURANCE=1,
                         RECORDED_TIME=loc(v["taken_at"]), ENTRY_TIME=loc(v["entered_at"]), TAKEN_USER_ID=tr.user(v["user_id"]),
                         ENTRY_USER_ID=tr.user(v["user_id"]), MEAS_VALUE=val, ISACCEPTED_YN="Y", ABNORMAL_C_NAME="Abnormal" if abn else None,
                         ABNORMAL_TYPE_C_NAME=abn, FLT_ID="ED" if int(v["dept_id"]) == ed_dept else "IP",
                         DOC_METHOD_C_NAME="Manual", NEEDS_COSIGN_C_NAME="No")

    # ---- notes
    def notes(self, e, pat_id):
        tr, csn = self.tr, e["csn"]
        for n in tr.by_csn["notes"][csn]:
            if not self.b4(n["created_at"]):
                continue
            signed = n["signed_at"] if self.b4(n["signed_at"]) else None
            prov = tr.prov.get(n["author"], {})
            note_csn = int(n["note_id"]) + NOTE_CSN_BASE
            self.add("HNO_INFO", NOTE_ID=n["note_id"], PAT_ID=pat_id, PAT_ENC_CSN_ID=int(csn), **C("IP_NOTE_TYPE", "IP_NOTE_TYPE", n["note_type"]),
                     ENTRY_USER_ID=tr.user(n["author"]), CURRENT_AUTHOR_ID=tr.user(n["author"]), CREATE_INSTANT_DTTM=utc(n["created_at"]),
                     CRT_INST_LOCAL_DTTM=loc(n["created_at"]), DATE_OF_SERVIC_DTTM=loc(n["service_at"]), LST_FILED_INST_DTTM=loc(signed or n["created_at"]),
                     UNSIGNED_YN="N" if signed else "Y", NOTE_DESC=n["note_type"], PRIORITY_YN="N")
            self.add("NOTE_ENC_INFO", CONTACT_SERIAL_NUM=note_csn, NOTE_ID=n["note_id"], CONTACT_DATE_REAL=dreal(n["created_at"]), CONTACT_DATE=day(n["created_at"]),
                     PAT_ENC_CSN_ID=int(csn), AUTHOR_USER_ID=tr.user(n["author"]), AUTH_LNKED_PROV_ID=n["author"], AUTHOR_SERVICE_C_NAME=prov.get("specialty"),
                     AUTHOR_PRVD_TYPE_C_NAME=prov.get("prov_type"), **C("NOTE_STATUS", "NOTE_STATUS", "Signed" if signed else "Incomplete"),
                     NOTE_TYPE_C_NAME=n["note_type"], NOTE_FORMAT_C_NAME="Plain Text", ENTRY_INSTANT_DTTM=loc(n["created_at"]),
                     NOTE_FILE_TIME_DTTM=utc(signed), NOT_FILETM_LOC_DTTM=loc(signed), SPEC_NOTE_TIME_DTTM=loc(n["service_at"]),
                     UPD_AUTHOR_INS_DTTM=loc(signed or n["created_at"]), COSIGN_REQUIRED_C_NAME="Not Required", SENSITIVE_STAT_C_NAME="Not Sensitive")
            for i, text in enumerate(n["text"].split("\n"), 1):
                self.add("HNO_NOTE_TEXT", NOTE_ID=n["note_id"], LINE=i, CONTACT_DATE_REAL=dreal(n["created_at"]), NOTE_CSN_ID=note_csn, NOTE_TEXT=text,
                         IS_ARCHIVED_YN="N", CONTACT_DATE=day(n["created_at"]), CHRON_ITEM_NUM=i)


# ----------------------------------------------------------------------------- driver
def schema(tr: Truth, snapshots):
    tables = {}
    for t in SCHEMA:
        cols = []
        for name, typ, src in columns(t):
            c = {"name": name, "type": TYPES[typ], "source": SOURCE[src]}
            if typ in ("ts", "utc"):
                c["tz"] = "America/New_York" if typ == "ts" else "UTC"
            if typ == "yn":
                c["type"] = "STRING(1)"
            cols.append(c)
        tables[t] = {"primary_key": SCHEMA[t][0].split(","), "as_of_column": AS_OF.get(t), "columns": cols}
    return {"feed": "clarity", "snapshot_rule": "rows documented before the snapshot's local midnight (America/New_York)",
            "encoding": "JSONL, one row per line, null columns omitted; TIMESTAMP values are naive strings in the column's tz",
            "snapshots": snapshots, "future_ok_columns": sorted(FUTURE_OK), "window_end": tr.end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "id_notes": {"CLARITY_ADT.EVENT_ID": "truth adt event_id; Transfer Out companions are event_id + 400000000",
                         "ORDER_MED_ID": "truth order_med_id for visit meds; 600000000 + n for Synthea home meds",
                         "PROBLEM_LIST_ID": "300000000 + n", "NOTE_ENC_INFO.CONTACT_SERIAL_NUM": "truth note_id + 100000000",
                         "INPATIENT_DATA_ID": "csn mod 1e9 + 50000000", "ED_IEV_PAT_INFO.EVENT_ID": "IEV + csn"},
            "tables": tables}


def render(truth_dir, out_dir) -> dict:
    tr = Truth(load_truth(truth_dir))
    for i, p in enumerate(tr.t["patients"]):
        p["_index"] = i
    out = Path(out_dir) / "clarity"
    out.mkdir(parents=True, exist_ok=True)
    counts, total, snaps, faults = {}, 0, [], []
    for midnight in tr.midnights:
        cut = midnight.astimezone(UTC)
        name = midnight.strftime("%Y-%m-%d")
        d = out / name
        d.mkdir(exist_ok=True)
        rows = Snapshot(tr, cut).build()
        counts = {}
        for t in SCHEMA:
            path = d / f"{t}.jsonl"
            with open(path, "w", encoding="utf-8") as f:
                for r in rows.get(t, []):
                    # Nulls are omitted; schema.json carries the full column list.
                    f.write(json.dumps({k: v for k, v in r.items() if v is not None}, ensure_ascii=False, separators=(",", ":")) + "\n")
            counts[t] = len(rows.get(t, []))
            total += path.stat().st_size
        snaps.append(name)
        deferred = sum(1 for e in tr.encs.values() if e["arrival_at"] >= cut)
        faults.append({"fault_id": "F32", "csn": "", "stream": "clarity", "event_time": cut.strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "description": f"Snapshot {name} holds documentation before {midnight.isoformat()}; {deferred} encounters not yet visible",
                       "expected": "App merges stream state with batch history and shows source freshness"})
    (out / "schema.json").write_text(json.dumps(schema(tr, snaps), indent=1))
    write_manifest(faults, out / "fault_manifest.jsonl")
    return {"snapshots": snaps, "tables": counts, "bytes": total}


def main():
    ap = argparse.ArgumentParser(description="Render Clarity style nightly snapshots from the truth tables.")
    ap.add_argument("--truth", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    s = render(a.truth, a.out)
    print(f"wrote {len(s['snapshots'])} snapshots to {a.out / 'clarity'} ({s['bytes'] / 1e6:.1f} MB); last snapshot {s['snapshots'][-1]}:")
    for t, n in sorted(s["tables"].items()):
        print(f"  {t:24s} {n:7d}")


if __name__ == "__main__":
    main()
