/**
 * Port of app/server/seen.py: what changed since you last looked. Looks come from append only rows (opening the
 * handoff, or Mark as seen on the board); your last look at time T is your latest look with at <= T, so looking at
 * an earlier time never moves your last look at a later time forward. A clinician's change counts when it applies
 * after the look, or at the same time and was saved after it; `saved` stands in for recorded_ts (both follow the
 * order rows were saved, across actions, owners, tasks and looks).
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { actor } from './assignment';
import { iso } from './py';

export const SOURCES = ['handoff', 'board'];

export type Look = Record<string, any> & { csn: string; by_id: string; at: number; seen_id: number; saved: number };

/** Looks in the order they apply: by the time viewed, then the order they were saved. */
export function ordered(looks: Look[]): Look[] {
  return [...looks].sort((a, b) => a.at - b.at || a.seen_id - b.seen_id);
}

/** csn to the latest look by `who` with at <= `at`, from looks at any patients. */
export function lastLooks(looks: Look[], who: string, at: number): Map<string, Look> {
  const out = new Map<string, Look>();
  for (const r of ordered(looks.filter((r) => r.by_id === who && r.at <= at))) out.set(r.csn, r);
  return out;
}

/** Whether a clinician's change (an action, task event or owner change) came after the look. */
export function after(row: any, look: Look): boolean {
  return row.at > look.at || (row.at === look.at && row.saved > look.saved);
}

/** Whether someone else made the change. */
export function byOther(row: any, me: string): boolean {
  return actor(row) !== me;
}

/** The changes, newest first; changes at the same time keep the order they were listed in. */
export function newestFirst(items: [number, any][]): any[] {
  return [...items].sort((a, b) => b[0] - a[0]).map(([, x]) => x);
}

/** The board row's since. */
export function summary(look: Look | null | undefined, items: any[] | null): any {
  return { count: (items ?? []).length, last_look: look ? iso(look.at) : null, first_time: !look };
}
