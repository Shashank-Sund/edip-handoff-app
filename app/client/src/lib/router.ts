import { useEffect, useState } from 'react';

/** `to` with the replay time of the current address (?at=), so in app links keep the clock. */
export function withAt(to: string): string {
  const at = new URLSearchParams(window.location.search).get('at');
  if (!at || !to.startsWith('/') || /[?&]at=/.test(to)) return to;
  const [path, hash] = to.split('#');
  return `${path}${path.includes('?') ? '&' : '?'}at=${encodeURIComponent(at)}${hash !== undefined ? `#${hash}` : ''}`;
}

/** A minimal history router: five routes do not need a library. */
export function navigate(to: string) {
  to = withAt(to);
  if (to === window.location.pathname + window.location.search) return;
  window.history.pushState({}, '', to);
  window.dispatchEvent(new PopStateEvent('popstate'));
  window.scrollTo({ top: 0 });
}

export function usePath() {
  const [path, setPath] = useState(() => window.location.pathname);
  useEffect(() => {
    const on = () => setPath(window.location.pathname);
    window.addEventListener('popstate', on);
    return () => window.removeEventListener('popstate', on);
  }, []);
  return path;
}

export function match(path: string, pattern: string): Record<string, string> | null {
  const a = path.replace(/\/+$/, '').split('/');
  const b = pattern.split('/');
  if (a.length !== b.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < b.length; i++) {
    if (b[i].startsWith(':')) params[b[i].slice(1)] = decodeURIComponent(a[i]);
    else if (a[i] !== b[i]) return null;
  }
  return params;
}

/** Click handler for in app links that keeps modifier clicks (new tab) working. */
export function linkProps(to: string) {
  return {
    href: withAt(to),
    onClick: (e: React.MouseEvent) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      navigate(to);
    },
  };
}
