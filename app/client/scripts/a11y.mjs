// Accessibility check with axe-core on every page, in light and dark. Usage: node scripts/a11y.mjs [baseUrl] [--json file]
// Injects axe-core from node_modules into the installed Chrome (or CHROME_PATH). Exits 1 on any violation.
// STATIC_DEMO=1 checks the static demo build (scripts/lib/static-demo.mjs).
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { chromium } from 'playwright-core';
import { firstCsn } from './lib/static-demo.mjs';

const args = process.argv.slice(2);
const jsonAt = args.indexOf('--json');
const jsonOut = jsonAt >= 0 ? args.splice(jsonAt, 2)[1] : null;
const base = args[0] || 'http://localhost:5180';
const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: 'chrome' });
const csn = await firstCsn(browser, base);
const pages = ['/', csn && `/handoff/${csn}`, '/analytics', '/templates', '/guide'].filter(Boolean).map((path) => ({ path }));
// A few extra states: other roles, a phone width and open dialogs. The open persona menu is left out: Radix
// hides the page behind a modal menu with aria-hidden, which axe reports although focus stays in the menu.
const extra = [
  csn && { path: `/handoff/${csn}`, persona: 'ed-md', label: 'physician' },
  csn && { path: `/handoff/${csn}`, persona: process.env.UNIT || 'unit-sgh-micu', label: 'unit nurse' },
  { path: '/', width: 390, label: 'phone' },
  { path: '/', label: 'clock popover', open: /Replay time/ },
  { path: '/', label: 'help panel', open: /How to read the board/ },
].filter(Boolean);

const report = [];
for (const dark of [false, true]) {
  for (const { path, persona = 'ed-rn', width = 1440, label, open } of [...pages, ...extra]) {
    // bypassCSP: a strict Content-Security-Policy (the static demo's) would block the injected axe script.
    const ctx = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: dark ? 'dark' : 'light', bypassCSP: true });
    await ctx.addInitScript(([p, d]) => {
      localStorage.setItem('edip.persona', p);
      localStorage.setItem('edip.theme', d ? 'dark' : 'light');
    }, [persona, dark]);
    const page = await ctx.newPage();
    await page.goto(base + path, { waitUntil: 'networkidle' });
    if (open) await page.getByRole('button', { name: open }).first().click();
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
