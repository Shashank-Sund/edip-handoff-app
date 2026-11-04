import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { caseTemplate, draft, edNurse, element, rule } from '@/test/fixtures';
import type { CaseTemplate, Element, Rule } from '@/lib/types';
import { ElementView } from './ElementView';
import { HistoryCard, SignPanel } from './Panels';

const wrap = (ui: React.ReactNode) => render(<TooltipProvider>{ui}</TooltipProvider>);
const ISO = ['No isolation needed', 'Contact', 'Droplet', 'Airborne', 'Contact plus (enteric)', 'Protective (neutropenic)', 'Other'];
const isolation = (over: Partial<Rule> = {}) =>
  rule({ rule: 'N07', element: 'E10', label: 'Isolation', status: 'attest', attest_options: ISO, attest_typed: { Other: { label: 'Type of isolation', note: null } }, ...over });
const allergy = (over: Partial<Rule> = {}) =>
  rule({ rule: 'N06', element: 'E09', label: 'Allergies', status: 'attest', attest_options: ['No known allergies confirmed', 'Add an allergy'], attest_typed: { 'Add an allergy': { label: 'Allergen', note: 'Reaction (optional)' } }, ...over });

function signPanel(r: Rule) {
  const onAttest = vi.fn(async () => true);
  wrap(<SignPanel tpl={caseTemplate({ draft: draft({ rules: [r], completeness: { score: 0.7, blocking: [r.rule], model_score: 0.7 } }) })} persona={edNurse} template="sbar_nurse" phase="boarding" onAttest={onAttest} onSign={vi.fn()} />);
  return onAttest;
}

function item(e: Element, canEdit = true, tpl: CaseTemplate = caseTemplate()) {
  const onAttest = vi.fn(async () => true);
  wrap(<ElementView e={e} tpl={tpl} canEdit={canEdit} onEdit={vi.fn(async () => true)} onAttest={onAttest} />);
  return onAttest;
}

describe('Real choices in Sign off', () => {
  it('offers every isolation choice, one tap each', async () => {
    const user = userEvent.setup();
    const r = isolation();
    const onAttest = signPanel(r);
    const group = screen.getByRole('group', { name: 'Choices for Isolation' });
    expect(within(group).getAllByRole('button').map((b) => b.textContent)).toEqual(ISO);
    await user.click(within(group).getByRole('button', { name: 'Droplet' }));
    expect(onAttest).toHaveBeenCalledWith(r, 'Droplet');
  });

  it('asks for the words of a typed choice before saving it', async () => {
    const user = userEvent.setup();
    const r = isolation();
    const onAttest = signPanel(r);
    await user.click(screen.getByRole('button', { name: 'Other' }));
    expect(onAttest).not.toHaveBeenCalled();
    const save = screen.getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled();
    await user.type(screen.getByLabelText('Type of isolation'), '   ');
    expect(save).toBeDisabled();
    await user.type(screen.getByLabelText('Type of isolation'), 'Contact and droplet');
    await user.click(save);
    expect(onAttest).toHaveBeenCalledWith(r, 'Other', { entry: '   Contact and droplet' });
    expect(screen.queryByLabelText('Type of isolation')).not.toBeInTheDocument();
  });

  it('takes an allergen and an optional reaction', async () => {
    const user = userEvent.setup();
    const r = allergy();
    const onAttest = signPanel(r);
    await user.click(screen.getByRole('button', { name: 'Add an allergy' }));
    await user.type(screen.getByLabelText('Reaction (optional)'), 'Hives');
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    await user.type(screen.getByLabelText('Allergen'), 'Penicillin');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(onAttest).toHaveBeenCalledWith(r, 'Add an allergy', { entry: 'Penicillin', entry_note: 'Hives' });
  });

  it('refuses typed text over 2,000 characters, as the server does', async () => {
    const user = userEvent.setup();
    signPanel(isolation());
    await user.click(screen.getByRole('button', { name: 'Other' }));
    await user.click(screen.getByLabelText('Type of isolation'));
    await user.paste('a'.repeat(2001));
    expect(screen.getByText('Keep it under 2,000 characters.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });
});

describe('Entered values on the item', () => {
  const entered = { rule: 'N07', option: 'Droplet', value: 'Droplet', note: null, by: 'Jordan Reyes, RN', at: '2026-03-10T14:50:00Z', version: 2 };
  const resolved = isolation({ resolution: { kind: 'attested', option: 'Droplet', by: 'Jordan Reyes, RN', entered: true } });

  it('shows the value with the amber tag, for the sender and the receiver', () => {
    for (const canEdit of [true, false]) {
      item(element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: null, entered, rules: [resolved] }), canEdit);
      expect(screen.getByText('Droplet')).toBeInTheDocument();
      expect(screen.getByText('Entered by Jordan Reyes, RN, not yet in the chart')).toBeInTheDocument();
      expect(screen.queryByText(/Attested: Droplet/)).not.toBeInTheDocument();
      document.body.innerHTML = '';
    }
  });

  it('shows an entered allergy with its reaction', () => {
    item(element({ id: 'E09', label: 'Allergies', kind: 'deterministic', data: [], entered: { ...entered, rule: 'N06', option: 'Add an allergy', value: 'Penicillin', note: 'Hives' }, rules: [] }));
    expect(screen.getByText('Penicillin: Hives')).toBeInTheDocument();
  });

  it('shows the chart value once the chart has one', () => {
    item(element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: { value: 'Contact' }, entered, rules: [] }));
    expect(screen.getByText(/Contact/)).toBeInTheDocument();
    expect(screen.queryByText(/not yet in the chart/)).not.toBeInTheDocument();
  });

  it('explains the tag on focus', async () => {
    const user = userEvent.setup();
    item(element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: null, entered, rules: [resolved] }), false);
    await user.tab();
    const tag = screen.getByRole('button', { name: /Entered by Jordan Reyes, RN, not yet in the chart/ });
    expect(tag).toHaveFocus();
    expect(tag).toHaveAccessibleDescription(/When the chart has a value, the chart value shows here/);
    expect(await screen.findByRole('dialog', { name: 'Not yet in the chart' })).toHaveTextContent('More in the guide');
  });

  it('lets the sender change a choice, and not the receiver', async () => {
    const user = userEvent.setup();
    const onAttest = item(element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: null, entered, rules: [resolved] }));
    await user.click(screen.getByRole('button', { name: 'Change Isolation' }));
    await user.click(screen.getByRole('button', { name: 'Airborne' }));
    expect(onAttest).toHaveBeenCalledWith(resolved, 'Airborne');
    document.body.innerHTML = '';
    item(element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: null, entered, rules: [resolved] }), false);
    expect(screen.queryByRole('button', { name: 'Change Isolation' })).not.toBeInTheDocument();
  });

  it('keeps the entered value in History', () => {
    const tpl = caseTemplate({ draft: draft({ rules: [isolation()] }) });
    wrap(<HistoryCard tpl={tpl} template="sbar_nurse" activity={[{ kind: 'attest', template: 'sbar_nurse', by: 'Jordan Reyes, RN', at: '2026-03-10T14:50:00Z', version: 2, detail: 'N07', entered: 'Penicillin', note: 'Hives' }]} />);
    expect(screen.getByText(/Isolation: Penicillin, Hives/)).toBeInTheDocument();
  });
});
