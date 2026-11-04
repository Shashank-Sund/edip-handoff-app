// End to end through the UI: the ED nurse attests and signs; the receiving nurse acknowledges with a read back.
// Usage: node scripts/flow.mjs [baseUrl] [screenshotDir]
// STATIC_DEMO=1: the static demo build. Actions live in the tab, so the persona is switched in the menu rather
// than by reloading; then the replay clock moves forward an hour. Requests to /api or failed requests fail.
import { chromium } from 'playwright-core';
import { STATIC_DEMO, watchRequests } from './lib/static-demo.mjs';

const base = process.argv[2] || 'http://localhost:5180';
const out = process.argv[3];
// EDIP_BEARER: a token for a deployed app (Databricks Apps accept workspace OAuth tokens).
const headers = process.env.EDIP_BEARER ? { Authorization: `Bearer ${process.env.EDIP_BEARER}` } : {};
const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: 'chrome' });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, extraHTTPHeaders: headers });
await ctx.addInitScript(() => localStorage.getItem('edip.persona') || localStorage.setItem('edip.persona', 'ed-rn'));
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => m.type() === 'error' && errors.push(`console ${m.text()}`));
watchRequests(page, 'flow', errors);
await page.goto(base + '/', { waitUntil: 'networkidle' });
// The first patient whose nurse handoff is still a draft: on a deployed app, actions from earlier runs persist.
const first = page.locator('tbody tr').filter({ hasText: /Nurse\s*Draft/ }).first();
const name = (await first.locator('a').first().textContent()).trim();
await first.click();
await page.waitForSelector('text=Sign off');
const panel = page.locator('aside[aria-label="Sign off and context"]');
for (let i = 0; i < 10; i++) {
  const btn = panel.locator('li button').first();
  if (!(await btn.count())) break;
  await btn.click();
  await page.waitForTimeout(400);
}
await panel.getByLabel('Your callback number').fill('x4471');
await panel.getByRole('button', { name: 'Sign', exact: true }).click();
await page.waitForSelector('text=Signed by Jordan Reyes, RN');
if (out) await page.screenshot({ path: `${out}/flow-signed.png` });
const dest = await page.locator('text=Going to').locator('..').locator('dd').textContent();
const unit = dest.trim().split(' ').slice(0, 2).join('-').toLowerCase();
if (STATIC_DEMO) {
  await page.getByRole('button', { name: /Switch persona/ }).click();
  const [fac, code] = dest.trim().split(' ');
  await page.getByRole('menuitem', { name: new RegExp(`${fac} ${code},`) }).first().click();
  await page.getByLabel('Read back').waitFor();
} else {
  await page.evaluate((u) => localStorage.setItem('edip.persona', `unit-${u}`), unit);
  await page.reload({ waitUntil: 'networkidle' });
}
await page.getByLabel('Read back').fill(`${name}: unstable, ICU level of care. I will repeat vitals and lactate on arrival.`);
await page.getByRole('button', { name: 'Accept handoff', exact: true }).click();
await page.waitForSelector('text=Handoff accepted');
if (out) await page.screenshot({ path: `${out}/flow-acked.png`, fullPage: false });
if (STATIC_DEMO) {
  // In the static demo a reload would clear the actions: go back to the board in the app.
  await page.getByRole('link', { name: 'Board' }).first().click();
  await page.waitForSelector('tbody tr');
} else {
  await page.goto(base + '/', { waitUntil: 'networkidle' });
}
const state = await page.locator('tbody tr', { hasText: name }).first().textContent();
if (STATIC_DEMO) {
  const clockLabel = () => page.getByRole('button', { name: /Replay time/ }).textContent();
  const before = await clockLabel();
  await page.getByRole('button', { name: 'Forward 1 hour' }).click();
  await page.waitForFunction((b) => ![...document.querySelectorAll('button')].some((x) => x.textContent === b), before);
  const after = await clockLabel();
  const still = await page.locator('tbody tr', { hasText: name }).first().textContent();
  if (before === after || !still.includes('Acknowledged')) errors.push(`clock: ${before} to ${after}, row ${still}`);
  if (out) await page.screenshot({ path: `${out}/flow-clock.png` });
}
await browser.close();
const ok = state.includes('Acknowledged') && !errors.length;
console.log(ok ? `flow ok: ${name} signed by the ED and acknowledged by ${unit}` : `flow failed: ${state} ${errors.join('; ')}`);
process.exit(ok ? 0 : 1);
