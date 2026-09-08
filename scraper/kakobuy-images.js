/* kfinds — galeria de poze a unui produs, prin Kakobuy (pentru botul de Discord)
 *
 * Primeste un link Taobao / Weidian / 1688 (sau direct de Kakobuy), randeaza
 * pagina Kakobuy cu sesiunea logata si scrie pe stdout un JSON:
 *   {"ok":true,"images":["https://...","..."]}
 *
 * REQUIRES A LOGIN (ca restul scraperelor):  node scraper/kakobuy-login.js
 *
 * Rulare:  node scraper/kakobuy-images.js "<url>" [max]
 */

const { chromium } = require('playwright');
const { hasSession, newContext, LOGIN_HINT } = require('./kakobuy-session');

const NAV_TIMEOUT = 30000;
const RENDER_TIMEOUT = 20000;

// Doar galeria produsului si pozele de varianta (Color), nimic altceva.
// ATENTIE: un selector gen [class*="swiper"] img pare tentant, dar prinde
// clasa .goods-img, adica tile-urile de produse RECOMANDATE - ajungeai cu
// poze de la alt produs in galerie.
// ATENTIE la .el-image__preview: clasa e folosita SI de poza mare a produsului,
// SI de fiecare poza din "Reference Photos" (QC). Fara sa o limitam la
// .prop-imgs, galeria produsului se umple cu pozele QC pe fond verde.
const MAIN_IMG = '.prop-imgs .preview-img img, .prop-imgs .jqzoom img, '
               + '.prop-imgs img.el-image__preview';
const THUMB_IMG = '.item-imgs-box li img, img.item-img, img.props-image';
// "Reference Photos" - pozele de QC facute de cumparatori. In DOM se vede doar
// cate o miniatura de album; lista completa sta in starea componentei Vue
// (qcGroup = [{mask_id, qc_list:[{image, createtime}]}]), de unde o citim.
const QC_IMG = '.qc-picture img, .qc-group img, .qc-item img';

function collectQcAlbums() {
  const el = document.querySelector('.qc-picture, .qc-group');
  if (!el) return [];
  let n = el, vm = null;
  for (let i = 0; i < 8 && n; i++, n = n.parentElement) if (n.__vue__) { vm = n.__vue__; break; }
  const src = (vm && vm.$parent && vm.$parent.qcGroup)
           || (vm && vm.qcGroup) || null;
  if (!src) return [];
  return src.map(g => (g.qc_list || []).map(p => p.image).filter(Boolean))
            .filter(a => a.length);
}

function toKakobuy(url) {
  if (/kakobuy\.com/i.test(url)) return url;
  return 'https://item.kakobuy.com/item/details?url=' + encodeURIComponent(url);
}

function collect(sel) {
  const out = [];
  const push = (e) => {
    let s = e && (e.currentSrc || e.getAttribute('src') || '');
    if (!s) return;
    if (s.startsWith('//')) s = 'https:' + s;
    s = s.split('?')[0];
    // alicdn/geilicdn lipesc dimensiunea in nume ("..._90x90.jpg", "..._400x400.jpg");
    // fara ea primim originalul, nu thumbnailul.
    s = s.replace(/_\d+x\d+(?:q\d+)?(?:\.(?:jpg|jpeg|png|webp))?$/i, '');
    // Unele originale sunt .heic; CDN-ul le serveste convertite daca cerem o dimensiune.
    if (/\.heic$/i.test(s)) s += '_1200x1200.jpg';
    if (!/\.(jpg|jpeg|png|webp)$/i.test(s)) return;
    if (/(^|\/)(logo|avatar|icon|flag)/i.test(s)) return;
    if (!out.includes(s)) out.push(s);
  };
  document.querySelectorAll(sel.main).forEach(push);
  document.querySelectorAll(sel.thumb).forEach(push);
  return out;
}

async function main() {
  const url = process.argv[2];
  const max = parseInt(process.argv[3] || '6', 10);
  const qcOnly = process.argv.includes('--qc');
  if (!url) {
    console.log(JSON.stringify({ ok: false, error: 'lipseste url' }));
    process.exit(1);
  }
  if (!hasSession()) {
    console.log(JSON.stringify({ ok: false, error: 'no-session', hint: LOGIN_HINT }));
    process.exit(2);
  }

  const browser = await chromium.launch({ headless: true });
  try {
    // keepImages: avem nevoie de <img> randate ca sa le citim src-ul
    const ctx = await newContext(browser, { keepImages: true });
    const page = await ctx.newPage();

    let loggedOut = false;
    page.on('response', async (r) => {
      if (!r.url().includes('/api/sapi/item')) return;
      try {
        const body = await r.text();
        if (/"code"\s*:\s*1055/.test(body) || /login first/i.test(body)) loggedOut = true;
      } catch { /* corpul a disparut deja */ }
    });

    await page.goto(toKakobuy(url), { waitUntil: 'domcontentloaded', timeout: NAV_TIMEOUT });
    const want = qcOnly ? QC_IMG : MAIN_IMG + ', ' + THUMB_IMG;
    try {
      await page.waitForSelector(want, { timeout: RENDER_TIMEOUT });
    } catch { /* mergem mai departe: poate a randat doar o parte */ }

    // Sectiunea de QC e jos in pagina si se incarca lazy.
    if (qcOnly) {
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await page.waitForTimeout(4000);
    }

    // Galeria se incarca lazy: asteptam sa apara mai multe miniaturi.
    try {
      await page.waitForFunction(
        (sel) => document.querySelectorAll(sel).length > 1,
        THUMB_IMG, { timeout: 8000 });
    } catch { /* un singur cadru e tot ce exista */ }

    if (qcOnly) {
      let albums = await page.evaluate(collectQcAlbums);
      if (!albums.length) {
        // Fallback: daca starea Vue nu e accesibila, macar miniaturile.
        const flat = await page.evaluate(collect, { main: QC_IMG, thumb: QC_IMG });
        albums = flat.map(u => [u]);
      }
      albums = albums.slice(0, max);
      const flatAll = albums.flat();
      console.log(JSON.stringify({ ok: albums.length > 0, albums, images: flatAll }));
      return;
    }

    const images = await page.evaluate(collect, { main: MAIN_IMG, thumb: THUMB_IMG });
    if (!images.length && loggedOut) {
      console.log(JSON.stringify({ ok: false, error: 'logged-out', hint: LOGIN_HINT }));
      process.exit(2);
    }
    // Fara motiv explicit, botul raporta 'necunoscut' cand galeria iesea goala.
    console.log(JSON.stringify(images.length
      ? { ok: true, images: images.slice(0, max) }
      : { ok: false, error: 'pagina nu a randat nicio poza', images: [] }));
  } catch (e) {
    console.log(JSON.stringify({ ok: false, error: String(e && e.message || e) }));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main();
