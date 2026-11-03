/**
 * Port of app/server/workflow.py MemoryStore: clinician actions in memory, append only. Every action carries
 * `at`, the time it applies to; a view at time T counts only actions with at <= T. `seq` stands in for
 * recorded_ts when ordering actions saved for the same `at` (both follow the order actions were saved).
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
export const KINDS = ['edit', 'attest', 'sign', 'ack'];
export type Action = Record<string, any> & { csn: string; kind: string; at: number; recorded_ts: number; seq: number };

export class MemoryStore {
  readonly kind = 'memory';
  private rows = new Map<string, Action[]>();
  private n = 0;

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

  reset() {
    this.rows.clear();
  }
}
