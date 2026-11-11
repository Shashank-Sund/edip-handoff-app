// End to end through the UI. Owners first: nurse A (Jordan Reyes) takes an unassigned patient, nurse B (Robin
// Asante) opens it, sees "Assigned to", acts on it (outside the assignment) and takes it over on the Take over page,
// whose ? opens the right How to use section. Then the handoff: the ED nurse attests and signs another patient (one
// with an action list) and its action list becomes tasks; the receiving nurse acknowledges with a read back, then
// marks a task done, one not needed and reassigns one to themselves (outside their role, as the patient still boards),
// finds them on the Tasks page, and its ? lands on the How to use section.
// Usage: node scripts/flow.mjs [baseUrl] [screenshotDir]
// The owner steps need two ED nurses, so they run with demo personas (mock mode, the static demo, a synthetic
// deployment) and are skipped, with a line saying so, when the app maps one signed in user through EDIP_ROLE_MAP
// (demo personas off): a single bearer token is a single person, who cannot be nurse A and nurse B. They are also
// skipped in live mode (a deployed app): its actions are a permanent record, so patients taken and signed by earlier
// runs, at later hours, refuse the next run's actions. The server tests run them on Postgres in CI.
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

/**
 * Move the replay clock on an hour and wait for the board of the new hour: the clock label to change and its
 * board to arrive. A networkidle wait alone returns at once, so a loop would race through every hour.
 */
async function forwardHour() {
  const before = await page.getByRole('button', { name: /Replay time/ }).textContent();
  const board = page.waitForResponse((r) => r.url().includes('/api/board'));
  await page.getByRole('button', { name: 'Forward 1 hour' }).click();
  await board;
  await page.waitForFunction((b) => ![...document.querySelectorAll('button')].some((x) => x.textContent === b), before);
  await page.waitForLoadState('networkidle');
}

// ---------------------------------------------------------------------------------------------- owners
const config = STATIC_DEMO ? { mode: 'static', demo_personas: true } : await (await fetch(`${base}/api/config`, { headers })).json();
let owners = 'skipped: demo personas are off, so the app has one ED identity (the signed in user) and no second nurse to hand over to';
if (config.mode === 'live') {
  owners = 'skipped: live mode keeps every action, so earlier runs leave patients that refuse these steps (they run in mock mode, the static demo and CI)';
} else if (config.demo_personas) {
  const take = page.getByRole('button', { name: /^Take / }).first();
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
    // Jordan's patients went to Robin, so My patients can be empty at this hour.
    await page.getByRole('tab', { name: /Everyone/ }).click();
    await page.waitForSelector('tbody tr');
    owners = `ok: ${patient} taken by Jordan Reyes, acted on by Robin Asante outside the assignment, taken over by Robin, ? lands on the guide`;
  }
}

// ---------------------------------------------------------------------------------------------- handoff
// The first patient whose nurse handoff is still a draft. The ED nurse's board opens on My patients; on a deployed
// app the drafts left may belong to a colleague, so look at Everyone. A deployed app also keeps the actions of
// earlier runs: a handoff they acted on at a later hour refuses this one ("You are viewing an earlier time"), so
// try the next draft, and move the replay clock on when none is left at this hour.
const EARLIER = 'You are viewing an earlier time; move the clock to now to act.';
const panel = page.locator('aside[aria-label="Sign off and context"]');
const refused = new Set();
let name = null;
for (let tries = 0; !name && tries < 24; tries++) {
  const everyone = page.getByRole('tab', { name: /Everyone/ });
  if (await everyone.count()) await everyone.click();
  await page.waitForSelector('tbody tr');
  const drafts = page.locator('tbody tr').filter({ hasText: /Nurse\s*Draft/ });
  let row = null;
  for (let i = 0; i < (await drafts.count()); i++) {
    const n = (await drafts.nth(i).locator('a').first().textContent()).trim();
    if (!refused.has(n)) { row = drafts.nth(i); name = n; break; }
  }
  if (!row) {
    if (STATIC_DEMO || !(await page.getByRole('button', { name: 'Forward 1 hour' }).count())) break;
    await forwardHour();
    continue;
  }
  const before = errors.length;
  await row.click();
  await panel.waitFor();
  // Signing turns the action list into tasks: take a handoff whose SBAR action list has items.
  if (!(await page.locator('#el-E24 li').count())) {
    refused.add(name);
    name = null;
    await page.goBack({ waitUntil: 'networkidle' });
    continue;
  }
  for (let i = 0; i < 10; i++) {
    const btn = panel.locator('li button[data-choice]').first();
    if (!(await btn.count())) break;
    await btn.click();
    await page.waitForTimeout(400);
    if (await page.getByText(EARLIER).count()) break;
  }
  if (await page.getByText(EARLIER).count()) {
    refused.add(name);
    name = null;
    // The refusal is a 409, which the browser logs as a console error: expected here, so not a failure.
    errors.splice(before).filter((e) => !/status of 409/.test(e)).forEach((e) => errors.push(e));
    await page.goBack({ waitUntil: 'networkidle' });
  }
}
if (!name) throw new Error(`no nurse draft with an action list that takes an action (skipped: ${[...refused].join('; ') || 'none'})`);
if (refused.size) console.log(`skipped ${refused.size} handoff(s) with no action list, or later actions from earlier runs: ${[...refused].join('; ')}`);
await panel.getByLabel('Your callback number').fill('x4471');
await panel.getByRole('button', { name: 'Sign', exact: true }).click();
// The signer is the ED nurse persona, or with demo personas off the signed in user (named as they signed in).
const signer = STATIC_DEMO ? 'Jordan Reyes, RN' : (await (await fetch(`${base}/api/me`, { headers: { 'X-Demo-Persona': 'ed-rn', ...headers } })).json()).name;
await page.waitForSelector(`text=Signed by ${signer}`);
if (out) await page.screenshot({ path: `${out}/flow-signed.png` });

// ------------------------------------------------------------------------------------------------ tasks
// The action list became tasks on this patient, the one the flow just signed (so a deployed app's earlier runs,
// whose records sit at later hours, do not refuse these steps). For the receiving nurse's steps (demo personas), make
// sure three are open: an action list with fewer gets tasks added by hand.
const tasksPanel = page.getByRole('region', { name: 'Tasks' });
const openList = tasksPanel.getByRole('list', { name: 'Open tasks' });
await tasksPanel.getByText(`signed by ${signer}`).first().waitFor();
const fromSign = await tasksPanel.getByRole('listitem').filter({ hasText: `signed by ${signer}` }).count();
for (let i = (await openList.getByRole('listitem').count()); config.demo_personas && i < 3; i++) {
  await tasksPanel.getByRole('button', { name: 'Add a task' }).click();
  await tasksPanel.getByLabel('What needs to be done').fill(`Flow check ${i + 1}`);
  await tasksPanel.getByRole('button', { name: 'Add task' }).click();
  await tasksPanel.getByRole('status').filter({ hasText: `Task added: Flow check ${i + 1}` }).waitFor();
}
if (!fromSign) errors.push(`signing ${name} made no tasks from its action list`);
let tasks = `${fromSign} task(s) from the action list`;
const dest = await page.locator('text=Going to').locator('..').locator('dd').textContent();
// The facility and unit code lead the text: "SGH 4C 4005-02 Observation".
const unit = dest.trim().split(/\s+/).slice(0, 2).join('-').toLowerCase();
// The read back needs the receiving nurse, a second person: with demo personas off there is only the signed in user.
let readback = `acknowledged by ${unit}`;
if (!config.demo_personas) {
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
if (config.demo_personas) {
  await page.getByLabel('Read back').fill(`${name}: unstable, ICU level of care. I will repeat vitals and lactate on arrival.`);
  await page.getByRole('button', { name: 'Accept handoff', exact: true }).click();
  await page.waitForSelector('text=Handoff accepted');
  if (out) await page.screenshot({ path: `${out}/flow-acked.png`, fullPage: false });

  // The receiving nurse, on the same patient: marks one task done, one not needed and gives one to themselves. The
  // patient is still in the ED, so the panel says that is outside their role (allowed, and recorded).
  const receiver = `unit-${unit}`;
  const names = async () => openList.getByRole('listitem').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label')));
  const [t1, t2, t3] = (await names()).slice(0, 3);
  const item = (t) => openList.getByRole('listitem', { name: t, exact: true }).first();
  const saved = (text) => tasksPanel.getByRole('status').filter({ hasText: text }).waitFor();
  if (!(await tasksPanel.getByText(/recorded as outside your role/).count())) errors.push('no outside role note for the receiving nurse while the patient boards');
  await item(t1).getByRole('button', { name: 'Done', exact: true }).click();
  await saved(`Done: ${t1}`);
  await item(t2).getByRole('button', { name: 'Not needed', exact: true }).click();
  await item(t2).getByLabel('Why is it not needed?').fill('Done in the ED before transfer');
  await item(t2).getByRole('button', { name: 'Save' }).click();
  await saved(`Not needed: ${t2}`);
  await item(t3).getByRole('button', { name: 'Reassign', exact: true }).click();
  await item(t3).getByLabel('Give it to').selectOption(receiver);
  await item(t3).getByRole('button', { name: 'Save' }).click();
  await saved(`: ${t3}`);
  if (out) await page.screenshot({ path: `${out}/flow-tasks.png` });
  // The Tasks page lists the task given to them, and the two they closed under Recently completed.
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Tasks' }).click();
  await page.getByRole('listitem', { name: t3, exact: true }).first().waitFor();
  await page.getByRole('button', { name: /Recently completed/ }).click();
  for (const t of [t1, t2]) {
    if (!(await page.getByRole('listitem', { name: t, exact: true }).count())) errors.push(`the Tasks page does not list "${t}" under Recently completed`);
  }
  // Its ? opens the How to use topic, and the full guide lands on it without loading the page again.
  await page.getByRole('button', { name: /^About this page/ }).click();
  const sheet = page.getByRole('dialog', { name: 'The Tasks page' });
  await sheet.waitFor();
  await page.evaluate(() => (window.__flowTasks = true));
  await sheet.getByRole('link', { name: 'Open the full guide' }).click();
  await page.waitForURL(/\/guide(\?[^#]*)?#tasks-page$/);
  const landed = await page.waitForFunction(() => {
    const r = document.getElementById('tasks-page')?.getBoundingClientRect();
    return !!r && r.top >= 0 && r.top < window.innerHeight / 2;
  }, null, { timeout: 5000 }).then(() => true, () => false);
  if (!landed) errors.push('the Tasks page ? did not land on the How to use section "The Tasks page"');
  if (!(await page.evaluate(() => window.__flowTasks))) errors.push('opening the full guide from the Tasks page ? loaded the page again');
  tasks += `; ${receiver}: done "${t1}", not needed "${t2}", reassigned "${t3}" to themselves; the Tasks page lists them; ? lands on the guide`;
} else {
  tasks += '; receiving nurse steps skipped: demo personas are off, so there is no receiving nurse to switch to';
}
// -------------------------------------------------------------------------------------- since you looked
// Person A opens the patient just signed (a look), person B adds a task to it, and A then sees "New" on the board
// row and the task in Since you last looked, which goes to the Tasks panel; back on the board the marker is gone.
// B adds one more and A clears it with Mark as seen. Each task text is new to this run and every step acts on the
// patient this run signed, at the clock the flow is on, so a deployed app's earlier runs do not refuse it (a look
// never is refused). With demo personas A is Jordan Reyes and B Robin Asante; live (demo personas off) A is
// EDIP_EMAIL and B EDIP_EMAIL_2, a second user in EDIP_ROLE_MAP, else the steps are skipped: one signed in user is
// one person, whose own changes never show.
const mainNav = () => page.getByRole('navigation', { name: 'Main' });
const toBoard = async () => {
  await mainNav().getByRole('link', { name: 'Board' }).click();
  await page.getByRole('tablist', { name: 'Filter by status' }).waitFor();
  const everyone = page.getByRole('tab', { name: /Everyone/ });
  if (await everyone.count()) await everyone.click();
  await page.locator('tbody tr', { hasText: name }).first().waitFor().catch(async (e) => {
    if (out) await page.screenshot({ path: `${out}/flow-since-fail.png`, fullPage: true });
    throw e;
  });
};
/** Switch to the person by their persona (demo personas) or their email (live), on the page shown now. */
async function as(who) {
  if (who.email) {
    await ctx.setExtraHTTPHeaders({ ...headers, 'X-Forwarded-Email': who.email });
    await page.reload({ waitUntil: 'networkidle' });
    await page.getByText(who.email).first().waitFor();
  } else {
    await become(who.id, who.name);
  }
}
/** The look a handoff page records after its case: wait for it (in the static demo it is answered in the tab). */
const looked = () => (STATIC_DEMO ? page.waitForTimeout(300) : page.waitForResponse((r) => r.url().includes('/seen') && r.request().method() === 'POST'));
const people = config.demo_personas
  ? [{ id: 'ed-rn', name: 'Jordan Reyes, RN' }, { id: 'ed-rn-2', name: 'Robin Asante, RN' }]
  : process.env.EDIP_EMAIL && process.env.EDIP_EMAIL_2 ? [{ email: process.env.EDIP_EMAIL }, { email: process.env.EDIP_EMAIL_2 }] : null;
let since = 'skipped: demo personas are off and EDIP_EMAIL_2 (a second user in EDIP_ROLE_MAP) is not set, so there is one person, whose own changes never show';
if (people) {
  const [a, b] = people;
  const run = Date.now().toString(36);
  const row = () => page.locator('tbody tr', { hasText: name }).first();
  const addTask = async (text) => {
    await tasksPanel.getByRole('button', { name: 'Add a task' }).click();
    await tasksPanel.getByLabel('What needs to be done').fill(text);
    await tasksPanel.getByRole('button', { name: 'Add task' }).click();
    await tasksPanel.getByRole('status').filter({ hasText: `Task added: ${text}` }).waitFor();
  };
  const bAdds = async (text) => {
    await as(b);
    await toBoard();
    const look = looked();
    await row().locator('a').first().click();
    await look;
    await addTask(text);
    await toBoard();
  };
  // A opens the patient: the look the next open compares with.
  await toBoard();
  await as(a);
  await toBoard();
  const first = looked();
  await row().locator('a').first().click();
  await first;
  await toBoard();
  const t1 = `Since check ${run}`;
  await bAdds(t1);
  await as(a);
  await toBoard();
  await row().getByText(/^New · \d+ changes?$/).waitFor();
  if (out) await page.screenshot({ path: `${out}/flow-since-board.png` });
  const look = looked();
  await row().locator('a').first().click();
  const box = page.getByRole('region', { name: /^Since you last looked/ });
  await box.waitFor();
  await look;
  const change = box.getByRole('button', { name: new RegExp(`added the task "${t1}"`) });
  if (!(await change.count())) errors.push(`Since you last looked does not list the task "${t1}"`);
  else {
    if (out) await page.screenshot({ path: `${out}/flow-since-banner.png` });
    await change.click();
    await page.waitForFunction(() => document.activeElement?.id === 'tasks', null, { timeout: 5000 }).catch(() => errors.push('the change did not go to the Tasks panel'));
  }
  // Opened: the marker is gone. Another change by B, cleared with Mark as seen.
  await toBoard();
  if (await row().getByText(/^New · /).count()) errors.push('the board still shows New after opening the patient');
  const t2 = `Since check ${run} again`;
  await bAdds(t2);
  await as(a);
  await toBoard();
  await row().getByText(/^New · 1 change$/).waitFor();
  await row().getByRole('button', { name: /^Mark as seen: / }).click();
  await row().getByText(/^New · /).waitFor({ state: 'detached' });
  since = `ok: ${a.name ?? a.email} sees New and the task "${t1}" by ${b.name ?? b.email} in Since you last looked, which goes to the Tasks panel; opening clears it; Mark as seen clears the next one`;
}

if (STATIC_DEMO) {
  // In the static demo a reload would clear the actions: go back to the board in the app.
  await page.getByRole('link', { name: 'Board' }).first().click();
  await page.waitForSelector('tbody tr');
} else {
  const at = new URL(page.url()).searchParams.get('at');   // the hour the flow moved the clock to
  await page.goto(base + '/' + (at ? `?at=${encodeURIComponent(at)}` : ''), { waitUntil: 'networkidle' });
}
// The since steps leave the ED nurse, whose board opens on My patients: the patient is under Everyone.
await page.getByRole('tablist', { name: 'Filter by status' }).waitFor();
if (await page.getByRole('tab', { name: /Everyone/ }).count()) await page.getByRole('tab', { name: /Everyone/ }).click();
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
const ok = state.includes(config.demo_personas ? 'Acknowledged' : 'Signed') && !errors.length;
console.log(`owners ${owners}`);
console.log(`tasks ${tasks}`);
console.log(`since ${since}`);
console.log(ok ? `flow ok: ${name} signed by ${signer}, ${readback}` : `flow failed: ${state} ${errors.join('; ')}`);
process.exit(ok ? 0 : 1);
