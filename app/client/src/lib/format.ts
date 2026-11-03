/** Times are shown in the facility's local time (America/New_York), whatever the viewer's time zone. */
export const FACILITY_TZ = 'America/New_York';

const fmt = (o: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat('en-US', { timeZone: FACILITY_TZ, ...o });
const timeFmt = fmt({ hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const dayFmt = fmt({ weekday: 'short', month: 'short', day: 'numeric' });
const fullFmt = fmt({ weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });

export const clock = (iso: string | null | undefined) => (iso ? timeFmt.format(new Date(iso)) : 'n/a');
export const day = (iso: string | null | undefined) => (iso ? dayFmt.format(new Date(iso)) : 'n/a');
export const dateTime = (iso: string | null | undefined) => (iso ? fullFmt.format(new Date(iso)).replace(',', '') : 'n/a');

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
