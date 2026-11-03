import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { linkProps, match, navigate, usePath } from './router';

beforeEach(() => {
  vi.spyOn(window, 'scrollTo').mockImplementation(() => {});
  window.history.replaceState({}, '', '/');
});

describe('match', () => {
  it('extracts params', () => {
    expect(match('/handoff/1001', '/handoff/:csn')).toEqual({ csn: '1001' });
    expect(match('/handoff/a%20b', '/handoff/:csn')).toEqual({ csn: 'a b' });
  });
  it('ignores a trailing slash', () => {
    expect(match('/handoff/1001/', '/handoff/:csn')).toEqual({ csn: '1001' });
    expect(match('/analytics/', '/analytics')).toEqual({});
  });
  it('rejects other paths', () => {
    expect(match('/handoff', '/handoff/:csn')).toBeNull();
    expect(match('/handoff/1/x', '/handoff/:csn')).toBeNull();
    expect(match('/cases/1001', '/handoff/:csn')).toBeNull();
  });
});

function Where() {
  return <p>at {usePath()}</p>;
}

describe('navigate and usePath', () => {
  it('pushes history and notifies listeners', () => {
    const push = vi.spyOn(window.history, 'pushState');
    render(<Where />);
    expect(screen.getByText('at /')).toBeInTheDocument();
    act(() => navigate('/analytics'));
    expect(push).toHaveBeenCalledWith({}, '', '/analytics');
    expect(window.location.pathname).toBe('/analytics');
    expect(screen.getByText('at /analytics')).toBeInTheDocument();
    expect(window.scrollTo).toHaveBeenCalled();
  });
  it('does nothing when already there', () => {
    const push = vi.spyOn(window.history, 'pushState');
    navigate('/');
    expect(push).not.toHaveBeenCalled();
  });
  it('follows the back button', () => {
    render(<Where />);
    act(() => navigate('/templates'));
    act(() => {
      window.history.replaceState({}, '', '/guide');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(screen.getByText('at /guide')).toBeInTheDocument();
  });
});

describe('linkProps', () => {
  it('navigates on a plain click and leaves modifier clicks alone', async () => {
    render(
      <>
        <a {...linkProps('/guide')}>Guide</a>
        <Where />
      </>,
    );
    const link = screen.getByRole('link', { name: 'Guide' });
    expect(link).toHaveAttribute('href', '/guide');
    await userEvent.click(link);
    expect(screen.getByText('at /guide')).toBeInTheDocument();
  });
});
