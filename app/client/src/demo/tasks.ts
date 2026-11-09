/**
 * Port of app/server/tasks.py: tasks from the action list of a signed handoff, and tasks added by hand. Tasks come
 * from append only events (create, done, not_needed, reopen, reassign, due); a task at time T is its events with
 * at <= T applied in order. Signing a handoff turns each action list item into a task; signing again or an addendum
 * adds only the items whose text (case and spacing aside) is not a task of that template yet.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { DAY, fromWall, HOUR, iso, pySplit, strip, wall } from './py';

export const KINDS = ['create', 'done', 'not_needed', 'reopen', 'reassign', 'due'];
export const ROLES = ['nurse', 'physician'];
export const CLOSED = ['done', 'not_needed'];
const UNDOABLE = ['done', 'not_needed', 'reassign', 'due'];
/** The discipline of each app role: the tasks of that role are theirs. The viewer has none. */
export const DISCIPLINE: Record<string, string> = { ed_nurse: 'nurse', unit_nurse: 'nurse', ed_physician: 'physician', admitting_physician: 'physician' };
/** Who has the patient: while boarding (or after a canceled admission, still in the ED), and once on the unit. */
export const HAS_PATIENT: Record<string, string[]> = {
  boarding: ['ed_nurse', 'ed_physician'], canceled: ['ed_nurse', 'ed_physician'], arrived: ['unit_nurse', 'admitting_physician'],
};
// The words of an action list owner that name a role; the first one wins, else the nurse. A medical service or
// specialty is the physician (any word ending in -ology too).
const NURSE_WORDS = ['rn', 'nurse', 'nurses', 'nursing'];
const PHYSICIAN_WORDS = ['md', 'physician', 'physicians', 'team', 'medicine', 'service', 'hospitalist', 'attending', 'resident',
  'orthopedics', 'psychiatry', 'surgery'];
// Due words that mean the time of signing, and a time of day, alone or after "by" or "at".
const NOW_WORDS = ['now', 'immediate', 'immediately', 'stat'];
const CLOCK = /^(?:(?:by|at)[ \t]+)?([01]?[0-9]|2[0-3]):([0-5][0-9])$/i;
const MINUTE = 60_000;
/** The Tasks page's "Due in the next 2 hours", and its recently completed tasks. */
export const SOON = 2 * HOUR;
export const RECENT = 12 * HOUR;

export type TaskEvent = Record<string, any> & { csn: string; kind: string; at: number; event_id: number; task_id: number };

/** The role an action list item's owner names; the first such word wins; anything else is the nurse. */
export function roleOf(owner: string | null | undefined): string {
  for (const w of (owner ?? '').toLowerCase().match(/[a-z]+/g) ?? []) {
    if (NURSE_WORDS.includes(w)) return 'nurse';
    if (PHYSICIAN_WORDS.includes(w) || w.endsWith('ology')) return 'physician';
  }
  return 'nurse';
}

/** When an action list item is due: "16:00", "by 16:00" or "at 16:00" is the next 16:00 at or after signing; "Now",
 * "immediate", "immediately" and "stat" the signing; anything else none. */
export function dueOf(due: string | null | undefined, signed: number): number | null {
  const text = strip(due ?? '');
  if (NOW_WORDS.includes(text.toLowerCase())) return signed;
  const m = CLOCK.exec(text);
  if (!m) return null;
  const day = Math.floor(wall(signed) / DAY) * DAY;
  for (let d = 0; d < 2; d++) {
    const t = fromWall(day + d * DAY + Number(m[1]) * HOUR + Number(m[2]) * MINUTE);
    if (t >= signed) return t;
  }
  throw new Error('no such time in the next two days');
}

/** An action list item's text as the duplicate check compares it: lowercase, single spaces. */
export function key(text: string): string {
  return pySplit(text.toLowerCase()).join(' ');
}

/** Task events in the order they apply: by the time they apply to, then the order they were saved. */
export function ordered(events: TaskEvent[]): TaskEvent[] {
  return [...events].sort((a, b) => a.at - b.at || a.event_id - b.event_id);
}

function assignee(e: any): any {
  return e.assignee_id ? { id: e.assignee_id, name: e.assignee_name || e.assignee_id } : null;
}

/** {task id: task} from task events with at <= `at` (every event when null), in the order the tasks were created. */
export function replay(events: TaskEvent[], at: number | null = null): Map<number, any> {
  const out = new Map<number, any>();
  for (const e of ordered(events.filter((x) => at === null || x.at <= at))) {
    const k = e.kind, tid = e.task_id;
    if (k === 'create') {
      out.set(tid, {
        id: tid, csn: e.csn, text: e.text, role: e.role, assignee: assignee(e), due: e.due ?? null, due_text: e.due_text ?? null,
        status: 'open', reason: null, source: e.template ? 'handoff' : 'added', template: e.template ?? null, version: e.version ?? null,
        owner_text: e.owner_text ?? null, created: { by: e.by_name, at: e.at }, closed: null, history: [],
      });
    }
    const t = out.get(tid);
    if (!t) continue; // an event of a task created later (or never): nothing to apply it to
    if (CLOSED.includes(k)) {
      Object.assign(t, { status: k, reason: k === 'not_needed' ? e.reason ?? null : null, closed: { by: e.by_name, at: e.at } });
    } else if (k === 'reopen') {
      Object.assign(t, { status: 'open', reason: null, closed: null });
    } else if (k === 'reassign') {
      Object.assign(t, { role: e.role, assignee: assignee(e) });
    } else if (k === 'due') {
      t.due = e.due ?? null;
    }
    t.history.push(e);
  }
  return out;
}

/** What Undo reverts on the task (the kind of its last change), or null. */
export function undoOf(task: any): string | null {
  const last = task.history[task.history.length - 1];
  return UNDOABLE.includes(last.kind) && !last.undo ? last.kind : null;
}

export function overdue(task: any, at: number): boolean {
  return task.status === 'open' && task.due !== null && task.due < at;
}

/** The Tasks page group of an open task: overdue, soon, later or none. */
export function group(task: any, at: number): string {
  if (task.due === null) return 'none';
  if (task.due < at) return 'overdue';
  return task.due <= at + SOON ? 'soon' : 'later';
}

function step(e: any): any {
  const out: any = { kind: e.kind, by: e.by_name, by_role: e.by_role, at: iso(e.at) };
  if (e.kind === 'not_needed') out.reason = e.reason ?? null;
  else if (e.kind === 'reassign') Object.assign(out, { role: e.role, to: e.assignee_name ?? null, from_role: e.from_role ?? null, from: e.from_assignee_name ?? null });
  else if (e.kind === 'due') Object.assign(out, { due: iso(e.due ?? null), from_due: iso(e.from_due ?? null) });
  if (e.undo) out.undo = true;
  if (e.outside_role) out.outside_role = true;
  return out;
}

/** A task as the API returns it at time `at`. */
export function shown(task: any, at: number): any {
  return {
    id: task.id, csn: task.csn, text: task.text, role: task.role, assignee: task.assignee, due: iso(task.due), due_text: task.due_text,
    overdue: overdue(task, at), status: task.status, reason: task.reason, source: task.source, template: task.template, version: task.version,
    owner_text: task.owner_text, created: { by: task.created.by, at: iso(task.created.at) },
    closed: task.closed ? { by: task.closed.by, at: iso(task.closed.at) } : null, undo: undoOf(task), history: task.history.map(step),
  };
}

/** Open and overdue tasks, for the board row. */
export function counts(tasks: any[], at: number): { open: number; overdue: number } {
  return { open: tasks.filter((t) => t.status === 'open').length, overdue: tasks.filter((t) => overdue(t, at)).length };
}
