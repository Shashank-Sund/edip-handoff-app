import { fromWall, iso, parseTs, pyEq, pyRound, strip, truthy, wall } from './py';

describe('Python semantics for the demo port', () => {
  it('rounds half to even on the exact binary value', () => {
    expect([0.5, 1.5, 2.5, 3.5, 2.675].map((x) => pyRound(x))).toEqual([0, 2, 2, 4, 3]);
    expect(pyRound(1 / 16, 3)).toBe(0.062);
    expect(pyRound(3 / 16, 3)).toBe(0.188);
    expect(pyRound(2.675, 2)).toBe(2.67); // 2.675 is just under the tie in binary
    expect(pyRound(2 / 3, 3)).toBe(0.667);
  });

  it('prints facility local time with the offset in force, across the fall back', () => {
    expect(iso(parseTs('2026-11-01T05:30:00Z'))).toBe('2026-11-01T01:30:00-04:00');
    expect(iso(parseTs('2026-11-01T06:30:00Z'))).toBe('2026-11-01T01:30:00-05:00');
    expect(iso(parseTs('2026-10-26T19:00:00.999Z'))).toBe('2026-10-26T15:00:00-04:00');
    // A week of wall clock days from 15:00 EDT ends at 15:00 EST.
    const start = parseTs('2026-10-26T15:00:00-04:00');
    expect(iso(fromWall(wall(start) + 7 * 86400000))).toBe('2026-11-02T15:00:00-05:00');
  });

  it('parses ISO times as datetime.fromisoformat does (naive is UTC) and rejects others', () => {
    expect(parseTs('2026-10-30T12:00:00')).toBe(Date.UTC(2026, 9, 30, 12));
    expect(parseTs('2026-10-30T12:00:00.250Z')).toBe(Date.UTC(2026, 9, 30, 12, 0, 0, 250));
    expect(parseTs('2026-10-30T08:00:00-04:00')).toBe(Date.UTC(2026, 9, 30, 12));
    expect(parseTs('2026-10-30')).toBe(Date.UTC(2026, 9, 30));
    for (const bad of ['nope', '2026-13-01', '2026-02-30T00:00:00', '']) expect(() => parseTs(bad)).toThrow();
  });

  it('follows Python truthiness, equality and strip', () => {
    expect([null, 0, '', [], {}, false].map(truthy)).toEqual([false, false, false, false, false, false]);
    expect(pyEq({ a: [1, true], b: null }, { b: null, a: [1, 1] })).toBe(true);
    expect(pyEq({ a: 1 }, { a: 1, b: null })).toBe(false);
    expect(strip('  x \x1c')).toBe('x');
    expect(strip('﻿x')).toBe('﻿x');
  });
});
