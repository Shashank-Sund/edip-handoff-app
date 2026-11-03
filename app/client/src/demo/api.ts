/**
 * The static demo's API: the same calls as src/lib/api.ts, answered in the browser by src/demo/server.ts over
 * the shipped fixtures. Loaded only in the VITE_DEMO=static build. Actions live in memory and reset on reload.
 */

import { ApiError } from '@/lib/api';
import { loadFixtures } from './load';
import { createServer, type DemoServer, type Request } from './server';

let server: Promise<DemoServer> | null = null;

function ready(): Promise<DemoServer> {
  server ??= loadFixtures().then(
    (f) => createServer(f),
    (e: Error) => {
      server = null; // let a retry load again
      throw new ApiError(`The demo data could not be loaded (${e.message}). Check your connection and try again.`, 0);
    },
  );
  return server;
}

export async function request<T>(req: Request): Promise<T> {
  const s = await ready();
  const r = s.handle(req);
  if (r.status >= 400) {
    const detail = typeof r.body?.detail === 'string' ? r.body.detail : `The demo answered ${r.status}.`;
    throw new ApiError(detail, r.status);
  }
  return r.body as T;
}
