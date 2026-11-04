import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppProvider } from '@/hooks/useApp';
import { caseSummary, caseTemplate, config, draft, element, rule } from '@/test/fixtures';
import type { CaseDetail } from '@/lib/types';
import Handoff from './Handoff';

vi.mock('@/lib/api', () => ({
  setPersona: vi.fn(),
  ApiError: class extends Error {},
  api: { case: vi.fn(), act: vi.fn(), reset: vi.fn() },
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

async function open() {
  vi.mocked(api.case).mockResolvedValue(detail);
  vi.mocked(api.act).mockResolvedValue(detail);
  render(
    <AppProvider config={config}>
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
    await user.click(within(aside).getByRole('button', { name: 'Other' }));
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
});
