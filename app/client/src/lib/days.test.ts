import { clinicalReason, clock, facilityDays, onDay, setTimeZone, span, timeZone, timeZoneName } from './format';

// The reference week: Mon Oct 26 15:00 EDT to Mon Nov 2 15:00 EST, across the November 1 clock change.
const START = '2026-10-26T19:00:00Z';
const END = '2026-11-02T20:00:00Z';

describe('facility days', () => {
  it('runs midnight to midnight in facility time, with one label per day', () => {
    const days = facilityDays(START, END);
    expect(days.map((d) => d.label)).toEqual(['Mon 26', 'Tue 27', 'Wed 28', 'Thu 29', 'Fri 30', 'Sat 31', 'Sun 1', 'Mon 2']);
    expect(new Set(days.map((d) => d.label)).size).toBe(days.length);
    expect(clock(new Date(days[1].start).toISOString())).toBe('00:00');
    expect(clock(new Date(days[7].start).toISOString())).toBe('00:00');
    // November 1 has 25 hours: the clock falls back.
    expect((days[6].end - days[6].start) / 3600000).toBe(25);
    expect((days[5].end - days[5].start) / 3600000).toBe(24);
  });
  it('keeps the wall clock hour when moving to a day across the clock change', () => {
    const days = facilityDays(START, END);
    const sat15 = '2026-10-31T19:00:00Z'; // 15:00 EDT
    const mon = onDay(sat15, days[7]);
    expect(clock(mon)).toBe('15:00');
    expect(mon).toBe('2026-11-02T20:00:00.000Z'); // 15:00 EST
    expect(clock(onDay('2026-11-02T13:30:00Z', days[0]))).toBe('08:30'); // 08:30 EST stays 08:30 EDT
  });
});

describe('time zone', () => {
  it('follows the server setting and ignores unknown names', () => {
    expect(timeZone()).toBe('America/New_York');
    setTimeZone('America/Chicago');
    expect(clock('2026-10-31T19:00:00Z')).toBe('14:00');
    expect(timeZoneName()).toMatch(/Central/);
    setTimeZone('Not/AZone');
    expect(timeZone()).toBe('America/Chicago');
    setTimeZone('America/New_York');
    expect(clock('2026-10-31T19:00:00Z')).toBe('15:00');
  });
});

describe('plain words', () => {
  it('writes rule reasons as clinicians do', () => {
    expect(clinicalReason('spo2 89')).toBe('SpO2 89%');
    expect(clinicalReason('hr 135')).toBe('HR 135');
    expect(clinicalReason('sbp 85')).toBe('SBP 85');
    expect(clinicalReason('rr 32')).toBe('RR 32');
    expect(clinicalReason('temp 38.5')).toBe('Temp 38.5 °C');
    expect(clinicalReason('gcs 12')).toBe('GCS 12');
    expect(clinicalReason('unaddressed critical Lactate 4.2')).toBe('Critical result not yet addressed: Lactate 4.2');
    expect(clinicalReason('preliminary result pending')).toBe('Preliminary result pending');
    expect(clinicalReason('ESI 2')).toBe('ESI 2');
  });
  it('says hours early in the week', () => {
    expect(span(9)).toBe('9 hours');
    expect(span(1)).toBe('1 hour');
    expect(span(30)).toBe('30 hours');
    expect(span(72)).toBe('3 days');
  });
});
