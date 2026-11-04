export type Severity = 'stable' | 'watcher' | 'unstable';
export type WorkflowState = 'drafting' | 'draft' | 'signed' | 'changed' | 'acknowledged' | 'transferred_unsigned';
export type Phase = 'boarding' | 'arrived' | 'canceled';

export interface Persona {
  id: string;
  name: string;
  role: 'ed_nurse' | 'ed_physician' | 'unit_nurse' | 'admitting_physician';
  role_label: string;
  unit: string | null;
  unit_name?: string;
  facility: string | null;
  default_template: string;
  can_sign: string[];
  can_ack: string[];
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
  };
  rows: CaseSummary[];
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
  activity: { kind: string; template: string; by: string; at: string; version: number; detail?: string; restore?: boolean; entered?: string; note?: string; removed?: string }[];
}

export interface Analytics {
  at: string;
  series: { t: string; ed: number; boarding: number }[];
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
