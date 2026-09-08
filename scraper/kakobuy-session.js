/* kfinds — shared Kakobuy browser session helpers
 *
 * Kakobuy's item API (hbapi.kakobuy.com/api/sapi/item) answers
 *   {"code":1055,"msg":"Please login first."}
 * to logged-out visitors, and the page then renders "This product may not
 * exist" for EVERY link. So prices can only be read from a logged-in session.
 *
 * The session is created once, by hand, with:  node scraper/kakobuy-login.js
 * It is stored as a Playwright storageState (cookies + localStorage) in
 * scraper/.kakobuy-session.json and reused by every scraper here.
 */

const fs = require('fs');
const path = require('path');

const SESSION_FILE = path.join(__dirname, '.kakobuy-session.json');

function hasSession() {
  return fs.existsSync(SESSION_FILE);
}

// Playwright throws on a malformed/empty state file — treat that as "no session".
function sessionState() {
  if (!hasSession()) return undefined;
  try {
    const raw = JSON.parse(fs.readFileSync(SESSION_FILE, 'utf8'));
    return (raw && (raw.cookies || raw.origins)) ? SESSION_FILE : undefined;
  } catch {
    return undefined;
  }
}

// Analytics / ads / chat widgets — pure latency on every page load.
const BLOCK_HOSTS = [
  'google-analytics.com', 'googletagmanager.com', 'google.com/ccm',
  'analytics.google.com', 'doubleclick.net', 'googleadservices.com',
  'facebook.net', 'facebook.com', 'reddit.com', 'redditstatic.com',
  'tiktok.com', 'bing.com', 'clarity.ms', 'hotjar.com', 'sentry.io',
  'crisp.chat', 'intercom.io', 'zendesk.com', 'cdn-cgi/rum'
];
// Heavy resource types we never need to read a price.
// NOTE: do NOT add 'stylesheet' — Kakobuy's app never boots without its CSS
// (the page stays blank and the item API is never even called).
const BLOCK_TYPES = new Set(['image', 'media', 'font']);

/* Attach request filtering to a context (once per context — cheaper than
 * per-page routing). `keepImages` is used by the admin, which also wants the
 * product photo, so it must let images through. */
async function applyBlocking(ctx, { keepImages = false } = {}) {
  await ctx.route('**/*', (route) => {
    const req = route.request();
    const url = req.url();
    if (BLOCK_HOSTS.some(h => url.includes(h))) return route.abort();
    const type = req.resourceType();
    if (keepImages && type === 'image') return route.continue();
    if (BLOCK_TYPES.has(type)) return route.abort();
    return route.continue();
  });
}

async function newContext(browser, opts = {}) {
  const ctx = await browser.newContext({
    locale: 'en-US',
    storageState: sessionState(),
    viewport: { width: 1280, height: 900 },
  });
  await applyBlocking(ctx, opts);
  return ctx;
}

const LOGIN_HINT =
  'Kakobuy requires a logged-in session to show prices.\n' +
  '  In the admin: open the Yupoo panel and press "Login Kakobuy".\n' +
  '  From a terminal: node scraper/kakobuy-login.js\n' +
  '  Either way a browser window opens — log in by hand, then the session is saved.';

module.exports = { SESSION_FILE, hasSession, sessionState, newContext, applyBlocking, LOGIN_HINT };
