import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppProvider, useApp } from '@/hooks/useApp';
import { caseSummary, caseTemplate, config, draft, edNurse, element, rule } from '@/test/fixtures';
import type { AppConfig, CaseDetail } from '@/lib/types';
import { READ_ONLY } from '@/lib/checks';
import Handoff from '@/pages/Handoff';
import { AckPanel } from '@/components/handoff/Panels';
import { ClockControl, PastBanner } from './ClockControl';

vi.mock('@/lib/api', () => ({
  setPersona: vi.fn(),
  ApiError: class extends Error {},
  api: { case: vi.fn(), act: vi.fn(), own: vi.fn(), colleagues: vi.fn(async () => []), patientTasks: vi.fn(async () => ({ at: '', csn: '', phase: 'boarding', tasks: [], counts: { open: 0, overdue: 0 }, access: { can_act: true, in_role: true, note: null }, people: [] })), reset: vi.fn() },
}));
const { api } = await import('@/lib/api');

const HOUR = 3600000;
const live: AppConfig = { ...config, mode: 'live', clock: 'live', demo_personas: false, past_view_hours: 72 };

function State() {
  const { at, apiAt, past } = useApp();
  return (
    <>
      <output aria-label="at">{at}</output>
      <output aria-label="apiAt">{apiAt}</output>
      <output aria-label="past">{past ?? ''}</output>
    </>
  );
}

function renderClock(cfg: AppConfig = live) {
  return render(
    <AppProvider config={cfg}>
      <TooltipProvider>
        <ClockControl />
        <PastBanner />
        <State />
      </TooltipProvider>
    </AppProvider>,
  );
}

const hoursAgo = (h: number) => new Date(Math.floor((Date.now() - h * HOUR) / HOUR) * HOUR).toISOString();
const text = (label: string) => screen.getByLabelText(label).textContent;

beforeEach(() => {
  window.history.replaceState({}, '', '/');
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
});

describe('live clock, earlier time', () => {
  it('opens live: no banner, nothing sent as the time, and View an earlier time next to Live', () => {
    renderClock();
    expect(screen.getByText('Live')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'View an earlier time' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Earlier time' })).toBeNull();
    expect(text('apiAt')).toBe('');
  });

  it('picks an hour, shows the read only banner and the time in the address, and goes back to live', async () => {
    const user = userEvent.setup();
    renderClock();
    await user.click(screen.getByRole('button', { name: 'View an earlier time' }));
    const panel = await screen.findByRole('dialog', { name: 'View an earlier time' });
    const hour = within(panel).getByLabelText(/^Hour on /);
    const options = within(hour).getAllByRole('option').filter((o) => (o as HTMLOptionElement).value);
    await user.selectOptions(hour, options[0]);
    const past = text('past')!;
    expect(past).toBe(new Date(Number((options[0] as HTMLOptionElement).value)).toISOString());
    expect(text('apiAt')).toBe(past);
    expect(new URLSearchParams(window.location.search).get('at')).toBe(past);
    const banner = screen.getByRole('region', { name: 'Earlier time' });
    expect(within(banner).getByText(/^Viewing .+, read only\.$/)).toBeInTheDocument();
    await user.click(within(banner).getByRole('button', { name: 'Back to live' }));
    expect(screen.queryByRole('region', { name: 'Earlier time' })).toBeNull();
    expect(text('past')).toBe('');
    expect(text('apiAt')).toBe('');
    expect(window.location.search).toBe('');
  });

  it('opens at an earlier time from the address; a later time is live', () => {
    const t = hoursAgo(5);
    window.history.replaceState({}, '', `/?at=${encodeURIComponent(t)}`);
    const { unmount } = renderClock();
    expect(text('past')).toBe(t);
    expect(screen.getByRole('button', { name: /^Viewing an earlier time: / })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Back 1 hour' })).toBeEnabled();
    unmount();
    window.history.replaceState({}, '', `/?at=${encodeURIComponent(new Date(Date.now() + HOUR).toISOString())}`);
    renderClock();
    expect(text('past')).toBe('');
    expect(window.location.search).toBe('');
  });

  it('pauses the 20 second refresh while viewing an earlier time', () => {
    vi.useFakeTimers();
    const t = hoursAgo(3);
    window.history.replaceState({}, '', `/?at=${encodeURIComponent(t)}`);
    const { unmount } = renderClock();
    act(() => vi.advanceTimersByTime(60000));
    expect(text('at')).toBe(t);
    unmount();
    window.history.replaceState({}, '', '/');
    renderClock();
    const first = text('at');
    act(() => vi.advanceTimersByTime(20000));
    expect(text('at')).not.toBe(first);
  });

  it('has no picker when looking back is off, and ignores a time in the address', () => {
    window.history.replaceState({}, '', `/?at=${encodeURIComponent(hoursAgo(2))}`);
    renderClock({ ...live, past_view_hours: 0 });
    expect(screen.queryByRole('button', { name: 'View an earlier time' })).toBeNull();
    expect(text('past')).toBe('');
  });

  it('keeps the replay clock as it was: the time is sent, and there is no banner', () => {
    renderClock(config);
    expect(text('apiAt')).toBe(config.default_at);
    expect(screen.queryByRole('button', { name: 'View an earlier time' })).toBeNull();
  });
});

describe('handoff at an earlier time', () => {
  const N07 = rule({ rule: 'N07', element: 'E10', label: 'Isolation', status: 'attest', attest_options: ['No isolation needed', 'Droplet'] });
  const detail: CaseDetail = {
    summary: caseSummary(),
    templates: {
      sbar_nurse: caseTemplate({
        draft: draft({
          sections: [{ id: 'A', label: 'Assessment', elements: [element({ narrative: { text: 'Stable on room air.' } }), element({ id: 'E10', label: 'Isolation', kind: 'deterministic', data: null, rules: [N07] })] }],
          rules: [N07],
          completeness: { score: 0.8, blocking: ['N07'], model_score: 0.8 },
        }),
      }),
    },
    activity: [],
  };
  const nurse = { ...live, personas: [edNurse] };

  async function open(at: string | null) {
    window.history.replaceState({}, '', at ? `/handoff/1001?at=${encodeURIComponent(at)}` : '/handoff/1001');
    vi.mocked(api.case).mockResolvedValue(detail);
    render(
      <AppProvider config={nurse}>
        <TooltipProvider>
          <Handoff csn="1001" />
        </TooltipProvider>
      </AppProvider>,
    );
    return screen.findByRole('complementary', { name: 'Sign off and context' });
  }

  it('turns off edit, attest and sign, and says why', async () => {
    const t = hoursAgo(4);
    const aside = await open(t);
    expect(api.case).toHaveBeenLastCalledWith('1001', t);
    expect(screen.getByText(READ_ONLY, { selector: '#read-only-why' })).toBeInTheDocument();
    const edit = screen.getByRole('button', { name: 'Edit Assessment' });
    expect(edit).toBeDisabled();
    expect(edit).toHaveAttribute('aria-describedby', 'read-only-why');
    for (const r of within(aside).getAllByRole('radio')) expect(r).toBeDisabled();
    expect(within(aside).getByLabelText('Your callback number')).toBeDisabled();
    expect(within(aside).getByRole('button', { name: 'Sign' })).toBeDisabled();
    expect(within(aside).getByText(READ_ONLY, { selector: '#sign-locked' })).toBeInTheDocument();
  });

  it('acts live, with the server deciding the time', async () => {
    const user = userEvent.setup();
    vi.mocked(api.act).mockResolvedValue(detail);
    const aside = await open(null);
    expect(api.case).toHaveBeenLastCalledWith('1001', '');
    expect(screen.queryByText(READ_ONLY)).toBeNull();
    expect(screen.getByRole('button', { name: 'Edit Assessment' })).toBeEnabled();
    await user.click(within(aside).getByRole('radio', { name: 'No isolation needed' }));
    expect(api.act).toHaveBeenCalledWith('1001', 'sbar_nurse', 'attest', { rule: 'N07', option: 'No isolation needed' }, '');
  });

  it('turns off the read back for the receiver', () => {
    render(
      <TooltipProvider>
        <AckPanel tpl={caseTemplate({ workflow: { state: 'signed' } })} locked onAck={vi.fn()} />
      </TooltipProvider>,
    );
    expect(screen.getByLabelText('Read back')).toBeDisabled();
    expect(screen.getByLabelText('Questions for the sender')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Accept handoff' })).toBeDisabled();
    expect(screen.getByText(READ_ONLY)).toHaveAttribute('id', 'accept-locked');
  });
});
