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

async function renderBoard() {
  vi.mocked(api.board).mockResolvedValue(board(rows));
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

  it('links the patient name for keyboard and new tab use', async () => {
    const table = await renderBoard();
    expect(within(table).getByRole('link', { name: 'Doe, Alex' })).toHaveAttribute('href', '/handoff/1001');
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
});
