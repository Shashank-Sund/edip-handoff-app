/// <reference types="node" />
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppProvider } from '@/hooks/useApp';
import { config } from '@/test/fixtures';
import type { AppConfig } from '@/lib/types';
import { Layout } from './Layout';

vi.mock('@/lib/api', () => ({ setPersona: vi.fn(), api: { reset: vi.fn() } }));

const NOTE = 'This demo replays one recorded week. Use the clock at the top right to move through it.';

function page(cfg: AppConfig = config) {
  return render(
    <AppProvider config={cfg}>
      <TooltipProvider>
        <Layout title="Board">content</Layout>
      </TooltipProvider>
    </AppProvider>,
  );
}

describe('First open note', () => {
  it('shows once in replay mode, near the clock, until closed', async () => {
    const user = userEvent.setup();
    const first = page();
    expect(screen.getByRole('complementary', { name: 'About the replay clock' })).toHaveTextContent(NOTE);
    await user.click(screen.getByRole('button', { name: 'Close this note' }));
    expect(screen.queryByRole('complementary', { name: 'About the replay clock' })).not.toBeInTheDocument();
    first.unmount();
    page();
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
  });

  it('is not shown with the live clock', () => {
    page({ ...config, clock: 'live' });
    expect(screen.queryByText(NOTE)).not.toBeInTheDocument();
  });

  it('still works when the browser blocks storage', async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    page();
    await user.click(screen.getByRole('button', { name: 'Close this note' }));
    expect(screen.queryByRole('complementary', { name: 'About the replay clock' })).not.toBeInTheDocument();
  });
});

describe('Brand', () => {
  it('is Healthcare Handoff, ED to inpatient, in the sidebar and the browser tab', () => {
    page();
    expect(screen.getAllByText('Healthcare Handoff').length).toBeGreaterThan(0);
    expect(screen.getAllByText('ED to inpatient').length).toBeGreaterThan(0);
    expect(screen.queryByText('ED Handoff')).not.toBeInTheDocument();
    const html = readFileSync(path.resolve(process.cwd(), 'index.html'), 'utf8');
    expect(html).toContain('<title>Healthcare Handoff</title>');
  });
});
