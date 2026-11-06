// End to end through the UI. Owners first: nurse A (Jordan Reyes) takes an unassigned patient, nurse B (Robin
// Asante) opens it, sees "Assigned to", acts on it (outside the assignment) and takes it over on the Take over page,
// whose ? opens the right How to use section. Then the handoff: the ED nurse attests and signs another patient and
// the receiving nurse acknowledges with a read back.
// Usage: node scripts/flow.mjs [baseUrl] [screenshotDir]
// The owner steps need two ED nurses, so they run with demo personas (mock mode, the static demo, a synthetic
// deployment) and are skipped, with a line saying so, when the app maps one signed in user through EDIP_ROLE_MAP
// (demo personas off): a single bearer token is a single person, who cannot be nurse A and nurse B.
// STATIC_DEMO=1: the static demo build. Actions live in the tab, so the persona is switched in the menu rather
// than by reloading; then the replay clock moves forward an hour. Requests to /api or failed requests fail.
import { chromium } from 'playwright-core';
import { STATIC_DEMO, watchRequests } from './lib/static-demo.mjs';

const base = process.argv[2] || 'http://localhost:5180';
const out = process.argv[3];
// EDIP_BEARER: a token for a deployed app (Databricks Apps accept workspace OAuth tokens). EDIP_EMAIL: the signed in
// user for a local live run (app/fixtures/live_local.py), sent as X-Forwarded-Email, which Databricks Apps would set.
const headers = {
  ...(process.env.EDIP_BEARER ? { Authorization: `Bearer ${process.env.EDIP_BEARER}` } : {}),
  ...(process.env.EDIP_EMAIL ? { 'X-Forwarded-Email': process.env.EDIP_EMAIL } : {}),
};
const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: 'chrome' });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, extraHTTPHeaders: headers });
await ctx.addInitScript(() => localStorage.getItem('edip.persona') || localStorage.setItem('edip.persona', 'ed-rn'));
const page = await ctx.newPage();
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
page.on('console', (m) => m.type() === 'error' && errors.push(`console ${m.text()}`));
watchRequests(page, 'flow', errors);
await page.goto(base + '/', { waitUntil: 'networkidle' });

/** Become another demo persona: in the menu for the static demo (a reload would clear its actions), else by reload. */
async function become(id, name) {
  if (STATIC_DEMO) {
    await page.getByRole('button', { name: /Switch persona/ }).click();
    await page.getByRole('menuitem', { name: new RegExp(`^${name}`) }).first().click();
  } else {
    await page.evaluate((p) => localStorage.setItem('edip.persona', p), id);
    await page.reload({ waitUntil: 'networkidle' });
  }
  await page.waitForSelector(`text=${name}`);
}

// ---------------------------------------------------------------------------------------------- owners
const demoPersonas = STATIC_DEMO || (await (await fetch(`${base}/api/config`, { headers })).json()).demo_personas;
let owners = 'skipped: demo personas are off, so the app has one ED identity (the signed in user) and no second nurse to hand over to';
if (demoPersonas) {
  const take = page.getByRole('button', { name: /^Take / }).first();
  // On a deployed app earlier runs took patients: when none is unassigned at this hour, move the replay clock on.
  const forward = page.getByRole('button', { name: 'Forward 1 hour' });
  for (let i = 0; i < 12 && !STATIC_DEMO && !(await take.count()) && (await forward.count()); i++) {
    await forward.click();
    await page.waitForLoadState('networkidle');
  }
  if (!(await take.count())) {
    owners = 'skipped: no unassigned boarding patient at this hour';
  } else {
    const patient = (await take.getAttribute('aria-label')).replace(/^Take /, '');
    await take.click();
    await page.waitForSelector(`text=${patient} is now yours.`);
    await become('ed-rn-2', 'Robin Asante, RN');
    await page.getByRole('tab', { name: /Everyone/ }).click();
    await page.getByRole('link', { name: patient, exact: true }).first().click();
    const line = page.getByRole('group', { name: 'Who owns this patient' });
    await line.getByText(/Assigned to Jordan Reyes, RN since/).waitFor();
    // Act on it: the first sign off choice, recorded outside the assignment.
    const panel = page.locator('aside[aria-label="Sign off and context"]');
    const choice = panel.locator('li button[data-choice]').first();
    if (await choice.count()) {
      await choice.click();
      await page.getByText(/outside the assignment \(assigned to Jordan Reyes, RN\)/).first().waitFor();
    }
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Take over' }).click();
    await page.getByRole('radio', { name: /Jordan Reyes, RN/ }).click();
    await page.getByRole('checkbox', { name: `Take ${patient}` }).waitFor();
    if (out) await page.screenshot({ path: `${out}/flow-takeover.png` });
    await page.getByRole('button', { name: /^Take \d+ patients?$/ }).click();
    const done = page.getByText(/^You took over \d+ patients? from Jordan Reyes, RN: /);
    await done.waitFor();
    if (!(await done.textContent()).includes(patient)) errors.push(`Take over did not list ${patient}`);
    // The ? of the patient list opens its How to use topic, and the full guide lands on that section.
    await page.getByRole('button', { name: /^About this list/ }).click();
    const sheet = page.getByRole('dialog', { name: 'Their patients' });
    await sheet.waitFor();
    // The link stays in the page: a page load would clear the static demo's actions.
    await page.evaluate(() => (window.__flow = true));
    await sheet.getByRole('link', { name: 'Open the full guide' }).click();
    await page.waitForURL(/\/guide(\?[^#]*)?#takeover-list$/);
    // The guide loads on demand, then scrolls the section to just under the header.
    const landed = await page.waitForFunction(() => {
      const r = document.getElementById('takeover-list')?.getBoundingClientRect();
      return !!r && r.top >= 0 && r.top < window.innerHeight / 2;
    }, null, { timeout: 5000 }).then(() => true, () => false);
    if (!landed) errors.push('the Take over ? did not land on the How to use section "Their patients"');
    if (!(await page.evaluate(() => window.__flow))) errors.push('opening the full guide from a ? loaded the page again');
    await become('ed-rn', 'Jordan Reyes, RN');
    await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Board' }).click();
    await page.waitForSelector('tbody tr');
    owners = `ok: ${patient} taken by Jordan Reyes, acted on by Robin Asante outside the assignment, taken over by Robin, ? lands on the guide`;
  }
}

// ---------------------------------------------------------------------------------------------- handoff
// The first patient whose nurse handoff is still a draft: on a deployed app, actions from earlier runs persist.
// The ED nurse's board opens on My patients; on a deployed app the drafts left may belong to a colleague, so look
// at Everyone.
const everyone = page.getByRole('tab', { name: /Everyone/ });
if (await everyone.count()) await everyone.click();
const first = page.locator('tbody tr').filter({ hasText: /Nurse\s*Draft/ }).first();
// Every earlier run signs one patient: when none is left unsigned at this hour, move the replay clock on.
for (let i = 0; i < 12 && !STATIC_DEMO && !(await first.count()) && (await page.getByRole('button', { name: 'Forward 1 hour' }).count()); i++) {
  await page.getByRole('button', { name: 'Forward 1 hour' }).click();
  await page.waitForLoadState('networkidle');
}
const name = (await first.locator('a').first().textContent()).trim();
await first.click();
const panel = page.locator('aside[aria-label="Sign off and context"]');
await panel.waitFor();
for (let i = 0; i < 10; i++) {
  const btn = panel.locator('li button[data-choice]').first();
  if (!(await btn.count())) break;
  await btn.click();
  await page.waitForTimeout(400);
}
await panel.getByLabel('Your callback number').fill('x4471');
await panel.getByRole('button', { name: 'Sign', exact: true }).click();
// The signer is the ED nurse persona, or with demo personas off the signed in user (named as they signed in).
const signer = STATIC_DEMO ? 'Jordan Reyes, RN' : (await (await fetch(`${base}/api/me`, { headers: { 'X-Demo-Persona': 'ed-rn', ...headers } })).json()).name;
await page.waitForSelector(`text=Signed by ${signer}`);
if (out) await page.screenshot({ path: `${out}/flow-signed.png` });
const dest = await page.locator('text=Going to').locator('..').locator('dd').textContent();
// The facility and unit code lead the text: "SGH 4C 4005-02 Observation".
const unit = dest.trim().split(/\s+/).slice(0, 2).join('-').toLowerCase();
// The read back needs the receiving nurse, a second person: with demo personas off there is only the signed in user.
let readback = `acknowledged by ${unit}`;
if (!demoPersonas) {
  readback = 'read back skipped: demo personas are off, so there is no receiving nurse to switch to';
} else if (STATIC_DEMO) {
  await page.getByRole('button', { name: /Switch persona/ }).click();
  const [fac, code] = dest.trim().split(' ');
  await page.getByRole('menuitem', { name: new RegExp(`${fac} ${code},`) }).first().click();
  await page.getByLabel('Read back').waitFor();
} else {
  await page.evaluate((u) => localStorage.setItem('edip.persona', `unit-${u}`), unit);
  await page.reload({ waitUntil: 'networkidle' });
}
if (demoPersonas) {
  await page.getByLabel('Read back').fill(`${name}: unstable, ICU level of care. I will repeat vitals and lactate on arrival.`);
  await page.getByRole('button', { name: 'Accept handoff', exact: true }).click();
  await page.waitForSelector('text=Handoff accepted');
  if (out) await page.screenshot({ path: `${out}/flow-acked.png`, fullPage: false });
}
if (STATIC_DEMO) {
  // In the static demo a reload would clear the actions: go back to the board in the app.
  await page.getByRole('link', { name: 'Board' }).first().click();
  await page.waitForSelector('tbody tr');
} else {
  const at = new URL(page.url()).searchParams.get('at');   // the hour the flow moved the clock to
  await page.goto(base + '/' + (at ? `?at=${encodeURIComponent(at)}` : ''), { waitUntil: 'networkidle' });
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
const ok = state.includes(demoPersonas ? 'Acknowledged' : 'Signed') && !errors.length;
console.log(`owners ${owners}`);
console.log(ok ? `flow ok: ${name} signed by ${signer}, ${readback}` : `flow failed: ${state} ${errors.join('; ')}`);
process.exit(ok ? 0 : 1);
