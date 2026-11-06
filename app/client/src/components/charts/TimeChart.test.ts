import { chartShift, zoneOffset } from './TimeChart';

const s = (iso: string) => Date.parse(iso) / 1000;
const DAY = 86400;

// The reference week: Mon Oct 26 15:00 EDT to Mon Nov 2 15:00 EST, across the November 1 clock change.
const WEEK = [s('2026-10-26T19:00:00Z'), s('2026-10-30T12:00:00Z'), s('2026-11-02T20:00:00Z')];

describe('chart day ticks in facility time', () => {
  it('reads the UTC offset of a zone at a moment', () => {
    expect(zoneOffset(s('2026-10-28T12:00:00Z'), 'America/New_York')).toBe(-4 * 3600);
    expect(zoneOffset(s('2026-11-02T12:00:00Z'), 'America/New_York')).toBe(-5 * 3600);
    expect(zoneOffset(s('2026-10-28T12:00:00Z'), 'UTC')).toBe(0);
  });

  it('puts facility midnight on a UTC midnight after the clock change, and an hour after it before', () => {
    const shift = chartShift(WEEK, 'America/New_York');
    expect(shift).toBe(-5 * 3600);
    // Mon Nov 2 00:00 EST is a day tick.
    expect((s('2026-11-02T05:00:00Z') + shift) % DAY).toBe(0);
    // Wed Oct 28 00:00 EDT falls an hour before the tick, so the tick (01:00 EDT) is still on Wednesday,
    // not at 20:00 the day before as with real UTC times.
    expect((s('2026-10-28T04:00:00Z') + shift) % DAY).toBe(DAY - 3600);
  });

  it('keeps every hour of the 25 hour day distinct and in order', () => {
    const shift = chartShift(WEEK, 'America/New_York');
    const hours = Array.from({ length: 26 }, (_, i) => s('2026-11-01T04:00:00Z') + i * 3600 + shift);
    expect(new Set(hours).size).toBe(hours.length);
    expect([...hours].sort((a, b) => a - b)).toEqual(hours);
  });

  it('uses the smaller offset east of UTC too, and no shift for an empty chart', () => {
    // Sydney moves from +10 to +11 on October 4, 2026.
    expect(chartShift([s('2026-10-01T00:00:00Z'), s('2026-10-07T00:00:00Z')], 'Australia/Sydney')).toBe(10 * 3600);
    expect(chartShift([], 'America/New_York')).toBe(0);
  });
});
