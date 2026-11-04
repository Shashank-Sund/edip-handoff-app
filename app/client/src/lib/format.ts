/**
 * Times are shown in the facility's local time, whatever the viewer's time zone: America/New_York unless the
 * server's /api/config names another (EDIP_TIMEZONE); setTimeZone() switches every formatter.
 */
export const DEFAULT_TZ = 'America/New_York';
/** The default facility time zone. */
export const FACILITY_TZ = DEFAULT_TZ;
let tz = DEFAULT_TZ;

const fmt = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat('en-US', { timeZone: tz, ...o });
let timeFmt: Intl.DateTimeFormat, dayFmt: Intl.DateTimeFormat, fullFmt: Intl.DateTimeFormat, partsFmt: Intl.DateTimeFormat;
let weekdayFmt: Intl.DateTimeFormat;
function build() {
  timeFmt = fmt({ hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  dayFmt = fmt({ weekday: 'short', month: 'short', day: 'numeric' });
  fullFmt = fmt({ weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  weekdayFmt = fmt({ weekday: 'short', day: 'numeric' });
  partsFmt = fmt({ year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
}
build();

/** Use the facilities' time zone from the server; an unknown name keeps the current one. */
export function setTimeZone(name: string | null | undefined) {
  if (!name || name === tz) return;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: name });
  } catch {
    return;
  }
  tz = name;
  build();
}
export const timeZone = () => tz;

/** "Eastern Time", the zone's everyday name. */
export function timeZoneName(): string {
  try {
    const p = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'longGeneric' }).formatToParts(new Date());
    return p.find((x) => x.type === 'timeZoneName')?.value ?? tz;
  } catch {
    return tz;
  }
}

export const clock = (iso: string | null | undefined) => (iso ? timeFmt.format(new Date(iso)) : 'n/a');
export const day = (iso: string | null | undefined) => (iso ? dayFmt.format(new Date(iso)) : 'n/a');
export const dateTime = (iso: string | null | undefined) => (iso ? fullFmt.format(new Date(iso)).replace(',', '') : 'n/a');

// ------------------------------------------------------------------------------------- facility days
interface Wall {
  y: number;
  m: number;
  d: number;
  h: number;
  mi: number;
}

function wallOf(t: number): Wall {
  const p: Record<string, number> = {};
  for (const x of partsFmt.formatToParts(new Date(t))) if (x.type !== 'literal') p[x.type] = Number(x.value);
  return { y: p.year, m: p.month, d: p.day, h: p.hour % 24, mi: p.minute };
}

/** Minutes the facility clock is ahead of UTC at t. */
function offsetAt(t: number): number {
  const w = wallOf(t);
  return Math.round((Date.UTC(w.y, w.m - 1, w.d, w.h, w.mi) - Math.floor(t / 60000) * 60000) / 60000);
}

/** The instant the facility clock shows this wall time (the earlier one when the clock repeats an hour). */
export function fromFacilityWall(y: number, m: number, d: number, h = 0, mi = 0): number {
  const guess = Date.UTC(y, m - 1, d, h, mi);
  const first = guess - offsetAt(guess - 12 * 3600000) * 60000;
  if (offsetAt(first) * 60000 === guess - first) return first;
  return guess - offsetAt(guess + 12 * 3600000) * 60000;
}

export interface FacilityDay {
  /** Facility midnight that starts the day. */
  start: number;
  /** The next facility midnight. */
  end: number;
  /** "Mon 26": weekday and date, so a week and a day never share a label. */
  label: string;
}

/** The facility's calendar days that the window [start, end) touches, midnight to midnight in its time zone. */
export function facilityDays(startIso: string, endIso: string): FacilityDay[] {
  const end = new Date(endIso).getTime();
  const out: FacilityDay[] = [];
  let w = wallOf(new Date(startIso).getTime());
  for (let i = 0; i < 62; i++) {
    const start = fromFacilityWall(w.y, w.m, w.d);
    if (start >= end) break;
    const next = new Date(Date.UTC(w.y, w.m - 1, w.d + 1));
    const nextStart = fromFacilityWall(next.getUTCFullYear(), next.getUTCMonth() + 1, next.getUTCDate());
    const parts = weekdayFmt.formatToParts(new Date(start + 12 * 3600000));
    const part = (k: string) => parts.find((x) => x.type === k)?.value ?? '';
    out.push({ start, end: nextStart, label: `${part('weekday')} ${part('day')}` });
    w = { y: next.getUTCFullYear(), m: next.getUTCMonth() + 1, d: next.getUTCDate(), h: 0, mi: 0 };
  }
  return out;
}

/** The same wall clock time as `iso` on another facility day, so 15:00 stays 15:00 across a clock change. */
export function onDay(iso: string, d: FacilityDay): string {
  const now = wallOf(new Date(iso).getTime());
  const target = wallOf(d.start + 12 * 3600000);
  return new Date(fromFacilityWall(target.y, target.m, target.d, now.h, now.mi)).toISOString();
}

/** "1 h 43 min", "25 min", "2 d 3 h". */
export function duration(minutes: number | null | undefined): string {
  if (minutes == null || Number.isNaN(minutes)) return 'n/a';
  const m = Math.max(0, Math.round(minutes));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  if (h < 48) return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
  return `${Math.floor(h / 24)} d ${h % 24} h`;
}

/** Minutes between two ISO times. */
export const minutesBetween = (a: string, b: string) => (new Date(b).getTime() - new Date(a).getTime()) / 60000;

/** "12 min ago" relative to the app clock. */
export function ago(iso: string | null | undefined, at: string): string {
  if (!iso) return 'n/a';
  const m = minutesBetween(iso, at);
  if (m < 1) return 'just now';
  return `${duration(m)} ago`;
}

/** Hours under two days, else days: "Over 9 hours", "Over 3 days". */
export function span(hours: number): string {
  if (hours < 48) return `${Math.max(0, Math.round(hours))} hour${Math.round(hours) === 1 ? '' : 's'}`;
  return `${Math.round(hours / 24)} days`;
}

const VITAL: Record<string, [string, string]> = { spo2: ['SpO2', '%'], hr: ['HR', ''], sbp: ['SBP', ''], rr: ['RR', ''], temp: ['Temp', ' °C'], gcs: ['GCS', ''] };

/** A severity rule reason in clinical words: "spo2 89" becomes "SpO2 89%", "unaddressed critical ..." reads plainly. */
export function clinicalReason(reason: string): string {
  const s = reason
    .replace(/^(spo2|hr|sbp|rr|temp|gcs) (-?\d+(?:\.\d+)?)$/i, (_, k: string, v: string) => `${VITAL[k.toLowerCase()][0]} ${v}${VITAL[k.toLowerCase()][1]}`)
    .replace(/^unaddressed critical /, 'critical result not yet addressed: ');
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

export const pct = (v: number | null | undefined) => (v == null ? 'n/a' : `${Math.round(v * 100)}%`);

const TRIGGERS: Record<string, string> = {
  pending_admit: 'Admit decision',
  bed_assigned: 'Bed assigned',
  bed_unassigned: 'Bed released',
  class_change: 'Patient class changed',
  cancel_transfer: 'Transfer canceled',
  transfer: 'Transferred to unit',
  critical_result: 'Critical result',
  note_signed: 'Note signed',
};
export const triggerLabel = (t: string) => TRIGGERS[t] ?? t.replace(/_/g, ' ');

export const LEVEL_LABEL: Record<string, string> = {
  ICU: 'ICU',
  STEPDOWN: 'Step down',
  TELE: 'Telemetry',
  MEDSURG: 'Med surg',
  OBS: 'Observation',
};

export function bed(unit?: string | null, room?: string | null, b?: string | null) {
  if (!unit) return 'Unassigned';
  return [unit, room && b ? `${room}-${b}` : room].filter(Boolean).join(' ');
}

/** Initials for an avatar: "Reyes, Jordan" or "Jordan Reyes, RN" -> "JR". */
export function initials(name: string) {
  const clean = name.replace(/,\s*(RN|MD|DO|NP|PA)$/i, '');
  const parts = clean.includes(',') ? clean.split(',').reverse() : clean.split(' ');
  return parts
    .map((p) => p.trim()[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase();
}
