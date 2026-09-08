/* kfinds — Kakobuy price + weight fetcher (headless)
 *
 * Kakobuy's price API is encrypted, so we render the product page in a headless
 * browser and read the displayed price (e.g. "CNY ￥388 ≈ $ 62.28") and Weight(g).
 * Works for any link regardless of the Yupoo title.
 *
 * REQUIRES A LOGIN: hbapi.kakobuy.com/api/sapi/item replies "Please login first"
 * to anonymous visitors and the page then claims every product "may not exist".
 * Create the session once with:  node scraper/kakobuy-login.js
 *
 * Standalone test:  node scraper/kakobuy-price.js "<kakobuy url>"
 * Batch (updates data/products.json + assets/products-data.js):
 *                   node scraper/kakobuy-price.js       (env: LIMIT, CONCURRENCY, FORCE=1, RETRY_DEAD=1)
 */

const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const { hasSession, newContext, LOGIN_HINT } = require('./kakobuy-session');

const DATA = path.join(__dirname, '..', 'data', 'products.json');
const JSOUT = path.join(__dirname, '..', 'assets', 'products-data.js');
const SELLERS_FILE = path.join(__dirname, 'sellers.json');
const NAV_TIMEOUT = 25000;
const RENDER_TIMEOUT = 15000;   // how long to wait for the price to render

// Rebuild the seller list (name + live product count) so the JS bundle stays in sync.
function buildSellerMeta(products) {
  let names = {};
  try {
    for (const s of JSON.parse(fs.readFileSync(SELLERS_FILE, 'utf8'))) names[s.seller] = s.name || s.seller;
  } catch { /* no config — fall back to raw seller ids */ }
  const counts = {};
  for (const p of products) counts[p.seller] = (counts[p.seller] || 0) + 1;
  return Object.keys(counts).map(seller => ({ seller, name: names[seller] || seller, count: counts[seller] }));
}

function writeJsBundle(products) {
  fs.writeFileSync(JSOUT,
    `/* Auto-generated — do not edit by hand. */\n` +
    `window.KF_PRODUCTS = ${JSON.stringify(products)};\n` +
    `window.KF_SELLERS = ${JSON.stringify(buildSellerMeta(products))};\n`);
}

// Batch-ul primeste deja linkuri Kakobuy, dar botul da linkul brut de
// Taobao/Weidian/1688. Fara conversie ajungeam pe pagina magazinului, unde
// scrie doar pretul de start ("¥440起") si nu exista modelele Kakobuy.
function toKakobuy(url) {
  if (/kakobuy\.com/i.test(url)) return url;
  return 'https://item.kakobuy.com/item/details?url=' + encodeURIComponent(url);
}

/* Watch the item API on a page. It is the only trustworthy way to tell
 * "you are logged out" (code 1055) apart from "this product is really gone" —
 * the rendered page shows the same "may not exist" text for both. */
function watchItemApi(page) {
  const state = { loggedOut: false, answered: false };
  page.on('response', async (r) => {
    if (!r.url().includes('/api/sapi/item')) return;
    state.answered = true;
    try {
      const body = await r.text();
      if (/"code"\s*:\s*1055/.test(body) || /login first/i.test(body)) state.loggedOut = true;
    } catch { /* body already gone — ignore */ }
  });
  return state;
}

// Read price/weight out of the rendered page. Tolerant of the several shapes
// Kakobuy uses ("CNY ￥388 ≈ $ 62.28", or ￥ and $ in separate nodes).
function extractFromPage() {
  const text = document.body.innerText || '';
  const num = (s) => (s == null ? null : parseFloat(String(s).replace(/,/g, '')));
  // Un produs cu variante afiseaza un interval ("CNY ￥388-￥698 ≈ $ 62.28-$112.03"),
  // uneori cu simbolul repetat, uneori doar cu al doilea numar. A doua parte e
  // optionala, ca sa mearga la fel si produsele cu pret unic.
  const combined =
       text.match(/CNY\s*[¥￥]\s*([\d.,]+)(?:\s*[-–—~]\s*[¥￥$]?\s*([\d.,]+))?\s*≈\s*\$?\s*([\d.,]+)(?:\s*[-–—~]\s*\$?\s*([\d.,]+))?/)
    || text.match(/[¥￥]\s*([\d.,]+)(?:\s*[-–—~]\s*[¥￥$]?\s*([\d.,]+))?\s*≈\s*\$?\s*([\d.,]+)(?:\s*[-–—~]\s*\$?\s*([\d.,]+))?/);
  let cny = combined ? num(combined[1]) : null;
  let cnyMax = combined ? num(combined[2]) : null;
  let usd = combined ? num(combined[3]) : null;
  let usdMax = combined ? num(combined[4]) : null;
  if (cny == null) {
    const y = text.match(/[¥￥]\s*([\d.,]+)(?:\s*[-–—~]\s*[¥￥]?\s*([\d.,]+))?/);
    if (y) { cny = num(y[1]); cnyMax = num(y[2]); }
    const d = text.match(/\$\s*([\d.,]+)(?:\s*[-–—~]\s*\$?\s*([\d.,]+))?/);
    if (d) { usd = num(d[1]); usdMax = num(d[2]); }
  }
  // Un "interval" care e de fapt acelasi numar nu e interval.
  if (cnyMax != null && cnyMax <= cny) cnyMax = null;
  if (usdMax != null && usdMax <= usd) usdMax = null;
  const weight = text.match(/Weight\s*\(g\)\s*[:：]?\s*([\d.]+)/i) || text.match(/([\d.]+)\s*g/);
  // The real product title from the source listing — Yupoo album titles are
  // often just "<brand code> price：438CNY", so this is the only usable name.
  const el = document.querySelector('h1, .goods-title, .item-title, .detail-title');
  const title = el ? el.textContent.trim().replace(/\s+/g, ' ').slice(0, 160) : '';
  return {
    priceCNY: cny,
    priceCNYMax: cnyMax,
    priceUSD: usd,
    priceUSDMax: usdMax,
    weightG: weight ? parseFloat(weight[1]) : null,
    title,
  };
}


/* Toate variantele, direct din starea paginii.
 *
 * Pretul mare afisat e al variantei selectate si, pe multe produse, ramane cel
 * din antet ("price") chiar si dupa ce dai click pe alt model - de aceea
 * baleiajul prin click raporta un singur pret. Lista completa de SKU-uri sta
 * insa in componenta Vue (itemInfo.skus.sku), cu pretul deja convertit in
 * dolari (cur_price), asa ca o citim de acolo: e si exact, si instant.
 *
 * Marimile fac parte din acelasi SKU ("BOOTS;38"), dar nu schimba pretul, deci
 * minimul si maximul peste toate SKU-urile sunt exact cele dintre modele. */
/* Preturile tuturor variantelor si pozele produsului, dintr-o singura citire a
 * starii paginii.
 *
 * Pretul mare afisat e al variantei selectate si, pe multe produse, ramane cel
 * din antet chiar si dupa ce dai click pe alt model - de aceea un baleiaj prin
 * click raporta un singur pret. Lista completa de SKU-uri sta insa in
 * componenta Vue (itemInfo.skus.sku), cu pretul deja convertit in dolari
 * (cur_price), impreuna cu pozele. Marimile fac parte din acelasi SKU
 * ("BOOTS;38") dar nu schimba pretul, deci minimul si maximul peste toate
 * SKU-urile sunt exact cele dintre modele.
 *
 * Totul sta intr-o singura functie fiindca `page.evaluate` trimite doar functia
 * data, fara ajutoarele din jurul ei. */
function readItemState() {
  const el = document.querySelector('.sku-content') || document.querySelector('.item-props');
  let n = el, vm = null;
  for (let i = 0; i < 10 && n; i++, n = n.parentElement) if (n.__vue__) { vm = n.__vue__; break; }
  let c = vm, info = null;
  for (let i = 0; i < 6 && c; i++, c = c.$parent) {
    if (c._data && c._data.itemInfo) { info = c._data.itemInfo; break; }
  }
  if (!info) return { prices: [], images: [] };

  const skus = Array.isArray(info.skus) ? info.skus : (info.skus || {}).sku;
  const prices = Array.isArray(skus)
    ? skus.map((s) => Number(s.cur_price)).filter((v) => v > 0) : [];

  // Catalogul intai, apoi cate o poza pe varianta. Galeria din pagina poate sa
  // nu randeze deloc (uneori apare un captcha in loc), dar starea e incarcata.
  const images = [];
  const push = (u) => {
    if (typeof u === 'string' && /^https?:/.test(u) && !images.includes(u)) images.push(u);
  };
  (Array.isArray(info.item_imgs) ? info.item_imgs : []).forEach((i) => push(i && (i.url || i)));
  push(info.pic_url);
  Object.values(info.props_img || {}).forEach(push);
  Object.values(info.sku_imgs || {}).forEach(push);

  return { prices, images };
}


/* Pretul afisat e al variantei selectate. Kakobuy nu arata un interval nicaieri:
 * ca sa afli minimul si maximul trebuie sa dai click pe fiecare model din
 * "Color". Marimea nu schimba pretul, deci lista de marimi e ignorata. */
// Modelele stau in .item-props ca .item > .spec > img.props-image. Clickul
// trebuie dat pe .item (pe .spec nu se selecteaza nimic), iar randul de marimi
// n-are poze, deci `:has(img)` il sare din constructie.
const SWATCH = '.item-props .item:has(img)';
const MAX_SWATCHES = 30;        // produse cu zeci de modele: destul, si marginim timpul
const SWATCH_SETTLE = 1200;     // cat asteptam pretul dupa un click

// Pretul curent, ca numar. null cat timp pagina inca randeaza.
function readPriceNow() {
  const el = document.querySelector('.sku-price, .price');
  const t = (el ? el.textContent : document.body.innerText) || '';
  const m = t.match(/[¥￥]\s*([\d.,]+)\s*≈\s*\$?\s*([\d.,]+)/);
  return m ? parseFloat(m[2].replace(/,/g, '')) : null;
}

/* Toate preturile in dolari ale unui produs, cate unul pe model.
 * Presupune pagina deja incarcata si pretul de baza randat. */
async function collectVariantPrices(page, basePrice) {
  // Intai lista de SKU-uri: e completa si nu costa nimic.
  const fromState = (await page.evaluate(readItemState).catch(() => ({}))).prices;
  if (fromState && fromState.length) return fromState;

  // Fara ea (alt layout, alta versiune de pagina) ramane varianta lenta:
  // click pe fiecare model si citit pretul afisat.
  const prices = basePrice != null ? [basePrice] : [];
  let swatches = [];
  try {
    // Modelele se randeaza dupa pret, deci le asteptam; fara asta gaseam zero
    // si raportam un singur pret pentru produse cu 20 de variante.
    await page.waitForSelector(SWATCH, { timeout: SWATCH_SETTLE * 3 }).catch(() => {});
    swatches = (await page.$$(SWATCH)).slice(0, MAX_SWATCHES);
  } catch { return prices; }
  if (swatches.length < 2) return prices;

  // Kakobuy deschide un dialog ("I have read and willing to take the risk")
  // care acopera toata pagina: fara sa-l inchidem, fiecare click asteapta
  // degeaba pana la timeout si nu se selecteaza niciun model.
  for (const sel of ['.risk-footer button', '.ant-modal-close']) {
    const btn = await page.$(sel);
    if (btn) { await btn.click({ timeout: 2000 }).catch(() => {}); break; }
  }

  let last = basePrice;
  for (const el of swatches) {
    try {
      await el.click({ timeout: 1500 })
        .catch(() => el.evaluate((n) => n.click()));
      // Doua modele pot avea acelasi pret, deci lipsa unei schimbari nu e o
      // eroare: asteptam scurt si citim oricum ce scrie acum.
      await page.waitForFunction(
        (prev) => {
          const t = document.body.innerText || '';
          const m = t.match(/[¥￥]\s*[\d.,]+\s*≈\s*\$?\s*([\d.,]+)/);
          return m ? parseFloat(m[1].replace(/,/g, '')) !== prev : false;
        }, last, { timeout: SWATCH_SETTLE, polling: 100 }).catch(() => {});
      const now = await page.evaluate(readPriceNow);
      if (now != null) { prices.push(now); last = now; }
    } catch { /* swatch acoperit sau disparut la re-render - trecem mai departe */ }
  }
  return prices;
}


/* Linkul scurt de afiliat (ikako.vip/xxxxx).
 *
 * Nu se poate compune din id-ul produsului: Kakobuy il creeaza pe server, legat
 * de contul logat, si il pune intr-un camp abia dupa ce apesi "Affiliate Share".
 * Deci il cerem la fel cum ar face-o un om. Costa ~6s in plus, de aceea se cere
 * anume, cu --share, nu la fiecare produs. */
async function fetchShareLink(page) {
  for (const sel of ['.risk-footer button', '.ant-modal-close']) {
    const btn = await page.$(sel);
    if (btn) { await btn.click({ timeout: 2000 }).catch(() => {}); break; }
  }
  const share = await page.$('text=Affiliate Share');
  if (!share) return null;
  await share.click({ timeout: 5000 }).catch(() => {});
  try {
    const handle = await page.waitForFunction(() => {
      for (const i of document.querySelectorAll('input, textarea')) {
        if (/https?:\/\/(ikako\.vip|sl\.kakobuy\.com)\//.test(i.value || '')) return i.value;
      }
      return false;
    }, null, { timeout: 12000, polling: 250 });
    return await handle.jsonValue();
  } catch {
    return null;
  }
}


/* Fetch one product on an ALREADY-OPEN page (pages are reused across products
 * — far cheaper than a fresh page each time).
 * Returns { priceCNY, priceUSD, weightG, dead, needsLogin }. */
async function fetchOne(page, kakobuyUrl, opts = {}) {
  const api = watchItemApi(page);
  await page.goto(toKakobuy(kakobuyUrl), { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT });

  // Wait until the page commits to an outcome instead of blindly sleeping:
  // either a price rendered, or it declared the product missing.
  let outcome = null;
  try {
    const handle = await page.waitForFunction(() => {
      const t = document.body.innerText || '';
      if (/[¥￥]\s*[\d.,]+/.test(t)) return 'price';
      if (/may not exist|does not exist|no longer|has been removed|not found/i.test(t)) return 'gone';
      return false;
    }, null, { timeout: RENDER_TIMEOUT, polling: 250 });
    outcome = await handle.jsonValue();
  } catch {
    outcome = 'timeout';
  }

  // A logged-out session renders "gone" for everything — report it as such so
  // the batch can stop immediately rather than marking the catalog dead.
  if (api.loggedOut) return { priceCNY: null, priceCNYMax: null, priceUSD: null, priceUSDMax: null, weightG: null, needsLogin: true };
  if (outcome === 'gone') return { priceCNY: null, priceCNYMax: null, priceUSD: null, priceUSDMax: null, weightG: null, dead: true };
  // Pretul afisat intarzie des sub incarcare (mai multe Chrome deodata): pana
  // acum asta arunca la gunoi si pozele, desi starea Vue era deja incarcata si
  // .bulk raporta "no photos found" pentru un produs perfect bun. Deci la
  // timeout mai citim o data starea si continuam daca a venit ceva din ea.
  let state = await page.evaluate(readItemState).catch(() => ({ prices: [], images: [] }));
  if (outcome === 'timeout' && !(state.images || []).length && !(state.prices || []).length) {
    return { priceCNY: null, priceCNYMax: null, priceUSD: null, priceUSDMax: null, weightG: null };
  }

  const out = await page.evaluate(extractFromPage);
  out.images = state.images || [];

  if (opts.share) {
    out.shareUrl = await fetchShareLink(page);
  }

  // Cu variante: dam click pe fiecare model si luam minimul si maximul. Doar
  // la cerere - in batch ne ajunge pretul afisat si asa dureaza destul.
  if (opts.variants) {
    const prices = await collectVariantPrices(page, out.priceUSD);
    if (prices.length) {
      out.priceUSD = Math.min(...prices);
      const max = Math.max(...prices);
      out.priceUSDMax = max > out.priceUSD ? max : null;
      out.variantCount = prices.length;
    }
  }
  return out;
}

async function runStandalone(url, opts = {}) {
  if (!hasSession()) console.warn(`! No saved session.\n${LOGIN_HINT}\n`);
  const browser = await chromium.launch({ headless: true });
  const ctx = await newContext(browser);
  const page = await ctx.newPage();
  const r = await fetchOne(page, url, opts);
  console.log(JSON.stringify(r, null, 2));
  if (r.needsLogin) console.error(`\n✗ Logged out.\n${LOGIN_HINT}`);
  await browser.close();
}

async function runBatch() {
  const products = JSON.parse(fs.readFileSync(DATA, 'utf8'));
  const limit = process.env.LIMIT ? parseInt(process.env.LIMIT) : products.length;
  const concurrency = process.env.CONCURRENCY ? parseInt(process.env.CONCURRENCY) : 8;
  const force = process.env.FORCE === '1';
  const retryDead = process.env.RETRY_DEAD === '1';

  if (!hasSession()) {
    console.error(`✗ No Kakobuy session — every product would come back "may not exist".\n${LOGIN_HINT}`);
    process.exit(1);
  }

  // incremental by default: only fetch products that don't have a price yet.
  // Products previously marked dead are skipped unless RETRY_DEAD=1 (they were
  // very likely mis-flagged by a logged-out run).
  const targets = products
    .filter(p => p.kakobuyLink
      && (force || p.priceCNY == null || !p.kakoTitle)   // also fill in missing titles
      && (retryDead || force || !p.dead))
    .slice(0, limit);

  console.log(`Fetching Kakobuy prices for ${targets.length} products (concurrency ${concurrency})…`);
  process.stdout.write(`TOTAL::${targets.length}\n`);
  const browser = await chromium.launch({ headless: true });
  const ctx = await newContext(browser);

  let done = 0, ok = 0, deadCount = 0;
  let abortLogin = false;   // set when Kakobuy reports the session is logged out

  const save = () => { fs.writeFileSync(DATA, JSON.stringify(products, null, 2)); writeJsBundle(products); };

  async function worker() {
    let page = await ctx.newPage();
    while (queue.length && !abortLogin) {
      const p = queue.shift();
      let got = false, priceStr = '', deadHit = false;
      for (let attempt = 1; attempt <= 2 && !got && !abortLogin; attempt++) {
        try {
          const r = await fetchOne(page, p.kakobuyLink);
          if (r.needsLogin) { abortLogin = true; break; }
          if (r.dead) deadHit = true;
          if (r.title) p.kakoTitle = r.title;   // real product name from the listing
          if (r.priceCNY != null) {
            p.priceCNY = r.priceCNY;
            if (r.priceUSD != null) p.priceUSD = r.priceUSD;
            if (r.weightG != null && r.weightG > 0) p.weight = `${r.weightG}g`;
            ok++; got = true; priceStr = `￥${r.priceCNY}`;
          }
        } catch (e) {
          if (attempt === 2) console.warn(`  ! ${p.name}: ${e.message.split('\n')[0]}`);
          // recover a crashed/closed page so the whole worker doesn't die
          if (page.isClosed()) { try { page = await ctx.newPage(); } catch { /* give up */ } }
        }
      }
      if (abortLogin) break;
      if (got) delete p.dead;
      // Only trust "gone" when we have no working price for it. A product that
      // priced fine before is far more likely hitting a render hiccup than to
      // have vanished — flagging it would hand it to the cleanup button.
      else if (deadHit && p.priceCNY == null) { p.dead = true; deadCount++; priceStr = '✗ dead'; }
      else if (deadHit) { priceStr = '~ keep'; }
      done++;
      process.stdout.write(`PROGRESS::${JSON.stringify({ done, total: targets.length, ok, name: p.name, price: priceStr || '—' })}\n`);
      if (done % 20 === 0) save();
    }
    await page.close().catch(() => {});
  }

  const queue = [...targets];
  await Promise.all(Array.from({ length: Math.max(1, concurrency) }, () => worker()));
  await browser.close();

  save();
  if (abortLogin) {
    console.error(`\n✗ Session expired / logged out — stopped early.\n${LOGIN_HINT}`);
    process.exit(1);
  }
  console.log(`\n✓ Updated ${ok}/${targets.length} prices` + (deadCount ? `, ${deadCount} dead` : '') +
    ` -> data/products.json + assets/products-data.js`);
}

/* Offline self-test: drives the REAL extractor over the page shapes Kakobuy
 * renders, so a format change is caught without needing a login or network.
 * Run:  node scraper/kakobuy-price.js --selftest */
const SAMPLES = [
  { name: 'standard "CNY ￥x ≈ $ y"', html: '<div>Price</div><div>CNY ￥388 ≈ $ 62.28</div><div>Weight(g): 520</div>',
    want: { priceCNY: 388, priceUSD: 62.28, weightG: 520 } },
  { name: 'no space after $', html: '<div>CNY ￥1299 ≈ $208.49</div><div>Weight (g)：1200</div>',
    want: { priceCNY: 1299, priceUSD: 208.49, weightG: 1200 } },
  { name: 'thousands separator', html: '<div>CNY ￥1,250.50 ≈ $ 200.71</div>',
    want: { priceCNY: 1250.5, priceUSD: 200.71, weightG: null } },
  { name: 'yen only, no conversion', html: '<div>￥229</div>',
    want: { priceCNY: 229, priceUSD: null, weightG: null } },
  { name: 'full-width yen ¥', html: '<div>CNY ¥89 ≈ $ 14.29</div>',
    want: { priceCNY: 89, priceUSD: 14.29, weightG: null } },
  { name: 'range, symbol repeated', html: '<div>CNY ￥388-￥698 ≈ $ 62.28-$112.03</div>',
    want: { priceCNY: 388, priceCNYMax: 698, priceUSD: 62.28, priceUSDMax: 112.03, weightG: null } },
  { name: 'range, bare second number', html: '<div>CNY ￥388-698 ≈ $ 62.28-112.03</div><div>Weight(g): 900</div>',
    want: { priceCNY: 388, priceCNYMax: 698, priceUSD: 62.28, priceUSDMax: 112.03, weightG: 900 } },
  { name: 'same number twice is not a range', html: '<div>CNY ￥388-￥388 ≈ $ 62.28-$62.28</div>',
    want: { priceCNY: 388, priceCNYMax: null, priceUSD: 62.28, priceUSDMax: null } },
  { name: 'no price at all', html: '<div>This product may not exist.</div>',
    want: { priceCNY: null, priceCNYMax: null, priceUSD: null, priceUSDMax: null, weightG: null } },
];

async function runSelfTest() {
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ locale: 'en-US' })).newPage();
  let failed = 0;
  for (const s of SAMPLES) {
    await page.setContent(`<body>${s.html}</body>`);
    const got = await page.evaluate(extractFromPage);
    const ok = Object.keys(s.want).every(k => got[k] === s.want[k]);
    if (!ok) failed++;
    console.log(`  ${ok ? '✓' : '✗'} ${s.name}`);
    if (!ok) console.log(`      want ${JSON.stringify(s.want)}\n      got  ${JSON.stringify(got)}`);
  }
  // Bucla de variante, pe o pagina care se poarta ca Kakobuy: pretul se
  // schimba la click pe model, iar marimile nu-l ating deloc.
  const PRICES = [96.72, 104.78, 96.72, 112.5];
  await page.setContent(`<body>
    <div class="sku-price" id="p">CNY ￥600 ≈ $ ${PRICES[0]}</div>
    <div class="item-props"><div class="row">Color :
      ${PRICES.map((_, i) => `<div class="item" data-i="${i}"><div class="spec"><img></div></div>`).join('')}
    </div><div class="row">Size :
      ${[38, 39].map(s2 => `<div class="item"><div class="spec">${s2}</div></div>`).join('')}
    </div></div>
    <scr` + `ipt>
      const P = ${JSON.stringify(PRICES)};
      document.querySelectorAll('.item-props .item[data-i]').forEach(li =>
        li.addEventListener('click', () => {
          const v = P[+li.dataset.i];
          setTimeout(() => { document.getElementById('p').textContent =
            'CNY ￥' + Math.round(v * 6.2) + ' ≈ $ ' + v; }, 60);
        }));
    </scr` + `ipt></body>`);
  const got = await collectVariantPrices(page, PRICES[0]);
  const min = Math.min(...got), max = Math.max(...got);
  const okVar = min === 96.72 && max === 112.5;
  if (!okVar) failed++;
  console.log(`  ${okVar ? '✓' : '✗'} variant sweep (min/max across models)`);
  if (!okVar) console.log(`      want 96.72/112.5
      got  ${min}/${max} from ${JSON.stringify(got)}`);

  await browser.close();
  const total = SAMPLES.length + 1;
  console.log(failed ? `
✗ ${failed}/${total} failed` : `
✓ all ${total} checks pass`);
  process.exit(failed ? 1 : 0);
}

// Exported so tests can drive the real fetch path against a stand-in server.
module.exports = { fetchOne, extractFromPage, collectVariantPrices, writeJsBundle };

// Only act when run directly — importing this file must not start a batch.
if (require.main === module) {
  const args = process.argv.slice(2);
  const variants = args.includes('--variants');   // click pe fiecare model -> interval
  const share = args.includes('--share');         // linkul scurt de afiliat
  const arg = args.find(a => !a.startsWith('--'));
  const run = args.includes('--selftest') ? runSelfTest()
    : arg ? runStandalone(arg, { variants, share })
    : runBatch();
  run.catch(e => { console.error(e); process.exit(1); });
}
