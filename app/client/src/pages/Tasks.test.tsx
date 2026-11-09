/** Tasks: the panel on the handoff page (every action, the outside role note, the viewer, the earlier time), the Tasks
 *  page (groups, scopes, recently completed), the board count, and the words for due times. */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppProvider } from '@/hooks/useApp';
import { TasksPanel } from '@/components/handoff/TasksPanel';
import { TaskCount } from '@/components/common/TaskCount';
import { READ_ONLY } from '@/lib/checks';
import { dueLabel, nextAt, stepLabel, taskCountLabel } from '@/lib/tasks';
import { AT, board, caseSummary, config, edNurse } from '@/test/fixtures';
import type { AppConfig, PatientTasks, Persona, Task, TaskRow, TasksPage } from '@/lib/types';
import Board from './Board';
import Tasks from './Tasks';

vi.mock('@/lib/api', () => ({
  setPersona: vi.fn(),
  ApiError: class extends Error {},
  api: { patientTasks: vi.fn(), addTask: vi.fn(), changeTask: vi.fn(), tasks: vi.fn(), board: vi.fn(), own: vi.fn(), reset: vi.fn() },
}));
const { api, ApiError } = await import('@/lib/api');

const task = (over: Partial<Task> = {}): Task => ({
  id: 1, csn: '1001', text: 'Repeat lactate', role: 'nurse', assignee: null, due: '2026-03-10T16:00:00Z', due_text: '16:00', overdue: false,
  status: 'open', reason: null, source: 'handoff', template: 'sbar_nurse', version: 2, owner_text: 'RN',
  created: { by: 'Jordan Reyes, RN', at: '2026-03-10T14:00:00Z' }, closed: null, undo: null,
  history: [{ kind: 'create', by: 'Jordan Reyes, RN', by_role: 'ed_nurse', at: '2026-03-10T14:00:00Z' }], ...over,
});
const people = [
  { id: 'ed-rn-2', name: 'Robin Asante, RN', role: 'nurse' as const, role_label: 'ED nurse' },
  { id: 'hosp-md', name: 'Morgan Patel, MD', role: 'physician' as const, role_label: 'Admitting physician' },
];
const tasksOf = (list: Task[], over: Partial<PatientTasks> = {}): PatientTasks => ({
  at: AT, csn: '1001', phase: 'boarding', tasks: list,
  counts: { open: list.filter((t) => t.status === 'open').length, overdue: list.filter((t) => t.overdue).length },
  access: { can_act: true, in_role: true, note: null }, people, ...over,
});
const viewer: Persona = { ...edNurse, id: 'viewer', name: 'Viewer', role: 'viewer', role_label: 'Viewer (read only)', can_sign: [] };
const unitNurse: Persona = { ...edNurse, id: 'unit-sgh-micu', name: 'Sam Okafor, RN', role: 'unit_nurse', role_label: 'Receiving nurse', unit: 'MICU', facility: 'SGH', can_sign: [], can_ack: ['sbar_nurse'] };
const live: AppConfig = { ...config, mode: 'live', clock: 'live', demo_personas: false, past_view_hours: 72 };

function wrap(ui: React.ReactNode, cfg: AppConfig = config) {
  return render(
    <AppProvider config={cfg}>
      <TooltipProvider>{ui}</TooltipProvider>
    </AppProvider>,
  );
}

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  window.history.replaceState({}, '', '/');
  window.localStorage.clear();
  vi.mocked(api.changeTask).mockReset();
  vi.mocked(api.addTask).mockReset();
});

describe('Tasks panel', () => {
  it('lists open tasks with who, when and where from, folds the closed ones, and counts open and overdue', async () => {
    const user = userEvent.setup();
    vi.mocked(api.patientTasks).mockResolvedValue(tasksOf([
      task(),
      task({ id: 2, text: 'Order a repeat ECG', role: 'physician', assignee: { id: 'ed-md', name: 'Alex Chen, MD' }, due: '2026-03-10T14:30:00Z', overdue: true, source: 'added', template: null }),
      task({ id: 3, text: 'Daily weights', due: null, due_text: 'daily' }),
      task({ id: 4, text: 'Call the family', status: 'not_needed', reason: 'Spoke at the bedside', closed: { by: 'Kai Moreno, MD', at: '2026-03-10T14:50:00Z' }, undo: 'not_needed' }),
    ]));
    wrap(<TasksPanel csn="1001" />);
    const panel = await screen.findByRole('region', { name: 'Tasks' });
    expect(panel).toHaveTextContent('3 open · 1 overdue');
    const open = within(panel).getByRole('list', { name: 'Open tasks' });
    const first = within(open).getByRole('listitem', { name: 'Repeat lactate' });
    expect(first).toHaveTextContent('Nurse');
    expect(first).toHaveTextContent('From the SBAR action list, signed by Jordan Reyes, RN');
    const ecg = within(open).getByRole('listitem', { name: 'Order a repeat ECG' });
    expect(ecg).toHaveTextContent('Alex Chen, MD');
    expect(ecg).toHaveTextContent('Overdue, was due');
    expect(ecg).toHaveTextContent('Added by Jordan Reyes, RN');
    expect(within(open).getByRole('listitem', { name: 'Daily weights' })).toHaveTextContent('"daily", no set time');
    expect(within(panel).queryByText('Call the family')).toBeNull();
    await user.click(within(panel).getByRole('button', { name: 'Done and not needed (1)' }));
    const closed = within(panel).getByRole('listitem', { name: 'Call the family' });
    expect(closed).toHaveTextContent('Not needed: Spoke at the bedside');
    expect(within(closed).getByRole('button', { name: 'Undo not needed' })).toBeEnabled();
    expect(within(closed).queryByRole('button', { name: 'Done' })).toBeNull();
    expect(within(panel).getByRole('button', { name: /About tasks/ })).toBeInTheDocument();
  });

  it('puts overdue first, then by due time, and shows five until Show all', async () => {
    const user = userEvent.setup();
    const hour = (h: number) => new Date(Date.parse(AT) + h * 3600_000).toISOString();
    vi.mocked(api.patientTasks).mockResolvedValue(tasksOf([
      task({ id: 1, text: 'No time', due: null }),
      task({ id: 2, text: 'In three hours', due: hour(3) }),
      task({ id: 3, text: 'Overdue', due: hour(-1), overdue: true }),
      task({ id: 4, text: 'In one hour', due: hour(1) }),
      task({ id: 5, text: 'Also no time', due: null }),
      task({ id: 6, text: 'In two hours', due: hour(2) }),
    ]));
    wrap(<TasksPanel csn="1001" />);
    const panel = await screen.findByRole('region', { name: 'Tasks' });
    const names = () => within(within(panel).getByRole('list', { name: 'Open tasks' })).getAllByRole('listitem').map((li) => li.getAttribute('aria-label'));
    expect(names()).toEqual(['Overdue', 'In one hour', 'In two hours', 'In three hours', 'No time']);
    await user.click(within(panel).getByRole('button', { name: 'Show all 6 open tasks' }));
    expect(names()).toEqual(['Overdue', 'In one hour', 'In two hours', 'In three hours', 'No time', 'Also no time']);
  });

  it('marks a task done, not needed with a reason, reassigns it, changes its due time and undoes', async () => {
    const user = userEvent.setup();
    vi.mocked(api.patientTasks).mockResolvedValue(tasksOf([task()]));
    vi.mocked(api.changeTask).mockResolvedValue(tasksOf([task({ status: 'done', closed: { by: 'Jordan Reyes, RN', at: AT }, undo: 'done' })]));
    wrap(<TasksPanel csn="1001" />);
    const item = await screen.findByRole('listitem', { name: 'Repeat lactate' });
    await user.click(within(item).getByRole('button', { name: 'Not needed' }));
    const save = within(item).getByRole('button', { name: 'Save' });
    expect(save).toBeDisabled();                                   // a reason is required
    await user.type(within(item).getByLabelText('Why is it not needed?'), 'Done before transfer');
    vi.mocked(api.changeTask).mockResolvedValueOnce(tasksOf([task()]));
    await user.click(save);
    expect(api.changeTask).toHaveBeenLastCalledWith(1, { kind: 'not_needed', reason: 'Done before transfer' }, AT);
    await user.click(within(item).getByRole('button', { name: 'Reassign' }));
    await user.selectOptions(within(item).getByLabelText('Give it to'), 'hosp-md');
    vi.mocked(api.changeTask).mockResolvedValueOnce(tasksOf([task()]));
    await user.click(within(item).getByRole('button', { name: 'Save' }));
    expect(api.changeTask).toHaveBeenLastCalledWith(1, { kind: 'reassign', to: 'hosp-md' }, AT);
    await user.click(within(item).getByRole('button', { name: 'Reassign' }));
    await user.selectOptions(within(item).getByLabelText('Give it to'), 'physician');
    vi.mocked(api.changeTask).mockResolvedValueOnce(tasksOf([task()]));
    await user.click(within(item).getByRole('button', { name: 'Save' }));
    expect(api.changeTask).toHaveBeenLastCalledWith(1, { kind: 'reassign', role: 'physician' }, AT);
    await user.click(within(item).getByRole('button', { name: 'Due time' }));
    await user.type(within(item).getByLabelText(/Due at/), '18:30');
    vi.mocked(api.changeTask).mockResolvedValueOnce(tasksOf([task()]));
    await user.click(within(item).getByRole('button', { name: 'Set due time' }));
    expect(api.changeTask).toHaveBeenLastCalledWith(1, { kind: 'due', due: nextAt('18:30', AT) }, AT);
    await user.click(within(item).getByRole('button', { name: 'Due time' }));
    vi.mocked(api.changeTask).mockResolvedValueOnce(tasksOf([task({ due: null })]));
    await user.click(within(item).getByRole('button', { name: 'No due time' }));
    expect(api.changeTask).toHaveBeenLastCalledWith(1, { kind: 'due', due: null }, AT);
    // After a change the panel loads the list again: it has the task done now.
    vi.mocked(api.patientTasks).mockResolvedValue(tasksOf([task({ status: 'done', closed: { by: 'Jordan Reyes, RN', at: AT }, undo: 'done' })]));
    await user.click(within(await screen.findByRole('listitem', { name: 'Repeat lactate' })).getByRole('button', { name: 'Done' }));
    expect(api.changeTask).toHaveBeenLastCalledWith(1, { kind: 'done' }, AT);
    expect(await screen.findByRole('status')).toHaveTextContent('Done: Repeat lactate');
    const panel = screen.getByRole('region', { name: 'Tasks' });
    await user.click(within(panel).getByRole('button', { name: 'Done and not needed (1)' }));
    await user.click(within(panel).getByRole('button', { name: 'Undo done' }));
    expect(api.changeTask).toHaveBeenLastCalledWith(1, { kind: 'undo' }, AT);
  });

  it('adds a task for the role or a colleague, with a time, and says why the server refused', async () => {
    const user = userEvent.setup();
    vi.mocked(api.patientTasks).mockResolvedValue(tasksOf([]));
    wrap(<TasksPanel csn="1001" />);
    const panel = await screen.findByRole('region', { name: 'Tasks' });
    expect(panel).toHaveTextContent('No open tasks.');
    await user.click(within(panel).getByRole('button', { name: 'Add a task' }));
    const form = within(panel).getByRole('form', { name: 'Add a task' });
    expect(within(form).getByRole('button', { name: 'Add task' })).toBeDisabled();
    await user.type(within(form).getByLabelText('What needs to be done'), 'Bring glasses from home');
    await user.selectOptions(within(form).getByLabelText('For'), 'ed-rn-2');
    await user.type(within(form).getByLabelText(/Due at/), '17:00');
    vi.mocked(api.addTask).mockResolvedValueOnce(tasksOf([task({ text: 'Bring glasses from home', source: 'added' })]));
    vi.mocked(api.patientTasks).mockResolvedValue(tasksOf([task({ text: 'Bring glasses from home', source: 'added' })]));
    await user.click(within(form).getByRole('button', { name: 'Add task' }));
    expect(api.addTask).toHaveBeenLastCalledWith('1001', { text: 'Bring glasses from home', to: 'ed-rn-2', due: nextAt('17:00', AT) }, AT);
    expect(await within(panel).findByRole('listitem', { name: 'Bring glasses from home' })).toBeInTheDocument();
    await user.click(within(panel).getByRole('button', { name: 'Add a task' }));
    await user.type(within(panel).getByLabelText('What needs to be done'), 'x');
    vi.mocked(api.addTask).mockRejectedValueOnce(new ApiError('You are viewing an earlier time; move the clock to now to act.', 409));
    await user.click(within(panel).getByRole('button', { name: 'Add task' }));
    expect(await within(panel).findByRole('alert')).toHaveTextContent('You are viewing an earlier time');
    expect(api.addTask).toHaveBeenLastCalledWith('1001', { text: 'x', role: 'nurse' }, AT);
  });

  it('says when acting is outside your role, and lets you act', async () => {
    const note = 'The ED nurse and physician have this patient while they board. You can still act on a task; it is recorded as outside your role.';
    vi.mocked(api.patientTasks).mockResolvedValue(tasksOf([task()], { access: { can_act: true, in_role: false, note } }));
    window.localStorage.setItem('edip.persona', unitNurse.id);
    wrap(<TasksPanel csn="1001" />, { ...config, personas: [edNurse, unitNurse] });
    const panel = await screen.findByRole('region', { name: 'Tasks' });
    expect(panel).toHaveTextContent(note);
    expect(within(panel).getByRole('button', { name: 'Done' })).toBeEnabled();
  });

  it('is read only for the viewer: no buttons work and nothing can be added', async () => {
    vi.mocked(api.patientTasks).mockResolvedValue(tasksOf([task()], { access: { can_act: false, in_role: false, note: 'The viewer role can read tasks but not change them.' } }));
    window.localStorage.setItem('edip.persona', 'viewer');
    wrap(<TasksPanel csn="1001" />, { ...config, personas: [edNurse, viewer] });
    const panel = await screen.findByRole('region', { name: 'Tasks' });
    expect(panel).toHaveTextContent('The viewer role can read tasks but not change them.');
    for (const name of ['Done', 'Not needed', 'Reassign', 'Due time']) expect(within(panel).getByRole('button', { name })).toBeDisabled();
    expect(within(panel).queryByRole('button', { name: 'Add a task' })).toBeNull();
  });

  it('turns the buttons off while the live app shows an earlier time, and says why', async () => {
    vi.mocked(api.patientTasks).mockResolvedValue(tasksOf([task()]));
    window.history.replaceState({}, '', `/?at=${encodeURIComponent(new Date(Date.now() - 3600_000).toISOString())}`);
    wrap(<TasksPanel csn="1001" />, live);
    const panel = await screen.findByRole('region', { name: 'Tasks' });
    expect(panel).toHaveTextContent(READ_ONLY);
    expect(within(panel).getByRole('button', { name: 'Done' })).toBeDisabled();
    expect(within(panel).getByRole('button', { name: 'Add a task' })).toBeDisabled();
  });
});

const row = (over: Partial<Task> = {}, pat: Partial<TaskRow> = {}): TaskRow => ({
  ...task(over), patient: { name: 'Doe, Alex', mrn: 'M0001', sex: 'F', age: 72 }, phase: 'boarding', facility: 'SGH', unit: 'MICU',
  severity: 'watcher', in_role: true, ...pat,
});
const page = (over: Partial<TasksPage> = {}): TasksPage => ({
  at: AT, scope: 'mine', role: 'nurse',
  groups: [
    { id: 'overdue', tasks: [row({ id: 7, text: 'Send the urine sample', overdue: true, due: '2026-03-10T14:00:00Z' })] },
    { id: 'soon', tasks: [row({ id: 8, text: 'Recheck blood pressure' })] },
    { id: 'later', tasks: [row({ id: 9, text: 'Confirm the bed', due: '2026-03-10T20:00:00Z' }, { patient: { name: 'Park, Robin', mrn: 'M0002', sex: 'M', age: 58 }, csn: '1002' })] },
    { id: 'none', tasks: [row({ id: 10, text: 'Bring the medication list', due: null, due_text: null })] },
  ],
  done: [row({ id: 11, text: 'Check the IV site', status: 'done', closed: { by: 'Jordan Reyes, RN', at: AT }, undo: 'done' })],
  counts: { open: 4, overdue: 1 }, ...over,
});

describe('Tasks page', () => {
  it('groups your open tasks by when they are due, each under its patient, with recently completed folded', async () => {
    const user = userEvent.setup();
    vi.mocked(api.tasks).mockResolvedValue(page());
    wrap(<Tasks />);
    const groups = await screen.findAllByRole('region');
    expect(groups.map((g) => g.getAttribute('aria-label'))).toEqual(['Overdue', 'Due in the next 2 hours', 'Later', 'No time']);
    expect(api.tasks).toHaveBeenLastCalledWith('mine', AT);
    const overdue = within(groups[0]).getByRole('listitem', { name: 'Send the urine sample' });
    expect(overdue).toHaveTextContent('Overdue, was due');
    expect(within(overdue).getByRole('link', { name: 'Doe, Alex' })).toHaveAttribute('href', expect.stringContaining('/handoff/1001'));
    expect(within(groups[2]).getByRole('link', { name: 'Park, Robin' })).toBeInTheDocument();
    expect(within(groups[3]).getByRole('listitem', { name: 'Bring the medication list' })).toHaveTextContent('No due time');
    expect(screen.queryByText('Check the IV site')).toBeNull();
    await user.click(screen.getByRole('button', { name: /Recently completed/ }));
    expect(screen.getByRole('listitem', { name: 'Check the IV site' })).toHaveTextContent('Done by Jordan Reyes, RN');
    expect(screen.getByRole('button', { name: /About this page/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /About overdue/ })).toBeInTheDocument();
  });

  it('switches to Everyone, acts on a task and reloads the list', async () => {
    const user = userEvent.setup();
    vi.mocked(api.tasks).mockResolvedValue(page());
    wrap(<Tasks />);
    await screen.findAllByRole('region');
    await user.click(screen.getByRole('tab', { name: 'Everyone' }));
    expect(api.tasks).toHaveBeenLastCalledWith('all', AT);
    vi.mocked(api.changeTask).mockResolvedValueOnce(tasksOf([]));
    vi.mocked(api.tasks).mockResolvedValue(page({ groups: page().groups.slice(1), counts: { open: 3, overdue: 0 } }));
    await user.click(within(screen.getByRole('listitem', { name: 'Send the urine sample' })).getByRole('button', { name: 'Done' }));
    expect(api.changeTask).toHaveBeenLastCalledWith(7, { kind: 'done' }, AT);
    expect(await screen.findByRole('status')).toHaveTextContent('Done: Send the urine sample');
    expect(screen.queryByRole('region', { name: 'Overdue' })).toBeNull();
  });

  it('loads who a task can go to only when Reassign opens', async () => {
    const user = userEvent.setup();
    vi.mocked(api.tasks).mockResolvedValue(page());
    vi.mocked(api.patientTasks).mockResolvedValue(tasksOf([]));
    wrap(<Tasks />);
    const item = await screen.findByRole('listitem', { name: 'Recheck blood pressure' });
    vi.mocked(api.patientTasks).mockClear();
    await user.click(within(item).getByRole('button', { name: 'Reassign' }));
    expect(api.patientTasks).toHaveBeenCalledWith('1001', AT);
    expect(await within(item).findByRole('option', { name: 'Morgan Patel, MD, Admitting physician' })).toBeInTheDocument();
  });

  it('says when you have none, and opens the viewer on Everyone, read only', async () => {
    vi.mocked(api.tasks).mockResolvedValue(page({ groups: page().groups.map((g) => ({ ...g, tasks: [] })), done: [], counts: { open: 0, overdue: 0 } }));
    window.localStorage.setItem('edip.persona', 'viewer');
    wrap(<Tasks />, { ...config, personas: [edNurse, viewer] });
    expect(await screen.findByText('No open tasks')).toBeInTheDocument();
    expect(api.tasks).toHaveBeenLastCalledWith('all', AT);
    expect(screen.getByText('The viewer role can read tasks but not change them.')).toBeInTheDocument();
  });
});

describe('Board task count', () => {
  it('shows open and overdue tasks on the row, and nothing when none is open', async () => {
    vi.mocked(api.board).mockResolvedValue(board([
      caseSummary({ csn: '1001', tasks: { open: 3, overdue: 1 } }),
      caseSummary({ csn: '1002', patient: { name: 'Park, Robin', mrn: 'M0002', sex: 'M', age: 58 }, tasks: { open: 0, overdue: 0 } }),
    ]));
    wrap(<Board />);
    const table = await screen.findByRole('table');
    await userEvent.click(within(screen.getByRole('tablist', { name: 'Whose patients' })).getByRole('tab', { name: /Everyone/ }));
    const r1 = within(table).getByRole('link', { name: 'Doe, Alex' }).closest('tr')!;
    expect(r1).toHaveTextContent('3 tasks · 1 overdue');
    expect(within(r1).getByRole('button', { name: '3 tasks · 1 overdue' })).toBeInTheDocument();
    const r2 = within(table).getByRole('link', { name: 'Park, Robin' }).closest('tr')!;
    expect(r2).not.toHaveTextContent(/\d tasks?/);
  });

  it('says tasks, not open (the Completeness column says open), singular for one, and nothing when none is open', () => {
    expect(taskCountLabel({ open: 8, overdue: 2 })).toBe('8 tasks · 2 overdue');
    expect(taskCountLabel({ open: 3, overdue: 0 })).toBe('3 tasks');
    expect(taskCountLabel({ open: 1, overdue: 1 })).toBe('1 task · 1 overdue');
    const { container } = render(<TaskCount counts={{ open: 1, overdue: 0 }} plain />);
    expect(container).toHaveTextContent(/^1 task$/);
    const none = render(<TaskCount counts={{ open: 0, overdue: 0 }} />);
    expect(none.container).toBeEmptyDOMElement();
  });
});

describe('task words', () => {
  it('names the due time, the action list words, and each change', () => {
    expect(dueLabel(task({ due: null, due_text: null }), AT)).toBe('No due time');
    expect(dueLabel(task({ due: null, due_text: 'q4h' }), AT)).toBe('"q4h", no set time');
    expect(dueLabel(task({ overdue: true, due: '2026-03-10T14:30:00Z' }), AT)).toBe('Overdue, was due 10:30');
    expect(dueLabel(task({ due: '2026-03-11T13:00:00Z' }), AT)).toBe('Due Wed 09:00');
    expect(stepLabel({ kind: 'not_needed', by: 'Kai Moreno, MD', by_role: 'ed_physician', at: AT, reason: 'Spoke with them', outside_role: true }))
      .toBe('Not needed by Kai Moreno, MD at 11:00 (outside their role): Spoke with them');
    expect(stepLabel({ kind: 'reassign', by: 'A', by_role: 'ed_nurse', at: AT, role: 'physician', to: null, undo: true })).toBe('Given back (Undo) to the physician by A at 11:00');
  });

  it('takes a time of day as the next time the hospital clock shows it', () => {
    // AT is 11:00 in New York (15:00 UTC, daylight saving time).
    expect(nextAt('12:30', AT)).toBe('2026-03-10T16:30:00.000Z');
    expect(nextAt('11:00', AT)).toBe('2026-03-10T15:00:00.000Z');
    expect(nextAt('09:00', AT)).toBe('2026-03-11T13:00:00.000Z');
    expect(nextAt('later', AT)).toBeNull();
  });
});
