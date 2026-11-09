import type { Analytics, AppConfig, Board, CaseDetail, Colleague, PatientTasks, Persona, TaskRole, TasksPage, Takeover, TemplateDef } from './types';

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
/** A task added by hand: for a role, or a colleague (to: their id); due an ISO time. */
export interface NewTask {
  text: string;
  role?: TaskRole;
  to?: string;
  due?: string;
}
/** A change to a task. due: an ISO time, or null to clear it. */
export type TaskChange =
  | { kind: 'done' | 'undo' }
  | { kind: 'not_needed'; reason: string }
  | { kind: 'reassign'; role?: TaskRole; to?: string }
  | { kind: 'due'; due: string | null };
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
  /** The Tasks page: your open tasks (mine), or every task on your board (all). */
  tasks(scope: 'mine' | 'all', at: string): Promise<TasksPage>;
  patientTasks(csn: string, at: string): Promise<PatientTasks>;
  addTask(csn: string, body: NewTask, at: string): Promise<PatientTasks>;
  changeTask(id: number, body: TaskChange, at: string): Promise<PatientTasks>;
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
    tasks: (scope, at) => go<TasksPage>('GET', '/api/tasks', at, undefined, { scope }),
    patientTasks: (csn, at) => go<PatientTasks>('GET', `/api/handoffs/${csn}/tasks`, at),
    addTask: (csn, body, at) => go<PatientTasks>('POST', `/api/handoffs/${csn}/tasks`, at, body),
    changeTask: (id, body, at) => go<PatientTasks>('POST', `/api/tasks/${id}`, at, body),
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
  tasks: (scope: 'mine' | 'all', at: string) => call<TasksPage>(`/api/tasks?scope=${scope}`, { at }),
  patientTasks: (csn: string, at: string) => call<PatientTasks>(`/api/handoffs/${csn}/tasks`, { at }),
  addTask: (csn: string, body: NewTask, at: string) =>
    call<PatientTasks>(`/api/handoffs/${csn}/tasks`, { method: 'POST', body: JSON.stringify(body), at }),
  changeTask: (id: number, body: TaskChange, at: string) =>
    call<PatientTasks>(`/api/tasks/${id}`, { method: 'POST', body: JSON.stringify(body), at }),
  reset: () => call<{ status: string }>('/api/demo/reset', { method: 'POST' }),
};
