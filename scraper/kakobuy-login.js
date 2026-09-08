/* kfinds — one-time Kakobuy login, so the price scraper can read prices.
 *
 * Opens a real (visible) browser at Kakobuy. YOU log in by hand in that window
 * — your credentials are never read, typed or stored by this script. Once the
 * site shows you as logged in, the session cookies are saved to
 * scraper/.kakobuy-session.json and every scraper reuses them.
 *
 * Run:  node scraper/kakobuy-login.js
 * Re-run whenever prices start coming back empty (session expired).
 */

const fs = require('fs');
const { chromium } = require('playwright');
const { SESSION_FILE } = require('./kakobuy-session');

const LOGIN_URL = 'https://www.kakobuy.com/login';
const PROBE_URL = 'https://item.kakobuy.com/item/details?url=' +
  encodeURIComponent('https://item.taobao.com/item.htm?id=756753317121');
const MAX_WAIT_MS = 10 * 60 * 1000;   // 10 minutes to finish logging in

// The item API is the ground truth: 1055 = still logged out, anything else = in.
async function isLoggedIn(ctx) {
  const page = await ctx.newPage();
  let loggedOut = false, answered = false;
  page.on('response', async (r) => {
    if (!r.url().includes('/api/sapi/item')) return;
    answered = true;
    try { loggedOut = /"code"\s*:\s*1055/.test(await r.text()); } catch { /* ignore */ }
  });
  try {
    await page.goto(PROBE_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    for (let i = 0; i < 15 && !answered; i++) await page.waitForTimeout(500);
  } catch { /* treat as not-verified */ }
  await page.close().catch(() => {});
  return answered && !loggedOut;
}

(async () => {
  console.log('Opening Kakobuy — log in in the browser window that just opened.');
  console.log('(Your credentials are typed only by you, into Kakobuy itself.)\n');

  const browser = await chromium.launch({ headless: false });
  const ctx = await browser.newContext({ locale: 'en-US', viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(LOGIN_URL, { waitUntil: 'domcontentloaded' }).catch(() => {});

  const started = Date.now();
  let ok = false;
  console.log('Waiting for login… (checking every 5s, up to 10 min)');
  // Line-based output so the admin UI can stream it live.
  while (Date.now() - started < MAX_WAIT_MS) {
    await page.waitForTimeout(5000);
    if (await isLoggedIn(ctx)) { ok = true; break; }
    console.log(`… still logged out (${Math.round((Date.now() - started) / 1000)}s) — finish the login in the browser window`);
  }

  if (!ok) {
    console.error('\n✗ Not logged in (timed out). Nothing saved — run the script again.');
    await browser.close();
    process.exit(1);
  }

  await ctx.storageState({ path: SESSION_FILE });
  await browser.close();
  console.log(`\n✓ Logged in. Session saved -> ${SESSION_FILE}`);
  console.log('  Now run:  node scraper/kakobuy-price.js');
})().catch(e => { console.error(e); process.exit(1); });
