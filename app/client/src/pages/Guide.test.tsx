import { render, screen, within } from '@testing-library/react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppProvider } from '@/hooks/useApp';
import { GUIDE, anchorFor, findTopic } from '@/components/guide/content';
import { NAV } from '@/components/layout/nav';
import { config } from '@/test/fixtures';
import Guide from './Guide';

vi.mock('@/lib/api', () => ({ setPersona: vi.fn(), api: { reset: vi.fn() } }));

function renderGuide() {
  return render(
    <AppProvider config={config}>
      <TooltipProvider>
        <Guide />
      </TooltipProvider>
    </AppProvider>,
  );
}

describe('Guide page', () => {
  it('has an anchor for every section and every topic, so the ? panels can deep link', () => {
    const { container } = renderGuide();
    for (const s of GUIDE) {
      expect(container.querySelector(`#${s.id}`), s.id).not.toBeNull();
      for (const t of s.topics) expect(container.querySelector(`#${anchorFor(`${s.id}.${t.id}`)}`), `${s.id}.${t.id}`).not.toBeNull();
    }
    expect(container.querySelector('#about')).not.toBeNull();
  });

  it('links every contents entry to an element on the page', () => {
    const { container } = renderGuide();
    const navs = screen.getAllByRole('navigation', { name: 'Guide contents', hidden: true });
    for (const nav of navs) {
      for (const a of within(nav).getAllByRole('link', { hidden: true })) {
        const id = a.getAttribute('href')!.slice(1);
        expect(container.querySelector(`#${id}`), id).not.toBeNull();
      }
    }
  });

  it('opens with the orientation and the first five minutes, in reading order', () => {
    renderGuide();
    const h2 = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
    expect(h2.indexOf('What this app does for you')).toBeLessThan(h2.indexOf('Your first five minutes'));
    expect(h2.indexOf('Your first five minutes')).toBeLessThan(h2.indexOf('Page by page'));
    expect(h2.indexOf('Page by page')).toBeLessThan(h2.indexOf('Glossary'));
    expect(screen.getAllByRole('heading', { level: 3, name: /^Step \d+:/ })).toHaveLength(GUIDE.find((s) => s.id === 'first')!.topics.length);
  });

  it('explains who owns a patient everywhere it shows: steps, roles, questions, pages, the clock and the glossary', () => {
    const ids = (s: string) => GUIDE.find((x) => x.id === s)!.topics.map((t) => t.id);
    const first = ids('first');
    expect(first.indexOf('take')).toBe(first.indexOf('board') + 1);
    expect(first.at(-1)).toBe('handover');
    expect(ids('questions')).toEqual(expect.arrayContaining(['assigned', 'shift', 'unassigned']));
    expect(ids('board')).toEqual(expect.arrayContaining(['tabs', 'owners', 'shift']));
    expect(ids('handoff')).toContain('owner');
    expect(ids('analytics')).toEqual(expect.arrayContaining(['unassigned', 'outside', 'handovers', 'to-sign']));
    expect(ids('takeover')).toEqual(['what', 'pick', 'list', 'confirm']);
    expect(ids('clock')).toEqual(expect.arrayContaining(['owners', 'shift-demo']));
    expect(ids('glossary')).toEqual(expect.arrayContaining(['owner', 'unassigned', 'take-over', 'outside', 'stale-owner', 'shift-change']));
    const titles = GUIDE.find((s) => s.id === 'glossary')!.topics.map((t) => t.title);
    expect(titles).toEqual([...titles].sort((a, b) => a.localeCompare(b, 'en', { sensitivity: 'base' })));
    renderGuide();
    for (const q of ['Why does it say Assigned to someone else?', 'What happens at shift change?', 'Why is my patient back in Unassigned?']) {
      expect(screen.getByRole('heading', { level: 3, name: q })).toBeInTheDocument();
    }
    // The page by page reference follows the sidebar: the Take over page comes after the board and the handoff.
    const h3 = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent);
    expect(h3.indexOf('The handoff page')).toBeLessThan(h3.indexOf('Take over'));
    expect(h3.indexOf('Take over')).toBeLessThan(h3.indexOf('Analytics'));
  });

  it('has a section for every page in the sidebar, and lists each page under Pages', () => {
    const pages = findTopic('sidebar.pages')!.topics[0];
    const { container } = render(<>{pages.body}</>);
    for (const n of NAV) {
      // The guide itself is the How to use page; every other page has its own Page by page section.
      if (n.to === '/guide') continue;
      const id = n.to === '/' ? 'board' : n.to.slice(1);
      const section = GUIDE.find((s) => s.id === id);
      expect(section, `${n.label}: a guide section with id ${id}`).toBeDefined();
      expect(section!.kind, n.label).toBe('reference');
      expect(container.textContent, `${n.label} under Pages`).toContain(n.label);
    }
  });

  it('keeps the license attribution', () => {
    renderGuide();
    expect(screen.getByText(/TradingView Lightweight Charts™ Copyright/)).toBeInTheDocument();
  });
});
