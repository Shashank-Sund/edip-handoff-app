/**
 * Port of app/server/workflow.py MemoryStore: clinician actions, and who owns each patient, in memory, append only. Every action carries
 * `at`, the time it applies to; a view at time T counts only actions with at <= T. `seq` stands in for
 * recorded_ts when ordering actions saved for the same `at` (both follow the order actions were saved).
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { KINDS as ASSIGNMENT_KINDS, type Assignment } from './assignment';

export const KINDS = ['edit', 'attest', 'sign', 'ack'];
export type Action = Record<string, any> & { csn: string; kind: string; at: number; recorded_ts: number; seq: number };

export class MemoryStore {
  readonly kind = 'memory';
  private rows = new Map<string, Action[]>();
  private assigned = new Map<string, Assignment[]>();
  private n = 0;
  private assignmentId = 0;

  add(kind: string, row: Record<string, any>): Action {
    if (!KINDS.includes(kind)) throw new Error(`unknown action ${kind}`);
    const a = { ...row, kind, recorded_ts: Date.now(), seq: ++this.n } as Action;
    let list = this.rows.get(a.csn);
    if (!list) this.rows.set(a.csn, (list = []));
    list.push(a);
    return a;
  }

  actions(csn: string, at: number | null = null): Action[] {
    return (this.rows.get(csn) ?? []).filter((r) => at === null || r.at <= at);
  }

  allActions(at: number | null = null): Action[] {
    return [...this.rows.values()].flat().filter((r) => at === null || r.at <= at);
  }

  /** One ownership change (claim, assign, release, handover); assignment_id gives the order it was saved. */
  assign(row: Record<string, any>): Assignment {
    if (!ASSIGNMENT_KINDS.includes(row.kind)) throw new Error(`unknown assignment ${row.kind}`);
    const a = { ...row, assignment_id: ++this.assignmentId, recorded_ts: Date.now() } as unknown as Assignment;
    let list = this.assigned.get(a.csn);
    if (!list) this.assigned.set(a.csn, (list = []));
    list.push(a);
    return a;
  }

  assignments(csn: string, at: number | null = null): Assignment[] {
    return (this.assigned.get(csn) ?? []).filter((r) => at === null || r.at <= at);
  }

  reset() {
    this.rows.clear();
    this.assigned.clear();
  }
}
