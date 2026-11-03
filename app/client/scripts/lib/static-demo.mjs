// Static demo mode for the browser checks (STATIC_DEMO=1): the build from `npm run build:demo` has no server,
// so the first patient comes from the page instead of /api/board, and every page is watched for requests to
// /api, failed requests and HTTP errors (each one is a problem).
export const STATIC_DEMO = process.env.STATIC_DEMO === '1';

/** Record requests that a static demo must not make. Call before page.goto. */
export function watchRequests(page, name, problems) {
  if (!STATIC_DEMO) return;
  page.on('request', (r) => {
    if (new URL(r.url()).pathname.startsWith('/api')) problems.push(`${name}: request to ${r.url()}`);
  });
  page.on('requestfailed', (r) => {
    // A request cut off because the page was closed or navigated is not a failure of the site.
    if (r.failure()?.errorText !== 'net::ERR_ABORTED') problems.push(`${name}: failed ${r.url()} ${r.failure()?.errorText}`);
  });
  page.on('response', (r) => r.status() >= 400 && problems.push(`${name}: HTTP ${r.status()} ${r.url()}`));
}

/** The csn of the first board row for the ED nurse. */
export async function firstCsn(browser, base, headers = {}) {
  if (!STATIC_DEMO) {
    const first = await (await fetch(`${base}/api/board`, { headers: { 'X-Demo-Persona': 'ed-rn', ...headers } })).json();
    return first.rows[0]?.csn;
  }
  const ctx = await browser.newContext();
  await ctx.addInitScript(() => localStorage.setItem('edip.persona', 'ed-rn'));
  const page = await ctx.newPage();
  await page.goto(base + '/', { waitUntil: 'networkidle' });
  await page.waitForSelector('tbody tr a[href^="/handoff/"]');
  const href = await page.locator('tbody tr a[href^="/handoff/"]').first().getAttribute('href');
  await ctx.close();
  return href.split('/').pop();
}
