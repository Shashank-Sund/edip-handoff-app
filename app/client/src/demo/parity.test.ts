/// <reference types="node" />
/**
 * Parity: the in-browser implementation answers every request exactly as the FastAPI app does.
 *
 * app/fixtures/parity.py drives the real server over a fixed scenario list and writes each request with its
 * status and response (plus the fixtures it used) to app/fixtures/parity, or to EDIP_PARITY_DIR. This test
 * replays the same requests, in the same order, through src/demo/server.ts and compares the JSON answers.
 *
 * Normalized: the `now` field of /api/health (the wall clock). Nothing else.
 *
 * Without the parity folder the test is skipped, unless EDIP_PARITY_REQUIRED is set (the CI parity job sets it
 * and runs parity.py first).
 */
/* eslint-disable @typescript-eslint/no-explicit-any */
import { existsSync, readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import path from 'node:path';
import { createServer } from './server';
import { parseLines } from './load';

// Vitest runs from app/client.
const dir = process.env.EDIP_PARITY_DIR || path.resolve(process.cwd(), '../fixtures/parity');
const have = existsSync(path.join(dir, 'scenarios.json'));
if (!have && process.env.EDIP_PARITY_REQUIRED) throw new Error(`No parity data in ${dir}: run app/fixtures/parity.py first.`);
if (!have) console.warn(`Parity test skipped: no ${path.join(dir, 'scenarios.json')} (run .venv/bin/python app/fixtures/parity.py)`);

/** The first place two JSON values differ, or null. */
function firstDiff(a: any, b: any, at = '$'): string | null {
  if (a === b) return null;
  if (typeof a !== typeof b || a === null || b === null || typeof a !== 'object') {
    return `${at}: expected ${JSON.stringify(a)?.slice(0, 300)}, got ${JSON.stringify(b)?.slice(0, 300)}`;
  }
  if (Array.isArray(a) !== Array.isArray(b)) return `${at}: array versus object`;
  if (Array.isArray(a)) {
    if (a.length !== b.length) return `${at}: expected ${a.length} items, got ${b.length}`;
    for (let i = 0; i < a.length; i++) {
      const d = firstDiff(a[i], b[i], `${at}[${i}]`);
      if (d) return d;
    }
    return null;
  }
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) {
    if (!(k in a)) return `${at}.${k}: unexpected key`;
    if (!(k in b)) return `${at}.${k}: missing key`;
    const d = firstDiff(a[k], b[k], `${at}.${k}`);
    if (d) return d;
  }
  return null;
}

describe.skipIf(!have)('static demo parity with the FastAPI app', () => {
  it('answers every scenario as the server does', () => {
    const read = (f: string) => readFileSync(path.join(dir, f));
    const spec = JSON.parse(read('scenarios.json').toString('utf8'));
    const server = createServer({
      encounters: JSON.parse(read('encounters.json').toString('utf8')),
      drafts: parseLines(gunzipSync(read('drafts.jsonl.gz')).toString('utf8')),
      meta: JSON.parse(read('meta.json').toString('utf8')),
      templates: JSON.parse(read('templates.json').toString('utf8')),
    });
    expect(spec.scenarios.length).toBeGreaterThan(100);
    const failures: string[] = [];
    for (const s of spec.scenarios) {
      const r = server.handle({ method: s.method, path: s.path, at: s.at, persona: s.persona, body: s.body, params: s.params });
      const body = JSON.parse(JSON.stringify(r.body));
      if (s.path === '/api/health') body.now = '<now>';
      const d = r.status !== s.status ? `status: expected ${s.status}, got ${r.status} (${JSON.stringify(body).slice(0, 200)})` : firstDiff(s.response, body);
      if (d) failures.push(`${s.name}: ${d}`);
    }
    expect(failures).toEqual([]);
  });
});
