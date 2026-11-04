import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { caseTemplate, draft, edNurse, rule } from '@/test/fixtures';
import type { CaseTemplate, Persona } from '@/lib/types';
import { AckPanel, SignPanel } from './Panels';

const wrap = (ui: React.ReactNode) => render(<TooltipProvider>{ui}</TooltipProvider>);

function sign(tpl: CaseTemplate, persona: Persona = edNurse, phase = 'boarding') {
  const onSign = vi.fn();
  const onAttest = vi.fn();
  wrap(<SignPanel tpl={tpl} persona={persona} template="sbar_nurse" phase={phase} onAttest={onAttest} onSign={onSign} />);
  return { onSign, onAttest };
}

describe('SignPanel', () => {
  it('disables Sign while blocking items exist, even with a callback', async () => {
    const user = userEvent.setup();
    const r = rule({ attest_options: ['None'] });
    const { onAttest } = sign(caseTemplate({ draft: draft({ rules: [r], completeness: { score: 0.7, blocking: ['R1'], model_score: 0.7 } }) }));
    expect(screen.getByText('1 item to resolve before signing')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Your callback number'), 'x4471');
    expect(screen.getByRole('button', { name: 'Sign' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'None' }));
    expect(onAttest).toHaveBeenCalledWith(r, 'None');
  });

  it('disables Sign until a callback is typed, then signs with it', async () => {
    const user = userEvent.setup();
    const { onSign } = sign(caseTemplate());
    const button = screen.getByRole('button', { name: 'Sign' });
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText('Your callback number'), '   ');
    expect(button).toBeDisabled();
    await user.type(screen.getByLabelText('Your callback number'), 'x4471');
    expect(button).toBeEnabled();
    await user.click(button);
    expect(onSign).toHaveBeenCalledWith('   x4471');
  });

  it('has no sign form once the patient left the ED with the newest version signed', () => {
    const signedTpl = caseTemplate({ workflow: { state: 'signed', signed: { by: 'Jordan Reyes, RN', role: 'ed_nurse', callback: 'x4471', at: '2026-03-10T14:50:00Z', version: 2 } } });
    sign(signedTpl, edNurse, 'arrived');
    expect(screen.queryByRole('button', { name: 'Sign' })).not.toBeInTheDocument();
    expect(screen.getByText(/this handoff is final/)).toBeInTheDocument();
  });

  it('still lets the ED sign a patient transferred without sign off', () => {
    sign(caseTemplate({ workflow: { state: 'transferred_unsigned' } }), edNurse, 'arrived');
    expect(screen.getByText('Transferred without sign off.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign' })).toBeInTheDocument();
  });

  it('asks to sign again after a change, and explains why', () => {
    const changed = caseTemplate({
      workflow: { state: 'changed', edited_since_signed: true, changed_since_signed: false, signed: { by: 'Jordan Reyes, RN', role: 'ed_nurse', callback: 'x4471', at: '2026-03-10T14:50:00Z', version: 2 } },
    });
    sign(changed);
    expect(screen.getByText('Changed after signing, sign again.')).toBeInTheDocument();
    expect(screen.getByText(/An edit or attestation was made after signing/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sign again' })).toBeInTheDocument();
  });

  it('refuses a callback without a number to dial', async () => {
    const user = userEvent.setup();
    sign(caseTemplate());
    await user.type(screen.getByLabelText('Your callback number'), 'x1');
    expect(screen.getByRole('button', { name: 'Sign' })).toBeDisabled();
    expect(screen.getByText('At least 3 digits, or an extension like x4471.')).toBeInTheDocument();
  });

  it('says when a bedside recheck expired', () => {
    const r = rule({ rule: 'N08', element: 'E14', level: 'stale', status: 'stale', label: 'Vital signs', attest_options: ['Rechecked at bedside, unchanged'],
      attest_expired: { by: 'Jordan Reyes, RN', at: '2026-03-10T14:00:00Z', until: '2026-03-10T14:30:00Z', limit_min: 30, severity: 'watcher' } });
    sign(caseTemplate({ draft: draft({ rules: [r], completeness: { score: 0.7, blocking: ['N08'], model_score: 0.7 } }) }));
    expect(screen.getByText(/counts for 30 minutes, so it expired at 10:30. Recheck again./)).toBeInTheDocument();
  });

  it('has no sign form for a role that cannot sign', () => {
    sign(caseTemplate(), { ...edNurse, can_sign: [] });
    expect(screen.queryByRole('button', { name: 'Sign' })).not.toBeInTheDocument();
    expect(screen.getByText('Your role does not sign this template.')).toBeInTheDocument();
  });
});

describe('AckPanel', () => {
  const signed = caseTemplate({ workflow: { state: 'signed', signed: { by: 'Jordan Reyes, RN', role: 'ed_nurse', callback: 'x4471', at: '2026-03-10T14:50:00Z', version: 2 } } });

  it('waits while the handoff is a draft', () => {
    wrap(<AckPanel tpl={caseTemplate()} onAck={vi.fn()} />);
    expect(screen.getByText(/Waiting for the ED clinician to sign/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Accept handoff' })).not.toBeInTheDocument();
  });

  it('requires a read back of at least a sentence and says why Accept is not ready', async () => {
    const user = userEvent.setup();
    const onAck = vi.fn();
    wrap(<AckPanel tpl={signed} onAck={onAck} />);
    const box = screen.getByRole('textbox', { name: 'Read back' });
    const button = screen.getByRole('button', { name: 'Accept handoff' });
    expect(button).toBeDisabled();
    expect(screen.getByText('Write your read back to accept.')).toBeInTheDocument();
    for (const text of ['1234567890', 'Watcher patient', 'aaaa aaaa aaaa aaaa']) {
      await user.clear(box);
      await user.type(box, text);
      expect(button).toBeDisabled();
      expect(screen.getByText(/Write at least a short sentence/)).toBeInTheDocument();
    }
    await user.clear(box);
    await user.type(box, '  Watcher, recheck vitals on arrival  ');
    expect(button).toBeEnabled();
    await user.type(screen.getByRole('textbox', { name: 'Questions for the sender' }), 'Lines?');
    await user.click(button);
    expect(onAck).toHaveBeenCalledWith('  Watcher, recheck vitals on arrival  ', 'Lines?');
  });

  it('waits for the ED to sign again after a change', () => {
    wrap(<AckPanel tpl={caseTemplate({ workflow: { state: 'changed', edited_since_signed: true } })} onAck={vi.fn()} />);
    expect(screen.getByText(/Waiting for the ED to sign again/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Accept handoff' })).not.toBeInTheDocument();
  });

  it('shows the read back once acknowledged', () => {
    wrap(
      <AckPanel
        tpl={caseTemplate({ workflow: { state: 'acknowledged', acknowledged: { by: 'Sam Lee, RN', at: '2026-03-10T15:10:00Z', version: 2, readback: '72 F sepsis, watcher' } } })}
        onAck={vi.fn()}
      />,
    );
    expect(screen.getByText('"72 F sepsis, watcher"')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Accept handoff' })).not.toBeInTheDocument();
  });
});
