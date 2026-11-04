/**
 * Port of the routing semantics of app/server/main.py in mock mode: the same paths, status codes and messages,
 * answered in the browser. Demo personas on, replay clock, in memory actions.
 *
 *   handle({ method, path, at, persona, body }) -> { status, body }
 *
 * Errors carry { detail } as FastAPI's do.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import { Dataset, type Fixtures } from './data';
import { ActionError, Views } from './logic';
import { demoPersonas } from './personas';
import { iso, parseTs, TZ } from './py';
import { MemoryStore } from './workflow';

export interface Request {
  method: 'GET' | 'POST';
  path: string;
  /** the `at` query parameter */
  at?: string | null;
  /** the X-Demo-Persona header */
  persona?: string | null;
  body?: any;
}
export interface Response {
  status: number;
  body: any;
}

class HttpError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

const TEMPLATES = [
  { id: 'sbar_nurse', name: 'SBAR', audience: 'Nurse' },
  { id: 'ipass_physician', name: 'I-PASS', audience: 'Physician' },
  { id: 'custom_sea58', name: 'Joint Commission minimum', audience: 'Any' },
];

/** Request bodies (main.py's pydantic models): required and optional fields, as model_dump() returns them. */
const BODIES: Record<string, [string[], string[]]> = {
  edit: [['element'], ['text', 'items', 'restore']],
  attest: [['rule', 'option'], []],
  sign: [['callback'], []],
  ack: [['readback'], ['questions']],
};

/** main.py FIELD: how the validation sentences name each field. */
const FIELD: Record<string, string> = {
  element: 'item', text: 'text', items: 'action items', restore: 'restore choice', rule: 'rule', option: 'attestation',
  callback: 'callback number', readback: 'read back', questions: 'questions',
};
const UNREADABLE = 'The request could not be read. Reload the page and try again.';
const missing = (k: string) => `The request is missing the ${FIELD[k] ?? k}.`;
const invalid = (k: string) => `The request has a value the app cannot use for the ${FIELD[k] ?? k}.`;

const isStr = (v: unknown) => typeof v === 'string';
const optStr = (v: unknown) => v === null || v === undefined || typeof v === 'string';

/** main.Item: text a string, owner and due a string or null; other keys dropped. */
function item(v: unknown): any {
  if (typeof v !== 'object' || v === null || Array.isArray(v)) return null;
  const o = v as Record<string, unknown>;
  if (!isStr(o.text) || !optStr(o.owner) || !optStr(o.due)) return null;
  return { text: o.text, owner: o.owner ?? null, due: o.due ?? null };
}

export function createServer(fixtures: Fixtures & { templates: any[] }, opts: { clock?: string | null } = {}) {
  const ds = new Dataset(fixtures);
  const store = new MemoryStore();
  const views = new Views(ds, store, fixtures.templates);
  const personas = demoPersonas(ds.meta.units ?? []);
  const defaultAt = opts.clock ? parseTs(opts.clock) : ds.busiest();

  function person(id: string | null | undefined): any {
    const pid = id || 'ed-rn';
    const p = personas.find((x) => x.id === pid);
    if (!p) throw new HttpError(400, `unknown persona ${pid}`);
    return { ...p, user_email: null };
  }

  function at(v: string | null | undefined): number {
    if (!v) return defaultAt;
    let t: number;
    try {
      t = parseTs(v);
    } catch {
      throw new HttpError(400, 'at must be an ISO time');
    }
    return Math.min(Math.max(t, ds.start), ds.end);
  }

  /** Validation as pydantic does it (strict strings and booleans), with main.plain_validation's sentences. */
  function dump(kind: string, body: any): any {
    const [required, optional] = BODIES[kind];
    if (typeof body !== 'object' || body === null || Array.isArray(body)) throw new HttpError(422, UNREADABLE);
    const out: any = {};
    for (const k of required) {
      if (!(k in body)) throw new HttpError(422, missing(k));
      if (!isStr(body[k])) throw new HttpError(422, invalid(k));
      out[k] = body[k];
    }
    for (const k of optional) {
      const v = body[k] ?? null;
      if (k === 'items') {
        if (v !== null) {
          if (!Array.isArray(v)) throw new HttpError(422, invalid(k));
          const items = v.map(item);
          if (items.some((i) => i === null)) throw new HttpError(422, invalid(k));
          out[k] = items;
          continue;
        }
      } else if (k === 'restore') {
        if (v !== null && typeof v !== 'boolean') throw new HttpError(422, invalid(k));
      } else if (!optStr(v)) {
        throw new HttpError(422, invalid(k));
      }
      out[k] = v;
    }
    return out;
  }

  function route(req: Request): any {
    const parts = req.path.replace(/^\/+/, '').split('/');
    if (parts[0] !== 'api') throw new HttpError(404, 'Not Found');
    const p = parts.slice(1);
    const key = p.join('/');
    const get = req.method === 'GET', post = req.method === 'POST';
    if (key === 'config') {
      if (!get) throw new HttpError(405, 'Method Not Allowed');
      return {
        mode: 'mock', clock: 'replay', demo_personas: true, ready: !ds.error, detail: ds.error, source: ds.source,
        drafts_from: ds.meta.drafts_from ?? null, models: ds.meta.models ?? [], window: { start: iso(ds.start), end: iso(ds.end) },
        default_at: iso(defaultAt), facilities: ds.meta.facilities ?? [], units: ds.meta.units ?? [], personas, store: store.kind,
        timezone: TZ, templates: TEMPLATES,
      };
    }
    if (key === 'templates') {
      if (!get) throw new HttpError(405, 'Method Not Allowed');
      return fixtures.templates;
    }
    if (key === 'board') {
      if (!get) throw new HttpError(405, 'Method Not Allowed');
      const t = at(req.at);
      return views.board(t, person(req.persona));
    }
    if (key === 'analytics') {
      if (!get) throw new HttpError(405, 'Method Not Allowed');
      return views.analytics(at(req.at));
    }
    if (key === 'me') {
      if (!get) throw new HttpError(405, 'Method Not Allowed');
      return person(req.persona);
    }
    if (key === 'health') {
      if (!get) throw new HttpError(405, 'Method Not Allowed');
      return { status: ds.error ? 'not ready' : 'ok', detail: ds.error, mode: 'mock', store: store.kind, encounters: ds.enc.size,
        drafts: ds.draftCount(), now: iso(Date.now()) };
    }
    if (key === 'demo/reset') {
      if (!post) throw new HttpError(405, 'Method Not Allowed');
      store.reset();
      return { status: 'reset' };
    }
    if (p[0] === 'handoffs' && p.length === 2 && p[1]) {
      if (!get) throw new HttpError(405, 'Method Not Allowed');
      const t = at(req.at);
      const c = views.case(decodeURIComponent(p[1]), t, person(req.persona));
      if (c === null) throw new HttpError(404, 'This patient is not on your board at this time.');
      return c;
    }
    if (p[0] === 'handoffs' && p.length === 4 && p[1] && p[2] && p[3] in BODIES) {
      if (!post) throw new HttpError(405, 'Method Not Allowed');
      const body = dump(p[3], req.body);
      const t = at(req.at);
      try {
        return views.act(p[3], decodeURIComponent(p[1]), decodeURIComponent(p[2]), t, person(req.persona), body);
      } catch (e) {
        if (e instanceof ActionError) throw new HttpError(409, e.message);
        throw e;
      }
    }
    throw new HttpError(404, 'Not Found');
  }

  function handle(req: Request): Response {
    try {
      return { status: 200, body: route(req) };
    } catch (e) {
      if (e instanceof HttpError) return { status: e.status, body: { detail: e.message } };
      throw e;
    }
  }

  return { handle, dataset: ds, store, personas, defaultAt };
}

export type DemoServer = ReturnType<typeof createServer>;
