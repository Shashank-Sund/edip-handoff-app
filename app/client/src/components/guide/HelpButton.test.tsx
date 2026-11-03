import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { GUIDE, findTopic } from './content';
import { HelpButton } from './HelpButton';

// Every source file except tests, as raw text, to find the topic ids the app uses.
const sources = import.meta.glob(['/src/**/*.tsx', '/src/**/*.ts', '!/src/**/*.test.*', '!/src/test/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

/** Quoted strings in topic= and help= props (Layout and Analytics panels pass help through to HelpButton). */
function usedTopics(): Map<string, string> {
  const found = new Map<string, string>();
  for (const [file, text] of Object.entries(sources)) {
    for (const m of text.matchAll(/\b(?:topic|help)=(?:"([^"]+)"|\{([^}]*)\})/g)) {
      const literal = m[1] ? [m[1]] : [...(m[2] ?? '').matchAll(/'([^']+)'/g)].map((x) => x[1]);
      for (const id of literal) found.set(id, file);
    }
  }
  return found;
}

describe('guide topics', () => {
  const used = usedTopics();

  it('finds the topic ids used in the app', () => {
    // A sanity check that the scan works; these are known call sites.
    for (const id of ['handoff.severity', 'handoff.sign', 'handoff.ack', 'board.columns', 'topbar.clock', 'board', 'analytics.census']) {
      expect(used.has(id), id).toBe(true);
    }
  });

  it.each([...usedTopics().entries()])('%s resolves to at least one topic (%s)', (id) => {
    const found = findTopic(id);
    expect(found).not.toBeNull();
    expect(found!.topics.length).toBeGreaterThan(0);
  });

  it('has unique section ids and unique topic ids within a section', () => {
    const sections = GUIDE.map((s) => s.id);
    expect(new Set(sections).size).toBe(sections.length);
    for (const s of GUIDE) {
      const ids = s.topics.map((t) => t.id);
      expect(new Set(ids).size, s.id).toBe(ids.length);
    }
  });

  it('returns null for an unknown section', () => {
    expect(findTopic('nowhere')).toBeNull();
  });
});

describe('HelpButton', () => {
  const renderButton = (topic: string, label?: string) =>
    render(
      <TooltipProvider>
        <HelpButton topic={topic} label={label} />
      </TooltipProvider>,
    );

  it('names the button after the label and topic', () => {
    renderButton('handoff.severity', 'About severity');
    expect(screen.getByRole('button', { name: 'About severity: ' + findTopic('handoff.severity')!.topics[0].title })).toBeInTheDocument();
  });

  it('opens a dialog with the topic title and a link to the full guide', async () => {
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
    const user = userEvent.setup();
    renderButton('handoff.sign', 'How sign off works');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /How sign off works/ }));
    const title = findTopic('handoff.sign')!.topics[0].title;
    const dialog = screen.getByRole('dialog', { name: title });
    const link = within(dialog).getByRole('link', { name: 'Open the full guide' });
    expect(link).toHaveAttribute('href', '/guide#handoff');
    await user.click(link);
    expect(window.location.pathname).toBe('/guide');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows every topic of a section when given a section id', async () => {
    const user = userEvent.setup();
    renderButton('board');
    await user.click(screen.getByRole('button', { name: /How to read this/ }));
    const section = GUIDE.find((s) => s.id === 'board')!;
    const dialog = screen.getByRole('dialog', { name: section.title });
    for (const t of section.topics) expect(within(dialog).getByRole('heading', { name: t.title })).toBeInTheDocument();
  });

  it('renders nothing for an unknown topic', () => {
    const { container } = renderButton('nowhere.at.all');
    expect(container).toBeEmptyDOMElement();
  });
});
