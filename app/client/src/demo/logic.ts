/**
 * Port of app/server/logic.py: views computed from the read model and the clinician actions at a time `at`.
 *
 *   board(at, person)        patients with a handoff: boarding now, or arrived on the unit in the last 12 hours
 *   case(csn, at, person)    one patient: every template's effective draft, its rules after resolution,
 *                            workflow state, versions, changes
 *   analytics(at)            census, boarding times, handoff timing and completeness across the week so far
 *   act(...)                 validates and records an edit, attestation, sign off or acknowledgement
 *   own(...)                 Take, Assign to a colleague, Release: who owns the patient for the ED role
 *   takeover(...), takeOver(...)   a colleague's patients at a shift change, and taking some or all of them
 *   patientTasks(...), addTask(...), taskAct(...), myTasks(...)   tasks (tasks.ts): one patient's, a new one, a
 *                            change to one, and the caller's across patients for the Tasks page
 *   look(...)                records a look at a patient (seen.ts): the board row and the case show what others changed
 *                            since the caller's last look
 *
 * Signing a handoff turns its action list into tasks; acting on a task while someone else has the patient is
 * allowed and recorded as outside the role (outside_role).
 *
 * Sign off follows the content: any edit or attestation after a signature, or a newer version whose clinical
 * content differs from the signed one, puts the template in state "changed" (sign again); the receiver cannot
 * accept it until the ED signs again. A stale vitals attestation counts only within that rule's age limit.
 *
 * Keep it in step with logic.py: app/fixtures/parity.py and src/demo/parity.test.ts compare the two.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { actor, counts, HANDOVER_BUCKETS, handoverBucket, nextShift, ownerAt, personKey, ROLES } from './assignment';
import { statusAt, type Dataset, type Row } from './data';
import { ACKS, SIGNS, sees } from './personas';
import { cmpStr, get, HOUR, iso, isDict, letters, median, or, pyEq, pyLen, pyRound, pySplit, pyStr, strip, truthy, ts } from './py';
import * as seen from './seen';
import type { Look } from './seen';
import * as tasks from './tasks';
import type { TaskEvent } from './tasks';
import type { Action, MemoryStore } from './workflow';

const ARRIVED_WINDOW = 12 * HOUR;
const MINUTE = 60_000;
const STALE_OPTION = 'Rechecked at bedside, unchanged';
const STALE_ATTEST = [STALE_OPTION];
const SEVERITY_RANK: Record<string, number> = { stable: 0, watcher: 1, unstable: 2 };
// Where the patient is and how they get there: the transfer itself changes these, which is no reason to sign again.
const NOT_CLINICAL = new Set(['E04', 'E28']);
const MAX_TEXT = 2000;
const MAX_CALLBACK = 40;
const EMPTY_EDIT = "Write the text, or use Restore to bring back the model's text.";
const TOO_LONG = 'Keep each text under 2,000 characters.';
const EARLIER = 'You are viewing an earlier time; move the clock to now to act.';
const READBACK = 'Write a short read back: the patient, the key risk and what you will do first.';
const CALLBACK = 'Add a callback number the receiver can dial: at least 3 digits, or an extension like x4471.';
const UNSIGNED = ['draft', 'transferred_unsigned'];
// Attestation choices that need words from the clinician. The template lists the choice (ai/templates); the app asks
// for the text. The first choice of a rule confirms there is nothing to add ("No isolation needed"); any other choice
// is a value the clinician entered, shown on the item until the chart has one.
const TYPED: Record<string, { label: string; note: string | null; required: string }> = {
  'E10|Other': { label: 'Type of isolation', note: null, required: 'Write the type of isolation.' },
  'E09|Add an allergy': { label: 'Allergen', note: 'Reaction (optional)', required: 'Write the allergen.' },
};
// Code status, allergies and isolation belong to the patient, not to one template: a value entered on any template
// shows on every template, and a template cannot confirm "none" while another holds an entered value (or the
// reverse). Allergies are a list; the others hold one value, the latest entered.
const PATIENT = new Set(['E08', 'E09', 'E10']);
const ALLERGY = 'Add an allergy';
const PATIENT_LABEL: Record<string, string> = { E08: 'Code status', E09: 'An allergy', E10: 'Isolation' };
const TEMPLATE_NAME: Record<string, string> = { sbar_nurse: 'SBAR', ipass_physician: 'I-PASS', custom_sea58: 'Joint Commission minimum' };
// The patient level items Take over lists when their attestation is not confirmed yet.
const CONFIRM_LABEL: Record<string, string> = { E08: 'Code status', E09: 'Allergies', E10: 'Isolation' };
const NOT_ED = 'Only ED nurses and physicians take patients.';
const SEEN_SOURCE = 'A look comes from the handoff page or the board.';
// The demo's starting tasks (seedTasks), per role: [text, due in ms from the default time, null for no due time].
const SEED_TASKS: [string, [string, number | null][]][] = [
  ['ed_nurse', [['Recheck blood pressure after the fluid bolus', -30 * MINUTE], ['Send the urine sample to the lab', 90 * MINUTE],
    ['Confirm the bed with the receiving unit', 5 * HOUR], ['Ask the family to bring the home medication list', null]]],
  ['ed_physician', [['Order the repeat ECG', -20 * MINUTE], ['Review the repeat troponin', HOUR],
    ['Talk with the family about the plan of care', 4 * HOUR], ['Reconcile the home medications', null]]],
];
// Tasks: what the panel and the Tasks page say, and the refusals.
const MAX_REASON = 200;
const TASK_READ_ONLY = 'The viewer role can read tasks but not change them.';
const NO_TASK = 'There is no such task.';
const TASK_TEXT = 'Write what needs to be done.';
const TASK_ROLE = 'Choose nurse or physician.';
const TASK_COLLEAGUE = 'Choose a colleague from the list.';
const TASK_ROLE_MISMATCH = 'Choose a colleague in that role, or only the role.';
const TASK_REASON = 'Write a short reason the task is not needed.';
const TASK_REASON_LONG = 'Keep the reason under 200 characters.';
const TASK_DUE = 'The due time must be a date and time.';
const TASK_CLOSED = 'This task is closed. Press Undo to open it again first.';
const TASK_UNDO = 'There is nothing to undo on this task.';
const TASK_KIND = 'Choose Done, Not needed, Reassign, Change due time or Undo.';
const OUTSIDE_ROLE: Record<string, string> = {
  boarding: 'The ED nurse and physician have this patient while they board. You can still act on a task; it is recorded as outside your role.',
  canceled: 'The ED nurse and physician have this patient. You can still act on a task; it is recorded as outside your role.',
  arrived: 'The receiving nurse and the admitting physician have this patient now. You can still act on a task; it is recorded as outside your role.',
};
// The template each ED role signs and owns the patient for, in Analytics.
const OWN_TEMPLATE: [string, string][] = [['ed_nurse', 'sbar_nurse'], ['ed_physician', 'ipass_physician']];

type Rules = Record<string, any>;

/** A request the rules do not allow; the message is shown to the user as is. */
export class ActionError extends Error {}

// ---------------------------------------------------------------------------------------------- helpers
function age(birth: string | null, at: number): number | null {
  if (!birth) return null;
  const [y, m, d] = String(birth).slice(0, 10).split('-').map(Number);
  const t = new Date(at);
  const [ay, am, ad] = [t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate()];
  return ay - y - (am < m || (am === m && ad < d) ? 1 : 0);
}

function elements(draft: any): Map<string, any> {
  const out = new Map<string, any>();
  for (const s of draft?.sections ?? []) for (const e of s.elements) out.set(e.id, e);
  return out;
}

const STRIPPED = new Set(['src', 'sources', 'confidence', 'unsupported_numbers', 'weakly_supported_numbers', 'minutes_in_ed',
  'minutes_boarding', 'latest_age_min']);

function stripMeta(v: any): any {
  if (isDict(v)) {
    const out: any = {};
    for (const [k, x] of Object.entries(v)) if (!STRIPPED.has(k)) out[k] = stripMeta(x);
    return out;
  }
  if (Array.isArray(v)) return v.map(stripMeta);
  return v;
}

function content(e: any): any {
  if (e.kind === 'narrative') {
    const n = or(get(e, 'narrative'), {}) as any;
    if (Array.isArray(get(n, 'items'))) return n.items.map((i: any) => ({ text: get(i, 'text'), owner: get(i, 'owner'), due: get(i, 'due') }));
    return e.id === 'E07' ? { value: get(n, 'value'), text: get(n, 'text') } : get(n, 'text');
  }
  if (truthy(get(e, 'entered'))) { // a value the clinician entered is content like any other
    return { chart: stripMeta(get(e, 'data')), entered: e.entered.map((x: any) => ({ value: x.value, note: x.note })) };
  }
  return stripMeta(get(e, 'data'));
}

/** Actions in the order they apply: by the time they apply to, then the order they were saved. */
function ordered(acts: Action[]): Action[] {
  return [...acts].sort((a, b) => a.at - b.at || a.recorded_ts - b.recorded_ts || a.seq - b.seq);
}

/** Python's sort by (at, recorded_ts) then a dict keyed by key(a): the latest wins, first seen order. */
function latestBy(acts: Action[], kind: string, key: (a: Action) => string): Map<string, Action> {
  const out = new Map<string, Action>();
  for (const a of ordered(acts.filter((x) => x.kind === kind))) out.set(key(a), a);
  return out;
}

function dropRecorded(v: any): any {
  if (isDict(v)) {
    const out: any = {};
    for (const [k, x] of Object.entries(v)) if (k !== 'recorded_ts') out[k] = dropRecorded(x);
    return out;
  }
  if (Array.isArray(v)) return v.map(dropRecorded);
  return v;
}

/** The content sign off is about: every item but where the patient is; a value recorded again unchanged is no change. */
function clinical(draft: any): any {
  const out: any = {};
  for (const [eid, e] of elements(draft)) if (!NOT_CLINICAL.has(eid)) out[eid] = dropRecorded(content(e));
  return out;
}

/** The latest sign off of a template, the actions up to and including it, and the actions after it. */
function lastSign(acts: Action[], template: string): [Action | null, Action[], Action[]] {
  const mine = ordered(acts.filter((a) => a.template === template));
  let i = -1;
  mine.forEach((a, j) => {
    if (a.kind === 'sign') i = j;
  });
  if (i < 0) return [null, [], mine];
  return [mine[i], mine.slice(0, i + 1), mine.slice(i + 1)];
}

/** {template id: {rule id: rule}} from the template definitions (ai/templates). */
export function ruleIndex(templates: any[]): Record<string, Rules> {
  const out: Record<string, Rules> = {};
  for (const t of templates) {
    const r: Rules = {};
    for (const x of t.rules ?? []) r[x.id] = x;
    out[t.id] = r;
  }
  return out;
}

/**
 * A rule that applies only in the high risk branch (it has `when`) replaces the template's other rules on the same
 * item: a watcher or unstable patient cannot be attested "Stable, nothing anticipated", and the item shows one rule.
 */
function supersede(d: any, rules: Rules): void {
  const present = new Set<string>((d.rules ?? []).map((r: any) => r.rule));
  const drop = new Set<string>();
  for (const [w, wd] of Object.entries(rules)) {
    if (!truthy(get(wd, 'when')) || !present.has(w)) continue;
    for (const [rid, rd] of Object.entries(rules)) {
      if (rid !== w && pyEq(get(rd, 'element'), get(wd, 'element')) && !truthy(get(rd, 'when'))) drop.add(rid);
    }
  }
  if (![...drop].some((x) => present.has(x))) return;
  d.rules = d.rules.filter((r: any) => !drop.has(r.rule));
  for (const e of elements(d).values()) {
    if (truthy(get(e, 'rules'))) e.rules = e.rules.filter((r: any) => !drop.has(r.rule));
  }
}

/** An attestation that enters a value: any choice but the rule's first, which confirms there is nothing to add. */
function entered(a: Action, options: any): boolean {
  return (truthy(options) && !pyEq(a.option, options[0])) || truthy(get(a, 'entry'));
}

function optionsOf(index: Record<string, Rules>, template: string, rule: string): any[] {
  return or(get(or(get(or(index[template] ?? null, {}), rule), {}), 'attest'), []) as any[];
}

function entryOf(a: Action): any {
  return {
    rule: a.rule, option: a.option, value: or(get(a, 'entry'), a.option), note: get(a, 'entry_note'), by: a.by, at: iso(a.at),
    version: a.version, template: a.template,
  };
}

/** a after b in the order actions apply: the time they apply to, then the order they were saved. */
function later(a: Action, b: Action): boolean {
  return a.at > b.at || (a.at === b.at && (a.recorded_ts > b.recorded_ts || (a.recorded_ts === b.recorded_ts && a.seq > b.seq)));
}

/**
 * The patient level values entered on any template (index: the rules of every template): E08 and E10 an entry or
 * null, E09 a list, and `none` each template's current "nothing to add" choice, keyed "template|element".
 */
export function patientEntries(acts: Action[], index: Record<string, Rules>): any {
  const current = new Map<string, Action>();
  const allergies = new Map<string, any>();
  for (const a of ordered(acts.filter((x) => x.kind === 'attest'))) {
    const el = get(or(get(or(index[a.template] ?? null, {}), a.rule), {}), 'element');
    if (!PATIENT.has(el)) continue;
    current.set(`${a.template}|${el}`, a);
    if (el === 'E09' && a.option === ALLERGY) {
      const key = strip(or(get(a, 'entry'), '') as string).toLowerCase();
      if (truthy(get(a, 'remove'))) allergies.delete(key);
      else if (key) allergies.set(key, entryOf(a));
    }
  }
  const out: any = { E09: [...allergies.values()], none: new Map<string, Action>() };
  for (const el of ['E08', 'E10']) {
    let best: Action | null = null;
    for (const [k, a] of current) {
      const t = k.slice(0, k.lastIndexOf('|'));
      if (k.endsWith(`|${el}`) && !truthy(get(a, 'remove')) && entered(a, optionsOf(index, t, a.rule)) && (best === null || later(a, best))) best = a;
    }
    out[el] = best ? entryOf(best) : null;
  }
  for (const [k, a] of current) {
    const t = k.slice(0, k.lastIndexOf('|'));
    if (!truthy(get(a, 'remove')) && !entered(a, optionsOf(index, t, a.rule))) out.none.set(k, a);
  }
  return out;
}

function callbackOk(cb: string): boolean {
  return (cb.match(/[0-9]/g) ?? []).length >= 3 || /^(x|ext\.?) ?[0-9]{2,}$/i.test(cb);
}

/** At least a sentence: three words, fifteen letters, and not one character repeated. */
function readbackOk(text: string): boolean {
  const ls = letters(text);
  const words = pySplit(text).filter((w) => letters(w).length > 0);
  return words.length >= 3 && ls.length >= 15 && new Set(ls.map((c) => c.toLowerCase())).size >= 2;
}

function clone<T>(v: T): T {
  return structuredClone(v);
}

// ------------------------------------------------------------------------------------------ effective draft
/**
 * The draft as the clinician sees it: edits applied over model text, rules resolved by attestations and edits.
 * Edits and attestations carry forward to newer versions of the same template, flagged when the model's text
 * changed underneath an edit.
 */
export function effective(row: Row, acts: Action[], at: number | null = null, rules: Rules | null = null, patient: any = null): any {
  const t = row.template;
  const rs: Rules = rules ?? {};
  const pat = patient ?? patientEntries(acts, { [t]: rs });
  const mine = acts.filter((a) => a.template === t);
  const edits = latestBy(mine, 'edit', (a) => a.element);
  const attests = latestBy(mine, 'attest', (a) => a.rule);
  const d = clone(row.draft);
  supersede(d, rs);
  const els = elements(d);
  for (const [eid, a] of edits) {
    const e = els.get(eid);
    if (!truthy(e) || e.kind !== 'narrative' || truthy(get(a, 'restore'))) continue;
    if (!('narrative' in e)) e.narrative = {};
    const n = or(e.narrative, {}) as any;
    e.narrative = n;
    const model = { text: get(n, 'text'), items: get(n, 'items') };
    if (get(a, 'items') !== null) {
      n.items = a.items.map((i: any) => ({
        text: i.text === undefined ? '' : i.text, owner: get(i, 'owner'), due: get(i, 'due'), sources: [], edited: true,
      }));
    } else {
      n.text = or(get(a, 'text'), '');
    }
    n.edited = { by: a.by, at: iso(a.at), version: a.version, model_changed: a.version !== row.version && !pyEq(model, get(a, 'model_before')) };
    n.model_text = model;
  }
  const sev = or(or(get(or(get(or(els.get('E07') ?? null, {}), 'narrative'), {}), 'value'), get(row, 'severity')), 'watcher') as string;
  for (const r of d.rules ?? []) {
    const e = els.get(r.element) ?? null;
    if (r.status === 'stale') {
      r.attest_options = STALE_ATTEST;
    } else if (r.status === 'attest' && truthy(get(or(rs[r.rule] ?? null, {}), 'attest'))) {
      // The choices of the current templates, not the ones the draft was written with: no redraft needed.
      r.attest_options = [...rs[r.rule].attest];
    }
    const typed: Record<string, { label: string; note: string | null }> = {};
    for (const o of (or(get(r, 'attest_options'), []) as any[])) {
      const x = TYPED[`${r.element}|${o}`];
      if (x) typed[o] = { label: x.label, note: x.note };
    }
    if (truthy(typed)) r.attest_typed = typed;
    if (r.status === 'attest' && PATIENT.has(r.element)) {
      // Patient level: an entered value from any template resolves it; otherwise this template's "none".
      const a = attests.get(r.rule);
      const found: any[] = r.element === 'E09' ? pat.E09 : [pat[r.element]].filter(Boolean);
      if (found.length && truthy(e)) {
        const last = found[found.length - 1];
        r.resolution = { kind: 'attested', option: last.option, by: last.by, at: last.at, version: last.version, entered: true };
        e.entered = found;
      } else if (a && !truthy(get(a, 'remove')) && !entered(a, get(r, 'attest_options'))) {
        r.resolution = { kind: 'attested', option: a.option, by: a.by, at: iso(a.at), version: a.version };
      }
    } else if (r.status === 'attest' || r.status === 'missing' || r.status === 'stale') {
      let a = attests.get(r.rule);
      if (a && r.status !== 'missing' && r.level === 'stale' && a.option === STALE_OPTION && at !== null) {
        const mm = or(get(or(rs[r.rule] ?? null, {}), 'max_age_min'), {}) as any;
        const limit = Object.prototype.hasOwnProperty.call(mm, sev) ? mm[sev] : 60;
        const until = a.at + limit * MINUTE;
        if (at > until) { // the recheck is as old as the vitals limit: recheck again
          r.attest_expired = { by: a.by, at: iso(a.at), until: iso(until), limit_min: limit, severity: sev };
          a = undefined;
        }
      }
      const n = e ? (or(get(e, 'narrative'), {}) as any) : null;
      if (a && r.status !== 'missing') {
        r.resolution = { kind: 'attested', option: a.option, by: a.by, at: iso(a.at), version: a.version };
      } else if (truthy(e) && e.kind === 'narrative' && truthy(get(n, 'edited')) &&
        (truthy(strip(or(get(e.narrative, 'text'), '') as string)) || truthy(get(e.narrative, 'items')))) {
        r.resolution = { kind: 'edited', by: e.narrative.edited.by };
      }
    }
  }
  for (const e of els.values()) { // element rules mirror the template level results
    for (const r of get(e, 'rules') ?? []) {
      const match = d.rules.find((x: any) => x.rule === r.rule);
      if (match) Object.assign(r, match);
    }
  }
  const judged = d.rules.filter((r: any) => r.status !== 'deferred');
  const ok = judged.filter((r: any) => r.status === 'pass' || truthy(get(r, 'resolution')));
  const blocking = judged
    .filter((r: any) => (r.level === 'hard' || r.level === 'stale') && r.status !== 'pass' && !truthy(get(r, 'resolution')))
    .map((r: any) => r.rule);
  d.completeness = { score: pyRound(ok.length / Math.max(1, judged.length), 3), blocking, model_score: row.draft.completeness.score };
  return d;
}

/**
 * draft, signed, changed (after signing: sign again), acknowledged, or transferred_unsigned (the patient left the ED
 * with no sign off); with who and when. versions: the rows to find the signed version in.
 */
/** Every action, of any template, saved up to and including the sign off s. */
function until(acts: Action[], s: Action): Action[] {
  return acts.filter((a) => !later(a, s));
}

export function workflow(row: Row | null, acts: Action[], versions: Row[], departed = false, rules: Rules | null = null,
  index: Record<string, Rules> | null = null): any {
  if (row === null) return { state: 'drafting' };
  const t = row.template;
  const [s, before, after] = lastSign(acts, t);
  const out: any = { state: 'draft', version: row.version };
  if (s === null) {
    if (departed) out.state = 'transferred_unsigned';
    return out;
  }
  Object.assign(out, { state: 'signed', signed: { by: s.by, role: s.role, callback: get(s, 'callback'), at: iso(s.at), version: s.version } });
  const idx = index ?? { [t]: rules ?? {} };
  const signedPat = patientEntries(until(acts, s), idx), nowPat = patientEntries(acts, idx);
  let changed = false;
  if (row.version !== s.version) {
    const base = versions.find((v) => v.template === t && v.version === s.version) ?? null;
    changed = base === null || !pyEq(clinical(effective(base, before, null, rules, signedPat)), clinical(effective(row, before, null, rules, signedPat)));
  }
  out.changed_since_signed = changed;
  // An edit or attestation after signing, here or (for code status, allergies, isolation) on another template.
  out.edited_since_signed = after.some((a) => a.kind === 'edit' || a.kind === 'attest')
    || !pyEq(clinical(effective(row, before, null, rules, signedPat)), clinical(effective(row, before, null, rules, nowPat)));
  if (changed || out.edited_since_signed) {
    out.state = 'changed';
    return out;
  }
  const acks = after.filter((a) => a.kind === 'ack');
  out.changed_since_acknowledged = !acks.length && before.some((a) => a.kind === 'ack');
  if (acks.length) {
    const k = acks[acks.length - 1];
    Object.assign(out, {
      state: 'acknowledged',
      acknowledged: { by: k.by, at: iso(k.at), version: k.version, readback: get(k, 'readback'), questions: get(k, 'questions') },
    });
  }
  return out;
}

export function diff(old: any, next: any): any[] {
  if (old === null || old === undefined) return [];
  const a = elements(old), b = elements(next);
  const out: any[] = [];
  for (const [eid, e] of b) {
    const before = a.has(eid) ? content(a.get(eid)) : null;
    const after = content(e);
    if (!pyEq(before, after)) out.push({ id: eid, label: e.label, kind: e.kind, before, after });
  }
  return out;
}

const pick = (o: any, keys: string[]) => Object.fromEntries(keys.map((k) => [k, get(o, k)]));

// -------------------------------------------------------------------------------------------------- views
export class Views {
  readonly rules: Record<string, Rules>;

  /**
   * templates: the template definitions (rule limits and branches). staleBoarding: with the live clock, leave off a
   * patient with an admit decision and no ED departure for longer than this (ms); the static demo replays, so null.
   * staleOwner (ms), shifts and shiftWarning (ms): as in logic.py, the ownership settings.
   */
  constructor(
    readonly ds: Dataset, readonly store: MemoryStore, templates: any[] = [], readonly staleBoarding: number | null = null,
    readonly staleOwner = 14 * HOUR, readonly shifts: [number, number][] = [[7, 0], [19, 0]], readonly shiftWarning = 30 * MINUTE,
  ) {
    this.rules = ruleIndex(templates);
  }

  private departure(csn: string, at: number): number | null {
    const e = this.ds.enc.get(csn) as any;
    return e.ed_departure_ts !== null && e.ed_departure_ts <= at ? e.ed_departure_ts : null;
  }

  private summary(csn: string, at: number, person: any): any {
    const e = this.ds.enc.get(csn);
    if (!e || e.admit_decision_ts === null || e.admit_decision_ts > at) return null;
    const latest = this.ds.latestAt(csn, at);
    const dep = this.departure(csn, at);
    if (dep !== null && at - dep > ARRIVED_WINDOW) return null;
    if (dep === null && this.staleBoarding !== null && at - e.admit_decision_ts > this.staleBoarding) return null;
    if (!latest.size) return null; // the first draft is not written yet
    const anyRow = latest.values().next().value as Row;
    const phase = statusAt(anyRow, at) === 'retracted' ? 'canceled' : dep !== null ? 'arrived' : 'boarding';
    const els = elements(anyRow.draft);
    const dest = or(get(or(els.get('E04') ?? null, {}), 'data'), {}) as any;
    if (!sees(person, e.facility, or(get(dest, 'pending_unit'), dep !== null ? get(dest, 'unit') : null))) return null;
    const acts = this.store.actions(csn, at);
    const rows = this.ds.rowsAt(csn, at);
    const ref = dep !== null ? Math.min(at, dep) : at;
    const templates: any = {};
    const pat = patientEntries(acts, this.rules);
    for (const [tid, row] of latest) {
      const eff = effective(row, acts, ref, this.rules[tid] ?? null, pat);
      templates[tid] = {
        version: row.version, as_of: iso(row.at), status: statusAt(row, at), trigger: row.trigger, score: eff.completeness.score,
        blocking: eff.completeness.blocking, workflow: workflow(row, acts, rows, phase === 'arrived', this.rules[tid] ?? null, this.rules),
      };
    }
    const sev = or(get(or(els.get('E07') ?? null, {}), 'narrative'), {}) as any;
    const flags: any = {};
    for (const k of ['E08', 'E09', 'E10']) flags[k] = get(or(els.get(k) ?? null, {}), 'data');
    const code = isDict(flags.E08) ? get(or(flags.E08, {}), 'value') : null;
    const isolation = isDict(flags.E10) ? get(or(flags.E10, {}), 'value') : null;
    const allergies = Array.isArray(flags.E09) ? flags.E09.map((a: any) => get(a, 'allergen')) : [];
    const shown = (x: any) => ({ value: x.value, note: x.note, by: x.by, template: x.template });
    const assigned = this.store.assignments(csn, at);
    const me = personKey(person);
    const owners: any = {};
    for (const role of ROLES) owners[role] = ownerAt(assigned, role, acts, at, this.staleOwner, me);
    const sinceDecision = (dep ?? at) - (e.admit_decision_ts as number);
    return {
      csn, facility: e.facility, phase,
      patient: { name: e.patient_name, mrn: e.mrn, sex: e.sex, age: age(e.birth_date, at) },
      chief_complaint: e.chief_complaint, esi: e.esi, service: or(get(dest, 'service'), e.service),
      arrival_ts: iso(e.arrival_ts), admit_decision_ts: iso(e.admit_decision_ts), ed_departure_ts: iso(dep),
      minutes_boarding: pyRound(sinceDecision / 1000 / 60),
      location: pick(dest, ['unit', 'room', 'bed']),
      destination: dep === null
        ? pick(dest, ['pending_unit', 'pending_room', 'pending_bed', 'level_of_care'])
        : { pending_unit: or(get(dest, 'unit'), get(dest, 'pending_unit')), pending_room: get(dest, 'room'), pending_bed: get(dest, 'bed'),
          level_of_care: get(dest, 'level_of_care') },
      severity: get(sev, 'value'), severity_floor: get(or(get(sev, 'floor'), {}), 'value'),
      code_status: code, isolation, allergies,
      // Entered in the sign off while the chart has none; the chart value replaces them when it arrives.
      entered: {
        code_status: pat.E08 && !truthy(code) ? shown(pat.E08) : null,
        isolation: pat.E10 && !truthy(isolation) ? shown(pat.E10) : null,
        allergies: !truthy(allergies) ? pat.E09.map(shown) : [],
      },
      last_update: { as_of: iso(anyRow.at), trigger: anyRow.trigger },
      templates,
      owners,
      // Open and overdue tasks, for the board row.
      tasks: tasks.counts([...tasks.replay(this.store.taskEvents(csn, at), at).values()], at),
    };
  }

  board(at: number, person: any): any {
    const rows: any[] = [];
    for (const c of this.ds.enc.keys()) {
      const s = this.summary(c, at, person);
      if (s) rows.push(s);
    }
    // What changed since the caller last looked at each patient (seen.ts): one read of their looks for the board.
    const looks = rows.length ? this.store.lastLooks(personKey(person), at) : new Map<string, Look>();
    for (const r of rows) {
      const look = looks.get(r.csn) ?? null;
      r.since = seen.summary(look, this.since(r.csn, at, person, look));
    }
    const rank = (r: any) => SEVERITY_RANK[or(r.severity, '') as string] ?? -1;
    rows.sort((a, b) => Number(a.phase !== 'boarding') - Number(b.phase !== 'boarding') || rank(b) - rank(a) || b.minutes_boarding - a.minutes_boarding);
    const boarding = rows.filter((r) => r.phase === 'boarding');
    const mine = or(person.can_sign, person.can_ack);
    const tpl = person.default_template;
    const tplOf = (r: any) => or(r.templates[tpl] ?? null, {}) as any;
    const state = (r: any) => get(get(tplOf(r), 'workflow') ?? {}, 'state');
    const toSign = boarding.filter((r) => state(r) === 'draft' || state(r) === 'changed');
    const kpis = {
      boarding: boarding.length,
      median_boarding_min: boarding.length ? pyRound(median(boarding.map((r) => r.minutes_boarding))) : null,
      unstable: boarding.filter((r) => r.severity === 'unstable').length,
      to_sign: truthy(person.can_sign) ? toSign.length : null,
      to_acknowledge: truthy(person.can_ack) ? rows.filter((r) => state(r) === 'signed').length : null,
      blocking: truthy(person.can_sign) ? toSign.filter((r) => truthy(get(tplOf(r), 'blocking'))).length : null,
      arrived: rows.filter((r) => r.phase === 'arrived').length,
      // ED roles: patients who went up before anyone signed their handoff for this role.
      left_unsigned: truthy(person.can_sign) ? rows.filter((r) => r.phase === 'arrived' && state(r) === 'transferred_unsigned').length : null,
    };
    let shift: any = null;
    if (ROLES.includes(person.role)) {
      const role = person.role;
      const owned = boarding.filter((r) => counts(r.owners[role]) && r.owners[role].you);
      (kpis as any).mine = owned.length;
      (kpis as any).unassigned = boarding.filter((r) => !counts(r.owners[role])).length;
      const next = nextShift(at, this.shifts);
      // Shift change: the board warns the owner of the patients whose handoff is not signed (or changed since).
      shift = {
        at: iso(next), minutes: pyRound((next - at) / MINUTE), warning: next - at <= this.shiftWarning, mine: owned.length,
        unsigned: owned.filter((r) => state(r) === 'draft' || state(r) === 'changed').length,
      };
    }
    return { at: iso(at), template: tpl, kpis, rows, scoped: truthy(mine), shift };
  }

  /** since: also what changed since the caller's last look (the handoff page; Take over reads without it). */
  case(csn: string, at: number, person: any, since = true): any {
    const summary = this.summary(csn, at, person);
    if (summary === null) return null;
    const acts = this.store.actions(csn, at);
    const dep = this.departure(csn, at);
    const ref = dep !== null ? Math.min(at, dep) : at;
    const byT = new Map<string, Row[]>();
    for (const r of this.ds.rowsAt(csn, at)) {
      let list = byT.get(r.template);
      if (!list) byT.set(r.template, (list = []));
      list.push(r);
    }
    const templates: any = {};
    const pat = patientEntries(acts, this.rules);
    for (const [tid, versions] of byT) {
      const row = versions[versions.length - 1];
      const rules = this.rules[tid] ?? null;
      const wf = workflow(row, acts, versions, summary.phase === 'arrived', rules, this.rules);
      const draft = effective(row, acts, ref, rules, pat);
      // What changed: since the signature when the template changed after signing (the text the ED signed,
      // clinician edits included), else since the previous version; both as the clinician sees them.
      let base: [number, any] | null = null;
      let since = 'previous';
      if (wf.state === 'changed') {
        const [s, before] = lastSign(acts, tid) as [Action, Action[], Action[]];
        const signed = versions.find((v) => v.version === s.version) ?? null;
        if (signed) {
          base = [s.version, effective(signed, before, s.at, rules, patientEntries(until(acts, s), this.rules))];
          since = 'signed';
        }
      } else if (versions.length > 1) {
        const prev = versions[versions.length - 2];
        base = [prev.version, effective(prev, acts, ref, rules, pat)];
      }
      templates[tid] = {
        draft,
        row: { ...pick(row, ['version', 'trigger', 'model']), status: statusAt(row, at), as_of: iso(row.at) },
        workflow: wf,
        changes: { since, version: base ? base[0] : null, elements: diff(base ? base[1] : null, draft) },
        versions: versions.map((v) => ({ version: v.version, as_of: iso(v.at), trigger: v.trigger, status: statusAt(v, at), severity: v.severity, score: v.score })),
      };
    }
    // Task events are in the activity too (kind "task", the event in "task", the task's text in detail).
    const events = this.store.taskEvents(csn, at);
    const text = new Map<number, string>([...tasks.replay(events, at).values()].map((t) => [t.id, t.text]));
    const log: any[] = acts.map((a) => ({
      kind: a.kind, template: a.template, by: a.by, at: iso(a.at) as string, version: a.version,
      detail: or(or(get(a, 'element'), get(a, 'rule')), get(a, 'option')),
      ...(truthy(get(a, 'restore')) ? { restore: true } : {}),
      ...(truthy(get(a, 'off_assignment')) ? { off_assignment: true, assigned_to: get(a, 'assigned_to') } : {}),
      ...(a.kind === 'attest' ? this.entry(a) : {}),
    }));
    for (const e of tasks.ordered(events)) {
      log.push({
        kind: 'task', template: null, by: e.by_name, at: iso(e.at) as string, version: null, detail: text.get(e.task_id) ?? null, task: e.kind,
        task_id: e.task_id, ...(e.outside_role ? { outside_role: true } : {}),
      });
    }
    // sorted(key=at string, reverse=True) keeps equal keys in their original order
    log.sort((a, b) => cmpStr(b.at, a.at));
    const out: any = { summary, templates, activity: log };
    if (since) {
      // Against the last look before this one: the page records its look after it has this answer.
      const look = this.store.lastLooks(personKey(person), at, csn).get(csn) ?? null;
      const items = this.since(csn, at, person, look);
      summary.since = seen.summary(look, items);
      out.since = { at: iso(at), ...summary.since, items: items ?? [] };
    }
    return out;
  }

  // -------------------------------------------------------------------------- since you last looked
  /** The illness severity the board shows, from the newest versions (latestAt), as summary() reads it. */
  severity(latest: Map<string, Row>): any {
    const row = latest.values().next().value as Row | undefined;
    return row ? get(or(get(or(elements(row.draft).get('E07') ?? null, {}), 'narrative'), {}), 'value') : null;
  }

  /**
   * What changed on the patient between the caller's last look and `at`, by anyone but the caller (seen.ts), newest
   * first; null when there is no look (the patient was never opened).
   */
  private since(csn: string, at: number, person: any, look: Look | null): any[] | null {
    if (look === null) return null;
    const me = personKey(person);
    const thenAt = look.at;
    const e = this.ds.enc.get(csn) as any;
    const acts = this.store.actions(csn, at);
    const now = this.ds.latestAt(csn, at);
    const then = this.ds.latestAt(csn, thenAt);
    const dep = this.departure(csn, at);
    const ref = dep !== null ? Math.min(at, dep) : at;
    const pat = patientEntries(acts, this.rules);
    const items: [number, any][] = [];
    // The pipeline: newer versions (without the illness severity item when the severity changed: its own line), the
    // severity and the phase.
    const sevThen = this.severity(then);
    const sevNow = this.severity(now);
    const severity = truthy(sevThen) && truthy(sevNow) && sevThen !== sevNow;
    for (const [tid, row] of now) {
      const old = then.get(tid) ?? null;
      if (old !== null && old.version === row.version) continue;
      let els: any[] = [];
      if (old !== null) {
        const rules = this.rules[tid] ?? null;
        els = diff(effective(old, acts, ref, rules, pat), effective(row, acts, ref, rules, pat))
          .filter((c: any) => !(severity && c.id === 'E07'))
          .map((c: any) => ({ id: c.id, label: c.label }));
        if (!els.length) continue; // rewritten with the same content (or only the severity): nothing to read
      }
      items.push([row.at, { kind: 'version', template: tid, from: old ? old.version : null, to: row.version, at: iso(row.at), elements: els }]);
    }
    if (severity) {
      const row = now.values().next().value as Row;
      items.push([row.at, { kind: 'severity', from: sevThen, to: sevNow, at: iso(row.at) }]);
    }
    if (dep !== null && dep > thenAt) items.push([dep, { kind: 'phase', phase: 'arrived', at: iso(dep) }]);
    if (this.ds.canceledAt(e, at) && !this.ds.canceledAt(e, thenAt)) {
      const when = this.ds.canceled.get(csn) ?? at;
      items.push([when, { kind: 'phase', phase: 'canceled', at: iso(when) }]);
    }
    // Clinicians: actions, owners and tasks after the look, by someone else. The tasks a sign off made from its action
    // list (a create with the template, at the sign's time, by the signer) are part of that sign's line.
    const events = this.store.taskEvents(csn, at);
    const made = new Map<string, number>();
    const key = (template: string, t: number, who: string | null) => `${template}|${t}|${who}`;
    for (const ev of events) {
      if (ev.kind !== 'create' || !truthy(ev.template)) continue;
      const k = key(ev.template, ev.at, actor(ev));
      made.set(k, (made.get(k) ?? 0) + 1);
    }
    const labels = new Map<string, Map<string, string>>();
    for (const [tid, row] of now) labels.set(tid, new Map([...elements(row.draft)].map(([id, x]) => [id, x.label])));
    for (const a of ordered(acts)) {
      if (!(seen.after(a, look) && seen.byOther(a, me))) continue;
      const el = a.kind === 'edit' ? get(a, 'element')
        : a.kind === 'attest' ? get(or(get(or(this.rules[a.template] ?? null, {}), get(a, 'rule')), {}), 'element') : null;
      const item: any = {
        kind: 'action', action: a.kind, template: a.template, by: a.by, at: iso(a.at), element: el ?? null,
        label: truthy(el) ? labels.get(a.template)?.get(el) ?? null : null,
      };
      if (a.kind === 'attest') {
        item.option = or(get(a, 'entry'), a.option);
        if (truthy(get(a, 'remove'))) item.removed = true;
      }
      if (truthy(get(a, 'restore'))) item.restore = true;
      const n = a.kind === 'sign' ? made.get(key(a.template, a.at, actor(a))) ?? 0 : 0;
      if (n) item.tasks = n;
      items.push([a.at, item]);
    }
    for (const r of this.store.assignments(csn, at)) {
      if (seen.after(r, look) && seen.byOther(r, me)) {
        items.push([r.at, { kind: 'owner', owner: r.kind, role: r.role, to: r.assignee_name, from: r.from_name, by: r.by_name, at: iso(r.at) }]);
      }
    }
    const text = new Map<number, string>([...tasks.replay(events, at).values()].map((t) => [t.id, t.text]));
    for (const ev of tasks.ordered(events)) {
      if (ev.kind === 'create' && truthy(ev.template)) continue; // counted with its sign off
      if (seen.after(ev, look) && seen.byOther(ev, me)) {
        items.push([ev.at, {
          kind: 'task', task: ev.kind, task_id: ev.task_id, text: text.get(ev.task_id) ?? null, by: ev.by_name, at: iso(ev.at),
          ...(truthy(ev.undo) ? { undo: true } : {}),
        }]);
      }
    }
    return seen.newestFirst(items);
  }

  /** A look at the patient at `at` (logic.py look): not a clinical action, so any role, also at an earlier time. */
  look(csn: string, at: number, person: any, source: string): any {
    if (!seen.SOURCES.includes(source)) throw new ActionError(SEEN_SOURCE);
    if (this.summary(csn, at, person) === null) throw new ActionError('This patient is not on your board at this time.');
    const row = this.store.look({ csn, by_id: personKey(person), by_name: person.name, role: person.role, user_email: person.user_email ?? null, at, source });
    return { csn, at: iso(at), since: seen.summary(row, []) };
  }

  /**
   * The demo's starting looks (logic.py seed_looks): each seeded owner looked at each patient they take a minute
   * before taking it, and both looked two hours before `at` at the first two other patients boarding at `at` who were
   * on the board then.
   */
  seedLooks(at: number, owners: any[]): any[] {
    const rows: any[] = owners.map((r) => ({ csn: r.csn, by_id: r.assignee_id, by_name: r.assignee_name, role: r.role, at: r.at - MINUTE }));
    const then = at - 2 * HOUR;
    const taken = new Set(owners.map((r) => r.csn));
    const picked: string[] = [];
    const encs = [...this.ds.enc.values()].sort((a, b) => (a.admit_decision_ts ?? at) - (b.admit_decision_ts ?? at) || cmpStr(a.csn, b.csn));
    for (const e of encs) {
      if (taken.has(e.csn) || e.admit_decision_ts === null || e.admit_decision_ts > then
        || (e.ed_departure_ts !== null && e.ed_departure_ts <= at) || this.ds.canceledAt(e, at)) continue;
      if (this.ds.rowsAt(e.csn, then).length) picked.push(e.csn);
      if (picked.length === 2) break;
    }
    const who = new Map<string, any>();
    for (const r of owners) who.set(r.role, r);
    for (const csn of picked) {
      for (const [role, r] of who) rows.push({ csn, by_id: r.assignee_id, by_name: r.assignee_name, role, at: then });
    }
    return rows.map((r) => ({ ...r, user_email: null, source: 'handoff' }));
  }

  /** History keeps a value the clinician entered, also once the chart has one and the item shows the chart. */
  private entry(a: Action): any {
    const options = get(or(get(or(this.rules[a.template] ?? null, {}), a.rule), {}), 'attest');
    if (truthy(get(a, 'remove'))) return { removed: get(a, 'entry') };
    if (!entered(a, options)) return {};
    return { entered: or(get(a, 'entry'), a.option), ...(truthy(get(a, 'entry_note')) ? { note: a.entry_note } : {}) };
  }

  // -------------------------------------------------------------------------------------------- analytics
  analytics(at: number): any {
    const ds = this.ds;
    const encs = [...ds.enc.values()];
    const series: any[] = [];
    const stop = Math.min(at, ds.end);
    for (let t = ds.start; t <= stop; t += HOUR) {
      let ed = 0, board = 0;
      // Boarding patients no one owns (or whose owner went stale) for each ED role, at this hour.
      const free: Record<string, number> = { ed_nurse: 0, ed_physician: 0 };
      for (const e of encs) {
        if (e.arrival_ts !== null && e.arrival_ts <= t && (e.left_ts === null || e.left_ts > t) && !ds.canceledAt(e, t)) ed++;
        if (e.admit_decision_ts !== null && e.admit_decision_ts <= t && (e.ed_departure_ts === null || e.ed_departure_ts > t) && truthy(ds.versions.get(e.csn) ?? null)
          && !ds.canceledAt(e, t) && (e.ed_departure_ts !== null || this.staleBoarding === null || t - e.admit_decision_ts <= this.staleBoarding)) {
          board++;
          const assigned = this.store.assignments(e.csn, t);
          const actsE = assigned.length ? this.store.actions(e.csn, t) : [];
          for (const role of ROLES) if (!(assigned.length && counts(ownerAt(assigned, role, actsE, t, this.staleOwner, null)))) free[role]++;
        }
      }
      series.push({ t: iso(t), ed, boarding: board, unassigned_rn: free.ed_nurse, unassigned_md: free.ed_physician });
    }
    const done = encs.filter((e) => e.ed_departure_ts !== null && e.ed_departure_ts <= at && e.admit_decision_ts !== null && truthy(ds.versions.get(e.csn) ?? null));
    const byUnit = new Map<string, number[]>();
    const timing: number[] = [], completeness: number[] = [];
    const blockers = new Map<string, number>(), sevMix = new Map<string, number>();
    const inc = (m: Map<string, number>, k: string) => m.set(k, (m.get(k) ?? 0) + 1);
    for (const e of done) {
      const dep = e.ed_departure_ts as number;
      const latest = ds.latestAt(e.csn, dep);
      const row = (or(latest.get('sbar_nurse') ?? null, latest.values().next().value ?? null) as Row | null);
      const dest = row ? (or(get(or(elements(row.draft).get('E04') ?? null, {}), 'data'), {}) as any) : {};
      const unit = `${pyStr(e.facility)} ${pyStr(or(get(dest, 'pending_unit'), e.unit))}`;
      let list = byUnit.get(unit);
      if (!list) byUnit.set(unit, (list = []));
      list.push((dep - (e.admit_decision_ts as number)) / 1000 / 60);
      timing.push(new Set((ds.versions.get(e.csn) as Row[]).filter((r) => r.at <= at).map((r) => r.at)).size);
      if (row) {
        completeness.push(row.draft.completeness.score);
        inc(sevMix, or(row.severity, 'unknown') as string);
        for (const r of row.draft.rules) {
          if ((r.level === 'hard' || r.level === 'stale') && r.status !== 'pass' && r.status !== 'deferred') inc(blockers, r.label);
        }
      }
    }
    const q = (v: number[], p: number) => {
      const s = [...v].sort((a, b) => a - b);
      return s.length ? pyRound(s[Math.min(s.length - 1, Math.floor(p * s.length))]) : null;
    };
    const units = [...byUnit.entries()].sort((a, b) => cmpStr(a[0], b[0]))
      .map(([u, v]) => ({ unit: u, n: v.length, median_min: q(v, 0.5), p90_min: q(v, 0.9) }));
    const acts = this.store.allActions(at);
    let signedBefore = 0;
    for (const e of done) {
      if (acts.some((a) => a.kind === 'sign' && a.csn === e.csn && a.at <= (e.ed_departure_ts as number))) signedBefore++;
    }
    const drafts = [...ds.versions.values()].flat().filter((r) => r.at <= at);
    // Counter.most_common(8): by count, ties in first seen order
    const common = [...blockers.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
    return {
      at: iso(at), series, units,
      admitted: done.length,
      drafts_per_patient: { median: q(timing, 0.5), max: timing.length ? Math.max(...timing) : null },
      completeness_at_transfer: { median: completeness.length ? median(completeness) : null, histogram: histogram(completeness) },
      blocking_at_transfer: common.map(([label, n]) => ({ label, n })),
      severity_at_transfer: Object.fromEntries(sevMix),
      signed_before_transfer: signedBefore,
      transferred_without_sign_off: done.length - signedBefore,
      drafts: drafts.length,
      model_calls: new Set(drafts.map((r) => `${r.csn}|${r.at}`)).size,
      addenda: new Set(drafts.filter((r) => statusAt(r, at) === 'addendum').map((r) => `${r.csn}|${r.at}`)).size,
      owners: this.ownerAnalytics(at, acts),
    };
  }

  /** logic.py _owner_analytics: outside an assignment, handovers around the shift change, admit decision to signature. */
  private ownerAnalytics(at: number, acts: Action[]): any {
    let since: number | null = null;
    for (const csn of this.ds.enc.keys()) for (const r of this.store.assignments(csn, at)) if (since === null || r.at < since) since = r.at;
    const ed = acts.filter((a) => ROLES.includes(a.role) && ['edit', 'attest', 'sign'].includes(a.kind));
    const outside = ROLES.map((role) => ({
      role, n: ed.filter((a) => a.role === role && truthy(get(a, 'off_assignment'))).length, of: ed.filter((a) => a.role === role).length,
    }));
    const handovers = new Map<string, number>();
    for (const csn of this.ds.enc.keys()) {
      for (const r of this.store.assignments(csn, at)) {
        if (r.kind === 'handover') {
          const b = handoverBucket(r.at, this.shifts);
          handovers.set(b, (handovers.get(b) ?? 0) + 1);
        }
      }
    }
    const byCsn = new Map<string, Action[]>();
    for (const a of acts) {
      if (!byCsn.has(a.csn)) byCsn.set(a.csn, []);
      byCsn.get(a.csn)!.push(a);
    }
    const waits = new Map<string, { role: string; owner: string | null; v: number[] }>();
    for (const [csn, mine] of byCsn) {
      const e = this.ds.enc.get(csn);
      if (!e || e.admit_decision_ts === null) continue;
      for (const [role, tpl] of OWN_TEMPLATE) {
        const signs = ordered(mine.filter((a) => a.kind === 'sign' && a.template === tpl));
        if (!signs.length) continue;
        const s = signs[0];
        const o = ownerAt(this.store.assignments(csn, s.at), role, mine.filter((a) => a.at <= s.at), s.at, this.staleOwner, null);
        const owner = counts(o) ? o.name : null;
        const k = `${role}|${owner ?? ''}|${owner === null}`;
        if (!waits.has(k)) waits.set(k, { role, owner, v: [] });
        waits.get(k)!.v.push((s.at - (e.admit_decision_ts as number)) / 60000);
      }
    }
    const toSign = [...waits.values()]
      .sort((a, b) => ROLES.indexOf(a.role) - ROLES.indexOf(b.role) || Number(a.owner === null) - Number(b.owner === null) || cmpStr(a.owner ?? '', b.owner ?? ''))
      .map((w) => ({ role: w.role, owner: w.owner, n: w.v.length, median_min: pyRound(median(w.v)) }));
    return { since: iso(since), outside, handovers: HANDOVER_BUCKETS.map((b) => ({ bucket: b, n: handovers.get(b) ?? 0 })), to_sign: toSign };
  }

  // ---------------------------------------------------------------------------------------------- actions
  act(kind: string, csn: string, template: string, at: number, person: any, body: any): any {
    const summary = this.summary(csn, at, person);
    if (summary === null) throw new ActionError('This patient is not on your board at this time.');
    const latest = this.ds.latestAt(csn, at).get(template);
    if (!latest) throw new ActionError('There is no draft for this template yet.');
    const acts = this.store.actions(csn, at);
    const rules = this.rules[template] ?? null;
    const arrived = summary.phase === 'arrived';
    const wf = workflow(latest, acts, this.ds.rowsAt(csn, at).filter((r) => r.template === template), arrived, rules, this.rules);
    const row: any = {
      csn, template, version: latest.version, at, by: person.name, role: person.role, user_email: get(person, 'user_email'),
      by_id: personKey(person),
    };
    if (kind === 'edit' || kind === 'attest' || kind === 'sign') {
      if (!(SIGNS[person.role] ?? []).includes(template)) throw new ActionError('Only the sending ED clinician for this template can change or sign it.');
      // After the patient leaves the ED the handoff is final, except for an addendum, or when the ED never signed it
      // or signed an older version: then the ED can still complete and sign it.
      const unsigned = arrived && (!truthy(get(wf, 'signed')) || wf.signed.version < latest.version);
      if (summary.phase !== 'boarding' && statusAt(latest, at) !== 'addendum' && !unsigned) throw new ActionError('The patient has left the ED; the handoff is final.');
    } else if (kind === 'ack') {
      if (!(ACKS[person.role] ?? []).includes(template)) throw new ActionError('Only the receiving clinician can acknowledge this handoff.');
    } else {
      throw new ActionError(`Unknown action ${kind}.`);
    }
    // The replay clock: an action cannot go before one already recorded for this handoff.
    if (this.store.actions(csn).some((a) => a.template === template && a.at > at)) throw new ActionError(EARLIER);
    const dep = this.departure(csn, at);
    const pat = patientEntries(acts, this.rules);
    const eff = effective(latest, acts, dep !== null ? Math.min(at, dep) : at, rules, pat);
    if (kind === 'edit') {
      const el = elements(eff).get(get(body, 'element'));
      if (!truthy(el) || el.kind !== 'narrative') throw new ActionError('Only narrative items can be edited; structured data comes from the chart.');
      if (el.id === 'E07') throw new ActionError('Illness severity comes from the rule floor and the model; it cannot be edited.');
      const model = or(get(elements(latest.draft).get(el.id), 'narrative'), {}) as any;
      Object.assign(row, { element: el.id, model_before: { text: get(model, 'text'), items: get(model, 'items') } });
      if (truthy(get(body, 'restore'))) {
        if (!truthy(get(or(get(el, 'narrative'), {}), 'edited'))) throw new ActionError("This item shows the model's text already; there is no edit to restore.");
        Object.assign(row, { restore: true, text: null, items: null });
      } else if (get(body, 'items') !== null) {
        const items = (body.items as any[]).map((i) => ({
          text: strip(or(get(i, 'text'), '') as string),
          owner: or(strip(or(get(i, 'owner'), '') as string), null),
          due: or(strip(or(get(i, 'due'), '') as string), null),
        }));
        if (!items.length || items.some((i) => !truthy(i.text))) throw new ActionError(EMPTY_EDIT);
        if (items.some((i) => Object.values(i).some((v) => truthy(v) && pyLen(v as string) > MAX_TEXT))) throw new ActionError(TOO_LONG);
        Object.assign(row, { text: null, items });
      } else {
        const text = strip(or(get(body, 'text'), '') as string);
        if (!truthy(text)) throw new ActionError(EMPTY_EDIT);
        if (pyLen(text) > MAX_TEXT) throw new ActionError(TOO_LONG);
        Object.assign(row, { text, items: null });
      }
    } else if (kind === 'attest') {
      const r = eff.rules.find((x: any) => x.rule === get(body, 'rule'));
      if (!r || !(or(get(r, 'attest_options'), []) as any[]).some((o) => pyEq(o, get(body, 'option')))) {
        throw new ActionError('Choose one of the listed attestations for this rule.');
      }
      Object.assign(row, { rule: r.rule, option: body.option, entry: null, entry_note: null });
      const typed = TYPED[`${r.element}|${body.option}`];
      if (PATIENT.has(r.element)) patientCheck(r, body, template, pat);
      if (typed && truthy(get(body, 'remove'))) {
        const wanted = strip(or(get(body, 'entry'), '') as string).toLowerCase();
        const listed: any[] = r.element === 'E09' ? pat.E09 : [];
        const gone = listed.find((x) => strip(x.value).toLowerCase() === wanted);
        if (!gone) throw new ActionError('That allergy is not on the list.');
        Object.assign(row, { entry: gone.value, remove: true });
      } else if (typed) {
        const entry = strip(or(get(body, 'entry'), '') as string);
        const note = typed.note ? strip(or(get(body, 'entry_note'), '') as string) : '';
        if (!truthy(entry)) throw new ActionError(typed.required);
        if (pyLen(entry) > MAX_TEXT || pyLen(note) > MAX_TEXT) throw new ActionError(TOO_LONG);
        Object.assign(row, { entry, entry_note: or(note, null) });
      }
    } else if (kind === 'sign') {
      if (eff.completeness.blocking.length) {
        const labels = eff.rules.filter((x: any) => eff.completeness.blocking.includes(x.rule)).map((x: any) => x.label);
        throw new ActionError('Resolve before signing: ' + labels.join(', ') + '.');
      }
      const callback = strip(or(get(body, 'callback'), '') as string);
      if (!truthy(callback)) throw new ActionError('Add a callback number so the receiver can reach you.');
      if (pyLen(callback) > MAX_CALLBACK || !callbackOk(callback)) throw new ActionError(CALLBACK);
      row.callback = callback;
    } else {
      if (UNSIGNED.includes(wf.state)) throw new ActionError('The handoff has not been signed yet.');
      if (wf.state === 'changed') throw new ActionError('Waiting for the ED to sign again: the handoff changed after it was signed.');
      const readback = strip(or(get(body, 'readback'), '') as string);
      const questions = strip(or(get(body, 'questions'), '') as string);
      if (!readbackOk(readback)) throw new ActionError(READBACK);
      if (pyLen(readback) > MAX_TEXT || pyLen(questions) > MAX_TEXT) throw new ActionError(TOO_LONG);
      Object.assign(row, { readback, questions: or(questions, null) });
    }
    // Allowed, never blocked: an ED clinician acting on a patient a colleague owns, recorded for Analytics.
    const owner = summary.owners[person.role] ?? null;
    if (counts(owner) && !owner.you) Object.assign(row, { off_assignment: true, assigned_to: owner.name });
    this.store.add(kind, row);
    // The action list becomes tasks; a sign again or an addendum adds only the new items.
    if (kind === 'sign') this.handoffTasks(csn, template, latest.version, at, row, eff);
    return this.case(csn, at, person);
  }

  // -------------------------------------------------------------------------------------------- ownership
  /** An assignment row for the caller's role; like actions, it cannot go before one already recorded. */
  private assignmentRow(csn: string, at: number, person: any, kind: string, to: any, owner: any): any {
    const role = person.role;
    if (this.store.assignments(csn).some((r) => r.role === role && r.at > at)) throw new ActionError(EARLIER);
    return {
      csn, role, kind, assignee_id: to ? to.id : null, assignee_name: to ? to.name : null, from_id: owner ? owner.id : null,
      from_name: owner ? owner.name : null, at, by_name: person.name, by_role: role, user_email: get(person, 'user_email'),
      by_id: personKey(person), detail: {},
    };
  }

  /**
   * The demo's starting owners (logic.py seed_owners): `nurse` owns up to three patients on the board half an hour
   * before the next shift change who board through it, `physician` the first two, each taken five minutes after its
   * first draft but no earlier than three hours before `at`.
   */
  seedOwners(at: number, nurse: any, physician: any): any[] {
    const ds = this.ds;
    const next = nextShift(at, this.shifts);
    const warn = next - 30 * MINUTE;
    const encs = [...ds.enc.values()].sort((a, b) => (a.admit_decision_ts ?? next) - (b.admit_decision_ts ?? next) || cmpStr(a.csn, b.csn));
    const taken: [string, number][] = [];
    for (const e of encs) {
      const rows = e.admit_decision_ts !== null ? ds.rowsAt(e.csn, warn) : [];
      if (!rows.length || (e.ed_departure_ts !== null && e.ed_departure_ts <= next) || ds.canceledAt(e, next)) continue;
      const t = Math.max(rows[0].at + 5 * MINUTE, at - 3 * HOUR);
      if (t <= warn) taken.push([e.csn, t]);
      if (taken.length === 3) break;
    }
    const out: any[] = [];
    taken.forEach(([csn, t], i) => {
      for (const [role, who] of [['ed_nurse', nurse], ['ed_physician', physician]] as [string, any][]) {
        if (role === 'ed_nurse' || i < 2) {
          out.push({ csn, role, kind: 'claim', assignee_id: who.id, assignee_name: who.name, from_id: null, from_name: null, at: t,
            by_name: who.name, by_role: role, user_email: null, by_id: who.id, detail: { seed: true } });
        }
      }
    });
    return out;
  }

  /**
   * The demo's starting tasks (logic.py seed_tasks): tasks added by hand by each seeded owner, spread in turn over the
   * patients they own by `at`, so the board counts and every Tasks page group show something at `at`.
   */
  seedTasks(at: number, owners: any[]): any[] {
    const rows: any[] = [];
    for (const [role, plan] of SEED_TASKS) {
      const mine = owners.filter((r) => r.role === role && r.at <= at);
      if (!mine.length) continue;
      plan.forEach(([text, due], i) => {
        const who = mine[i % mine.length];
        rows.push({
          csn: who.csn, kind: 'create', template: null, item_key: null, text, role: tasks.DISCIPLINE[role], assignee_id: null, assignee_name: null,
          due: due !== null ? at + due : null, at: who.at, by_name: who.assignee_name, by_role: role, user_email: null, by_id: who.assignee_id,
        });
      });
    }
    return rows;
  }

  /** Take (claim), Assign to a colleague or Release, for the caller's ED role. */
  own(kind: string, csn: string, at: number, person: any, to: string | null, colleagues: any[]): any {
    if (!ROLES.includes(person.role)) throw new ActionError(NOT_ED);
    const summary = this.summary(csn, at, person);
    if (summary === null) throw new ActionError('This patient is not on your board at this time.');
    if (summary.phase !== 'boarding') throw new ActionError('The patient has left the ED, so they no longer need an ED owner.');
    const owner = summary.owners[person.role];
    const me = personKey(person);
    const current = counts(owner) ? owner : null;
    let row: any;
    if (kind === 'claim') {
      if (current && current.you) throw new ActionError('This patient is already yours.');
      row = this.assignmentRow(csn, at, person, kind, { id: me, name: person.name }, owner);
    } else if (kind === 'assign') {
      const target = colleagues.find((c) => c.id === to) ?? null;
      if (target === null) throw new ActionError('Choose a colleague from the list.');
      if (target.id === me) throw new ActionError('To take this patient yourself, press Take.');
      if (current && current.id === target.id) throw new ActionError(`${target.name} already owns this patient.`);
      row = this.assignmentRow(csn, at, person, kind, target, owner);
    } else if (kind === 'release') {
      if (owner === null) throw new ActionError('No one owns this patient, so there is nothing to release.');
      if (owner.id !== me) throw new ActionError(`Only ${owner.name} can release this patient. Use Take or Assign instead.`);
      row = this.assignmentRow(csn, at, person, kind, null, owner);
    } else {
      throw new ActionError('Choose Take, Assign or Release.');
    }
    this.store.assign(row);
    return this.case(csn, at, person);
  }

  /** Take over at a shift change: colleagues with their patient counts, and the chosen colleague's patients. */
  takeover(at: number, person: any, colleague: string | null, colleagues: any[]): any {
    const role = person.role;
    if (!ROLES.includes(role)) throw new ActionError(NOT_ED);
    const owned = new Map<string, any[]>();
    for (const c of this.ds.enc.keys()) {
      const s = this.summary(c, at, person);
      if (s && s.phase === 'boarding' && counts(s.owners[role])) {
        const id = s.owners[role].id;
        if (!owned.has(id)) owned.set(id, []);
        owned.get(id)!.push(s);
      }
    }
    const me = personKey(person);
    const others = colleagues.filter((c) => c.id !== me);
    const chosen = others.find((c) => c.id === colleague) ?? null;
    if (truthy(colleague) && chosen === null) throw new ActionError('Choose a colleague from the list.');
    const tpl = person.default_template;
    const rank = (r: any) => SEVERITY_RANK[or(r.severity, '') as string] ?? -1;
    const theirs = [...(chosen ? owned.get(colleague as string) ?? [] : [])]
      .sort((a, b) => rank(b) - rank(a) || b.minutes_boarding - a.minutes_boarding || cmpStr(a.csn, b.csn));
    const rows = theirs.map((s) => {
      const t = this.case(s.csn, at, person, false).templates[tpl] ?? null;
      const wf = t ? t.workflow : { state: 'drafting' };
      const confirm = new Set<string>((t ? t.draft.rules : [])
        .filter((r: any) => r.element in CONFIRM_LABEL && r.status === 'attest' && !truthy(get(r, 'resolution')))
        .map((r: any) => r.element));
      return {
        csn: s.csn, patient: s.patient, severity: s.severity, chief_complaint: s.chief_complaint, facility: s.facility,
        destination: s.destination, minutes_boarding: s.minutes_boarding, owner: s.owners[role], state: wf.state,
        signed: get(wf, 'signed'), acknowledged: get(wf, 'acknowledged'),
        blocking: t ? t.draft.completeness.blocking.length : 0,
        to_confirm: Object.keys(CONFIRM_LABEL).filter((e) => confirm.has(e)).map((e) => CONFIRM_LABEL[e]),
        changed: t && wf.state === 'changed' ? t.changes.elements.map((c: any) => c.label) : [],
      };
    });
    return {
      at: iso(at), role, template: tpl, colleagues: others.map((c) => ({ ...c, patients: (owned.get(c.id) ?? []).length })),
      colleague: chosen, rows,
    };
  }

  /** Take some or all of a colleague's patients in one go: one handover row per patient, all or none. */
  takeOver(at: number, person: any, colleague: string, csns: string[], colleagues: any[]): any {
    const view = this.takeover(at, person, colleague, colleagues);
    if (view.colleague === null) throw new ActionError('Choose a colleague from the list.');
    if (!csns.length) throw new ActionError('Choose at least one patient to take over.');
    const theirs = new Map<string, any>(view.rows.map((r: any) => [r.csn, r]));
    const rows: any[] = [];
    for (const csn of new Set(csns)) {
      const r = theirs.get(csn);
      if (!r) {
        const e = this.ds.enc.get(csn);
        const who = e ? e.patient_name : 'that patient';
        throw new ActionError(`${view.colleague.name} no longer owns ${who}. Reload the list and try again.`);
      }
      rows.push(this.assignmentRow(csn, at, person, 'handover', { id: personKey(person), name: person.name }, r.owner));
    }
    for (const row of rows) this.store.assign(row);
    return { ...this.takeover(at, person, colleague, colleagues), taken: rows.map((r) => r.csn) };
  }

  // ------------------------------------------------------------------------------------------------ tasks
  /**
   * The action list (E24) of a handoff signed at `at` as tasks: one create per item whose text is not a task of this
   * template yet, by the clinician who signed (who: the sign off row). (logic.py's backfill_tasks, for deployments from
   * before tasks, is live mode only and has no port.)
   */
  private handoffTasks(csn: string, template: string, version: number, at: number, who: any, draft: any): any[] {
    const el = or(elements(draft).get('E24') ?? null, {}) as any;
    const items = (or(get(or(get(el, 'narrative'), {}), 'items'), []) as any[]);
    const have = new Set(this.store.taskEvents(csn).filter((e) => e.kind === 'create' && e.template === template).map((e) => e.item_key));
    const rows: any[] = [];
    for (const i of items) {
      const text = strip(or(get(i, 'text'), '') as string);
      const k = tasks.key(text);
      if (!k || have.has(k)) continue;
      have.add(k);
      rows.push({
        csn, kind: 'create', template, version, item_key: k, text, role: tasks.roleOf(get(i, 'owner')), assignee_id: null, assignee_name: null,
        due: tasks.dueOf(get(i, 'due'), at), due_text: or(strip(or(get(i, 'due'), '') as string), null),
        owner_text: or(strip(or(get(i, 'owner'), '') as string), null), at, by_name: who.by, by_role: who.role,
        user_email: get(who, 'user_email'), by_id: get(who, 'by_id'),
      });
    }
    return rows.length ? this.store.addTasks(rows) : [];
  }

  /** Whether the person has the patient: ED roles while boarding, the receiving nurse and admitting physician after. */
  private inRole(person: any, summary: any): boolean {
    return (tasks.HAS_PATIENT[summary.phase] ?? []).includes(person.role);
  }

  /** Who a task of this patient can go to: the nurses (ED and the destination unit's) and the physicians. */
  taskPeople(summary: any, people: any[]): any[] {
    const unit = summary.destination.pending_unit ?? null;
    return people.filter((p) => p.role in tasks.DISCIPLINE && sees(p, summary.facility, unit))
      .map((p) => ({ id: p.id, name: p.name, role: tasks.DISCIPLINE[p.role], role_label: p.role_label ?? null }));
  }

  /** A task is the caller's when it was given to them, or is for their role on a patient they have (ED: they own). */
  private mine(t: any, summary: any, person: any): boolean {
    if (t.assignee) return t.assignee.id === personKey(person);
    const role = person.role;
    if (t.role !== (tasks.DISCIPLINE[role] ?? null) || !this.inRole(person, summary)) return false;
    if (ROLES.includes(role)) {
      const owner = summary.owners[role];
      return counts(owner) && owner.you;
    }
    return true;
  }

  /** One patient's tasks at `at`, their counts, whether the caller can act, and who a task can go to; null when off the board. */
  patientTasks(csn: string, at: number, person: any, people: any[]): any {
    const summary = this.summary(csn, at, person);
    if (summary === null) return null;
    const found = [...tasks.replay(this.store.taskEvents(csn, at), at).values()];
    const viewer = person.role === 'viewer', inRole = this.inRole(person, summary);
    return {
      at: iso(at), csn, phase: summary.phase, tasks: found.map((t) => tasks.shown(t, at)), counts: tasks.counts(found, at),
      access: { can_act: !viewer, in_role: inRole, note: viewer ? TASK_READ_ONLY : inRole ? null : OUTSIDE_ROLE[summary.phase] },
      people: this.taskPeople(summary, people),
    };
  }

  /** The summary for a task change: on the board, not the viewer, and not before a task event already recorded. */
  private taskCheck(csn: string, at: number, person: any): any {
    const summary = this.summary(csn, at, person);
    if (summary === null) throw new ActionError('This patient is not on your board at this time.');
    if (person.role === 'viewer') throw new ActionError(TASK_READ_ONLY);
    if (this.store.taskEvents(csn).some((e) => e.at > at)) throw new ActionError(EARLIER);
    return summary;
  }

  private event(csn: string, kind: string, at: number, person: any, summary: any, fields: any): any {
    const row: any = { csn, kind, at, by_name: person.name, by_role: person.role, user_email: get(person, 'user_email'), by_id: personKey(person), ...fields };
    if (!this.inRole(person, summary)) row.outside_role = true; // allowed, never blocked; recorded
    return row;
  }

  private static due(raw: any): number | null {
    if (raw === null || raw === undefined) return null;
    try {
      return ts(raw);
    } catch {
      throw new ActionError(TASK_DUE);
    }
  }

  /** [role, colleague or null] from a body with role and to (a colleague's id). */
  private target(body: any, summary: any, people: any[]): [string, any] {
    const role = get(body, 'role'), to = get(body, 'to');
    if (truthy(to)) {
      const p = this.taskPeople(summary, people).find((x) => x.id === to) ?? null;
      if (p === null) throw new ActionError(TASK_COLLEAGUE);
      if (truthy(role) && role !== p.role) throw new ActionError(TASK_ROLE_MISMATCH);
      return [p.role, p];
    }
    if (!tasks.ROLES.includes(role)) throw new ActionError(TASK_ROLE);
    return [role, null];
  }

  /** A task added by hand: text, role (nurse or physician), optionally a colleague and a due time. */
  addTask(csn: string, at: number, person: any, body: any, people: any[]): any {
    const summary = this.taskCheck(csn, at, person);
    const text = strip(or(get(body, 'text'), '') as string);
    if (!truthy(text)) throw new ActionError(TASK_TEXT);
    if (pyLen(text) > MAX_TEXT) throw new ActionError(TOO_LONG);
    const [role, to] = this.target(body, summary, people);
    const due = Views.due(get(body, 'due'));
    this.store.addTasks([this.event(csn, 'create', at, person, summary, {
      template: null, item_key: null, text, role, assignee_id: to ? to.id : null, assignee_name: to ? to.name : null, due,
    })]);
    return this.patientTasks(csn, at, person, people);
  }

  /** A change to one task: done, not_needed (with a reason), reassign, due, or undo. */
  taskAct(taskId: number, at: number, person: any, body: any, people: any[]): any {
    const create = this.store.allTaskEvents().find((e) => e.kind === 'create' && e.task_id === taskId) ?? null;
    if (create === null) throw new ActionError(NO_TASK);
    const csn = create.csn;
    const summary = this.taskCheck(csn, at, person); // also refuses a task created after `at` (the replay clock)
    const t = tasks.replay(this.store.taskEvents(csn, at), at).get(taskId);
    const kind = get(body, 'kind');
    const ev = (k: string, f: any = {}) => this.event(csn, k, at, person, summary, { task_id: taskId, ...f });
    let row: any;
    if (tasks.CLOSED.includes(kind)) {
      if (t.status !== 'open') throw new ActionError(TASK_CLOSED);
      const reason = strip(or(get(body, 'reason'), '') as string);
      if (kind === 'not_needed' && !truthy(reason)) throw new ActionError(TASK_REASON);
      if (pyLen(reason) > MAX_REASON) throw new ActionError(TASK_REASON_LONG);
      row = ev(kind, { reason: kind === 'not_needed' ? reason : null });
    } else if (kind === 'reassign' || kind === 'due') {
      if (t.status !== 'open') throw new ActionError(TASK_CLOSED);
      const was = { from_role: t.role, from_assignee_id: t.assignee ? t.assignee.id : null, from_assignee_name: t.assignee ? t.assignee.name : null };
      if (kind === 'reassign') {
        const [role, to] = this.target(body, summary, people);
        if (role === t.role && (to ? to.id : null) === was.from_assignee_id) {
          throw new ActionError(`The task is with ${to ? to.name : `the ${role}`} already.`);
        }
        row = ev('reassign', { role, assignee_id: to ? to.id : null, assignee_name: to ? to.name : null, ...was });
      } else {
        const due = Views.due(get(body, 'due'));
        if (due === t.due) throw new ActionError('The task has that due time already.');
        row = ev('due', { due, from_due: t.due });
      }
    } else if (kind === 'undo') {
      const last = tasks.undoOf(t);
      if (last === null) throw new ActionError(TASK_UNDO);
      const e = t.history[t.history.length - 1];
      if (tasks.CLOSED.includes(last)) {
        row = ev('reopen', { undo: true });
      } else if (last === 'reassign') {
        row = ev('reassign', {
          role: e.from_role, assignee_id: e.from_assignee_id ?? null, assignee_name: e.from_assignee_name ?? null, from_role: t.role,
          from_assignee_id: t.assignee ? t.assignee.id : null, from_assignee_name: t.assignee ? t.assignee.name : null, undo: true,
        });
      } else {
        row = ev('due', { due: e.from_due ?? null, from_due: t.due, undo: true });
      }
    } else {
      throw new ActionError(TASK_KIND);
    }
    this.store.addTasks([row]);
    return this.patientTasks(csn, at, person, people);
  }

  /** The Tasks page: the caller's open tasks across patients (or every task on their board), grouped, and the recently closed. */
  myTasks(at: number, person: any, scope = 'mine'): any {
    const groups: Record<string, [any, any][]> = { overdue: [], soon: [], later: [], none: [] };
    const done: [any, any][] = [];
    const byCsn = new Map<string, TaskEvent[]>();
    for (const e of this.store.allTaskEvents(at)) {
      let list = byCsn.get(e.csn);
      if (!list) byCsn.set(e.csn, (list = []));
      list.push(e);
    }
    const me = personKey(person);
    for (const [csn, events] of byCsn) {
      const summary = this.summary(csn, at, person);
      if (summary === null) continue;
      const patient = {
        csn, patient: summary.patient, phase: summary.phase, facility: summary.facility, unit: summary.destination.pending_unit ?? null,
        severity: summary.severity, in_role: this.inRole(person, summary),
      };
      for (const t of tasks.replay(events, at).values()) {
        const mine = scope === 'all' || this.mine(t, summary, person);
        if (t.status === 'open' && mine) groups[tasks.group(t, at)].push([t, patient]);
        else if (t.status !== 'open' && at - t.closed.at <= tasks.RECENT && (mine || t.history[t.history.length - 1].by_id === me)) done.push([t, patient]);
      }
    }
    const dueKey = (t: any) => (t.due === null ? Infinity : t.due);
    const byDue = (a: [any, any], b: [any, any]) => {
      const x = dueKey(a[0]), y = dueKey(b[0]);
      return (x === y ? 0 : x < y ? -1 : 1) || a[0].created.at - b[0].created.at || a[0].id - b[0].id;
    };
    const ids = ['overdue', 'soon', 'later', 'none'];
    const out = ids.map((g) => ({ id: g, tasks: [...groups[g]].sort(byDue).map(([t, p]) => ({ ...tasks.shown(t, at), ...p })) }));
    done.sort((a, b) => b[0].closed.at - a[0].closed.at || b[0].id - a[0].id);
    return {
      at: iso(at), scope, role: tasks.DISCIPLINE[person.role] ?? null, groups: out, done: done.map(([t, p]) => ({ ...tasks.shown(t, at), ...p })),
      counts: { open: ids.reduce((n, g) => n + groups[g].length, 0), overdue: groups.overdue.length },
    };
  }
}

/**
 * Code status, allergies and isolation are one value for the patient: a template cannot confirm "none" while a
 * value is entered, or enter one while another template confirmed "none".
 */
function patientCheck(r: any, body: any, template: string, pat: any): void {
  const el = r.element;
  const options = or(get(r, 'attest_options'), []) as any[];
  if (truthy(get(body, 'remove')) && el === 'E09' && body.option === ALLERGY) return;
  if (options.length && pyEq(body.option, options[0])) {
    const found = el === 'E09' ? (pat.E09.length ? pat.E09[pat.E09.length - 1] : null) : pat[el];
    if (found && (el === 'E09' || found.template !== template)) {
      const where = TEMPLATE_NAME[found.template] ?? found.template;
      const change = el === 'E09' ? 'Remove it there first.' : 'Change it there first.';
      throw new ActionError(`${PATIENT_LABEL[el]} was entered as ${found.value} by ${found.by} on the ${where}. ${change}`);
    }
    return;
  }
  for (const [k, a] of pat.none as Map<string, Action>) {
    const t = k.slice(0, k.lastIndexOf('|'));
    if (k.endsWith(`|${el}`) && t !== template) {
      throw new ActionError(`${a.by} confirmed "${a.option}" on the ${TEMPLATE_NAME[a.template] ?? a.template}. Change it there first.`);
    }
  }
}

function histogram(values: number[]): any[] {
  const bins: [number, number, string][] = [[0.0, 0.5, 'under 50%'], [0.5, 0.6, '50 to 59%'], [0.6, 0.7, '60 to 69%'], [0.7, 0.8, '70 to 79%'],
    [0.8, 0.9, '80 to 89%'], [0.9, 1.01, '90% or more']];
  return bins.map(([lo, hi, label]) => ({ bin: label, n: values.filter((v) => lo <= v && v < hi).length }));
}
