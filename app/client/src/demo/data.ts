/**
 * Port of app/server/data.py: the read model (encounters and every handoff draft version) from the fixtures.
 * A view takes a time `at` (ms, UTC) and shows the world as it was then.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { bisectRight, fromWall, HOUR, DAY, ts, wall } from './py';

const TS_FIELDS = ['arrival_ts', 'admit_decision_ts', 'ed_departure_ts', 'last_event_ts'];

export type Enc = Record<string, any> & {
  csn: string;
  arrival_ts: number | null;
  admit_decision_ts: number | null;
  ed_departure_ts: number | null;
  last_event_ts: number | null;
  left_ts: number | null;
};
export type Row = Record<string, any> & { csn: string; template: string; version: number; at: number; draft: any };

/**
 * The status each version of one encounter had before the job marked it retracted in place (written_status), as
 * jobs/handoff_job.py assigns it from the generation points: draft before the transfer, final at the first
 * transfer point, addendum after it.
 */
function writtenStatus(rows: Row[]): void {
  if (!rows.some((r) => r.status === 'retracted')) return;
  const moves = rows.filter((r) => r.trigger === 'transfer').map((r) => r.at);
  const transferred = moves.length ? Math.min(...moves) : null;
  for (const r of rows) {
    r.written_status = transferred === null || r.at < transferred ? 'draft' : r.at === transferred ? 'final' : 'addendum';
  }
}

/**
 * A version's status at `at`. A canceled admission's versions are retracted from retracted_ts on (the cancel's
 * time) and have their written status before it; without retracted_ts (written by an older handoff job) they are
 * retracted at every time.
 */
export function statusAt(r: Row, at: number): string {
  if (r.status === 'retracted' && r.retracted_ts !== null && at < r.retracted_ts) return r.written_status;
  return r.status;
}

export interface Fixtures {
  encounters: any[];
  drafts: any[];
  meta: any;
}

export class Dataset {
  meta: any;
  source: string;
  error: string | null = null;
  enc = new Map<string, Enc>();
  versions = new Map<string, Row[]>();
  private keys = new Map<string, number[]>();
  /** When each canceled admission was canceled: null when a version has no retracted_ts (an older handoff job). */
  canceled = new Map<string, number | null>();
  start: number;
  end: number;

  constructor({ encounters, drafts, meta }: Fixtures, source = 'fixtures') {
    this.meta = meta;
    this.source = source;
    for (const raw of encounters) {
      const e: any = { ...raw };
      for (const k of TS_FIELDS) e[k] = ts(e[k]);
      // Discharged patients carry no ED departure; their last event is the discharge.
      e.left_ts = e.ed_departure_ts ?? (e.last_event_kind === 'discharge' ? e.last_event_ts : null);
      this.enc.set(e.csn, e);
    }
    for (const raw of drafts) {
      const r: any = { ...raw };
      r.at = ts(r.as_of);
      r.retracted_ts = ts(r.retracted_ts);
      if (typeof r.draft === 'string') r.draft = JSON.parse(r.draft);
      if (typeof r.blocking === 'string') r.blocking = JSON.parse(r.blocking);
      let list = this.versions.get(r.csn);
      if (!list) this.versions.set(r.csn, (list = []));
      list.push(r);
    }
    for (const [csn, rows] of this.versions) {
      rows.sort((a, b) => a.at - b.at || (a.template < b.template ? -1 : a.template > b.template ? 1 : 0) || a.version - b.version);
      this.keys.set(csn, rows.map((r) => r.at));
      writtenStatus(rows);
      const out = rows.filter((r) => r.status === 'retracted').map((r) => r.retracted_ts as number | null);
      if (out.length) this.canceled.set(csn, out.includes(null) ? null : Math.min(...(out as number[])));
    }
    const w = meta?.window ?? {};
    if (w.start) {
      this.start = ts(w.start) as number;
      // wall clock days, so the window ends at the same local hour
      this.end = fromWall(wall(this.start) + (w.days ?? 7) * DAY);
    } else {
      const times = [...this.versions.values()].flatMap((rows) => rows.map((r) => r.at));
      if (!times.length) times.push(Date.now());
      this.start = Math.min(...times);
      this.end = Math.max(...times);
    }
  }

  /**
   * Whether encounter e's admission is canceled at `at`: from the cancel on when its drafts carry the time, else (no
   * drafts, or retracted by an older handoff job) as its status is now.
   */
  canceledAt(e: Enc, at: number): boolean {
    const when = this.canceled.get(e.csn);
    if (when !== undefined && when !== null) return at >= when;
    return this.canceled.has(e.csn) || e.status === 'canceled';
  }

  rowsAt(csn: string, at: number): Row[] {
    const rows = this.versions.get(csn) ?? [];
    return rows.slice(0, bisectRight(this.keys.get(csn) ?? [], at));
  }

  /** template to its latest row at `at`, in first seen order (Python dict order). */
  latestAt(csn: string, at: number): Map<string, Row> {
    const out = new Map<string, Row>();
    for (const r of this.rowsAt(csn, at)) out.set(r.template, r);
    return out;
  }

  private hasRowsAt(csn: string, at: number): boolean {
    const k = this.keys.get(csn);
    return !!k && k.length > 0 && k[0] <= at;
  }

  /** The hour of the window with the most patients boarding: a good default demo time. */
  busiest(): number {
    let best = this.start, bestN = -1;
    for (let t = this.start + 6 * HOUR; t < this.end; t += HOUR) {
      let n = 0;
      for (const e of this.enc.values()) {
        if (e.admit_decision_ts !== null && e.admit_decision_ts <= t && (e.ed_departure_ts === null || e.ed_departure_ts > t) && this.hasRowsAt(e.csn, t)) n++;
      }
      if (n > bestN) {
        best = t;
        bestN = n;
      }
    }
    return best;
  }

  draftCount(): number {
    let n = 0;
    for (const v of this.versions.values()) n += v.length;
    return n;
  }
}
