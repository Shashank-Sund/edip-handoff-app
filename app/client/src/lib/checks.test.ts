import { callbackOk, canChange, readbackOk, readbackProblem, signOffStatus } from './checks';
import type { CaseTemplate, Persona } from './types';
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

describe('signOffStatus', () => {
  const tpl = (state: string, blocking: string[] = [], over: object = {}) =>
    ({ draft: { completeness: { score: 0.7, blocking, model_score: 0.7 } }, row: { version: 2, status: 'draft' }, workflow: { state, ...over } }) as unknown as CaseTemplate;
  const nurse = { can_sign: ['sbar_nurse'], can_ack: [] } as unknown as Persona;
  const unit = { can_sign: [], can_ack: ['sbar_nurse'] } as unknown as Persona;
  const viewer = { can_sign: [], can_ack: [] } as unknown as Persona;

  it('says what is left for the sender, and when it is ready', () => {
    expect(signOffStatus(tpl('draft', ['N07', 'N08']), nurse, 'sbar_nurse', 'boarding')).toBe('Sign off: 2 items to resolve');
    expect(signOffStatus(tpl('draft', ['N07']), nurse, 'sbar_nurse', 'boarding')).toBe('Sign off: 1 item to resolve');
    expect(signOffStatus(tpl('draft'), nurse, 'sbar_nurse', 'boarding')).toBe('Ready to sign');
    expect(signOffStatus(tpl('changed'), nurse, 'sbar_nurse', 'boarding')).toBe('Ready to sign again');
    expect(signOffStatus(tpl('transferred_unsigned'), nurse, 'sbar_nurse', 'arrived')).toBe('Ready to sign');
    expect(signOffStatus(tpl('signed'), nurse, 'sbar_nurse', 'boarding')).toBe('Handoff status: Signed');
  });

  it('gives the receiver and the viewer the state', () => {
    expect(signOffStatus(tpl('signed'), unit, 'sbar_nurse', 'boarding')).toBe('Ready to accept');
    expect(signOffStatus(tpl('acknowledged', [], { changed_since_acknowledged: true }), unit, 'sbar_nurse', 'boarding')).toBe('Ready to accept');
    expect(signOffStatus(tpl('draft'), unit, 'sbar_nurse', 'boarding')).toBe('Waiting for the ED to sign');
    expect(signOffStatus(tpl('changed'), unit, 'sbar_nurse', 'boarding')).toBe('Waiting for the ED to sign again');
    expect(signOffStatus(tpl('acknowledged'), unit, 'sbar_nurse', 'boarding')).toBe('Handoff status: Acknowledged');
    expect(signOffStatus(tpl('draft', ['N07']), viewer, 'sbar_nurse', 'boarding')).toBe('Handoff status: Draft, not signed yet');
  });
});
