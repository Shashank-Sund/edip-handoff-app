import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppProvider, useApp } from '@/hooks/useApp';
import { config } from '@/test/fixtures';
import { clock } from '@/lib/format';
import { ClockControl } from './ClockControl';

vi.mock('@/lib/api', () => ({ setPersona: vi.fn(), api: { reset: vi.fn(async () => ({ status: 'reset' })) } }));
const { api } = await import('@/lib/api');

// The reference week across the November 1 clock change, at Sat Oct 31 15:00 EDT.
const week = { ...config, window: { start: '2026-10-26T19:00:00Z', end: '2026-11-02T20:00:00Z' }, default_at: '2026-10-31T19:00:00Z' };

function Now() {
  return <output aria-label="now">{useApp().at}</output>;
}

async function open() {
  const user = userEvent.setup();
  render(
    <AppProvider config={week}>
      <TooltipProvider>
        <ClockControl />
        <Now />
      </TooltipProvider>
    </AppProvider>,
  );
  await user.click(screen.getByRole('button', { name: /Replay time/ }));
  return { user, panel: await screen.findByRole('dialog') };
}

beforeEach(() => window.history.replaceState({}, '', '/'));

describe('ClockControl days', () => {
  it('has one button per facility day, the current one pressed, and keeps the hour across the clock change', async () => {
    const { user, panel } = await open();
    const days = within(panel).getAllByRole('button', { name: /^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d+$/ });
    expect(days.map((d) => d.textContent)).toEqual(['Mon 26', 'Tue 27', 'Wed 28', 'Thu 29', 'Fri 30', 'Sat 31', 'Sun 1', 'Mon 2']);
    expect(within(panel).getByRole('button', { name: 'Sat 31' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(within(panel).getByRole('button', { name: 'Mon 2' }));
    expect(clock(screen.getByLabelText('now').textContent)).toBe('15:00');
    expect(within(panel).getByRole('button', { name: 'Mon 2' })).toHaveAttribute('aria-pressed', 'true');
    // The replay time is in the address, for reloads and shared links.
    expect(new URLSearchParams(window.location.search).get('at')).toBe('2026-11-02T20:00:00.000Z');
  });

  it('asks before clearing all demo actions', async () => {
    const { user, panel } = await open();
    await user.click(within(panel).getByRole('button', { name: 'Clear all demo actions' }));
    expect(api.reset).not.toHaveBeenCalled();
    await user.click(within(panel).getByRole('button', { name: 'Cancel' }));
    expect(api.reset).not.toHaveBeenCalled();
    await user.click(within(panel).getByRole('button', { name: 'Clear all demo actions' }));
    await user.click(within(panel).getByRole('button', { name: 'Clear all' }));
    expect(api.reset).toHaveBeenCalledTimes(1);
  });

  it('opens at the time in the address', () => {
    window.history.replaceState({}, '', '/?at=2026-10-28T16%3A30%3A00.000Z');
    render(
      <AppProvider config={week}>
        <Now />
      </AppProvider>,
    );
    expect(screen.getByLabelText('now').textContent).toBe('2026-10-28T16:30:00.000Z');
  });
});
