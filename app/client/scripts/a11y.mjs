// Accessibility check with axe-core on every page, in light and dark. Usage: node scripts/a11y.mjs [baseUrl] [--json file]
// Injects axe-core from node_modules into the installed Chrome (or CHROME_PATH). Exits 1 on any violation.
// STATIC_DEMO=1 checks the static demo build (scripts/lib/static-demo.mjs).
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { chromium } from 'playwright-core';
import { firstCsn, STATIC_DEMO } from './lib/static-demo.mjs';

const args = process.argv.slice(2);
const jsonAt = args.indexOf('--json');
const jsonOut = jsonAt >= 0 ? args.splice(jsonAt, 2)[1] : null;
const base = args[0] || 'http://localhost:5180';
const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: 'chrome' });
const csn = await firstCsn(browser, base);
/** A patient with open tasks (the demo seeds some): the first one on the ED nurse's Tasks page. */
async function taskCsn() {
  const ctx = await browser.newContext();
  await ctx.addInitScript(() => localStorage.setItem('edip.persona', 'ed-rn'));
  const page = await ctx.newPage();
  await page.goto(base + '/tasks', { waitUntil: 'networkidle' });
  const link = page.locator('main a[href^="/handoff/"]').first();
  const href = (await link.count()) ? await link.getAttribute('href') : null;
  await ctx.close();
  return href ? href.split('?')[0].split('/').pop() : null;
}
const tasked = await taskCsn();
/**
 * Opening a handoff records a look, so its Since you last looked box shows once. A server in mock mode starts the demo
 * over before each view of the box; the static demo starts over on every load. Live, nothing can be started over, so
 * those views are left out (with a line saying so).
 */
const mock = STATIC_DEMO || (await (await fetch(`${base}/api/config`)).json()).mode === 'mock';
async function freshLooks() {
  if (!STATIC_DEMO) await fetch(`${base}/api/demo/reset`, { method: 'POST' });
}
/** A patient with changes since the ED nurse last looked (the demo seeds looks): the first board row marked New. */
async function sinceCsn() {
  const ctx = await browser.newContext();
  await ctx.addInitScript(() => localStorage.setItem('edip.persona', 'ed-rn'));
  const page = await ctx.newPage();
  await page.goto(base + '/', { waitUntil: 'networkidle' });
  const link = page.locator('tbody tr', { hasText: /New · \d+ change/ }).first().locator('a[href^="/handoff/"]').first();
  const href = (await link.count()) ? await link.getAttribute('href') : null;
  await ctx.close();
  return href ? href.split('?')[0].split('/').pop() : null;
}
let changed = null;
if (mock) {
  await freshLooks();
  changed = await sinceCsn();
} else {
  console.log('Since you last looked views skipped: they need mock mode or the static demo, which can start the demo over');
}
const pages = ['/', csn && `/handoff/${csn}`, '/tasks', '/takeover', '/analytics', '/templates', '/guide'].filter(Boolean).map((path) => ({ path }));
// A few extra states: other roles, a phone width and open dialogs. The open persona menu is left out: Radix
// hides the page behind a modal menu with aria-hidden, which axe reports although focus stays in the menu.
const extra = [
  csn && { path: `/handoff/${csn}`, persona: 'ed-md', label: 'physician' },
  csn && { path: `/handoff/${csn}`, persona: process.env.UNIT || 'unit-sgh-micu', label: 'unit nurse' },
  { path: '/', width: 390, label: 'phone' },
  { path: '/', label: 'clock popover', open: /Replay time/ },
  { path: '/', label: 'help panel', open: /How to read the board/ },
  // Take over as the second ED nurse, relieving the first (whose demo patients are listed), and its ? panel.
  { path: '/takeover', persona: 'ed-rn-2', label: 'colleague picked', pick: /Jordan Reyes/ },
  { path: '/takeover', label: 'help panel', open: /About choosing a colleague/ },
  { path: '/', label: 'Everyone tab', tab: /Everyone/ },
  // The Tasks page for the physician seeded with tasks, Everyone, the viewer, a phone, and its ? panel; the panel on
  // the handoff page is on every handoff above.
  { path: '/tasks', persona: 'ed-md-2', label: 'physician' },
  { path: '/tasks', label: 'Everyone tab', tab: /Everyone/ },
  { path: '/tasks', persona: 'viewer', label: 'viewer' },
  { path: '/tasks', width: 390, label: 'phone' },
  { path: '/tasks', label: 'help panel', open: /About this page/ },
  tasked && { path: `/handoff/${tasked}`, label: 'tasks panel' },
  tasked && { path: `/handoff/${tasked}`, persona: 'viewer', label: 'tasks panel, viewer' },
  tasked && { path: `/handoff/${tasked}`, label: 'tasks panel, add a task open', open: /^Add a task/ },
  // What changed since you looked: the board marker with Mark as seen and its ? panel, the box on the handoff page,
  // its ? panel, and a phone.
  changed && { path: '/', label: 'since help panel', fresh: true, open: /^About Mark as seen/ },
  changed && { path: `/handoff/${changed}`, label: 'since you last looked', fresh: true, wait: /^Since you last looked/ },
  changed && { path: `/handoff/${changed}`, label: 'since help panel', fresh: true, open: /^About Since you last looked/ },
  changed && { path: `/handoff/${changed}`, width: 390, label: 'since you last looked, phone', fresh: true, wait: /^Since you last looked/ },
].filter(Boolean);

const report = [];
for (const dark of [false, true]) {
  for (const { path, persona = 'ed-rn', width = 1440, label, open, pick, tab, fresh, wait } of [...pages, ...extra]) {
    if (fresh) await freshLooks();
    // bypassCSP: a strict Content-Security-Policy (the static demo's) would block the injected axe script.
    const ctx = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: dark ? 'dark' : 'light', bypassCSP: true });
    await ctx.addInitScript(([p, d]) => {
      localStorage.setItem('edip.persona', p);
      localStorage.setItem('edip.theme', d ? 'dark' : 'light');
    }, [persona, dark]);
    const page = await ctx.newPage();
    await page.goto(base + path, { waitUntil: 'networkidle' });
    if (wait) await page.getByRole('region', { name: wait }).waitFor();
    if (open) await page.getByRole('button', { name: open }).first().click();
    if (pick) await page.getByRole('radio', { name: pick }).first().click();
    if (tab) await page.getByRole('tab', { name: tab }).first().click();
    // Let entrance transitions finish so contrast is measured on final colors.
    await page.waitForTimeout(900);
    await page.addScriptTag({ content: axeSource });
    const result = await page.evaluate(async () => {
      // eslint-disable-next-line no-undef
      const r = await axe.run(document, { resultTypes: ['violations'] });
      return r.violations.map((v) => ({
        id: v.id,
        impact: v.impact,
        help: v.help,
        nodes: v.nodes.map((n) => ({ target: n.target.join(' '), summary: n.failureSummary, html: n.html.slice(0, 200) })),
      }));
    });
    const name = `${path}${label ? ` ${label}` : ''} (${dark ? 'dark' : 'light'})`;
    report.push({ page: name, violations: result });
    const count = result.reduce((s, v) => s + v.nodes.length, 0);
    console.log(`${name}: ${result.length ? `${result.length} rules, ${count} nodes` : 'no violations'}`);
    for (const v of result) {
      console.log(`  [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length})`);
      for (const n of v.nodes.slice(0, 4)) console.log(`      ${n.target}`);
    }
    await ctx.close();
  }
}
await browser.close();
if (jsonOut) writeFileSync(jsonOut, JSON.stringify(report, null, 2));
const total = report.reduce((s, p) => s + p.violations.length, 0);
console.log(total ? `${total} violations across ${report.length} page views` : 'no violations');
process.exit(total ? 1 : 0);
