/**
 * Python semantics the server relies on, so the in-browser port computes exactly what app/server computes:
 * truthiness, deep equality, round() (half to even, on the exact binary value), str.strip(), statistics.median,
 * datetime parsing (datetime.fromisoformat, naive means UTC) and the facility local ISO format of data.iso().
 *
 * Times are numbers: milliseconds since the epoch, UTC. The fixtures carry at most millisecond precision.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */

export const TZ = 'America/New_York';
export const HOUR = 3_600_000;
export const DAY = 24 * HOUR;

/** Python truthiness: None, False, 0, "", [] and {} are false. */
export function truthy(v: unknown): boolean {
  if (v === null || v === undefined || v === false || v === 0 || v === '') return false;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === 'object') return Object.keys(v as object).length > 0;
  return true;
}

/** `a or b` */
export function or<A, B>(a: A, b: B): A | B {
  return truthy(a) ? a : b;
}

export function isDict(v: unknown): v is Record<string, any> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/** dict.get(k): a missing key is None. */
export function get(d: any, k: string): any {
  if (!isDict(d)) return null;
  const v = d[k];
  return v === undefined ? null : v;
}

/** Python ==, for JSON values (True == 1 and dicts compare without order). */
export function pyEq(a: any, b: any): boolean {
  if (a === undefined) a = null;
  if (b === undefined) b = null;
  if (typeof a === 'boolean' && typeof b === 'number') return Number(a) === b;
  if (typeof a === 'number' && typeof b === 'boolean') return a === Number(b);
  if (a === null || b === null || typeof a !== 'object' || typeof b !== 'object') return a === b;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) return a.length === b.length && a.every((x: any, i: number) => pyEq(x, b[i]));
  const ka = Object.keys(a);
  if (ka.length !== Object.keys(b).length) return false;
  return ka.every((k) => Object.prototype.hasOwnProperty.call(b, k) && pyEq(a[k], b[k]));
}

/** str(v) as an f-string prints it. */
export function pyStr(v: unknown): string {
  if (v === null || v === undefined) return 'None';
  if (v === true) return 'True';
  if (v === false) return 'False';
  return String(v);
}

// Characters str.isspace() accepts (JavaScript's \s differs: it has U+FEFF and lacks U+001C to U+001F, U+0085).
const PY_WS = '\\t\\n\\x0b\\x0c\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000';
const STRIP = new RegExp(`^[${PY_WS}]+|[${PY_WS}]+$`, 'g');
export function strip(s: string): string {
  return s.replace(STRIP, '');
}

/** len(str): code points, not UTF-16 units. */
export function pyLen(s: string): number {
  return [...s].length;
}

/**
 * round(x, nd): the exact binary value rounded half to even, as CPython does. toFixed(100) prints the exact
 * decimal expansion for every double the app rounds (at least 2**-47 in magnitude), so ties are detected exactly.
 */
export function pyRound(x: number, nd = 0): number {
  if (!Number.isFinite(x)) return x;
  const neg = x < 0;
  const s = Math.abs(x).toFixed(100);
  const dot = s.indexOf('.');
  const intPart = s.slice(0, dot);
  const frac = s.slice(dot + 1);
  const keep = intPart + frac.slice(0, nd);
  const next = frac.charCodeAt(nd) - 48;
  const rest = frac.slice(nd + 1);
  let up = false;
  if (next > 5) up = true;
  else if (next === 5) {
    if (/[1-9]/.test(rest)) up = true;
    else up = (keep.charCodeAt(keep.length - 1) - 48) % 2 === 1; // exact tie: to even
  }
  let digits = BigInt(keep);
  if (up) digits += 1n;
  const str = digits.toString().padStart(nd + 1, '0');
  const out = Number(nd ? `${str.slice(0, str.length - nd)}.${str.slice(str.length - nd)}` : str);
  return neg ? -out : out;
}

/** statistics.median */
export function median(values: number[]): number {
  const v = [...values].sort((a, b) => a - b);
  const n = v.length;
  if (!n) throw new Error('no median for empty data');
  return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}

/** Python str ordering (code points); equal to UTF-16 order for the app's ASCII keys. */
export function cmpStr(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

// ------------------------------------------------------------------------------------------------- time
const ISO = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2})(?::(\d{2})(?::(\d{2})(?:[.,](\d{1,6}))?)?)?)?(?:([+-])(\d{2}):?(\d{2})(?::?(\d{2})(?:\.(\d{1,6}))?)?)?$/;

/**
 * data.ts(): an aware time from an ISO string; naive values are UTC. Accepts the forms datetime.fromisoformat
 * reads that the app and its clients send (dates, times to the microsecond, Z or an offset). Throws on others.
 */
export function parseTs(v: string): number {
  const m = ISO.exec(String(v).replace(/Z/g, '+00:00'));
  if (!m) throw new RangeError(`Invalid isoformat string: '${v}'`);
  const [, y, mo, d, h = '0', mi = '0', s = '0', f = '', sign, oh, om, os = '0'] = m;
  const ms = Number((f + '000').slice(0, 3));
  const Y = Number(y), M = Number(mo), D = Number(d), H = Number(h), MI = Number(mi), S = Number(s);
  if (M < 1 || M > 12 || D < 1 || D > daysIn(Y, M) || H > 23 || MI > 59 || S > 59) {
    throw new RangeError(`Invalid isoformat string: '${v}'`);
  }
  let t = Date.UTC(Y, M - 1, D, H, MI, S, ms);
  if (Y < 100) t = new Date(t).setUTCFullYear(Y);
  if (sign) {
    const off = (Number(oh) * 3600 + Number(om) * 60 + Number(os)) * 1000;
    if (Number(oh) > 23 || Number(om) > 59) throw new RangeError(`Invalid isoformat string: '${v}'`);
    t -= sign === '+' ? off : -off;
  }
  return t;
}

function daysIn(y: number, m: number): number {
  return new Date(Date.UTC(y, m, 0)).getUTCDate();
}

/** data.ts() for fixture values: null for None or "". */
export function ts(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  return parseTs(String(v));
}

let fmt: Intl.DateTimeFormat | null = null;
const offsets = new Map<number, number>();

/** Minutes east of UTC in the facility time zone at t. Zone transitions fall on whole UTC hours. */
export function tzOffset(t: number): number {
  const hour = Math.floor(t / HOUR);
  const hit = offsets.get(hour);
  if (hit !== undefined) return hit;
  fmt ??= new Intl.DateTimeFormat('en-US', {
    timeZone: TZ, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  const at = hour * HOUR;
  const p: Record<string, number> = {};
  for (const x of fmt.formatToParts(new Date(at))) if (x.type !== 'literal') p[x.type] = Number(x.value);
  const wall = Date.UTC(p.year, p.month - 1, p.day, p.hour % 24, p.minute, p.second);
  const off = Math.round((wall - at) / 60000);
  offsets.set(hour, off);
  return off;
}

const pad = (n: number, w = 2) => String(n).padStart(w, '0');

/** data.iso(): facility local time with offset, to the second (truncated), as clinicians read it. */
export function iso(t: number | null): string | null {
  if (t === null) return null;
  const sec = Math.floor(t / 1000) * 1000;
  const off = tzOffset(sec);
  const w = new Date(sec + off * 60000);
  const a = Math.abs(off);
  return `${pad(w.getUTCFullYear(), 4)}-${pad(w.getUTCMonth() + 1)}-${pad(w.getUTCDate())}T${pad(w.getUTCHours())}:` +
    `${pad(w.getUTCMinutes())}:${pad(w.getUTCSeconds())}${off < 0 ? '-' : '+'}${pad(Math.floor(a / 60))}:${pad(a % 60)}`;
}

/** Wall clock fields of t in the facility zone, as a UTC epoch number (for wall clock arithmetic). */
export function wall(t: number): number {
  return t + tzOffset(t) * 60000;
}

/**
 * datetime.replace(tzinfo=ZoneInfo(TZ)) with fold=0 on a wall clock time: the offset in force before a
 * change wins, both for repeated hours and for skipped ones.
 */
export function fromWall(w: number): number {
  const before = tzOffset(w - DAY);
  if (tzOffset(w - before * 60000) === before) return w - before * 60000;
  const after = tzOffset(w + DAY);
  if (tzOffset(w - after * 60000) === after) return w - after * 60000;
  return w - before * 60000;
}

/** bisect.bisect_right */
export function bisectRight(a: number[], x: number): number {
  let lo = 0, hi = a.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (x < a[mid]) hi = mid;
    else lo = mid + 1;
  }
  return lo;
}
