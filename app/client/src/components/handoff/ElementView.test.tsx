import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { caseTemplate, element } from '@/test/fixtures';
import type { Element } from '@/lib/types';
import { ElementView } from './ElementView';

function show(e: Element, opts: { canEdit?: boolean; onEdit?: (id: string, body: unknown) => Promise<boolean> } = {}) {
  const onEdit = vi.fn(opts.onEdit ?? (async () => true));
  const onAttest = vi.fn();
  const utils = render(
    <TooltipProvider>
      <ElementView e={e} tpl={caseTemplate()} canEdit={opts.canEdit ?? false} onEdit={onEdit} onAttest={onAttest} />
    </TooltipProvider>,
  );
  return { ...utils, onEdit, onAttest };
}

const marks = (container: HTMLElement) => [...container.querySelectorAll('mark')].map((m) => m.textContent);

describe('FlaggedText', () => {
  it('highlights only whole flagged numbers', () => {
    const { container } = show(
      element({
        narrative: {
          text: 'HR 120 then 12 overnight; lactate 4.61, repeat 4.6. Cr 1.12 and 12.',
          unsupported_numbers: ['12', '4.6'],
        },
      }),
    );
    expect(marks(container)).toEqual(['12', '4.6', '12']);
    expect(container.textContent).toContain('HR 120 then 12 overnight; lactate 4.61, repeat 4.6. Cr 1.12 and 12.');
  });

  it('does not treat the dot in a flagged number as a wildcard', () => {
    const { container } = show(element({ narrative: { text: 'K 4x6 and 4.6', unsupported_numbers: ['4.6'] } }));
    expect(marks(container)).toEqual(['4.6']);
  });

  it('makes highlights focusable so keyboard users reach the explanation', () => {
    const { container } = show(element({ narrative: { text: 'SpO2 88 on room air', unsupported_numbers: ['88'] } }));
    expect(container.querySelector('mark')).toHaveAttribute('tabindex', '0');
  });

  it('leaves text alone when nothing is flagged', () => {
    const { container } = show(element({ narrative: { text: 'HR 120' } }));
    expect(marks(container)).toEqual([]);
    expect(screen.getByText('HR 120')).toBeInTheDocument();
  });

  it('highlights in action items too', () => {
    const { container } = show(
      element({ id: 'E24', label: 'Action list', narrative: { items: [{ text: 'Repeat lactate at 1600 if above 2', unsupported_numbers: ['2'], owner: 'Unit RN', due: '16:00' }] } }),
    );
    expect(marks(container)).toEqual(['2']);
    expect(screen.getByText('Unit RN')).toBeInTheDocument();
    expect(screen.getByText('16:00')).toBeInTheDocument();
  });
});

describe('severity element', () => {
  const sev = (model_value: 'stable' | 'watcher' | 'unstable') =>
    element({
      id: 'E07',
      label: 'Illness severity',
      narrative: {
        value: 'unstable',
        model_value,
        floor: { value: 'unstable', reasons: ['SBP under 90 in the last hour'] },
        text: 'Unstable by rule: SBP under 90 in the last hour. Improving after fluids.',
      },
    });

  it('says Raised by rule when the model called it lower', () => {
    show(sev('watcher'));
    expect(screen.getByText(/Raised by rule/)).toBeInTheDocument();
    expect(screen.getByText(/The model called it watcher/)).toBeInTheDocument();
    expect(screen.getByText('Unstable')).toBeInTheDocument();
  });

  it('says Rule floor when the model agreed', () => {
    show(sev('unstable'));
    expect(screen.getByText(/Rule floor/)).toBeInTheDocument();
    expect(screen.queryByText(/Raised by rule/)).not.toBeInTheDocument();
  });

  it('shows the model text without the rule prefix', () => {
    show(sev('watcher'));
    expect(screen.getByText('Improving after fluids.')).toBeInTheDocument();
  });

  it('is never editable', () => {
    show(sev('watcher'), { canEdit: true });
    expect(screen.queryByRole('button', { name: /Edit Illness severity/ })).not.toBeInTheDocument();
  });
});

describe('elements not in the feeds', () => {
  it.each(['E19', 'E21', 'E22'])('%s renders as a single compact line', (id) => {
    const { container } = show(element({ id, label: 'Lines and drains', kind: 'deterministic', data: { anything: 1 } }));
    expect(screen.getByRole('heading', { name: 'Lines and drains' })).toBeInTheDocument();
    expect(screen.getByText(/Not in the reference feeds/)).toBeInTheDocument();
    expect(container.querySelector('pre')).toBeNull();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});

describe('Editor', () => {
  it('parses "text | owner | due" lines and drops blank ones', async () => {
    const user = userEvent.setup();
    const { onEdit } = show(element({ id: 'E24', label: 'Action list', narrative: { items: [] } }), { canEdit: true });
    await user.click(screen.getByRole('button', { name: 'Edit Action list' }));
    const box = screen.getByRole('textbox', { name: 'Edit Action list' });
    await user.clear(box);
    await user.type(box, 'Repeat lactate | Unit RN | 16:00{enter}{enter}  Call family  {enter}Check K |  | tonight');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(onEdit).toHaveBeenCalledWith('E24', {
      items: [
        { text: 'Repeat lactate', owner: 'Unit RN', due: '16:00' },
        { text: 'Call family', owner: null, due: null },
        { text: 'Check K', owner: null, due: 'tonight' },
      ],
    });
  });

  it('starts from the current items', async () => {
    const user = userEvent.setup();
    show(element({ id: 'E24', label: 'Action list', narrative: { items: [{ text: 'Follow cultures', owner: 'MD', due: null }] } }), { canEdit: true });
    await user.click(screen.getByRole('button', { name: 'Edit Action list' }));
    expect(screen.getByRole('textbox', { name: 'Edit Action list' })).toHaveValue('Follow cultures | MD | ');
  });

  it('saves plain text trimmed and closes on success', async () => {
    const user = userEvent.setup();
    const { onEdit } = show(element({ narrative: { text: 'Old text' } }), { canEdit: true });
    await user.click(screen.getByRole('button', { name: 'Edit Assessment' }));
    const box = screen.getByRole('textbox', { name: 'Edit Assessment' });
    await user.clear(box);
    await user.type(box, '  New text  ');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(onEdit).toHaveBeenCalledWith('E11', { text: 'New text' });
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('stays open when the save fails, and Cancel closes it', async () => {
    const user = userEvent.setup();
    show(element({ narrative: { text: 'Old text' } }), { canEdit: true, onEdit: async () => false });
    await user.click(screen.getByRole('button', { name: 'Edit Assessment' }));
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(screen.getByRole('textbox')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('hides Edit without permission', () => {
    show(element({ narrative: { text: 'Old text' } }), { canEdit: false });
    expect(screen.queryByRole('button', { name: /Edit/ })).not.toBeInTheDocument();
  });
});
