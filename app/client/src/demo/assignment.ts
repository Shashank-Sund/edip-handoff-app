/**
 * Port of app/server/assignment.py: who owns each ED patient (one nurse owner and one physician owner), and the
 * ED's shift changes. Owners come from append only rows (claim, assign, release, handover); the owner for a role at
 * time T is the latest row for (csn, role) with at <= T. An owner idle for longer than the stale limit no longer
 * counts: the patient shows as Unassigned, with the stale owner named.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { DAY, fromWall, HOUR, iso, strip, wall } from './py';

export const ROLES = ['ed_nurse', 'ed_physician'];
export const KINDS = ['claim', 'assign', 'release', 'handover'];
const OWNS = ['claim', 'assign', 'handover'];
const MINUTE = 60_000;

/** main.py's defaults: EDIP_SHIFT_CHANGES, EDIP_SHIFT_WARNING_MINUTES and EDIP_STALE_OWNER_HOURS. */
export const SHIFT_CHANGES: [number, number][] = [[7, 0], [19, 0]];
export const SHIFT_WARNING_MINUTES = 30;
export const STALE_OWNER_HOURS = 14;

export type Assignment = Record<string, any> & { csn: string; role: string; kind: string; at: number; assignment_id: number };

/** Who a person is for ownership (the persona id in the demo). */
export function personKey(person: any): string {
  return person.key || person.id;
}

/** Who did a clinician action: by_id when the app recorded it, else the signed in email. */
export function actor(a: any): string | null {
  return a.by_id || strip(a.user_email ?? '').toLowerCase() || null;
}

function ordered(rows: Assignment[]): Assignment[] {
  return [...rows].sort((a, b) => a.at - b.at || a.assignment_id - b.assignment_id);
}

/** The owner for `role` at `at`, or null when no one owns the patient; stale and you as in assignment.py. */
export function ownerAt(rows: Assignment[], role: string, acts: any[], at: number, staleMs: number, me: string | null): any {
  const mine = ordered(rows.filter((r) => r.role === role && r.at <= at));
  if (!mine.length || !OWNS.includes(mine[mine.length - 1].kind)) return null;
  const last = mine[mine.length - 1];
  const who = last.assignee_id;
  let active = last.at;
  for (const a of acts) if (a.at <= at && actor(a) === who && a.at > active) active = a.at;
  return { id: who, name: last.assignee_name, since: iso(last.at), last_active: iso(active), stale: at - active > staleMs, you: who === me };
}

/** Whether an owner counts: someone owns the patient and has not gone stale. */
export function counts(owner: any): boolean {
  return !!owner && !owner.stale;
}

/** The first shift change after `at`, at the facility's local time of day. */
export function nextShift(at: number, changes: [number, number][]): number {
  const day = Math.floor(wall(at) / DAY) * DAY;
  for (let d = 0; d < 3; d++) {
    for (const [h, m] of changes) {
      const t = fromWall(day + d * DAY + h * HOUR + m * MINUTE);
      if (t > at) return t;
    }
  }
  throw new Error('no shift change in the next two days');
}

/** The latest shift change at or before `at`, at the facility's local time of day. */
export function lastShift(at: number, changes: [number, number][]): number {
  const day = Math.floor(wall(at) / DAY) * DAY;
  for (let d = 0; d < 3; d++) {
    for (const [h, m] of [...changes].reverse()) {
      const t = fromWall(day - d * DAY + h * HOUR + m * MINUTE);
      if (t <= at) return t;
    }
  }
  throw new Error('no shift change in the last two days');
}

/** How far a handover was from the nearest shift change, for Analytics. */
export const HANDOVER_BUCKETS = ['More than 30 min before', 'Up to 30 min before', 'Up to 30 min after', 'More than 30 min after'];

export function handoverBucket(at: number, changes: [number, number][]): string {
  const before = nextShift(at, changes), after = lastShift(at, changes);
  const near = at - after <= before - at ? after : before;
  const m = (at - near) / MINUTE;
  return HANDOVER_BUCKETS[m < -30 ? 0 : m < 0 ? 1 : m <= 30 ? 2 : 3];
}
