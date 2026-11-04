/**
 * Port of app/server/logic.py: views computed from the read model and the clinician actions at a time `at`.
 *
 *   board(at, person)        patients with a handoff: boarding now, or arrived on the unit in the last 12 hours
 *   case(csn, at, person)    one patient: every template's effective draft, its rules after resolution,
 *                            workflow state, versions, changes
 *   analytics(at)            census, boarding times, handoff timing and completeness across the week so far
 *   act(...)                 validates and records an edit, attestation, sign off or acknowledgement
 *
 * Sign off follows the content: any edit or attestation after a signature, or a newer version whose clinical
 * content differs from the signed one, puts the template in state "changed" (sign again); the receiver cannot
 * accept it until the ED signs again. A stale vitals attestation counts only within that rule's age limit.
 *
 * Keep it in step with logic.py: app/fixtures/parity.py and src/demo/parity.test.ts compare the two.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import type { Dataset, Row } from './data';
import { ACKS, SIGNS, sees } from './personas';
import { cmpStr, get, HOUR, iso, isDict, letters, median, or, pyEq, pyLen, pyRound, pySplit, pyStr, strip, truthy } from './py';
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
export function effective(row: Row, acts: Action[], at: number | null = null, rules: Rules | null = null): any {
  const t = row.template;
  const rs: Rules = rules ?? {};
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
    if (r.status === 'attest' || r.status === 'missing' || r.status === 'stale') {
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
        if (e && entered(a, get(r, 'attest_options'))) {
          // A value the clinician entered (Droplet, DNR, an allergy): shown until the chart has one.
          r.resolution.entered = true;
          e.entered = {
            rule: r.rule, option: a.option, value: or(get(a, 'entry'), a.option), note: get(a, 'entry_note'), by: a.by, at: iso(a.at),
            version: a.version,
          };
        }
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
export function workflow(row: Row | null, acts: Action[], versions: Row[], departed = false, rules: Rules | null = null): any {
  if (row === null) return { state: 'drafting' };
  const t = row.template;
  const [s, before, after] = lastSign(acts, t);
  const out: any = { state: 'draft', version: row.version };
  if (s === null) {
    if (departed) out.state = 'transferred_unsigned';
    return out;
  }
  Object.assign(out, { state: 'signed', signed: { by: s.by, role: s.role, callback: get(s, 'callback'), at: iso(s.at), version: s.version } });
  let changed = false;
  if (row.version !== s.version) {
    const base = versions.find((v) => v.template === t && v.version === s.version) ?? null;
    changed = base === null || !pyEq(clinical(effective(base, before, null, rules)), clinical(effective(row, before, null, rules)));
  }
  out.changed_since_signed = changed;
  out.edited_since_signed = after.some((a) => a.kind === 'edit' || a.kind === 'attest');
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
   */
  constructor(readonly ds: Dataset, readonly store: MemoryStore, templates: any[] = [], readonly staleBoarding: number | null = null) {
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
    const phase = anyRow.status === 'retracted' ? 'canceled' : dep !== null ? 'arrived' : 'boarding';
    const els = elements(anyRow.draft);
    const dest = or(get(or(els.get('E04') ?? null, {}), 'data'), {}) as any;
    if (!sees(person, e.facility, or(get(dest, 'pending_unit'), dep !== null ? get(dest, 'unit') : null))) return null;
    const acts = this.store.actions(csn, at);
    const rows = this.ds.rowsAt(csn, at);
    const ref = dep !== null ? Math.min(at, dep) : at;
    const templates: any = {};
    for (const [tid, row] of latest) {
      const eff = effective(row, acts, ref, this.rules[tid] ?? null);
      templates[tid] = {
        version: row.version, as_of: iso(row.at), status: row.status, trigger: row.trigger, score: eff.completeness.score,
        blocking: eff.completeness.blocking, workflow: workflow(row, acts, rows, phase === 'arrived', this.rules[tid] ?? null),
      };
    }
    const sev = or(get(or(els.get('E07') ?? null, {}), 'narrative'), {}) as any;
    const flags: any = {};
    for (const k of ['E08', 'E09', 'E10']) flags[k] = get(or(els.get(k) ?? null, {}), 'data');
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
      code_status: isDict(flags.E08) ? get(or(flags.E08, {}), 'value') : null,
      isolation: isDict(flags.E10) ? get(or(flags.E10, {}), 'value') : null,
      allergies: Array.isArray(flags.E09) ? flags.E09.map((a: any) => get(a, 'allergen')) : [],
      last_update: { as_of: iso(anyRow.at), trigger: anyRow.trigger },
      templates,
    };
  }

  board(at: number, person: any): any {
    const rows: any[] = [];
    for (const c of this.ds.enc.keys()) {
      const s = this.summary(c, at, person);
      if (s) rows.push(s);
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
    return { at: iso(at), template: tpl, kpis, rows, scoped: truthy(mine) };
  }

  case(csn: string, at: number, person: any): any {
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
    for (const [tid, versions] of byT) {
      const row = versions[versions.length - 1];
      const rules = this.rules[tid] ?? null;
      const wf = workflow(row, acts, versions, summary.phase === 'arrived', rules);
      const draft = effective(row, acts, ref, rules);
      // What changed: since the signature when the template changed after signing (the text the ED signed,
      // clinician edits included), else since the previous version; both as the clinician sees them.
      let base: [number, any] | null = null;
      let since = 'previous';
      if (wf.state === 'changed') {
        const [s, before] = lastSign(acts, tid) as [Action, Action[], Action[]];
        const signed = versions.find((v) => v.version === s.version) ?? null;
        if (signed) {
          base = [s.version, effective(signed, before, s.at, rules)];
          since = 'signed';
        }
      } else if (versions.length > 1) {
        const prev = versions[versions.length - 2];
        base = [prev.version, effective(prev, acts, ref, rules)];
      }
      templates[tid] = {
        draft,
        row: { ...pick(row, ['version', 'status', 'trigger', 'model']), as_of: iso(row.at) },
        workflow: wf,
        changes: { since, version: base ? base[0] : null, elements: diff(base ? base[1] : null, draft) },
        versions: versions.map((v) => ({ version: v.version, as_of: iso(v.at), trigger: v.trigger, status: v.status, severity: v.severity, score: v.score })),
      };
    }
    const log = acts.map((a) => ({
      kind: a.kind, template: a.template, by: a.by, at: iso(a.at) as string, version: a.version,
      detail: or(or(get(a, 'element'), get(a, 'rule')), get(a, 'option')),
      ...(truthy(get(a, 'restore')) ? { restore: true } : {}),
      ...(a.kind === 'attest' ? this.entry(a) : {}),
    }));
    // sorted(key=at string, reverse=True) keeps equal keys in their original order
    log.sort((a, b) => cmpStr(b.at, a.at));
    return { summary, templates, activity: log };
  }

  /** History keeps a value the clinician entered, also once the chart has one and the item shows the chart. */
  private entry(a: Action): any {
    const options = get(or(get(or(this.rules[a.template] ?? null, {}), a.rule), {}), 'attest');
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
      for (const e of encs) {
        if (e.arrival_ts !== null && e.arrival_ts <= t && (e.left_ts === null || e.left_ts > t) && e.status !== 'canceled') ed++;
        if (e.admit_decision_ts !== null && e.admit_decision_ts <= t && (e.ed_departure_ts === null || e.ed_departure_ts > t) && truthy(ds.versions.get(e.csn) ?? null)
          && (e.ed_departure_ts !== null || this.staleBoarding === null || t - e.admit_decision_ts <= this.staleBoarding)) board++;
      }
      series.push({ t: iso(t), ed, boarding: board });
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
      addenda: new Set(drafts.filter((r) => r.status === 'addendum').map((r) => `${r.csn}|${r.at}`)).size,
    };
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
    const wf = workflow(latest, acts, this.ds.rowsAt(csn, at).filter((r) => r.template === template), arrived, rules);
    const row: any = {
      csn, template, version: latest.version, at, by: person.name, role: person.role, user_email: get(person, 'user_email'),
    };
    if (kind === 'edit' || kind === 'attest' || kind === 'sign') {
      if (!(SIGNS[person.role] ?? []).includes(template)) throw new ActionError('Only the sending ED clinician for this template can change or sign it.');
      // After the patient leaves the ED the handoff is final, except for an addendum, or when the ED never signed it
      // or signed an older version: then the ED can still complete and sign it.
      const unsigned = arrived && (!truthy(get(wf, 'signed')) || wf.signed.version < latest.version);
      if (summary.phase !== 'boarding' && latest.status !== 'addendum' && !unsigned) throw new ActionError('The patient has left the ED; the handoff is final.');
    } else if (kind === 'ack') {
      if (!(ACKS[person.role] ?? []).includes(template)) throw new ActionError('Only the receiving clinician can acknowledge this handoff.');
    } else {
      throw new ActionError(`Unknown action ${kind}.`);
    }
    // The replay clock: an action cannot go before one already recorded for this handoff.
    if (this.store.actions(csn).some((a) => a.template === template && a.at > at)) throw new ActionError(EARLIER);
    const dep = this.departure(csn, at);
    const eff = effective(latest, acts, dep !== null ? Math.min(at, dep) : at, rules);
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
      if (typed) {
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
    this.store.add(kind, row);
    return this.case(csn, at, person);
  }
}

function histogram(values: number[]): any[] {
  const bins: [number, number, string][] = [[0.0, 0.5, 'under 50%'], [0.5, 0.6, '50 to 59%'], [0.6, 0.7, '60 to 69%'], [0.7, 0.8, '70 to 79%'],
    [0.8, 0.9, '80 to 89%'], [0.9, 1.01, '90% or more']];
  return bins.map(([lo, hi, label]) => ({ bin: label, n: values.filter((v) => lo <= v && v < hi).length }));
}
