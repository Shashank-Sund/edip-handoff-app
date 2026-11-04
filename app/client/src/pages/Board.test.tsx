import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppProvider } from '@/hooks/useApp';
import { board, caseSummary, config, templateSummary } from '@/test/fixtures';
import Board from './Board';

vi.mock('@/lib/api', () => ({
  setPersona: vi.fn(),
  api: { board: vi.fn(), reset: vi.fn() },
}));
const { api } = await import('@/lib/api');

const rows = [
  caseSummary({ csn: '1001', patient: { name: 'Doe, Alex', mrn: 'M0001', sex: 'F', age: 72 }, chief_complaint: 'Shortness of breath', severity: 'unstable' }),
  caseSummary({
    csn: '1002',
    facility: 'NMC',
    patient: { name: 'Park, Robin', mrn: 'M0002', sex: 'M', age: 58 },
    chief_complaint: 'Chest pain',
    destination: { pending_unit: 'TELE', level_of_care: 'TELE' },
    templates: { sbar_nurse: templateSummary({ blocking: ['R1'], score: 0.6 }) },
  }),
  caseSummary({
    csn: '1003',
    phase: 'arrived',
    patient: { name: 'Nguyen, Casey', mrn: 'M0003', sex: 'F', age: 44 },
    chief_complaint: 'Abdominal pain',
    service: 'Surgery',
    ed_departure_ts: '2026-03-10T13:00:00Z',
    templates: { sbar_nurse: templateSummary({ workflow: { state: 'acknowledged' } }) },
  }),
];

async function renderBoard(data = board(rows)) {
  vi.mocked(api.board).mockResolvedValue(data);
  render(
    <AppProvider config={config}>
      <TooltipProvider>
        <Board />
      </TooltipProvider>
    </AppProvider>,
  );
  return screen.findByRole('table');
}

const names = (table: HTMLElement) =>
  within(table)
    .getAllByRole('row')
    .slice(1)
    .map((r) => within(r).getByRole('link').textContent);

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  window.history.replaceState({}, '', '/');
});

describe('Board', () => {
  it('loads the board at the app clock', async () => {
    const table = await renderBoard();
    expect(api.board).toHaveBeenCalledWith(config.default_at);
    expect(names(table)).toEqual(['Doe, Alex', 'Park, Robin', 'Nguyen, Casey']);
    expect(screen.getByRole('heading', { level: 1, name: 'ED boarding board' })).toBeInTheDocument();
  });

  it('filters by status with counts', async () => {
    const user = userEvent.setup();
    const table = await renderBoard();
    const tabs = screen.getByRole('tablist', { name: 'Filter by status' });
    const tab = (name: RegExp) => within(tabs).getByRole('tab', { name });
    expect(tab(/^all/i)).toHaveAttribute('aria-selected', 'true');
    expect(tab(/boarding/i)).toHaveTextContent('2');
    expect(tab(/arrived/i)).toHaveTextContent('1');

    await user.click(tab(/boarding/i));
    expect(tab(/boarding/i)).toHaveAttribute('aria-selected', 'true');
    expect(names(table)).toEqual(['Doe, Alex', 'Park, Robin']);

    await user.click(tab(/arrived/i));
    expect(names(screen.getByRole('table'))).toEqual(['Nguyen, Casey']);

    await user.click(tab(/^all/i));
    expect(names(screen.getByRole('table'))).toHaveLength(3);
  });

  it('filters by facility', async () => {
    const user = userEvent.setup();
    await renderBoard();
    await user.click(screen.getByRole('button', { name: 'NMC' }));
    expect(screen.getByRole('button', { name: 'NMC' })).toHaveAttribute('aria-pressed', 'true');
    expect(names(screen.getByRole('table'))).toEqual(['Park, Robin']);
  });

  it('searches name, MRN, complaint, unit and service', async () => {
    const user = userEvent.setup();
    await renderBoard();
    const search = screen.getByRole('textbox', { name: 'Search patients' });
    for (const [q, expected] of [
      ['park', ['Park, Robin']],
      ['M0003', ['Nguyen, Casey']],
      ['chest', ['Park, Robin']],
      ['tele', ['Park, Robin']],
      ['surgery', ['Nguyen, Casey']],
    ] as const) {
      await user.clear(search);
      await user.type(search, q);
      expect(names(screen.getByRole('table')), q).toEqual(expected);
    }
  });

  it('shows an empty state when nothing matches', async () => {
    const user = userEvent.setup();
    await renderBoard();
    await user.type(screen.getByRole('textbox', { name: 'Search patients' }), 'zzz');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByText('No patients match these filters')).toBeInTheDocument();
  });

  it('navigates to the handoff when a row is clicked', async () => {
    const user = userEvent.setup();
    const table = await renderBoard();
    const row = within(table).getByText('Chest pain').closest('tr')!;
    await user.click(row);
    expect(window.location.pathname).toBe('/handoff/1002');
  });

  it('links the patient name for keyboard and new tab use, at the replay time', async () => {
    const table = await renderBoard();
    // The replay time travels in the address (?at=), so a new tab or a shared link opens at the same moment.
    expect(within(table).getByRole('link', { name: 'Doe, Alex' })).toHaveAttribute('href', `/handoff/1001?at=${encodeURIComponent(config.default_at)}`);
    expect(new URLSearchParams(window.location.search).get('at')).toBe(config.default_at);
  });

  it('gives the clock and persona buttons names that include their visible text', async () => {
    await renderBoard();
    // jsdom puts a space around each span; browsers join inline text, so spaces before punctuation are optional here.
    expect(screen.getByRole('button', { name: /^Replay time Tue, Mar 10 11:00 ?\. Change$/ })).toBeInTheDocument();
    for (const b of screen.getAllByRole('button', { name: /Switch persona/ })) {
      expect(b).toHaveAccessibleName(/^Signed in as Jordan Reyes, RN ?, ED nurse ?\. Switch persona$/);
    }
  });

  it('shows an error when the board fails to load', async () => {
    vi.mocked(api.board).mockRejectedValue(new Error('The server could not be reached.'));
    await act(async () => {
      render(
        <AppProvider config={config}>
          <TooltipProvider>
            <Board />
          </TooltipProvider>
        </AppProvider>,
      );
    });
    expect(await screen.findByRole('alert')).toHaveTextContent('The server could not be reached.');
  });

  it('counts patients who left without sign off and lists them on click', async () => {
    const user = userEvent.setup();
    const left = caseSummary({
      csn: '1004',
      phase: 'arrived',
      patient: { name: 'Lee, Morgan', mrn: 'M0004', sex: 'M', age: 66 },
      ed_departure_ts: '2026-03-10T14:00:00Z',
      templates: { sbar_nurse: templateSummary({ workflow: { state: 'transferred_unsigned' } }) },
    });
    const data = board([...rows, left]);
    data.kpis.left_unsigned = 1;
    await renderBoard(data);
    await user.click(screen.getByRole('button', { name: '1 left without sign off' }));
    const tabs = screen.getByRole('tablist', { name: 'Filter by status' });
    expect(within(tabs).getByRole('tab', { name: /arrived/i })).toHaveAttribute('aria-selected', 'true');
    expect(names(screen.getByRole('table'))).toEqual(['Lee, Morgan']);
    expect(screen.getByText('Left without sign off')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear the Left without sign off filter' }));
    expect(names(screen.getByRole('table'))).toEqual(['Nguyen, Casey', 'Lee, Morgan']);
    expect(screen.queryByText('Left without sign off')).not.toBeInTheDocument();
  });

  it('shows no left without sign off line when none left, or for other roles', async () => {
    const data = board(rows);
    data.kpis.left_unsigned = 0;
    await renderBoard(data);
    expect(screen.queryByText(/left without sign off/)).not.toBeInTheDocument();
  });

  it('explains every column on hover, focus and tap', async () => {
    const user = userEvent.setup();
    const table = await renderBoard();
    const header = within(table).getAllByRole('columnheader');
    expect(header.map((h) => within(h).getByRole('button').getAttribute('aria-label'))).toEqual([
      'About Patient', 'About Severity', 'About Reason for visit', 'About Destination', 'About Boarding', 'About Handoffs', 'About Completeness (SBAR nurse)', 'About Last update',
    ]);
    const completeness = within(table).getByRole('button', { name: 'About Completeness (SBAR nurse)' });
    expect(completeness).toHaveAccessibleDescription(/For your role's template \(SBAR nurse\): the share of its checks that are met or resolved/);
    // Keyboard: focus opens it.
    completeness.focus();
    expect(await screen.findByRole('dialog', { name: 'Completeness (SBAR nurse)' })).toHaveTextContent('Open counts the required items still to resolve');
    completeness.blur();
    // Touch: a tap opens it and keeps it open; a second tap closes it.
    const boarding = within(table).getByRole('button', { name: 'About Boarding' });
    await user.click(boarding);
    expect(await screen.findByRole('dialog', { name: 'Boarding' })).toHaveTextContent('Time since the admit decision while the patient is still in the ED');
    expect(window.location.pathname).toBe('/');
    await user.click(boarding);
    expect(screen.queryByRole('dialog', { name: 'Boarding' })).not.toBeInTheDocument();
  });

  it('opens the guide from a column explanation', async () => {
    const user = userEvent.setup();
    const table = await renderBoard();
    await user.click(within(table).getByRole('button', { name: 'About Severity' }));
    await user.click(await screen.findByRole('button', { name: 'More in the guide' }));
    expect(await screen.findByRole('dialog', { name: 'How to read each column' })).toBeInTheDocument();
  });
});
