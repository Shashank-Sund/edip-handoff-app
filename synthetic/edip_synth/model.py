"""Truth records: one normalized world that every renderer reads.

All timestamps are timezone aware UTC datetimes. Renderers convert to each
interface's local conventions.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Patient:
    pat_id: str
    mrn: str
    epi: str
    synthea_id: str
    first: str
    middle: str
    last: str
    birth_date: str
    sex: str
    race: str
    ethnicity: str
    language: str
    interpreter: bool
    marital: str
    address: str
    city: str
    state: str
    zip: str
    phone: str
    ssn: str
    payer: str
    weight_kg: float
    height_cm: float
    emergency_contact: dict
    problems: list
    home_meds: list
    prior_ed_visits: int
    prior_admissions: int
    allergy_review: str = "Reviewed"      # Reviewed, No Known Allergies, Not on File
    merged_into: Optional[str] = None     # set on a retired duplicate (fault F12)
    merged_at: Optional[datetime] = None


@dataclass
class Encounter:
    csn: str
    har: str
    pat_id: str
    facility: str
    scenario: str
    scenario_label: str
    disposition: str          # ADMIT_IP, ADMIT_OBS, DISCHARGE, LWBS, AMA, LBTC, TRANSFER, CANCELED
    arrival_mode: str
    arrival_at: datetime
    esi: int
    chief_complaint: str
    dx: list
    ed_attending: str = ""
    ed_resident: str = ""
    ed_nurse: str = ""
    triage_at: Optional[datetime] = None
    roomed_at: Optional[datetime] = None
    provider_at: Optional[datetime] = None
    decision_at: Optional[datetime] = None     # admit decision, or discharge decision
    bed_request_at: Optional[datetime] = None
    bed_assigned_at: Optional[datetime] = None
    departure_at: Optional[datetime] = None    # left the ED (transfer, discharge, LWBS)
    class_change_at: Optional[datetime] = None
    ed_bay: str = ""
    admitting_prov: str = ""
    admit_service: str = ""
    level_of_care: str = ""
    unit_dept_id: str = ""
    unit: str = ""
    room: str = ""
    bed: str = ""
    unit_nurse: str = ""
    isolation: str = ""
    inpatient_los_h: float = 0.0
    weight_kg: float = 0.0
    return_of: str = ""        # prior CSN for a 72 hour return
    registered_pat_id: str = ""  # pat_id used at registration when it differs (merge)
    consults: list = field(default_factory=list)
    flags: list = field(default_factory=list)  # fault IDs touching this encounter
    state_at_end: str = ""


@dataclass
class AdtEvent:
    event_id: str
    csn: str
    pat_id: str
    seq: int
    kind: str                 # arrival, registration, triage, roomed, provider, allergy_update, isolation_update,
                              # pending_admit, class_change, bed_assigned, bed_unassigned, transfer, cancel_transfer,
                              # discharge, cancel_discharge, cancel_admit, merge, code_status_update
    effective_at: datetime
    recorded_at: datetime
    patient_class: str        # E, I, OBS
    facility: str
    dept_id: str
    unit: str
    room: str
    bed: str
    prior: dict
    user_id: str
    attending: str
    details: dict = field(default_factory=dict)


@dataclass
class EdEvent:
    csn: str
    line: int
    name: str
    event_at: datetime
    recorded_at: datetime
    user_id: str
    adt_event_id: str = ""


@dataclass
class BedRequest:
    pend_id: str
    csn: str
    requested_at: datetime
    unit: str
    dept_id: str
    level_of_care: str
    isolation: str
    assigned_at: Optional[datetime] = None
    room: str = ""
    bed: str = ""
    completed_at: Optional[datetime] = None
    deleted: bool = False
    delete_cause: str = ""
    linked_event_id: str = ""


@dataclass
class Order:
    order_id: str
    csn: str
    order_type: str           # Lab, Imaging, Consult, ADT, Code Status
    code: str
    name: str
    loinc: str
    priority: str
    ordered_at: datetime
    provider: str
    accession: str = ""
    collected_at: Optional[datetime] = None
    status: str = "Ordered"   # status as of window end


@dataclass
class Result:
    order_id: str
    csn: str
    line: int
    lab: str
    loinc: str
    name: str
    value: str
    numeric: Optional[float]
    units: str
    ref_low: Optional[float]
    ref_high: Optional[float]
    flag: str                 # "", H, L, HH, LL, A
    status: str               # P, F, C
    observed_at: datetime
    resulted_at: datetime
    comment: str = ""


@dataclass
class MedOrder:
    order_med_id: str
    csn: str
    rxcui: str
    name: str
    dose: str
    unit: str
    route: str
    freq: str
    kind: str
    ordered_at: datetime
    verified_at: datetime
    prescriber: str
    rate: str = ""
    comment: str = ""
    status: str = "Active"


@dataclass
class MarAdmin:
    order_med_id: str
    csn: str
    line: int
    action: str               # Given, New Bag, Rate Change, Stopped, Held
    taken_at: datetime
    scheduled_at: datetime
    dose: str
    unit: str
    route: str
    rate: str
    user_id: str
    dept_id: str


@dataclass
class Vital:
    csn: str
    line: int
    taken_at: datetime
    entered_at: datetime
    user_id: str
    dept_id: str
    hr: int
    sbp: int
    dbp: int
    map: int
    rr: int
    temp: Optional[float]
    spo2: int
    o2_device: str
    o2_flow: Optional[float]
    pain: Optional[int]
    gcs: Optional[int]
    validated: bool = True


@dataclass
class Note:
    note_id: str
    csn: str
    note_type: str
    loinc: str
    author: str
    created_at: datetime
    service_at: datetime
    signed_at: Optional[datetime]
    text: str
    versions: list = field(default_factory=list)   # [(status, at)]


@dataclass
class Allergy:
    allergy_id: str
    pat_id: str
    allergen: str
    code: str
    system: str
    reaction: str
    severity: str
    noted_at: datetime
    added_csn: str = ""       # set when added during a visit in the window (fault F21)
    status: str = "Active"


@dataclass
class CodeStatus:
    ocs_id: str
    pat_id: str
    csn: str
    status: str
    activated_at: datetime
    entered_by: str


@dataclass
class Infection:
    infection_id: str
    pat_id: str
    csn: str
    infection: str
    isolation: str
    added_at: datetime
    source: str               # history, this_visit


@dataclass
class World:
    config: dict
    facilities: list
    departments: list
    providers: list
    patients: list = field(default_factory=list)
    encounters: list = field(default_factory=list)
    adt: list = field(default_factory=list)
    ed_events: list = field(default_factory=list)
    bed_requests: list = field(default_factory=list)
    orders: list = field(default_factory=list)
    results: list = field(default_factory=list)
    med_orders: list = field(default_factory=list)
    mar: list = field(default_factory=list)
    vitals: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    allergies: list = field(default_factory=list)
    code_status: list = field(default_factory=list)
    infections: list = field(default_factory=list)
    census: list = field(default_factory=list)     # hourly unit occupancy
    faults: list = field(default_factory=list)     # truth level fault manifest rows
    render_plan: dict = field(default_factory=dict)
