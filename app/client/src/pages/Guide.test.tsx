import { render, screen, within } from '@testing-library/react';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppProvider } from '@/hooks/useApp';
import { GUIDE, anchorFor } from '@/components/guide/content';
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

  it('keeps the license attribution', () => {
    renderGuide();
    expect(screen.getByText(/TradingView Lightweight Charts™ Copyright/)).toBeInTheDocument();
  });
});
