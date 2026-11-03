/**
 * Loads the demo data that the static build ships under /data: meta.json, encounters.json, templates.json and
 * drafts.jsonl.gz, which is decompressed here with DecompressionStream('gzip') (the CDN does not compress
 * it). If something on the way already decoded it, the bytes are used as they are.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import type { Fixtures } from './data';

export function parseLines(text: string): any[] {
  const out: any[] = [];
  for (const line of text.split('\n')) if (line.trim()) out.push(JSON.parse(line));
  return out;
}

async function fetchOk(url: string): Promise<Response> {
  const res = await fetch(url, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`${url} answered ${res.status}`);
  return res;
}

async function gunzipText(res: Response): Promise<string> {
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) return new TextDecoder().decode(bytes);
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return new Response(stream).text();
}

export async function loadFixtures(base = '/data/'): Promise<Fixtures & { templates: any[] }> {
  const [meta, encounters, templates, drafts] = await Promise.all([
    fetchOk(base + 'meta.json').then((r) => r.json()),
    fetchOk(base + 'encounters.json').then((r) => r.json()),
    fetchOk(base + 'templates.json').then((r) => r.json()),
    fetchOk(base + 'drafts.jsonl.gz').then(gunzipText).then(parseLines),
  ]);
  return { meta, encounters, templates, drafts };
}
