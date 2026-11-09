export type Severity = 'stable' | 'watcher' | 'unstable';
export type WorkflowState = 'drafting' | 'draft' | 'signed' | 'changed' | 'acknowledged' | 'transferred_unsigned';
export type Phase = 'boarding' | 'arrived' | 'canceled';

export interface Persona {
  id: string;
  name: string;
  role: 'ed_nurse' | 'ed_physician' | 'unit_nurse' | 'admitting_physician' | 'viewer';
  role_label: string;
  unit: string | null;
  unit_name?: string;
  facility: string | null;
  default_template: string;
  can_sign: string[];
  can_ack: string[];
  /** Who the person is for owning patients: the persona id with demo personas, else the sign in email. */
  key?: string;
}

/** The two ED roles that own patients: one nurse owner and one physician owner per patient. */
export type EdRole = 'ed_nurse' | 'ed_physician';

/** Who owns a patient for one ED role. stale: no action, take or assignment within the limit, so it counts as Unassigned. */
export interface Owner {
  id: string;
  name: string;
  since: string;
  last_active: string;
  stale: boolean;
  you: boolean;
}

/** A colleague in your ED role, for Assign and Take over. */
export interface Colleague {
  id: string;
  name: string;
  you?: boolean;
  /** Take over: how many boarding patients they own now. */
  patients?: number;
}

/** The next shift change, for ED roles: warning is true in the half hour (or the set time) before it. */
export interface ShiftInfo {
  at: string;
  minutes: number;
  warning: boolean;
  /** Boarding patients you own, and how many of them have a handoff for your role not signed yet. */
  mine: number;
  unsigned: number;
}

export interface TakeoverRow {
  csn: string;
  patient: { name: string; mrn: string; sex: string; age: number | null };
  severity: Severity | null;
  chief_complaint: string;
  facility: string;
  destination: { pending_unit?: string; pending_room?: string; pending_bed?: string; level_of_care?: string };
  minutes_boarding: number;
  owner: Owner;
  state: WorkflowState;
  signed?: { by: string; role: string; callback?: string; at: string; version: number } | null;
  acknowledged?: { by: string; at: string; version: number } | null;
  /** Items that still block signing the handoff for your role. */
  blocking: number;
  /** Code status, allergies and isolation not confirmed yet. */
  to_confirm: string[];
  /** Changed after signing: the items that changed since the signature. */
  changed: string[];
}

export interface Takeover {
  at: string;
  role: EdRole;
  template: string;
  colleagues: Colleague[];
  colleague: Colleague | null;
  rows: TakeoverRow[];
  /** After a Take over: the patients just taken. */
  taken?: string[];
}

/** Tasks: who a task is for (nurse or physician), and its state. */
export type TaskRole = 'nurse' | 'physician';
export type TaskStatus = 'open' | 'done' | 'not_needed';
export type TaskEventKind = 'create' | 'done' | 'not_needed' | 'reopen' | 'reassign' | 'due';

/** Open and overdue tasks of a patient (the board row). */
export interface TaskCounts {
  open: number;
  overdue: number;
}

/** One change to a task, with who and when. */
export interface TaskStep {
  kind: TaskEventKind;
  by: string;
  by_role: string;
  at: string;
  reason?: string | null;
  role?: TaskRole;
  to?: string | null;
  from_role?: TaskRole | null;
  from?: string | null;
  due?: string | null;
  from_due?: string | null;
  /** Recorded by Undo. */
  undo?: boolean;
  /** Done by someone who did not have the patient then. */
  outside_role?: boolean;
}

export interface Task {
  id: number;
  csn: string;
  text: string;
  role: TaskRole;
  /** A colleague the task was given to, or null when it is for the role. */
  assignee: { id: string; name: string } | null;
  due: string | null;
  /** The action list's own words for when (such as "ongoing"), for a task from a handoff. */
  due_text: string | null;
  overdue: boolean;
  status: TaskStatus;
  reason: string | null;
  source: 'handoff' | 'added';
  template: string | null;
  version: number | null;
  /** The action list's own words for who. */
  owner_text: string | null;
  created: { by: string; at: string };
  closed: { by: string; at: string } | null;
  /** What Undo would revert, or null when there is nothing to undo. */
  undo: 'done' | 'not_needed' | 'reassign' | 'due' | null;
  history: TaskStep[];
}

/** Someone a task can go to. */
export interface TaskPerson {
  id: string;
  name: string;
  role: TaskRole;
  role_label: string | null;
}

/** GET /api/handoffs/{csn}/tasks, and the answer to every task change. */
export interface PatientTasks {
  at: string;
  csn: string;
  phase: Phase;
  tasks: Task[];
  counts: TaskCounts;
  /** can_act: false for the viewer. in_role: you have the patient now; note: what the panel says otherwise. */
  access: { can_act: boolean; in_role: boolean; note: string | null };
  people: TaskPerson[];
}

/** A task on the Tasks page, with its patient. */
export interface TaskRow extends Task {
  patient: { name: string; mrn: string; sex: string; age: number | null };
  phase: Phase;
  facility: string;
  unit: string | null;
  severity: Severity | null;
  in_role: boolean;
}

export type TaskGroupId = 'overdue' | 'soon' | 'later' | 'none';

/** GET /api/tasks */
export interface TasksPage {
  at: string;
  scope: 'mine' | 'all';
  role: TaskRole | null;
  groups: { id: TaskGroupId; tasks: TaskRow[] }[];
  /** Closed in the last 12 hours, newest first. */
  done: TaskRow[];
  counts: TaskCounts;
}

export interface TemplateInfo {
  id: string;
  name: string;
  audience: string;
}

export interface Unit {
  dept_id: string;
  facility: string;
  code: string;
  name: string;
  level: string;
  beds: number;
}

export interface AppConfig {
  mode: 'mock' | 'live';
  clock: 'replay' | 'live';
  demo_personas: boolean;
  source: string;
  drafts_from: string;
  models: string[];
  window: { start: string; end: string };
  default_at: string;
  facilities: { code: string; name: string }[];
  units: Unit[];
  personas: Persona[];
  templates: TemplateInfo[];
  store: string;
  /** False until the data is loaded (live mode before setup finishes); detail says why. */
  ready?: boolean;
  detail?: string | null;
  /** The facilities' time zone (IANA name); times are shown in it. */
  timezone?: string;
  /** Live clock only: how many hours back a clinician can look, read only (0: not at all). */
  past_view_hours?: number;
  /** The ED shift changes (facility local times), how long before one the board warns, and the stale owner limit. */
  shifts?: { changes: string[]; warning_minutes: number; stale_owner_hours: number };
}

export interface Workflow {
  state: WorkflowState;
  version?: number;
  signed?: { by: string; role: string; callback?: string; at: string; version: number };
  acknowledged?: { by: string; at: string; version: number; readback?: string; questions?: string | null };
  /** A newer version whose clinical content differs from the signed one. */
  changed_since_signed?: boolean;
  /** An edit or attestation after the signature. */
  edited_since_signed?: boolean;
  /** Signed again after the receiver had accepted an earlier signature. */
  changed_since_acknowledged?: boolean;
}

export interface TemplateSummary {
  version: number;
  as_of: string;
  status: 'draft' | 'final' | 'addendum' | 'retracted';
  trigger: string;
  score: number;
  blocking: string[];
  workflow: Workflow;
}

export interface CaseSummary {
  csn: string;
  facility: string;
  phase: Phase;
  patient: { name: string; mrn: string; sex: string; age: number | null };
  chief_complaint: string;
  esi: number | null;
  service: string | null;
  arrival_ts: string;
  admit_decision_ts: string;
  ed_departure_ts: string | null;
  minutes_boarding: number;
  location: { unit?: string; room?: string; bed?: string };
  destination: { pending_unit?: string; pending_room?: string; pending_bed?: string; level_of_care?: string };
  severity: Severity | null;
  severity_floor: Severity | null;
  code_status: string | null;
  isolation: string | null;
  allergies: string[];
  /** Entered in the sign off while the chart has none. */
  entered?: { code_status: EnteredFlag | null; isolation: EnteredFlag | null; allergies: EnteredFlag[] };
  last_update: { as_of: string; trigger: string };
  templates: Record<string, TemplateSummary>;
  /** The nurse and physician owner, or null when no one owns the patient for that role. */
  owners?: Record<EdRole, Owner | null>;
  /** Open and overdue tasks. */
  tasks?: TaskCounts;
}

export interface Board {
  at: string;
  template: string;
  kpis: {
    boarding: number;
    median_boarding_min: number | null;
    unstable: number;
    to_sign: number | null;
    to_acknowledge: number | null;
    blocking: number | null;
    arrived: number;
    /** ED roles: arrived on the unit with no sign off of the role's template; null for other roles. */
    left_unsigned?: number | null;
    /** ED roles: boarding patients you own, and boarding patients no one owns for your role. */
    mine?: number;
    unassigned?: number;
  };
  rows: CaseSummary[];
  /** ED roles: the next shift change; null for other roles. */
  shift?: ShiftInfo | null;
}

export interface Rule {
  rule: string;
  element: string;
  level: 'hard' | 'stale' | 'soft';
  label: string;
  status: 'pass' | 'missing' | 'stale' | 'attest' | 'deferred';
  message?: string;
  evidence?: string[];
  attest_options?: string[];
  /** Choices that need words: the field label, and the label of an optional note (the reaction to an allergen). */
  attest_typed?: Record<string, { label: string; note: string | null }>;
  /** entered: the choice entered a value (Droplet, DNR, an allergy) rather than confirming there is nothing. */
  resolution?: { kind: 'attested' | 'edited'; option?: string; by: string; at?: string; version?: number; entered?: boolean };
  /** A bedside recheck older than the rule's limit for the severity: it no longer counts. */
  attest_expired?: { by: string; at: string; until: string; limit_min: number; severity: string };
}

export interface NarrativeItem {
  text: string;
  owner?: string | null;
  due?: string | null;
  sources?: string[];
  unsupported_numbers?: string[];
  edited?: boolean;
}

export interface Narrative {
  value?: Severity;
  text?: string;
  items?: NarrativeItem[];
  sources?: string[];
  confidence?: number;
  unsupported_numbers?: string[];
  weakly_supported_numbers?: string[];
  model_value?: Severity;
  floor?: { value: Severity; reasons: string[] };
  edited?: { by: string; at: string; version: number; model_changed: boolean };
  model_text?: { text?: string; items?: NarrativeItem[] };
}

export interface Element {
  id: string;
  label: string;
  kind: 'deterministic' | 'narrative' | 'process';
  data?: unknown;
  narrative?: Narrative;
  rules: Rule[];
  /** Values the ED clinician entered for a chart item the chart does not have yet (code status, allergies,
   *  isolation): patient level, the same on every template. Allergies can hold several. */
  entered?: EnteredValue[];
}

export interface EnteredValue {
  rule: string;
  option: string;
  value: string;
  note: string | null;
  by: string;
  at: string;
  version: number;
  /** The template it was entered on. */
  template: string;
}

/** An entered value as the patient box and the board show it. */
export interface EnteredFlag {
  value: string;
  note: string | null;
  by: string;
  template: string;
}

export interface Section {
  id: string;
  label: string;
  elements: Element[];
}

export interface VitalSet {
  taken_ts: string;
  hr?: number;
  sbp?: number;
  dbp?: number;
  map?: number;
  rr?: number;
  spo2?: number;
  temp?: number;
  pain?: number;
  gcs?: number;
  o2_device?: string;
}

export interface LabPoint {
  ts: string;
  value: number;
  units?: string;
  flag?: string | null;
}

export interface Draft {
  template: string;
  name: string;
  audience: string;
  as_of: string;
  sections: Section[];
  rules: Rule[];
  completeness: { score: number; blocking: string[]; model_score: number };
  severity: Severity;
  evidence: Record<string, Record<string, unknown>>;
  trend: { vitals: VitalSet[]; labs: Record<string, LabPoint[]> };
}

export interface Change {
  id: string;
  label: string;
  kind: string;
  before: unknown;
  after: unknown;
}

export interface CaseTemplate {
  draft: Draft;
  row: { version: number; status: string; trigger: string; model: string; as_of: string };
  workflow: Workflow;
  changes: { since: 'signed' | 'previous'; version: number | null; elements: Change[] };
  versions: { version: number; as_of: string; trigger: string; status: string; severity: Severity; score: number }[];
}

export interface CaseDetail {
  summary: CaseSummary;
  templates: Record<string, CaseTemplate>;
  activity: {
    kind: string; template: string | null; by: string; at: string; version: number | null; detail?: string | null; restore?: boolean; entered?: string; note?: string; removed?: string;
    /** Done by an ED clinician on a patient a colleague owned. */
    off_assignment?: boolean; assigned_to?: string;
    /** kind "task": the task event, its task, and whether it was outside the role (detail: the task's text). */
    task?: TaskEventKind; task_id?: number; outside_role?: boolean;
  }[];
}

export interface Analytics {
  at: string;
  /** unassigned_rn, unassigned_md: boarding patients with no nurse owner, and with no physician owner, that hour. */
  series: { t: string; ed: number; boarding: number; unassigned_rn?: number; unassigned_md?: number }[];
  units: { unit: string; n: number; median_min: number | null; p90_min: number | null }[];
  admitted: number;
  drafts_per_patient: { median: number | null; max: number | null };
  completeness_at_transfer: { median: number | null; histogram: { bin: string; n: number }[] };
  blocking_at_transfer: { label: string; n: number }[];
  severity_at_transfer: Record<string, number>;
  signed_before_transfer: number;
  transferred_without_sign_off: number;
  drafts: number;
  model_calls: number;
  addenda: number;
  /** Owners: ED actions outside an assignment (of all ED edits, attestations and signatures), handovers by distance
   *  from the nearest shift change, and minutes from admit decision to the first signature by who owned the patient. */
  owners?: {
    /** When the first owner was recorded, or null: before it every patient is unassigned, which says nothing. */
    since: string | null;
    outside: { role: EdRole; n: number; of: number }[];
    handovers: { bucket: string; n: number }[];
    to_sign: { role: EdRole; owner: string | null; n: number; median_min: number }[];
  };
}

export interface TemplateDef {
  id: string;
  name: string;
  audience: string;
  description?: string;
  sections: { id: string; label: string; elements: string[] }[];
  rules: { id: string; element: string; level: string; attest?: string[]; at?: string; max_age_min?: Record<string, number> }[];
  narrative: string[];
}
