import { FACILITY_TZ, ago, bed, clock, dateTime, day, duration, initials, pct, triggerLabel } from './format';

describe('duration', () => {
  it('shows minutes under an hour', () => {
    expect(duration(0)).toBe('0 min');
    expect(duration(25)).toBe('25 min');
    expect(duration(59.4)).toBe('59 min');
  });
  it('shows hours and minutes, dropping zero minutes', () => {
    expect(duration(60)).toBe('1 h');
    expect(duration(103)).toBe('1 h 43 min');
    expect(duration(47 * 60 + 59)).toBe('47 h 59 min');
  });
  it('switches to days at 48 hours', () => {
    expect(duration(48 * 60)).toBe('2 d 0 h');
    expect(duration(51 * 60 + 10)).toBe('2 d 3 h');
  });
  it('clamps negatives and handles missing values', () => {
    expect(duration(-5)).toBe('0 min');
    expect(duration(null)).toBe('n/a');
    expect(duration(undefined)).toBe('n/a');
    expect(duration(Number.NaN)).toBe('n/a');
  });
});

describe('facility time', () => {
  it('uses America/New_York', () => {
    expect(FACILITY_TZ).toBe('America/New_York');
  });
  it('formats clock times in facility time on both sides of the November 2026 DST change', () => {
    // EDT (UTC-4) before 2026-11-01 06:00Z, EST (UTC-5) after.
    expect(clock('2026-10-31T12:00:00Z')).toBe('08:00');
    expect(clock('2026-11-01T05:30:00Z')).toBe('01:30');
    expect(clock('2026-11-01T06:30:00Z')).toBe('01:30');
    expect(clock('2026-11-01T12:00:00Z')).toBe('07:00');
    expect(clock('2026-11-01T04:05:00Z')).toBe('00:05');
  });
  it('formats date and time without the first comma', () => {
    expect(dateTime('2026-10-31T12:00:00Z')).toBe('Sat Oct 31, 08:00');
    expect(dateTime('2026-11-01T12:00:00Z')).toBe('Sun Nov 1, 07:00');
    // 03:30Z on Nov 1 is still Oct 31 in New York.
    expect(dateTime('2026-11-01T03:30:00Z')).toBe('Sat Oct 31, 23:30');
    expect(day('2026-11-01T03:30:00Z')).toBe('Sat, Oct 31');
  });
  it('prints n/a for missing times', () => {
    expect(clock(null)).toBe('n/a');
    expect(dateTime(undefined)).toBe('n/a');
    expect(day('')).toBe('n/a');
  });
});

describe('ago', () => {
  const at = '2026-03-10T15:00:00Z';
  it('is relative to the app clock', () => {
    expect(ago('2026-03-10T14:48:00Z', at)).toBe('12 min ago');
    expect(ago('2026-03-10T13:17:00Z', at)).toBe('1 h 43 min ago');
  });
  it('says just now under a minute and in the future', () => {
    expect(ago('2026-03-10T14:59:30Z', at)).toBe('just now');
    expect(ago('2026-03-10T15:05:00Z', at)).toBe('just now');
  });
  it('handles a missing time', () => {
    expect(ago(null, at)).toBe('n/a');
  });
});

describe('triggerLabel', () => {
  it('maps known triggers', () => {
    expect(triggerLabel('pending_admit')).toBe('Admit decision');
    expect(triggerLabel('critical_result')).toBe('Critical result');
  });
  it('falls back to words for unknown triggers', () => {
    expect(triggerLabel('new_order_placed')).toBe('new order placed');
  });
});

describe('bed', () => {
  it('joins unit, room and bed', () => {
    expect(bed('MICU', '4', 'B')).toBe('MICU 4-B');
    expect(bed('MICU', '4', null)).toBe('MICU 4');
    expect(bed('MICU', null, 'B')).toBe('MICU');
  });
  it('says Unassigned without a unit', () => {
    expect(bed(null, '4', 'B')).toBe('Unassigned');
    expect(bed()).toBe('Unassigned');
  });
});

describe('initials', () => {
  it('handles "Last, First" and "First Last, RN"', () => {
    expect(initials('Reyes, Jordan')).toBe('JR');
    expect(initials('Jordan Reyes, RN')).toBe('JR');
    expect(initials('sam lee, md')).toBe('SL');
  });
  it('keeps at most two letters', () => {
    expect(initials('Mary Ann Smith')).toBe('MA');
    expect(initials('Cher')).toBe('C');
  });
});

describe('pct', () => {
  it('rounds a fraction to a whole percent', () => {
    expect(pct(0)).toBe('0%');
    expect(pct(0.875)).toBe('88%');
    expect(pct(1)).toBe('100%');
    expect(pct(null)).toBe('n/a');
  });
});
