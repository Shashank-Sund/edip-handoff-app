import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppProvider } from '@/hooks/useApp';
import { AT, board, caseSummary, caseTemplate, config, draft, element } from '@/test/fixtures';
import { sinceElements, sinceLabel, sinceText } from '@/lib/since';
import type { CaseDetail, Since, SinceItem } from '@/lib/types';
import Board from './Board';
import Handoff from './Handoff';

vi.mock('@/lib/api', () => ({
  setPersona: vi.fn(),
  ApiError: class extends Error {},
  api: {
    board: vi.fn(), case: vi.fn(), act: vi.fn(), own: vi.fn(), seen: vi.fn(), colleagues: vi.fn(async () => []), reset: vi.fn(),
    patientTasks: vi.fn(async () => ({ at: '', csn: '', phase: 'boarding', tasks: [], counts: { open: 0, overdue: 0 }, access: { can_act: true, in_role: true, note: null }, people: [] })),
  },
}));
const { api } = await import('@/lib/api');

const LOOK = '2026-03-10T12:50:00Z';
const ITEMS: SinceItem[] = [
  { kind: 'action', action: 'sign', template: 'ipass_physician', by: 'Kai Moreno, MD', at: '2026-03-10T14:40:00Z', element: null, label: null, tasks: 4 },
  { kind: 'version', template: 'sbar_nurse', from: 1, to: 2, at: '2026-03-10T14:13:00Z', elements: [{ id: 'E06', label: 'Working diagnosis' }] },
  { kind: 'task', task: 'done', task_id: 4, text: 'Repeat lactate', by: 'Robin Asante, RN', at: '2026-03-10T13:30:00Z' },
  { kind: 'owner', owner: 'claim', role: 'ed_physician', to: 'Kai Moreno, MD', from: null, by: 'Kai Moreno, MD', at: '2026-03-10T13:00:00Z' },
];
const since = (over: Partial<Since> = {}): Since => ({ at: AT, count: ITEMS.length, last_look: LOOK, first_time: false, items: ITEMS, ...over });

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  vi.mocked(api.seen).mockReset();
  vi.mocked(api.seen).mockResolvedValue({ csn: '1001', at: AT, since: { count: 0, last_look: AT, first_time: false } });
});

describe('since words', () => {
  it('counts changes, says nothing for none or a first look, and words each change', () => {
    expect(sinceLabel({ count: 3, last_look: LOOK, first_time: false })).toBe('New · 3 changes');
    expect(sinceLabel({ count: 1, last_look: LOOK, first_time: false })).toBe('New · 1 change');
    expect(sinceLabel({ count: 0, last_look: LOOK, first_time: false })).toBeNull();
    expect(sinceLabel({ count: 0, last_look: null, first_time: true })).toBeNull();
    expect(ITEMS.map(sinceText)).toEqual([
      'Kai Moreno, MD signed the I-PASS (4 tasks added)',
      'A new SBAR draft (version 2): Working diagnosis changed',
      'Robin Asante, RN marked done "Repeat lactate"',
      'Kai Moreno, MD took the patient (physician)',
    ]);
    expect(sinceText({ kind: 'phase', phase: 'arrived', at: AT })).toBe('The patient arrived on the unit');
    expect(sinceText({ kind: 'severity', from: 'watcher', to: 'unstable', at: AT })).toBe('Severity changed from watcher to unstable');
    expect([...sinceElements(since(), 'sbar_nurse')]).toEqual(['E06']);
    expect(sinceText({ ...(ITEMS[0] as Extract<SinceItem, { kind: 'action' }>), tasks: 1 })).toBe('Kai Moreno, MD signed the I-PASS (1 task added)');
    expect(sinceText({ ...(ITEMS[0] as Extract<SinceItem, { kind: 'action' }>), tasks: undefined })).toBe('Kai Moreno, MD signed the I-PASS');
    const sev: SinceItem = { kind: 'severity', from: 'watcher', to: 'unstable', at: AT };
    expect([...sinceElements(since({ items: [sev] }), 'ipass_physician')]).toEqual(['E07']);
  });
});

describe('Board: since you looked', () => {
  const rows = [
    caseSummary({ csn: '1001', patient: { name: 'Doe, Alex', mrn: 'M0001', sex: 'F', age: 72 }, since: { count: 3, last_look: LOOK, first_time: false } }),
    caseSummary({ csn: '1002', patient: { name: 'Park, Robin', mrn: 'M0002', sex: 'M', age: 58 }, since: { count: 0, last_look: null, first_time: true } }),
    caseSummary({ csn: '1003', patient: { name: 'Nguyen, Casey', mrn: 'M0003', sex: 'F', age: 44 }, since: { count: 0, last_look: LOOK, first_time: false } }),
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
    await screen.findByRole('table');
    await userEvent.click(within(screen.getByRole('tablist', { name: 'Whose patients' })).getByRole('tab', { name: /Everyone/ }));
    return screen.getByRole('table');
  }
  const row = (table: HTMLElement, name: string) => within(table).getByRole('link', { name }).closest('tr')!;

  it('shows New with the count, Not opened yet, and nothing when nothing changed', async () => {
    const table = await renderBoard();
    expect(within(row(table, 'Doe, Alex')).getByText('New · 3 changes')).toBeInTheDocument();
    expect(within(row(table, 'Park, Robin')).getByText('Not opened yet')).toBeInTheDocument();
    expect(within(row(table, 'Park, Robin')).queryByRole('button', { name: /as seen/ })).toBeNull();
    const quiet = row(table, 'Nguyen, Casey');
    expect(within(quiet).queryByText(/New ·|Not opened yet/)).toBeNull();
    // Help on the marker and on Mark as seen lands on the guide.
    expect(within(row(table, 'Doe, Alex')).getByRole('button', { name: /^About Mark as seen: Mark as seen/ })).toBeInTheDocument();
  });

  it('Mark as seen records a look from the board at the board time and loads the board again', async () => {
    const user = userEvent.setup();
    const table = await renderBoard();
    vi.mocked(api.board).mockClear();
    await user.click(within(row(table, 'Doe, Alex')).getByRole('button', { name: 'Mark as seen: Doe, Alex' }));
    expect(api.seen).toHaveBeenCalledWith('1001', 'board', board(rows).at);
    expect(api.board).toHaveBeenCalled();
  });
});

describe('Handoff: since you last looked', () => {
  const detail = (s?: Since): CaseDetail => ({
    summary: caseSummary(),
    templates: {
      sbar_nurse: caseTemplate({
        draft: draft({
          sections: [{ id: 'S', label: 'Situation', elements: [
            element({ id: 'E06', label: 'Working diagnosis', kind: 'narrative', narrative: { text: 'Cellulitis.' } }),
            element({ id: 'E05', label: 'Chief complaint', kind: 'narrative', narrative: { text: 'Leg redness.' } }),
          ] }],
        }),
      }),
    },
    activity: [],
    ...(s ? { since: s } : {}),
  });
  async function open(d: CaseDetail) {
    vi.mocked(api.case).mockResolvedValue(d);
    vi.mocked(api.act).mockResolvedValue(detail(since({ count: 0, last_look: AT, items: [] })));
    render(
      <AppProvider config={config}>
        <TooltipProvider>
          <Handoff csn="1001" />
        </TooltipProvider>
      </AppProvider>,
    );
    await screen.findByRole('complementary', { name: 'Sign off and context' });
  }

  it('lists the changes since the look before, records this look once, and highlights the changed items', async () => {
    await open(detail(since()));
    const box = screen.getByRole('region', { name: /Since you last looked \(2 h 10 min ago\)/ });
    expect(within(box).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      '10:40Kai Moreno, MD signed the I-PASS (4 tasks added)',
      '10:13A new SBAR draft (version 2): Working diagnosis changed',
      '09:30Robin Asante, RN marked done "Repeat lactate"',
      '09:00Kai Moreno, MD took the patient (physician)',
    ]);
    expect(within(box).getByRole('button', { name: /^About Since you last looked: Since you last looked, on the handoff page/ })).toBeInTheDocument();
    expect(api.seen).toHaveBeenCalledTimes(1);
    expect(api.seen).toHaveBeenCalledWith('1001', 'handoff', AT);
    // The changed item has the What changed highlight and its marker; the other does not.
    const changed = document.getElementById('el-E06')!;
    expect(changed.className).toContain('before:bg-flag');
    expect(within(changed).getByText('New since you looked')).toBeInTheDocument();
    expect(document.getElementById('el-E05')!.className).not.toContain('before:bg-flag');
  });

  it('goes to a change: its item, with focus', async () => {
    const scroll = vi.fn();
    Element.prototype.scrollIntoView = scroll;
    const user = userEvent.setup();
    await open(detail(since()));
    await user.click(screen.getByRole('button', { name: /A new SBAR draft/ }));
    await vi.waitFor(() => expect(document.activeElement?.id).toBe('el-E06'));
    expect(scroll).toHaveBeenCalled();
  });

  it('keeps the box after an action on the page, whose answer (and the reload) is against this look', async () => {
    const user = userEvent.setup();
    await open(detail(since()));
    vi.mocked(api.case).mockResolvedValue(detail(since({ count: 0, last_look: AT, items: [] })));
    await user.click(screen.getByRole('button', { name: 'Edit Chief complaint' }));
    await user.type(screen.getByRole('textbox', { name: 'Edit Chief complaint' }), ' Worse today.');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Saved');
    expect(api.act).toHaveBeenCalled();
    await vi.waitFor(() => expect(api.case).toHaveBeenCalledTimes(2));
    expect(screen.getByRole('region', { name: /Since you last looked/ })).toBeInTheDocument();
    expect(api.seen).toHaveBeenCalledTimes(1);
  });

  it('shows no box on a first look or when nothing changed, and records the look', async () => {
    await open(detail(since({ count: 0, last_look: null, first_time: true, items: [] })));
    expect(screen.queryByRole('region', { name: /Since you last looked/ })).toBeNull();
    expect(api.seen).toHaveBeenCalledWith('1001', 'handoff', AT);
  });
});
