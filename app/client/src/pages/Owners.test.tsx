/** Who owns each patient: the board's My patients and Everyone, the shift change bar, the handoff owner line, Take
 *  over, the sidebar entry, and the read only past view. */
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppProvider } from '@/hooks/useApp';
import { SidebarBody } from '@/components/layout/Sidebar';
import { READ_ONLY } from '@/lib/checks';
import { board, caseSummary, caseTemplate, config, edNurse } from '@/test/fixtures';
import type { AppConfig, CaseDetail, Owner, Persona, Takeover as TakeoverData } from '@/lib/types';
import Analytics from './Analytics';
import Board from './Board';
import Handoff from './Handoff';
import Takeover from './Takeover';

vi.mock('@/lib/api', () => ({
  setPersona: vi.fn(),
  ApiError: class extends Error {},
  api: { board: vi.fn(), case: vi.fn(), act: vi.fn(), own: vi.fn(), colleagues: vi.fn(), takeover: vi.fn(), takeOver: vi.fn(), analytics: vi.fn(), reset: vi.fn() },
}));
const { api } = await import('@/lib/api');

const owner = (over: Partial<Owner> = {}): Owner => ({
  id: 'ed-rn', name: 'Jordan Reyes, RN', since: '2026-03-10T14:00:00Z', last_active: '2026-03-10T14:00:00Z', stale: false, you: true, ...over,
});
const robin = owner({ id: 'ed-rn-2', name: 'Robin Asante, RN', you: false });
const unitNurse: Persona = { ...edNurse, id: 'unit-sgh-micu', name: 'Sam Okafor, RN', role: 'unit_nurse', role_label: 'Receiving nurse', unit: 'MICU', facility: 'SGH', can_sign: [], can_ack: ['sbar_nurse'] };
const live: AppConfig = { ...config, mode: 'live', clock: 'live', demo_personas: false, past_view_hours: 72 };

const rows = [
  caseSummary({ csn: '1001', patient: { name: 'Doe, Alex', mrn: 'M0001', sex: 'F', age: 72 }, owners: { ed_nurse: owner(), ed_physician: null } }),
  caseSummary({ csn: '1002', patient: { name: 'Park, Robin', mrn: 'M0002', sex: 'M', age: 58 }, owners: { ed_nurse: null, ed_physician: null } }),
  caseSummary({ csn: '1003', patient: { name: 'Lee, Sam', mrn: 'M0003', sex: 'M', age: 61 }, owners: { ed_nurse: robin, ed_physician: null } }),
  // A stale owner counts as Unassigned.
  caseSummary({ csn: '1004', patient: { name: 'Kim, Ari', mrn: 'M0004', sex: 'F', age: 39 }, owners: { ed_nurse: owner({ stale: true }), ed_physician: null } }),
];
const data = { ...board(rows), kpis: { ...board(rows).kpis, mine: 1, unassigned: 2 }, shift: { at: '2026-03-10T23:00:00Z', minutes: 20, warning: true, mine: 1, unsigned: 1 } };

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
});

const groupRows = (table: HTMLElement) =>
  within(table).getAllByRole('row').slice(1).map((r) => r.querySelector('th')?.textContent?.split(/\d/)[0] ?? within(r).getByRole('link').textContent);

describe('Board owners', () => {
  it('opens on My patients: Unassigned first, then yours, and Everyone shows every patient', async () => {
    const user = userEvent.setup();
    vi.mocked(api.board).mockResolvedValue(data);
    wrap(<Board />);
    const table = await screen.findByRole('table');
    const tabs = screen.getByRole('tablist', { name: 'Whose patients' });
    expect(within(tabs).getByRole('tab', { name: /My patients/ })).toHaveAttribute('aria-selected', 'true');
    expect(within(tabs).getByRole('tab', { name: /My patients/ })).toHaveTextContent('1');
    expect(groupRows(table)).toEqual(['Unassigned', 'Park, Robin', 'Kim, Ari', 'Your patients', 'Doe, Alex']);
    await user.click(within(tabs).getByRole('tab', { name: /Everyone/ }));
    expect(groupRows(screen.getByRole('table'))).toEqual(['Doe, Alex', 'Park, Robin', 'Lee, Sam', 'Kim, Ari']);
  });

  it('shows the owners on each row, a stale owner as Unassigned', async () => {
    vi.mocked(api.board).mockResolvedValue(data);
    wrap(<Board />);
    const table = await screen.findByRole('table');
    const row = (name: string) => within(table).getByRole('link', { name }).closest('tr')!;
    expect(row('Doe, Alex')).toHaveTextContent('RN:Nurse owner: Jordan Reyes (you)MD:Physician owner: Unassigned');
    expect(row('Kim, Ari')).toHaveTextContent('RN:Nurse owner: Unassigned');
  });

  it('takes a patient from Unassigned without opening it', async () => {
    const user = userEvent.setup();
    vi.mocked(api.board).mockResolvedValue(data);
    vi.mocked(api.own).mockResolvedValue({ summary: rows[1], templates: {}, activity: [] } as CaseDetail);
    wrap(<Board />);
    await screen.findByRole('table');
    await user.click(screen.getAllByRole('button', { name: 'Take Park, Robin' })[0]);
    expect(api.own).toHaveBeenCalledWith('1002', { kind: 'claim' }, config.default_at);
    expect(await screen.findByText('Park, Robin is now yours.')).toHaveAttribute('role', 'status');
    expect(window.location.pathname).toBe('/');
  });

  it('shows the shift change bar with the count and a link to Take over', async () => {
    vi.mocked(api.board).mockResolvedValue(data);
    wrap(<Board />);
    await screen.findByRole('table');
    expect(screen.getByText(/^Shift change at \d\d:\d\d: 1 of your patients has a handoff that needs your signature\.$/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Take over from a colleague' }).getAttribute('href')).toMatch(/^\/takeover\?at=/);
    expect(screen.getByRole('button', { name: /About shift change/ })).toBeInTheDocument();
  });

  it('gives other roles the owners but no My patients tab and no bar', async () => {
    vi.mocked(api.board).mockResolvedValue({ ...data, shift: null });
    wrap(<Board />, { ...config, personas: [unitNurse] });
    const table = await screen.findByRole('table');
    expect(screen.queryByRole('tablist', { name: 'Whose patients' })).toBeNull();
    expect(within(table).getByRole('link', { name: 'Lee, Sam' }).closest('tr')).toHaveTextContent('Robin Asante');
    expect(screen.queryByRole('button', { name: /^Take / })).toBeNull();
  });
});

describe('Handoff owner line', () => {
  const detail = (owners: CaseDetail['summary']['owners'], phase: 'boarding' | 'arrived' = 'boarding'): CaseDetail => ({
    summary: caseSummary({ owners, phase }), templates: { sbar_nurse: caseTemplate() }, activity: [],
  });
  const colleagues = [{ id: 'ed-rn', name: 'Jordan Reyes, RN', you: true }, { id: 'ed-rn-2', name: 'Robin Asante, RN', you: false }];

  async function open(d: CaseDetail, cfg: AppConfig = config) {
    vi.mocked(api.case).mockResolvedValue(d);
    vi.mocked(api.own).mockResolvedValue(d);
    vi.mocked(api.colleagues).mockResolvedValue(colleagues);
    wrap(<Handoff csn="1001" />, cfg);
    return screen.findByRole('group', { name: 'Who owns this patient' });
  }

  it('says quietly who it is assigned to, and still lets you take it', async () => {
    const user = userEvent.setup();
    const line = await open(detail({ ed_nurse: robin, ed_physician: null }));
    expect(line).toHaveTextContent('Nurse owner: Robin Asante');
    expect(line).toHaveTextContent('Physician owner: Unassigned');
    expect(line).toHaveTextContent(/Assigned to Robin Asante, RN since .+ You can still act on this handoff; it is recorded as outside your assignment\./);
    expect(within(line).queryByRole('button', { name: 'Release' })).toBeNull();
    await user.click(within(line).getByRole('button', { name: 'Take' }));
    expect(api.own).toHaveBeenCalledWith('1001', { kind: 'claim' }, config.default_at);
    expect(await screen.findByText('You took this patient.')).toBeInTheDocument();
  });

  it('assigns to a colleague from a list of your role, without you or the owner', async () => {
    const user = userEvent.setup();
    const line = await open(detail({ ed_nurse: owner(), ed_physician: null }));
    expect(within(line).queryByRole('button', { name: 'Take' })).toBeNull();
    await user.click(within(line).getByRole('button', { name: /Assign to a colleague/ }));
    const menu = await screen.findByRole('menu');
    expect(within(menu).getAllByRole('menuitem').map((m) => m.textContent)).toEqual(['Robin Asante, RN']);
    await user.click(within(menu).getByRole('menuitem', { name: 'Robin Asante, RN' }));
    expect(api.own).toHaveBeenCalledWith('1001', { kind: 'assign', to: 'ed-rn-2' }, config.default_at);
    await user.click(within(line).getByRole('button', { name: 'Release' }));
    expect(api.own).toHaveBeenLastCalledWith('1001', { kind: 'release' }, config.default_at);
  });

  it('names a stale owner and how long since they acted', async () => {
    const line = await open(detail({ ed_nurse: owner({ stale: true, last_active: '2026-03-10T00:00:00Z' }), ed_physician: null }));
    expect(line).toHaveTextContent('Nurse owner: Unassigned (Jordan Reyes has not acted on this patient for 15 h');
  });

  it('turns the buttons off after the patient left the ED', async () => {
    const line = await open(detail({ ed_nurse: null, ed_physician: null }, 'arrived'));
    expect(within(line).getByRole('button', { name: 'Take' })).toBeDisabled();
    expect(line).toHaveTextContent('The patient has left the ED, so owners no longer change.');
  });

  it('turns the buttons off while the live app shows an earlier time', async () => {
    const t = new Date(Math.floor((Date.now() - 4 * 3600000) / 3600000) * 3600000).toISOString();
    window.history.replaceState({}, '', `/handoff/1001?at=${encodeURIComponent(t)}`);
    const line = await open(detail({ ed_nurse: robin, ed_physician: null }), { ...live, personas: [edNurse] });
    expect(api.case).toHaveBeenLastCalledWith('1001', t);
    expect(within(line).getByRole('button', { name: 'Take' })).toBeDisabled();
    expect(within(line).getByRole('button', { name: /Assign to a colleague/ })).toBeDisabled();
  });

  it('shows the owners to a receiving nurse without buttons', async () => {
    const line = await open(detail({ ed_nurse: robin, ed_physician: null }), { ...config, personas: [unitNurse] });
    expect(line).toHaveTextContent('Nurse owner: Robin Asante');
    expect(within(line).queryAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([expect.stringMatching(/^About owners/)]);
  });
});

describe('Take over', () => {
  const view = (colleague: boolean): TakeoverData => ({
    at: config.default_at, role: 'ed_nurse', template: 'sbar_nurse',
    colleagues: [{ id: 'ed-rn-2', name: 'Robin Asante, RN', patients: 2 }],
    colleague: colleague ? { id: 'ed-rn-2', name: 'Robin Asante, RN' } : null,
    rows: colleague ? [
      { csn: '1001', patient: rows[0].patient, severity: 'unstable', chief_complaint: 'Sepsis', facility: 'SGH', destination: { pending_unit: 'MICU' }, minutes_boarding: 200,
        owner: robin, state: 'changed', signed: { by: 'Robin Asante, RN', role: 'ed_nurse', at: '2026-03-10T14:10:00Z', version: 2 }, blocking: 0, to_confirm: [], changed: ['Contingency plan'] },
      { csn: '1002', patient: rows[1].patient, severity: 'watcher', chief_complaint: 'Chest pain', facility: 'SGH', destination: {}, minutes_boarding: 90,
        owner: robin, state: 'draft', blocking: 2, to_confirm: ['Allergies', 'Isolation'], changed: [] },
    ] : [],
  });

  it("lists the colleague's patients, then takes the ones ticked in one go", async () => {
    const user = userEvent.setup();
    vi.mocked(api.takeover).mockImplementation(async (c) => view(!!c));
    vi.mocked(api.takeOver).mockResolvedValue({ ...view(true), rows: [], colleagues: [{ id: 'ed-rn-2', name: 'Robin Asante, RN', patients: 1 }], taken: ['1001'] });
    window.history.replaceState({}, '', '/takeover');
    wrap(<Takeover />);
    await user.click(await screen.findByRole('radio', { name: /Robin Asante, RN/ }));
    expect(api.takeover).toHaveBeenLastCalledWith('ed-rn-2', config.default_at);
    const table = await screen.findByRole('table');
    const row = (name: string) => within(table).getByRole('link', { name }).closest('tr')!;
    expect(row('Doe, Alex')).toHaveTextContent('Changed after signing');
    expect(row('Doe, Alex')).toHaveTextContent('Contingency plan');
    expect(row('Park, Robin')).toHaveTextContent('2 items to resolve before signing');
    expect(row('Park, Robin')).toHaveTextContent('AllergiesIsolation');
    expect(screen.getByRole('button', { name: 'Take 2 patients' })).toBeEnabled();
    await user.click(screen.getByRole('checkbox', { name: 'Take Park, Robin' }));
    await user.click(screen.getByRole('button', { name: 'Take 1 patient' }));
    expect(api.takeOver).toHaveBeenCalledWith('ed-rn-2', ['1001'], config.default_at);
    expect(await screen.findByRole('status')).toHaveTextContent('You took over 1 patient from Robin Asante, RN: Doe, Alex.');
  });

  it('is read only at an earlier time in the live app', async () => {
    const user = userEvent.setup();
    const t = new Date(Math.floor((Date.now() - 4 * 3600000) / 3600000) * 3600000).toISOString();
    window.history.replaceState({}, '', `/takeover?at=${encodeURIComponent(t)}`);
    vi.mocked(api.takeover).mockImplementation(async (c) => view(!!c));
    wrap(<Takeover />, { ...live, personas: [edNurse] });
    await user.click(await screen.findByRole('radio', { name: /Robin Asante, RN/ }));
    expect(api.takeover).toHaveBeenLastCalledWith('ed-rn-2', t);
    expect(await screen.findByRole('button', { name: 'Take 2 patients' })).toBeDisabled();
    expect(screen.getByText(READ_ONLY)).toBeInTheDocument();
  });

  it('tells other roles it is for the ED', async () => {
    wrap(<Takeover />, { ...config, personas: [unitNurse] });
    expect(await screen.findByText('Take over is for ED nurses and physicians')).toBeInTheDocument();
    expect(api.takeover).not.toHaveBeenCalled();
  });
});

describe('Analytics owners', () => {
  it('shows outside actions, handovers around the shift change and time to sign by owner, each with its ?', async () => {
    vi.mocked(api.analytics).mockResolvedValue({
      at: config.default_at, series: [{ t: config.default_at, ed: 20, boarding: 5, unassigned_rn: 3, unassigned_md: 4 }], units: [], admitted: 0,
      drafts_per_patient: { median: null, max: null }, completeness_at_transfer: { median: null, histogram: [] }, blocking_at_transfer: [],
      severity_at_transfer: {}, signed_before_transfer: 0, transferred_without_sign_off: 0, drafts: 0, model_calls: 0, addenda: 0,
      owners: {
        outside: [{ role: 'ed_nurse', n: 2, of: 8 }, { role: 'ed_physician', n: 0, of: 0 }],
        handovers: [{ bucket: 'More than 30 min before', n: 0 }, { bucket: 'Up to 30 min before', n: 3 }, { bucket: 'Up to 30 min after', n: 1 }, { bucket: 'More than 30 min after', n: 0 }],
        to_sign: [{ role: 'ed_nurse', owner: 'Jordan Reyes, RN', n: 2, median_min: 125 }, { role: 'ed_nurse', owner: null, n: 1, median_min: 300 }],
      },
    });
    wrap(<Analytics />);
    expect(await screen.findByText('Unassigned boarding patients')).toBeInTheDocument();
    expect(screen.getByText('25% of 8 actions')).toBeInTheDocument();
    expect(screen.getByText('No actions yet')).toBeInTheDocument();
    const hand = screen.getByText('Up to 30 min before').closest('li')!;
    expect(hand).toHaveTextContent('3');
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('row').slice(1).map((r) => r.textContent)).toEqual(['Jordan ReyesNurse owner22 h 5 min', 'UnassignedNurse owner15 h']);
    for (const name of ['Unassigned boarding patients', 'Actions outside an assignment', 'Handovers around the shift change', 'Admit decision to signature, by owner']) {
      expect(screen.getAllByRole('button', { name: new RegExp(`How to read this chart: ${name}`) })).toHaveLength(1);
    }
  });
});

describe('Sidebar', () => {
  const links = (cfg: AppConfig) => {
    const r = wrap(<SidebarBody collapsed={false} theme="light" onTheme={() => {}} />, cfg);
    const out = within(screen.getByRole('navigation', { name: 'Main' })).getAllByRole('link').map((a) => a.textContent);
    r.unmount();
    return out;
  };

  it('shows Take over to the ED roles only', () => {
    expect(links(config)).toEqual(['Board', 'Take over', 'Analytics', 'Templates', 'How to use']);
    expect(links({ ...config, personas: [unitNurse] })).toEqual(['Board', 'Analytics', 'Templates', 'How to use']);
  });
});
