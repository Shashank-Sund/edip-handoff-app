/** Tasks (app/server/tasks.py): words the panel, the Tasks page and the board use for them. */
import { clock, day, fromFacilityWall, timeZone } from './format';
import type { Task, TaskGroupId, TaskRole, TaskStep } from './types';

export const TASK_ROLE: Record<TaskRole, string> = { nurse: 'Nurse', physician: 'Physician' };

/** The Tasks page groups, in order. */
export const GROUP_TITLE: Record<TaskGroupId, string> = {
  overdue: 'Overdue',
  soon: 'Due in the next 2 hours',
  later: 'Later',
  none: 'No time',
};

/** "16:30", or "Thu 16:30" when it is not the day of the clock. */
export function when(iso: string, at: string): string {
  return day(iso) === day(at) ? clock(iso) : `${day(iso).split(',')[0]} ${clock(iso)}`;
}

/** Who the task is for: a colleague, or the role. */
export const forWhom = (t: Task) => (t.assignee ? t.assignee.name : TASK_ROLE[t.role]);

/** When the task is due, in words: overdue, a time, the action list's own words, or no due time. */
export function dueLabel(t: Task, at: string): string {
  if (t.due) return t.overdue ? `Overdue, was due ${when(t.due, at)}` : `Due ${when(t.due, at)}`;
  if (t.due_text) return `"${t.due_text}", no set time`;
  return 'No due time';
}

/** Where the task came from. */
export function origin(t: Task, templateName: (id: string) => string): string {
  if (t.source === 'handoff') return `From the ${templateName(t.template ?? '')} action list, signed by ${t.created.by}`;
  return `Added by ${t.created.by}`;
}

/** One line of a task's history. */
export function stepLabel(s: TaskStep): string {
  const by = `${s.by} at ${clock(s.at)}`;
  const outside = s.outside_role ? ' (outside their role)' : '';
  switch (s.kind) {
    case 'create':
      return `Created by ${by}${outside}`;
    case 'done':
      return `Done by ${by}${outside}`;
    case 'not_needed':
      return `Not needed by ${by}${outside}: ${s.reason}`;
    case 'reopen':
      return `Opened again (Undo) by ${by}${outside}`;
    case 'reassign':
      return `${s.undo ? 'Given back (Undo)' : 'Reassigned'} to ${s.to ?? `the ${TASK_ROLE[s.role ?? 'nurse'].toLowerCase()}`} by ${by}${outside}`;
    case 'due':
      return `${s.undo ? 'Due time put back (Undo)' : 'Due time changed'} to ${s.due ? clock(s.due) : 'none'} by ${by}${outside}`;
  }
}

/** What Undo would do, for the button's label. */
export const UNDO_LABEL: Record<NonNullable<Task['undo']>, string> = {
  done: 'Undo done',
  not_needed: 'Undo not needed',
  reassign: 'Undo reassign',
  due: 'Undo due time',
};

/**
 * The next time the facility clock shows `hhmm` ("16:30"), at or after `at`: the time a clinician means when they
 * pick a time of day. ISO, or null when `hhmm` is not a time.
 */
export function nextAt(hhmm: string, at: string): string | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!m) return null;
  const now = new Date(at).getTime();
  const p: Record<string, number> = {};
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: timeZone(), year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date(now));
  for (const x of parts) if (x.type !== 'literal') p[x.type] = Number(x.value);
  for (let d = 0; d < 2; d++) {
    const day = new Date(Date.UTC(p.year, p.month - 1, p.day + d));
    const t = fromFacilityWall(day.getUTCFullYear(), day.getUTCMonth() + 1, day.getUTCDate(), Number(m[1]), Number(m[2]));
    if (t >= now) return new Date(t).toISOString();
  }
  return null;
}
