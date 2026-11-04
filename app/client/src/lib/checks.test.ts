import { callbackOk, canChange, readbackOk, readbackProblem } from './checks';
import { caseTemplate } from '@/test/fixtures';

describe('read back', () => {
  it('needs at least a sentence: three words, fifteen letters, not one repeated character', () => {
    expect(readbackOk('Watcher, recheck vitals on arrival')).toBe(true);
    expect(readbackOk('Watcher patient')).toBe(false);
    expect(readbackOk('aaaa aaaa aaaa aaaa')).toBe(false);
    expect(readbackOk('ok 12 34 56 78 90')).toBe(false);
    expect(readbackOk('   ')).toBe(false);
  });
  it('says why it is not ready', () => {
    expect(readbackProblem('')).toBe('Write your read back to accept.');
    expect(readbackProblem('Watcher')).toMatch(/at least a short sentence/);
    expect(readbackProblem('Watcher, recheck vitals '.repeat(100))).toMatch(/2,000/);
    expect(readbackProblem('Watcher, recheck vitals on arrival')).toBeNull();
  });
});

describe('callback', () => {
  it('needs at least 3 digits or an extension', () => {
    for (const ok of ['x4471', '(617) 555-0142', '555', 'ext. 12', 'X12']) expect(callbackOk(ok)).toBe(true);
    for (const bad of ['x1', '12', 'call me', '5'.repeat(41)]) expect(callbackOk(bad)).toBe(false);
  });
});

describe('canChange', () => {
  const signed = (version: number) => caseTemplate({ workflow: { state: 'signed', signed: { by: 'RN', role: 'ed_nurse', at: '2026-03-10T14:50:00Z', version } } });
  it('lets the ED change while boarding, for an addendum, and after transfer while unsigned or signed on an older version', () => {
    expect(canChange(caseTemplate(), 'boarding')).toBe(true);
    expect(canChange(caseTemplate(), 'arrived')).toBe(true);
    expect(canChange(signed(1), 'arrived')).toBe(true);
    expect(canChange(signed(2), 'arrived')).toBe(false);
    expect(canChange(caseTemplate(), 'canceled')).toBe(false);
    expect(canChange({ ...signed(2), row: { ...signed(2).row, status: 'addendum' } }, 'arrived')).toBe(true);
  });
});
