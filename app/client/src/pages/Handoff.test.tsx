import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppProvider } from '@/hooks/useApp';
import { caseSummary, caseTemplate, config, draft, edNurse, element, rule } from '@/test/fixtures';
import type { CaseDetail } from '@/lib/types';
import { TAB_HELP } from '@/lib/labels';
import Handoff from './Handoff';

vi.mock('@/lib/api', () => ({
  setPersona: vi.fn(),
  ApiError: class extends Error {},
  api: { case: vi.fn(), act: vi.fn(), own: vi.fn(), colleagues: vi.fn(async () => []), reset: vi.fn() },
}));
const { api } = await import('@/lib/api');

const N07 = rule({
  rule: 'N07', element: 'E10', label: 'Isolation', status: 'attest',
  attest_options: ['No isolation needed', 'Droplet', 'Other'], attest_typed: { Other: { label: 'Type of isolation', note: null } },
});
const detail: CaseDetail = {
  summary: caseSummary({ code_status: null }),
  templates: {
    sbar_nurse: caseTemplate({
      draft: draft({
        sections: [{ id: 'B', label: 'Background', elements: [element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: null, rules: [N07] })] }],
        rules: [N07],
        completeness: { score: 0.8, blocking: ['N07'], model_score: 0.8 },
      }),
    }),
  },
  activity: [],
};

async function open(d: CaseDetail = detail, cfg = config) {
  vi.mocked(api.case).mockResolvedValue(d);
  vi.mocked(api.act).mockResolvedValue(d);
  render(
    <AppProvider config={cfg}>
      <TooltipProvider>
        <Handoff csn="1001" />
      </TooltipProvider>
    </AppProvider>,
  );
  return screen.findByRole('complementary', { name: 'Sign off and context' });
}

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  window.history.replaceState({}, '', '/handoff/1001');
});

describe('Handoff page', () => {
  it('lets the right column scroll on its own at xl, with Sign off at its top', async () => {
    const aside = await open();
    for (const c of ['xl:sticky', 'xl:top-20', 'xl:self-start', 'xl:max-h-[calc(100dvh-6rem)]', 'xl:overflow-y-auto', 'xl:overscroll-contain', 'scroll-subtle']) {
      expect(aside).toHaveClass(c);
    }
    expect(aside.firstElementChild).toHaveAttribute('id', 'signoff');
  });

  it('pins a bar below xl that says what is left and goes to the Sign off panel', async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    const user = userEvent.setup();
    await open();
    const bar = screen.getByRole('button', { name: /Sign off: 1 item to resolve/ });
    expect(bar.parentElement).toHaveClass('sticky', 'bottom-3', 'xl:hidden');
    await user.click(bar);
    expect(scroll).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });
    expect(document.getElementById('signoff')).toHaveFocus();
  });

  it('sends the typed words with a typed choice', async () => {
    const user = userEvent.setup();
    const aside = await open();
    await user.click(within(aside).getByRole('radio', { name: 'Other' }));
    await user.type(within(aside).getByLabelText('Type of isolation'), 'Contact and droplet');
    await user.click(within(aside).getByRole('button', { name: 'Save' }));
    expect(api.act).toHaveBeenCalledWith('1001', 'sbar_nurse', 'attest', { rule: 'N07', option: 'Other', entry: 'Contact and droplet' }, config.default_at);
    expect(await screen.findByText('Isolation: Contact and droplet entered')).toBeInTheDocument();
  });

  it('explains a missing code status in the patient box', async () => {
    await open();
    const flag = screen.getByRole('button', { name: 'Code status not documented' });
    expect(flag).toHaveAccessibleDescription(/The chart has no code status/);
  });

  it('shows entered values in the patient box, marked as not in the chart', async () => {
    const flag = { by: 'Jordan Reyes, RN', template: 'sbar_nurse', note: null };
    await open({
      ...detail,
      summary: caseSummary({
        code_status: null,
        allergies: [],
        isolation: null,
        entered: { code_status: { ...flag, value: 'DNR' }, isolation: { ...flag, value: 'Droplet' }, allergies: [{ ...flag, value: 'Penicillin', note: 'Hives' }, { ...flag, value: 'Latex' }] },
      }),
    });
    const dnr = screen.getByText('DNR · entered, not in chart');
    expect(dnr.closest('span')).toHaveClass('text-sev-unstable');
    expect(screen.getByRole('button', { name: 'DNR · entered, not in chart' })).toHaveAccessibleDescription(/Entered by Jordan Reyes, RN on the SBAR during sign off/);
    expect(screen.getByText('Droplet · entered, not in chart').closest('span')).toHaveClass('text-sev-watcher');
    expect(screen.getByText('Penicillin · entered, not in chart')).toBeInTheDocument();
    expect(screen.getByText('Latex · entered, not in chart')).toBeInTheDocument();
    expect(screen.queryByText('Code status not documented')).not.toBeInTheDocument();
    expect(screen.queryByText('No allergies documented')).not.toBeInTheDocument();
  });

  it('tells the receiver to ask the ED about a missing code status', async () => {
    const unit = { ...edNurse, id: 'unit-sgh-micu', role: 'unit_nurse' as const, can_sign: [], can_ack: ['sbar_nurse'] };
    await open(detail, { ...config, personas: [unit] });
    expect(screen.getByRole('button', { name: 'Code status not documented' })).toHaveAccessibleDescription('The chart has no code status. Ask the ED, or check the chart.');
  });

  it('uses the short tab names below xl, so nothing is cut at 1024', async () => {
    await open({ ...detail, templates: { ...detail.templates, custom_sea58: detail.templates.sbar_nurse } }, { ...config, templates: [...config.templates, { id: 'custom_sea58', name: 'Joint Commission minimum', audience: 'Any' }] });
    const tab = screen.getByRole('tab', { name: /Minimum/ });
    expect(within(tab).getByText('Minimum')).toHaveClass('xl:hidden');
    expect(within(tab).getByText('Joint Commission minimum')).toHaveClass('hidden', 'xl:inline');
  });

  it('has the tabs help on a phone too, and the guide says what each tab is', async () => {
    const user = userEvent.setup();
    await open();
    const phone = screen.getAllByRole('button', { name: /About the tabs/ }).find((b) => b.classList.contains('sm:hidden'))!;
    await user.click(phone);
    const sheet = await screen.findByRole('dialog', { name: /The three tabs/ });
    for (const text of Object.values(TAB_HELP)) expect(sheet).toHaveTextContent(text);
  });
});
