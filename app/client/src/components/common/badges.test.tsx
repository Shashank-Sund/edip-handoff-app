import { render, screen } from '@testing-library/react';
import { ProgressRing, SeverityBadge, StateBadge } from './badges';

describe('SeverityBadge', () => {
  it.each([
    ['stable', 'Stable'],
    ['watcher', 'Watcher'],
    ['unstable', 'Unstable'],
  ] as const)('prints the word for %s', (value, word) => {
    render(<SeverityBadge value={value} />);
    expect(screen.getByText(word)).toBeInTheDocument();
  });
  it('prints n/a without a value', () => {
    render(<SeverityBadge value={null} />);
    expect(screen.getByText('n/a')).toBeInTheDocument();
  });
});

describe('StateBadge', () => {
  it.each([
    ['drafting', 'Drafting'],
    ['draft', 'Draft'],
    ['signed', 'Signed'],
    ['acknowledged', 'Acknowledged'],
  ] as const)('prints the word for %s', (state, word) => {
    render(<StateBadge state={state} />);
    expect(screen.getByText(word)).toBeVisible();
  });
  it('keeps the word for screen readers when compact', () => {
    render(<StateBadge state="signed" compact />);
    expect(screen.getByText('Signed')).toHaveClass('sr-only');
  });
  it('adds Updated when changed', () => {
    render(<StateBadge state="signed" changed />);
    expect(screen.getByText('Updated')).toBeInTheDocument();
  });
});

describe('ProgressRing', () => {
  const offset = (value: number) => {
    const { container, unmount } = render(<ProgressRing value={value} size={28} stroke={4} />);
    const ring = container.querySelectorAll('circle')[1];
    const dash = Number(ring.getAttribute('stroke-dasharray'));
    const off = Number(ring.getAttribute('stroke-dashoffset'));
    unmount();
    return off / dash;
  };
  it('fills in proportion to the value', () => {
    expect(offset(0.25)).toBeCloseTo(0.75);
  });
  it('clamps below 0 and above 1', () => {
    expect(offset(-0.5)).toBeCloseTo(1);
    expect(offset(1.7)).toBeCloseTo(0);
  });
  it('is hidden from assistive technology', () => {
    const { container } = render(<ProgressRing value={0.5} />);
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden');
  });
});
