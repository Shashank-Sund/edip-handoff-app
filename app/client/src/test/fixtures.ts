/** Small typed fixtures shared by the component tests. */
import type { AppConfig, Board, CaseSummary, CaseTemplate, Draft, Element, Persona, Rule, TemplateSummary } from '@/lib/types';

export const AT = '2026-03-10T15:00:00Z';

export const edNurse: Persona = {
  id: 'ed-rn',
  name: 'Jordan Reyes, RN',
  role: 'ed_nurse',
  role_label: 'ED nurse',
  unit: null,
  facility: null,
  default_template: 'sbar_nurse',
  can_sign: ['sbar_nurse'],
  can_ack: [],
};

export const config: AppConfig = {
  mode: 'mock',
  clock: 'replay',
  demo_personas: true,
  source: 'synthetic',
  drafts_from: 'mock',
  models: ['mock-model'],
  window: { start: '2026-03-09T00:00:00Z', end: '2026-03-16T00:00:00Z' },
  default_at: AT,
  facilities: [
    { code: 'SGH', name: 'South General' },
    { code: 'NMC', name: 'North Medical' },
  ],
  units: [{ dept_id: '1', facility: 'SGH', code: 'MICU', name: 'Medical ICU', level: 'ICU', beds: 12 }],
  personas: [edNurse],
  templates: [{ id: 'sbar_nurse', name: 'SBAR nurse', audience: 'unit nurse' }],
  store: 'memory',
};

export function templateSummary(over: Partial<TemplateSummary> = {}): TemplateSummary {
  return {
    version: 2,
    as_of: '2026-03-10T14:40:00Z',
    status: 'draft',
    trigger: 'bed_assigned',
    score: 0.8,
    blocking: [],
    workflow: { state: 'draft' },
    ...over,
  };
}

export function caseSummary(over: Partial<CaseSummary> = {}): CaseSummary {
  return {
    csn: '1001',
    facility: 'SGH',
    phase: 'boarding',
    patient: { name: 'Doe, Alex', mrn: 'M0001', sex: 'F', age: 72 },
    chief_complaint: 'Shortness of breath',
    esi: 2,
    service: 'Medicine',
    arrival_ts: '2026-03-10T08:00:00Z',
    admit_decision_ts: '2026-03-10T11:00:00Z',
    ed_departure_ts: null,
    minutes_boarding: 240,
    location: { unit: 'ED', room: '12', bed: 'A' },
    destination: { pending_unit: 'MICU', pending_room: '4', pending_bed: 'B', level_of_care: 'ICU' },
    severity: 'watcher',
    severity_floor: null,
    code_status: 'Full Code',
    isolation: null,
    allergies: [],
    last_update: { as_of: '2026-03-10T14:40:00Z', trigger: 'bed_assigned' },
    templates: { sbar_nurse: templateSummary() },
    ...over,
  };
}

export function board(rows: CaseSummary[]): Board {
  return {
    at: AT,
    template: 'sbar_nurse',
    kpis: { boarding: 2, median_boarding_min: 200, unstable: 1, to_sign: 2, to_acknowledge: null, blocking: 1, arrived: 1 },
    rows,
  };
}

export function rule(over: Partial<Rule> = {}): Rule {
  return { rule: 'R1', element: 'E10', level: 'hard', label: 'Isolation status', status: 'missing', message: 'Isolation status is not documented.', ...over };
}

export function draft(over: Partial<Draft> = {}): Draft {
  return {
    template: 'sbar_nurse',
    name: 'SBAR nurse',
    audience: 'unit nurse',
    as_of: '2026-03-10T14:40:00Z',
    sections: [],
    rules: [],
    completeness: { score: 0.9, blocking: [], model_score: 0.9 },
    severity: 'watcher',
    evidence: {},
    trend: { vitals: [], labs: {} },
    ...over,
  };
}

export function caseTemplate(over: Partial<CaseTemplate> = {}): CaseTemplate {
  return {
    draft: draft(),
    row: { version: 2, status: 'draft', trigger: 'bed_assigned', model: 'mock-model', as_of: '2026-03-10T14:40:00Z' },
    workflow: { state: 'draft' },
    changes: { since: 'previous', version: null, elements: [] },
    versions: [],
    ...over,
  };
}

export function element(over: Partial<Element> = {}): Element {
  return { id: 'E11', label: 'Assessment', kind: 'narrative', narrative: {}, rules: [], ...over };
}
