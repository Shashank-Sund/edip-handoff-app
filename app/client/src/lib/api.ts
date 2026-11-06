import type { Analytics, AppConfig, Board, CaseDetail, Colleague, Persona, Takeover, TemplateDef } from './types';

/** An error the server explains in plain words (HTTP 409 for rules, 404 for scope). */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

let persona = 'ed-rn';
export function setPersona(id: string) {
  persona = id;
}

async function call<T>(path: string, init?: RequestInit & { at?: string }): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (init?.at) url.searchParams.set('at', init.at);
  let res: Response;
  try {
    res = await fetch(url.pathname + url.search, {
      ...init,
      headers: { 'Content-Type': 'application/json', 'X-Demo-Persona': persona, ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError('The server could not be reached. Check that the backend is running.', 0);
  }
  if (!res.ok) {
    let message = `The server answered ${res.status}.`;
    try {
      const body = await res.json();
      if (typeof body?.detail === 'string') message = body.detail;
    } catch {
      // keep the generic message
    }
    throw new ApiError(message, res.status);
  }
  return (await res.json()) as T;
}

type Kind = 'edit' | 'attest' | 'sign' | 'ack';
/** Take (claim), Assign to a colleague (to: their id), or Release, for your ED role. */
export type OwnerBody = { kind: 'claim' | 'release' } | { kind: 'assign'; to: string };
interface Api {
  config(): Promise<AppConfig>;
  me(): Promise<Persona>;
  templates(): Promise<TemplateDef[]>;
  board(at: string): Promise<Board>;
  case(csn: string, at: string): Promise<CaseDetail>;
  analytics(at: string): Promise<Analytics>;
  act(csn: string, template: string, kind: Kind, body: unknown, at: string): Promise<CaseDetail>;
  own(csn: string, body: OwnerBody, at: string): Promise<CaseDetail>;
  colleagues(): Promise<Colleague[]>;
  takeover(colleague: string | null, at: string): Promise<Takeover>;
  takeOver(colleague: string, csns: string[], at: string): Promise<Takeover>;
  reset(): Promise<{ status: string }>;
}

/**
 * The static demo (VITE_DEMO=static build) answers the same calls in the browser, from shipped fixtures. The
 * flag is a build time constant, so the default build drops this branch and the demo code entirely.
 */
function staticDemo(): Api {
  const go = <T,>(method: 'GET' | 'POST', path: string, at?: string, body?: unknown, params?: Record<string, string>) =>
    import('@/demo/api').then((m) => m.request<T>({ method, path, at, persona, body, params }));
  return {
    config: () => go<AppConfig>('GET', '/api/config'),
    me: () => go<Persona>('GET', '/api/me'),
    templates: () => go<TemplateDef[]>('GET', '/api/templates'),
    board: (at) => go<Board>('GET', '/api/board', at),
    case: (csn, at) => go<CaseDetail>('GET', `/api/handoffs/${csn}`, at),
    analytics: (at) => go<Analytics>('GET', '/api/analytics', at),
    act: (csn, template, kind, body, at) => go<CaseDetail>('POST', `/api/handoffs/${csn}/${template}/${kind}`, at, body),
    own: (csn, body, at) => go<CaseDetail>('POST', `/api/handoffs/${csn}/owner`, at, body),
    colleagues: () => go<Colleague[]>('GET', '/api/colleagues'),
    takeover: (colleague, at) => go<Takeover>('GET', '/api/takeover', at, undefined, colleague ? { colleague } : undefined),
    takeOver: (colleague, csns, at) => go<Takeover>('POST', '/api/takeover', at, { colleague, csns }),
    reset: () => go<{ status: string }>('POST', '/api/demo/reset'),
  };
}

export const api: Api = __STATIC_DEMO__ ? staticDemo() : {
  config: () => call<AppConfig>('/api/config'),
  me: () => call<Persona>('/api/me'),
  templates: () => call<TemplateDef[]>('/api/templates'),
  board: (at: string) => call<Board>('/api/board', { at }),
  case: (csn: string, at: string) => call<CaseDetail>(`/api/handoffs/${csn}`, { at }),
  analytics: (at: string) => call<Analytics>('/api/analytics', { at }),
  act: (csn: string, template: string, kind: Kind, body: unknown, at: string) =>
    call<CaseDetail>(`/api/handoffs/${csn}/${template}/${kind}`, { method: 'POST', body: JSON.stringify(body), at }),
  own: (csn: string, body: OwnerBody, at: string) => call<CaseDetail>(`/api/handoffs/${csn}/owner`, { method: 'POST', body: JSON.stringify(body), at }),
  colleagues: () => call<Colleague[]>('/api/colleagues'),
  takeover: (colleague: string | null, at: string) =>
    call<Takeover>(colleague ? `/api/takeover?colleague=${encodeURIComponent(colleague)}` : '/api/takeover', { at }),
  takeOver: (colleague: string, csns: string[], at: string) =>
    call<Takeover>('/api/takeover', { method: 'POST', body: JSON.stringify({ colleague, csns }), at }),
  reset: () => call<{ status: string }>('/api/demo/reset', { method: 'POST' }),
};
