import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { caseTemplate, draft, edNurse, element, rule } from '@/test/fixtures';
import type { CaseTemplate, Draft, Element, EnteredValue, Rule } from '@/lib/types';
import { ElementView } from './ElementView';
import { HistoryCard, SignPanel } from './Panels';

const wrap = (ui: React.ReactNode) => render(<TooltipProvider>{ui}</TooltipProvider>);
const ISO = ['No isolation needed', 'Contact', 'Droplet', 'Airborne', 'Contact plus (enteric)', 'Protective (neutropenic)', 'Other'];
const isolation = (over: Partial<Rule> = {}) =>
  rule({ rule: 'N07', element: 'E10', label: 'Isolation', status: 'attest', attest_options: ISO, attest_typed: { Other: { label: 'Type of isolation', note: null } }, ...over });
const allergy = (over: Partial<Rule> = {}) =>
  rule({ rule: 'N06', element: 'E09', label: 'Allergies', status: 'attest', attest_options: ['No known allergies confirmed', 'Add an allergy'], attest_typed: { 'Add an allergy': { label: 'Allergen', note: 'Reaction (optional)' } }, ...over });
const value = (over: Partial<EnteredValue> = {}): EnteredValue => ({ rule: 'N07', option: 'Droplet', value: 'Droplet', note: null, by: 'Jordan Reyes, RN', at: '2026-03-10T14:50:00Z', version: 2, template: 'sbar_nurse', ...over });

function withRules(rules: Rule[], entered: Record<string, EnteredValue[]> = {}): Draft {
  const blocking = rules.filter((r) => !r.resolution && r.level !== 'soft').map((r) => r.rule);
  return draft({
    rules,
    completeness: { score: 0.7, blocking, model_score: 0.7 },
    sections: [{ id: 'B', label: 'Background', elements: rules.map((r) => element({ id: r.element, label: r.label, kind: 'deterministic', data: null, rules: [r], entered: entered[r.element] })) }],
  });
}

function signPanel(d: Draft, onAttest = vi.fn(async () => true)) {
  wrap(<SignPanel tpl={caseTemplate({ draft: d })} persona={edNurse} template="sbar_nurse" phase="boarding" onAttest={onAttest} onSign={vi.fn()} />);
  return onAttest;
}

function item(e: Element, canEdit = true, tpl: CaseTemplate = caseTemplate()) {
  wrap(<ElementView e={e} tpl={tpl} canEdit={canEdit} onEdit={vi.fn(async () => true)} onAttest={vi.fn()} />);
}

describe('The choice group in Sign off', () => {
  it('is one radio group: None set apart, then the values, no checks before a choice', async () => {
    const user = userEvent.setup();
    const r = isolation();
    const onAttest = signPanel(withRules([r]));
    const group = screen.getByRole('radiogroup', { name: 'Isolation: choose one' });
    expect(within(group).getAllByRole('radio').map((b) => b.textContent)).toEqual(ISO);
    expect(within(group).getAllByRole('radio').every((b) => b.getAttribute('aria-checked') === 'false')).toBe(true);
    expect(group).toHaveTextContent(/^None:No isolation neededOr enter:Contact/);
    await user.click(within(group).getByRole('radio', { name: 'Droplet' }));
    expect(onAttest).toHaveBeenCalledWith(r, 'Droplet');
  });

  it('moves between choices with the arrow keys without choosing', async () => {
    const user = userEvent.setup();
    const onAttest = signPanel(withRules([isolation()]));
    const radios = screen.getAllByRole('radio');
    expect(radios.map((x) => x.tabIndex)).toEqual([0, -1, -1, -1, -1, -1, -1]);
    radios[0].focus();
    await user.keyboard('{ArrowRight}{ArrowRight}');
    expect(radios[2]).toHaveFocus();
    expect(onAttest).not.toHaveBeenCalled();
    await user.keyboard('{Enter}');
    expect(onAttest).toHaveBeenCalledWith(expect.objectContaining({ rule: 'N07' }), 'Droplet');
  });

  it('checks only the chosen value once resolved, and Change opens a typed value filled in', async () => {
    const user = userEvent.setup();
    const r = isolation({ resolution: { kind: 'attested', option: 'Other', by: 'Jordan Reyes, RN', entered: true } });
    signPanel(withRules([r], { E10: [value({ option: 'Other', value: 'Contact and droplet' })] }));
    await user.click(screen.getByRole('button', { name: 'Change Isolation' }));
    expect(screen.getAllByRole('radio').filter((x) => x.getAttribute('aria-checked') === 'true').map((x) => x.textContent)).toEqual(['Other']);
    await user.click(screen.getByRole('radio', { name: 'Other' }));
    expect(screen.getByLabelText('Type of isolation')).toHaveValue('Contact and droplet');
  });

  it('asks for the words of a typed choice before saving it', async () => {
    const user = userEvent.setup();
    const r = isolation();
    const onAttest = signPanel(withRules([r]));
    await user.click(screen.getByRole('radio', { name: 'Other' }));
    expect(onAttest).not.toHaveBeenCalled();
    const save = screen.getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled();
    await user.type(screen.getByLabelText('Type of isolation'), 'Contact and droplet');
    await user.click(save);
    expect(onAttest).toHaveBeenCalledWith(r, 'Other', { entry: 'Contact and droplet' });
  });

  it('refuses typed text over 2,000 characters, as the server does', async () => {
    const user = userEvent.setup();
    signPanel(withRules([isolation()]));
    await user.click(screen.getByRole('radio', { name: 'Other' }));
    await user.click(screen.getByLabelText('Type of isolation'));
    await user.paste('a'.repeat(2001));
    expect(screen.getByText('Keep it under 2,000 characters.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('keeps a list of allergies, each removable, and adds another', async () => {
    const user = userEvent.setup();
    const r = allergy({ resolution: { kind: 'attested', option: 'Add an allergy', by: 'Jordan Reyes, RN', entered: true } });
    const onAttest = signPanel(withRules([r], { E09: [value({ rule: 'N06', option: 'Add an allergy', value: 'Penicillin', note: 'Hives' }), value({ rule: 'N06', option: 'Add an allergy', value: 'Latex' })] }));
    await user.click(screen.getByRole('button', { name: 'Change or add Allergies' }));
    const list = screen.getByRole('list', { name: 'Allergies entered' });
    expect(within(list).getAllByRole('listitem').map((x) => x.textContent)).toEqual(['Penicillin: Hives', 'Latex']);
    await user.click(screen.getByRole('button', { name: 'Remove Penicillin' }));
    expect(onAttest).toHaveBeenCalledWith(r, 'Add an allergy', { entry: 'Penicillin', remove: true });
    await user.click(screen.getByRole('button', { name: 'Change or add Allergies' }));
    await user.click(screen.getByRole('radio', { name: 'Add an allergy' }));
    expect(screen.getByLabelText('Allergen')).toHaveValue('');
    await user.type(screen.getByLabelText('Allergen'), 'Aspirin');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(onAttest).toHaveBeenLastCalledWith(r, 'Add an allergy', { entry: 'Aspirin', entry_note: '' });
  });

  it('moves focus to the next open item after a choice, and never drops it on the page', async () => {
    const user = userEvent.setup();
    function Live() {
      const [rules, setRules] = useState<Rule[]>([isolation(), allergy()]);
      return (
        <SignPanel
          tpl={caseTemplate({ draft: withRules(rules) })}
          persona={edNurse}
          template="sbar_nurse"
          phase="boarding"
          onSign={vi.fn()}
          onAttest={async (r, o) => {
            setRules((all) => all.map((x) => (x.rule === r.rule ? { ...x, resolution: { kind: 'attested', option: o, by: 'Jordan Reyes, RN' } } : x)));
            return true;
          }}
        />
      );
    }
    wrap(<Live />);
    await user.click(screen.getByRole('radio', { name: 'No isolation needed' }));
    expect(screen.getByRole('radio', { name: 'No known allergies confirmed' })).toHaveFocus();
    await user.click(screen.getByRole('radio', { name: 'No known allergies confirmed' }));
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement?.id).toBe('rule-N06');
  });
});

describe('Entered values on the item', () => {
  const resolved = isolation({ resolution: { kind: 'attested', option: 'Droplet', by: 'Jordan Reyes, RN', entered: true } });

  it('shows the value as an amber pill with the tag, for the sender and the receiver, and no choices', () => {
    for (const canEdit of [true, false]) {
      item(element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: null, entered: [value()], rules: [resolved] }), canEdit);
      expect(screen.getByText('Droplet').closest('span')).toHaveClass('text-sev-watcher');
      expect(screen.getByText('Entered by Jordan Reyes, RN, not yet in the chart')).toBeInTheDocument();
      expect(screen.queryByRole('radio')).not.toBeInTheDocument();
      document.body.innerHTML = '';
    }
  });

  it('shows an entered code status other than full code in red, and every entered allergy', () => {
    item(element({ id: 'E08', label: 'Code status', kind: 'deterministic', data: null, entered: [value({ option: 'DNR', value: 'DNR' })], rules: [] }));
    expect(screen.getByText('DNR').closest('span')).toHaveClass('text-sev-unstable');
    document.body.innerHTML = '';
    item(element({ id: 'E09', label: 'Allergies', kind: 'deterministic', data: [], entered: [value({ value: 'Penicillin', note: 'Hives' }), value({ value: 'Latex' })], rules: [] }));
    expect(screen.getByText('Penicillin: Hives')).toBeInTheDocument();
    expect(screen.getByText('Latex')).toBeInTheDocument();
  });

  it('shows the chart value once the chart has one', () => {
    item(element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: { value: 'Contact' }, entered: [value()], rules: [] }));
    expect(screen.getByText(/Contact/)).toBeInTheDocument();
    expect(screen.queryByText(/not yet in the chart/)).not.toBeInTheDocument();
  });

  it('sends the sender to Sign off to resolve an open item', async () => {
    const user = userEvent.setup();
    const li = document.createElement('li');
    li.id = 'rule-N07';
    li.tabIndex = -1;
    li.scrollIntoView = vi.fn();
    document.body.appendChild(li);
    item(element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: null, rules: [isolation()] }));
    await user.click(screen.getByRole('button', { name: /^Resolve in Sign off ?: Isolation$/ }));
    expect(li).toHaveFocus();
    li.remove();
    document.body.innerHTML = '';
    item(element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: null, rules: [isolation()] }), false);
    expect(screen.queryByRole('button', { name: /Resolve in Sign off/ })).not.toBeInTheDocument();
  });

  it('explains the tag on focus', async () => {
    const user = userEvent.setup();
    item(element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: null, entered: [value()], rules: [resolved] }), false);
    await user.tab();
    const tag = screen.getByRole('button', { name: /Entered by Jordan Reyes, RN, not yet in the chart/ });
    expect(tag).toHaveFocus();
    expect(tag).toHaveAccessibleDescription(/Entered by Jordan Reyes, RN on the SBAR during sign off/);
  });

  it('keeps repeated tags out of the tab order but describes them', async () => {
    const user = userEvent.setup();
    const tpl = caseTemplate({ changes: { since: 'previous', version: 1, elements: [{ id: 'E11', label: 'ED course', kind: 'narrative', before: 'a', after: 'b' }] } });
    item(element({ id: 'E11', label: 'ED course', narrative: { text: 'Fluids given.' } }), false, tpl);
    const changed = screen.getByText('Changed').closest('[data-explain]')!;
    expect(changed.tagName).toBe('SPAN');
    expect(changed).toHaveAccessibleDescription('Different from the previous version.');
    expect(screen.getByText('Drafted by the model').closest('[data-explain]')).not.toHaveAttribute('tabindex');
    await user.tab();
    expect(document.activeElement?.textContent).not.toMatch(/Changed|Drafted by the model/);
  });

  it('keeps entered and removed values in History', () => {
    const tpl = caseTemplate({ draft: draft({ rules: [allergy()] }) });
    wrap(
      <HistoryCard
        tpl={tpl}
        template="sbar_nurse"
        activity={[
          { kind: 'attest', template: 'sbar_nurse', by: 'Jordan Reyes, RN', at: '2026-03-10T14:50:00Z', version: 2, detail: 'N06', entered: 'Penicillin', note: 'Hives' },
          { kind: 'attest', template: 'sbar_nurse', by: 'Jordan Reyes, RN', at: '2026-03-10T14:55:00Z', version: 2, detail: 'N06', removed: 'Penicillin' },
        ]}
      />,
    );
    expect(screen.getByText(/Allergies: Penicillin, Hives/)).toBeInTheDocument();
    expect(screen.getByText(/Allergies: removed Penicillin/)).toBeInTheDocument();
  });
});
