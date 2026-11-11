/**
 * Port of app/server/workflow.py MemoryStore: clinician actions, who owns each patient, tasks, and each clinician's looks
 * at a patient, in memory, append only. Every action carries `at`, the time it applies to; a view at time T counts only
 * actions with at <= T. `seq` stands in for recorded_ts when ordering actions saved for the same `at` (both follow the
 * order actions were saved); `saved`, on every row of every list, does so across them (seen.ts).
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { KINDS as ASSIGNMENT_KINDS, type Assignment } from './assignment';
import { KINDS as TASK_KINDS, type TaskEvent } from './tasks';
import { lastLooks, SOURCES, type Look } from './seen';

export const KINDS = ['edit', 'attest', 'sign', 'ack'];
export type Action = Record<string, any> & { csn: string; kind: string; at: number; recorded_ts: number; seq: number; saved: number };

export class MemoryStore {
  readonly kind = 'memory';
  private rows = new Map<string, Action[]>();
  private assigned = new Map<string, Assignment[]>();
  private n = 0;
  private assignmentId = 0;
  private tasks = new Map<string, TaskEvent[]>();
  private events = 0;
  private taskIds = 0;
  private looks: Look[] = [];
  private saved = 0;

  add(kind: string, row: Record<string, any>): Action {
    if (!KINDS.includes(kind)) throw new Error(`unknown action ${kind}`);
    const a = { ...row, kind, recorded_ts: Date.now(), seq: ++this.n, saved: ++this.saved } as Action;
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
    const a = { ...row, assignment_id: ++this.assignmentId, recorded_ts: Date.now(), saved: ++this.saved } as unknown as Assignment;
    let list = this.assigned.get(a.csn);
    if (!list) this.assigned.set(a.csn, (list = []));
    list.push(a);
    return a;
  }

  assignments(csn: string, at: number | null = null): Assignment[] {
    return (this.assigned.get(csn) ?? []).filter((r) => at === null || r.at <= at);
  }

  /**
   * Task events, saved together; event_id gives the order they were saved, and a create gets a new task_id. A create
   * from a handoff whose item is a task of that template already is skipped. Returns the rows saved.
   */
  addTasks(rows: Record<string, any>[]): TaskEvent[] {
    const out: TaskEvent[] = [];
    for (const r of rows) {
      if (!TASK_KINDS.includes(r.kind)) throw new Error(`unknown task event ${r.kind}`);
      let list = this.tasks.get(r.csn);
      if (!list) this.tasks.set(r.csn, (list = []));
      if (r.kind === 'create' && r.template && list.some((e) => e.kind === 'create' && e.template === r.template && e.item_key === r.item_key)) continue;
      const e = { ...r, ...(r.kind === 'create' ? { task_id: ++this.taskIds } : {}), event_id: ++this.events, recorded_ts: Date.now(), saved: ++this.saved } as unknown as TaskEvent;
      list.push(e);
      out.push(e);
    }
    return out;
  }

  taskEvents(csn: string, at: number | null = null): TaskEvent[] {
    return (this.tasks.get(csn) ?? []).filter((r) => at === null || r.at <= at);
  }

  allTaskEvents(at: number | null = null): TaskEvent[] {
    return [...this.tasks.values()].flat().filter((r) => at === null || r.at <= at);
  }

  /** One look at a patient (opening the handoff, or Mark as seen); seen_id gives the order it was saved. */
  look(row: Record<string, any>): Look {
    if (!SOURCES.includes(row.source)) throw new Error(`unknown look ${row.source}`);
    const r = { ...row, seen_id: this.looks.length + 1, recorded_ts: Date.now(), saved: ++this.saved } as unknown as Look;
    this.looks.push(r);
    return r;
  }

  /** csn to the latest look by `who` with at <= `at`, at one patient (csn) or all. */
  lastLooks(who: string, at: number, csn: string | null = null): Map<string, Look> {
    return lastLooks(this.looks.filter((r) => csn === null || r.csn === csn), who, at);
  }

  /** Every look, in the order saved (the tests read them). */
  allLooks(): Look[] {
    return [...this.looks];
  }

  reset() {
    this.rows.clear();
    this.assigned.clear();
    this.tasks.clear();
    this.looks = [];
  }
}
