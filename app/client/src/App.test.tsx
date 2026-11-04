import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import { board, caseSummary, config, templateSummary } from '@/test/fixtures';
import { useLoad } from '@/hooks/useLoad';
import { withAt } from '@/lib/router';
import type { Persona } from '@/lib/types';
import App from './App';

vi.mock('@/lib/api', () => ({
  setPersona: vi.fn(),
  ApiError: class extends Error {},
  api: { config: vi.fn(), me: vi.fn(), board: vi.fn(), reset: vi.fn() },
}));
const { api } = await import('@/lib/api');

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  window.history.replaceState({}, '', '/');
});

describe('routes', () => {
  it('shows Not found with a link to the board for an unknown address', async () => {
    vi.mocked(api.config).mockResolvedValue(config);
    window.history.replaceState({}, '', '/nope/at/all');
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'This page does not exist' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to the board' })).toHaveAttribute('href', expect.stringMatching(/^\/(\?at=.*)?$/));
  });

  it('says the data is not loaded yet in live mode before setup', async () => {
    vi.mocked(api.config).mockResolvedValue({ ...config, ready: false, detail: 'The handoff data is not in Lakebase yet.' });
    render(<App />);
    expect(await screen.findByText(/The handoff data is not in Lakebase yet. This page checks again every 30 seconds./)).toBeInTheDocument();
  });

  it('shows the board without the action counts for the read only viewer', async () => {
    const viewer: Persona = { ...config.personas[0], id: 'viewer', name: 'Viewer', role: 'ed_nurse', can_sign: [], can_ack: [] };
    vi.mocked(api.config).mockResolvedValue({ ...config, personas: [viewer] });
    const b = board([caseSummary({ templates: { sbar_nurse: templateSummary() } })]);
    vi.mocked(api.board).mockResolvedValue({ ...b, kpis: { ...b.kpis, to_sign: null, to_acknowledge: null, blocking: null } });
    render(<App />);
    expect(await screen.findByText('Boarding now')).toBeInTheDocument();
    expect(screen.queryByText('To sign')).not.toBeInTheDocument();
    expect(screen.queryByText('To acknowledge')).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /Completeness \(SBAR nurse\)/ })).toBeInTheDocument();
  });
});

describe('useLoad', () => {
  it('does not keep showing an earlier answer when a reload fails', async () => {
    let fail = false;
    const { result, rerender } = renderHook(({ k }) => useLoad(() => (fail ? Promise.reject(new Error('down')) : Promise.resolve(k)), [k]), {
      initialProps: { k: 1 },
    });
    await waitFor(() => expect(result.current.data).toBe(1));
    fail = true;
    rerender({ k: 2 });
    await waitFor(() => expect(result.current.error).toBe('down'));
    expect(result.current.data).toBeNull();
    fail = false;
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.data).toBe(2));
  });
});

describe('withAt', () => {
  it('carries the replay time to in app links', () => {
    expect(withAt('/analytics')).toBe('/analytics');
    window.history.replaceState({}, '', '/?at=2026-10-28T16%3A30%3A00.000Z');
    expect(withAt('/analytics')).toBe('/analytics?at=2026-10-28T16%3A30%3A00.000Z');
    expect(withAt('/guide#clock')).toBe('/guide?at=2026-10-28T16%3A30%3A00.000Z#clock');
    expect(withAt('https://example.org/')).toBe('https://example.org/');
  });
});
