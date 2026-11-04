import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { caseTemplate, draft, element } from '@/test/fixtures';
import type { CaseTemplate, Element } from '@/lib/types';
import { ElementView } from './ElementView';
import { HistoryCard } from './Panels';
import { SectionBadge } from '@/components/common/SectionBadge';

function show(e: Element, canEdit = true, tpl: CaseTemplate = caseTemplate()) {
  const onEdit = vi.fn(async () => true);
  render(
    <TooltipProvider>
      <ElementView e={e} tpl={tpl} canEdit={canEdit} onEdit={onEdit} onAttest={vi.fn()} />
    </TooltipProvider>,
  );
  return onEdit;
}

describe('Restore and empty edits', () => {
  it('offers Restore on an edited item and sends it', async () => {
    const user = userEvent.setup();
    const onEdit = show(element({ id: 'E25', label: 'Contingency plan', narrative: { text: 'Mine', edited: { by: 'RN', at: '2026-03-10T14:00:00Z', version: 2, model_changed: false } } }));
    await user.click(screen.getByRole('button', { name: "Restore the model's text for Contingency plan" }));
    expect(onEdit).toHaveBeenCalledWith('E25', { restore: true });
  });

  it('has no Restore on model text', () => {
    show(element({ id: 'E25', label: 'Contingency plan', narrative: { text: 'Model' } }));
    expect(screen.queryByRole('button', { name: /Restore/ })).not.toBeInTheDocument();
  });

  it('will not save empty text and points to Restore', async () => {
    const user = userEvent.setup();
    const onEdit = show(element({ id: 'E25', label: 'Contingency plan', narrative: { text: 'Model' } }));
    await user.click(screen.getByRole('button', { name: 'Edit Contingency plan' }));
    await user.clear(screen.getByRole('textbox', { name: 'Edit Contingency plan' }));
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.getByText("Write the text, or use Restore to bring back the model's text.")).toBeInTheDocument();
    expect(onEdit).not.toHaveBeenCalled();
  });
});

describe('plain clinical words', () => {
  it('writes the rule floor reasons with units', () => {
    show(element({
      id: 'E07', label: 'Illness severity',
      narrative: { value: 'unstable', model_value: 'unstable', floor: { value: 'unstable', reasons: ['spo2 89', 'temp 38.5'] }, text: 'Unstable by rule: spo2 89; temp 38.5. Needs oxygen.' },
    }), false);
    expect(screen.getByText(/SpO2 89%; Temp 38.5 °C/)).toBeInTheDocument();
    // The decimal point in a reason does not cut the rule prefix short.
    expect(screen.getByText('Needs oxygen.')).toBeInTheDocument();
  });

  it('names the admitting provider without an id label', () => {
    show(element({ id: 'E03', label: 'Receiving team', kind: 'deterministic', data: { service: 'Medicine', admitting_id: 'E100218' } }), false);
    expect(screen.getByText('Provider E100218')).toBeInTheDocument();
    expect(screen.queryByText(/ID E100218/)).not.toBeInTheDocument();
  });

  it('shows item names in the history, not rule ids', () => {
    const tpl = caseTemplate({
      draft: draft({
        rules: [{ rule: 'P09', element: 'E16', level: 'hard', label: 'Pending results', status: 'attest' }],
        sections: [{ id: 'A', label: 'Action list', elements: [element({ id: 'E25', label: 'Contingency plan' })] }],
      }),
    });
    render(
      <TooltipProvider>
      <HistoryCard
        tpl={tpl}
        template="sbar_nurse"
        activity={[
          { kind: 'attest', template: 'sbar_nurse', by: 'RN', at: '2026-03-10T14:10:00Z', version: 2, detail: 'P09' },
          { kind: 'edit', template: 'sbar_nurse', by: 'RN', at: '2026-03-10T14:05:00Z', version: 2, detail: 'E25' },
          { kind: 'edit', template: 'sbar_nurse', by: 'RN', at: '2026-03-10T14:20:00Z', version: 2, detail: 'E25', restore: true },
        ]}
      />
      </TooltipProvider>,
    );
    expect(screen.getByText(/Pending results/)).toBeInTheDocument();
    expect(screen.getAllByText(/Contingency plan/)).toHaveLength(2);
    expect(screen.getByText('Model text restored by RN')).toBeInTheDocument();
    expect(screen.queryByText(/P09/)).not.toBeInTheDocument();
  });

  it('shows a readable section badge', () => {
    render(
      <>
        <SectionBadge id="HDR" label="Handoff" />
        <SectionBadge id="S1" label="Situation awareness" />
      </>,
    );
    expect(screen.queryByText('HDR')).not.toBeInTheDocument();
    expect(screen.getByText('H')).toBeInTheDocument();
    expect(screen.getByText('S1')).toBeInTheDocument();
  });
});
