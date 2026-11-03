// End to end through the UI: the ED nurse attests and signs; the receiving nurse acknowledges with a read back.
// Usage: node scripts/flow.mjs [baseUrl] [screenshotDir]
import { chromium } from 'playwright-core';

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
await page.goto(base + '/', { waitUntil: 'networkidle' });
const first = page.locator('tbody tr').first();
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
await page.evaluate((u) => localStorage.setItem('edip.persona', `unit-${u}`), unit);
await page.reload({ waitUntil: 'networkidle' });
await page.getByLabel('Read back').fill(`${name}: unstable, ICU level of care. I will repeat vitals and lactate on arrival.`);
await page.getByRole('button', { name: 'Accept handoff', exact: true }).click();
await page.waitForSelector('text=Handoff accepted');
if (out) await page.screenshot({ path: `${out}/flow-acked.png`, fullPage: false });
await page.goto(base + '/', { waitUntil: 'networkidle' });
const state = await page.locator('tbody tr').first().textContent();
await browser.close();
const ok = state.includes('Acknowledged') && !errors.length;
console.log(ok ? `flow ok: ${name} signed by the ED and acknowledged by ${unit}` : `flow failed: ${state} ${errors.join('; ')}`);
process.exit(ok ? 0 : 1);
