import type { Analytics, AppConfig, Board, CaseDetail, Persona, TemplateDef } from './types';

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

export const api = {
  config: () => call<AppConfig>('/api/config'),
  me: () => call<Persona>('/api/me'),
  templates: () => call<TemplateDef[]>('/api/templates'),
  board: (at: string) => call<Board>('/api/board', { at }),
  case: (csn: string, at: string) => call<CaseDetail>(`/api/handoffs/${csn}`, { at }),
  analytics: (at: string) => call<Analytics>('/api/analytics', { at }),
  act: (csn: string, template: string, kind: 'edit' | 'attest' | 'sign' | 'ack', body: unknown, at: string) =>
    call<CaseDetail>(`/api/handoffs/${csn}/${template}/${kind}`, { method: 'POST', body: JSON.stringify(body), at }),
  reset: () => call<{ status: string }>('/api/demo/reset', { method: 'POST' }),
};
