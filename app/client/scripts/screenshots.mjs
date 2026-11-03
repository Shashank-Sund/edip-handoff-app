// Screenshots of every page (desktop, phone, dark) for design review, plus checks: no console errors and no
// horizontal scroll at 390 px. Usage: node scripts/screenshots.mjs [baseUrl] [outDir]
// Uses the installed Chrome (or CHROME_PATH).
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright-core';

const base = process.argv[2] || 'http://localhost:5180';
const out = process.argv[3] || 'screenshots';
mkdirSync(out, { recursive: true });
const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: 'chrome' });
const problems = [];

async function shot(name, path, { width = 1440, height = 900, dark = false, persona = 'ed-rn', full = true, before } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 2, colorScheme: dark ? 'dark' : 'light' });
  await ctx.addInitScript(([p, d]) => {
    localStorage.setItem('edip.persona', p);
    localStorage.setItem('edip.theme', d ? 'dark' : 'light');
  }, [persona, dark]);
  const page = await ctx.newPage();
  page.on('console', (m) => m.type() === 'error' && problems.push(`${name}: console ${m.text()}`));
  page.on('pageerror', (e) => problems.push(`${name}: ${e.message}`));
  await page.goto(base + path, { waitUntil: 'networkidle' });
  await page.waitForTimeout(700);
  if (before) await before(page);
  const sideways = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  if (sideways) problems.push(`${name}: scrolls sideways at ${width}px`);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: full });
  await ctx.close();
}

const first = await (await fetch(`${base}/api/board`, { headers: { 'X-Demo-Persona': 'ed-rn' } })).json();
const csn = first.rows[0]?.csn;
await shot('board', '/');
await shot('board-dark', '/', { dark: true });
await shot('board-phone', '/', { width: 390, height: 844 });
await shot('board-unit', '/', { persona: process.env.UNIT || 'unit-sgh-micu' });
if (csn) {
  await shot('handoff', `/handoff/${csn}`);
  await shot('handoff-physician', `/handoff/${csn}`, { persona: 'ed-md' });
  await shot('handoff-dark', `/handoff/${csn}`, { dark: true });
  await shot('handoff-phone', `/handoff/${csn}`, { width: 390, height: 844 });
  await shot('handoff-tablet', `/handoff/${csn}`, { width: 1024, height: 900 });
}
await shot('analytics', '/analytics');
await shot('analytics-dark', '/analytics', { dark: true });
await shot('analytics-phone', '/analytics', { width: 390, height: 844 });
await shot('templates', '/templates');
await shot('guide', '/guide');
await shot('guide-phone', '/guide', { width: 390, height: 844 });
await shot('help-clock', '/', { full: false, before: async (p) => { await p.getByRole('button', { name: /What is this clock/ }).click(); await p.waitForTimeout(400); } });
if (csn) await shot('help-sign', `/handoff/${csn}`, { full: false, before: async (p) => { await p.getByRole('button', { name: /How sign off works/ }).first().click(); await p.waitForTimeout(400); } });
await shot('collapsed', '/', { full: false, before: async (p) => { await p.locator('aside button[aria-label="Collapse sidebar"]').click(); await p.waitForTimeout(300); } });
await browser.close();
console.log(problems.length ? problems.join('\n') : 'no problems');
process.exit(problems.length ? 1 : 0);
