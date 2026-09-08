"""
Comanda .image — ia un link de poza (sau un album Yupoo), scoate fundalul,
pune produsul pe alb cu logoul CS Finds si trimite rezultatul in chat.
Merge si cu poze atasate direct la mesaj (fara link).

Foloseste exact acelasi layout ca aplicatia Poze Produse (brand.py).
"""
import asyncio
import io
import json
import logging
import re
import os
import subprocess
import time
from functools import partial
from pathlib import Path

import discord
from discord.ext import commands
from PIL import Image

import brand
import linkgen
import yupoo_scrape as ys

log = logging.getLogger("welcome-bot.image")

MAX_UPLOAD = 8 * 1024 * 1024      # marja sub limita Discord de 10 MB
MAX_BATCH = 4                     # cate poze trimitem dintr-un album
MAX_TOTAL = 10                    # limita Discord de fisiere pe mesaj
# Discord randeaza cel mult 4 imagini intr-o galerie de embed-uri, oricate ai
# trimite. Trimiteam 8 si se vedeau 4, iar titlul mintea.
QC_ALBUMS = 3                     # cate albume QC trimitem
QC_PER_ALBUM = 4                  # cate poze pe album trimitem
# Pozele de QC se trimit ca fisiere, nu ca embed-uri: proxy-ul Discord pentru
# imagini externe le baga intr-o caseta 768x1024 si turteste orice poza
# landscape, exact acolo unde e rigla. Ca atasament, pastreaza proportiile.
QC_MAX_SIDE = 2048                # 4032px original -> ~660 KB, rigla ramane citibila

# Taobao / Weidian / 1688 blocheaza cererile server-side, asa ca pozele se iau
# randand pagina Kakobuy cu sesiunea logata din proiectul kfinds (Playwright).
# Cautam scriptele intai langa bot (`scraper/`, cum vin in arhiva), apoi in
# proiectul kfinds de pe masina lui Kevin. Asa merge si la o instalare curata,
# si aici, fara sa schimbe nimeni nimic.
_LANGA_BOT = Path(__file__).resolve().parent / "scraper" / "kakobuy-images.js"
_IN_KFINDS = Path.home() / "Desktop" / "kfinds.net" / "scraper" / "kakobuy-images.js"
KAKO_SCRIPT = Path(os.getenv(
    "KAKOBUY_SCRIPT", _LANGA_BOT if _LANGA_BOT.exists() else _IN_KFINDS))
KAKO_TIMEOUT = 90                 # randarea paginii dureaza 10-20 s
# Acelasi randare de pagina, dar pentru pret: Kakobuy isi cripteaza API-ul de
# preturi, deci singura sursa e textul afisat ("CNY ¥388-¥698 ≈ $ 62.28-$112.03").
KAKO_PRICE_SCRIPT = Path(os.getenv(
    "KAKOBUY_PRICE_SCRIPT", KAKO_SCRIPT.parent / "kakobuy-price.js"))
KAKO_PRICE_TIMEOUT = 150          # un click pe fiecare model, deci mai lent decat pozele
KAKO_RETRY_PAUSE = 4              # secunde de asteptare inainte de reincercare


def _process(data: bytes, with_logo: bool, model: str = None) -> bytes:
    """Ruleaza pe thread separat: rembg + compunere. Blocheaza, deci nu in event loop."""
    logo = brand.load_logo() if with_logo else None
    img = brand.brand(Image.open(io.BytesIO(data)), logo, nobg=True, model=model)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92, optimize=True)
    buf.seek(0)
    return buf.getvalue()


def shrink(data: bytes, side: int = None) -> bytes:
    """Micsoreaza o poza pastrand proportiile, ca sa incapa in limita Discord."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((side or QC_MAX_SIDE, side or QC_MAX_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88, optimize=True)
    return buf.getvalue()


async def process_async(data: bytes, with_logo: bool = True,
                        model: str = None) -> bytes:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_process, data, with_logo, model))


STORE_HOSTS = ("taobao.com", "tmall.com", "weidian.com", "1688.com", "kakobuy.com")


def kakobuy_images(url: str, count: int = MAX_BATCH, timeout: int = None,
                   qc: bool = False) -> list:
    """Linkurile pozelor unui produs, prin Kakobuy. Ridica exceptie cu motivul.

    qc=True -> pozele din "Reference Photos", facute de cumparatori. Sunt in
    alta sectiune a paginii si nu se amesteca cu pozele de catalog.
    """
    if not KAKO_SCRIPT.exists():
        raise RuntimeError(f"lipseste scriptul {KAKO_SCRIPT}")
    args = ["node", str(KAKO_SCRIPT), url, str(count)] + (["--qc"] if qc else [])
    proc = subprocess.run(
        args, cwd=str(KAKO_SCRIPT.parent.parent), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=timeout or KAKO_TIMEOUT)
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        raise RuntimeError((proc.stderr or "node nu a raspuns").strip()[:200])
    data = json.loads(line[-1])
    if not data.get("ok"):
        err = data.get("error", "necunoscut")
        if err in ("no-session", "logged-out"):
            raise RuntimeError("sesiunea Kakobuy a expirat - ruleaza "
                               "`node scraper/kakobuy-login.js` in kfinds.net")
        raise RuntimeError(err)
    return data["images"]


def _fmt_price(value: float) -> str:
    """62.0 -> '62', 62.28 -> '62'. In titluri pretul e rotund, ca la mana."""
    return str(int(round(value)))


def kakobuy_item(url: str, timeout: int = None, share: bool = False) -> dict:
    """Pretul, pozele, titlul si greutatea de pe pagina Kakobuy. Gol daca nu iese.

    Pretul afisat e al modelului selectat, deci scriptul da click pe fiecare
    model din "Color" ca sa afle minimul si maximul (marimea nu schimba pretul).
    De aceea dureaza mai mult decat luarea pozelor.

    Pozele vin din aceeasi citire, deci nu mai randam pagina a doua oara.

    Nu ridica exceptie: e un bonus la formular, nu ceva de care sa depinda
    postarea. Motivul esecului ajunge in log.
    """
    empty = {"price": "", "images": [], "title": "", "weight": None, "share": ""}
    if not KAKO_PRICE_SCRIPT.exists():
        log.info(f"lipseste scriptul {KAKO_PRICE_SCRIPT}")
        return empty

    # Pagina nu randeaza intotdeauna din prima (uneori apare un captcha, alteori
    # e doar lenta) si atunci iese complet goala. Masurat: aceeasi pagina care
    # da gol acum merge la reluare. Fara reincercare, un .bulk sare produsul in
    # tacere - de aceea o singura repetare, nu mai multe: daca sesiunea chiar e
    # picata, nu are rost sa asteptam de doua ori la fiecare produs.
    data = None
    for incercare in (1, 2):
        try:
            args = ["node", str(KAKO_PRICE_SCRIPT), url, "--variants"]
            if share:
                args.append("--share")      # +6s: cere linkul scurt de afiliat
            proc = subprocess.run(
                args,
                cwd=str(KAKO_PRICE_SCRIPT.parent.parent), capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout or KAKO_PRICE_TIMEOUT)
            # Modul standalone scrie JSON pe mai multe linii, deci parsam tot stdout.
            data = json.loads((proc.stdout or "").strip())
        except Exception as e:
            log.info(f"Datele nu au venit de la Kakobuy: {e}")
            return empty
        if data.get("needsLogin"):
            log.info("sesiunea Kakobuy a expirat - ruleaza "
                     "`node scraper/kakobuy-login.js`")
            return empty
        if data.get("priceUSD") or data.get("images"):
            break
        if incercare == 1:
            log.info(f"Kakobuy a raspuns gol pentru {url}; reincerc o data")
            # Golul vine aproape mereu de la incarcare (mai multe Chrome
            # deodata): reluat imediat da acelasi gol, deci lasam pagina sa
            # respire cateva secunde inainte de a doua incercare.
            time.sleep(KAKO_RETRY_PAUSE)

    low, high = data.get("priceUSD"), data.get("priceUSDMax")
    price = ""
    if low:
        price = _fmt_price(low)
        if high and _fmt_price(high) != price:
            price = f"{price}-{_fmt_price(high)}"
    weight = data.get("weightG")
    return {"price": price,
            "images": data.get("images") or [],
            "title": data.get("title") or "",       # numele din magazin
            "weight": int(weight) if weight else None,
            "share": data.get("shareUrl") or ""}


def kakobuy_session_ok(url: str = None) -> tuple:
    """(merge, motiv). Verifica pe un produs cunoscut daca sesiunea mai e buna.

    Sesiunea Kakobuy expira fara sa anunte pe nimeni: pretul iese gol, pozele
    lipsesc, si botul pare doar "prost" zile intregi. Verificarea o prinde.
    """
    probe = url or os.getenv("KAKOBUY_PROBE_URL",
                             "https://weidian.com/item.html?itemID=7565891040")
    item = kakobuy_item(probe)
    if item["price"] or item["images"]:
        return True, ""
    return False, ("sesiunea Kakobuy pare picata - ruleaza "
                   "`node scraper/kakobuy-login.js` in kfinds.net")


def kakobuy_qc_albums(url: str, albums: int = 4, timeout: int = None) -> list:
    """-> [[url poza, ...], ...]. Fiecare album e setul QC al unei comenzi."""
    if not KAKO_SCRIPT.exists():
        raise RuntimeError(f"lipseste scriptul {KAKO_SCRIPT}")
    proc = subprocess.run(
        ["node", str(KAKO_SCRIPT), url, str(albums), "--qc"],
        cwd=str(KAKO_SCRIPT.parent.parent), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        timeout=timeout or KAKO_TIMEOUT)
    line = (proc.stdout or "").strip().splitlines()
    if not line:
        raise RuntimeError((proc.stderr or "node nu a raspuns").strip()[:200])
    data = json.loads(line[-1])
    if not data.get("ok"):
        err = data.get("error", "necunoscut")
        if err in ("no-session", "logged-out"):
            raise RuntimeError("sesiunea Kakobuy a expirat - ruleaza "
                               "`node scraper/kakobuy-login.js` in kfinds.net")
        raise RuntimeError(err)
    return data.get("albums") or []


def collect_sources(url: str):
    """-> lista de (eticheta, bytes). Album Yupoo / produs de magazin / poza singura."""
    if any(h in url for h in STORE_HOSTS):
        photos = kakobuy_images(url)
        return [(f"{i:02d}", ys.fetch(p, referer="https://www.taobao.com/"))
                for i, p in enumerate(photos, 1)]

    if "/albums/" in url:
        page_url = url if "?" in url else url + "?uid=1"
        page = ys.fetch_html(page_url)
        photos = ys.photo_urls(page)[:MAX_BATCH]
        if not photos:
            return []
        return [(f"{i:02d}", ys.fetch(p, referer=page_url))
                for i, p in enumerate(photos, 1)]
    return [("01", ys.fetch(url))]


IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def _is_image(att: discord.Attachment) -> bool:
    ctype = (att.content_type or "").lower()
    return ctype.startswith("image/") or att.filename.lower().endswith(IMG_EXTS)


async def read_attachments(attachments: list) -> list:
    """-> lista de (eticheta, bytes) pentru pozele atasate la mesaj."""
    out = []
    for i, att in enumerate(attachments, 1):
        out.append((f"{i:02d}", await att.read()))
    return out


def collect_many(urls: list) -> list:
    """Aduna pozele de la mai multe linkuri deodata. Un link picat nu opreste restul."""
    out, errors = [], []
    for i, u in enumerate(urls, 1):
        if len(out) >= MAX_TOTAL:
            break
        try:
            for j, (_, data) in enumerate(collect_sources(u), 1):
                if len(out) >= MAX_TOTAL:
                    break
                out.append((f"{i:02d}-{j:02d}" if len(urls) > 1 else f"{j:02d}", data))
        except Exception as e:
            errors.append(f"{u}: {e}")
    return out, errors


async def send_qc_albums(dest, url: str, albums: int = QC_ALBUMS) -> int:
    """Trimite albumele de QC in `dest` (canal sau thread). -> cate albume au iesit.

    Ridica exceptie daca scraperul pica, ca sa poata decide apelantul ce zice.
    Scoasa din comanda `.qc` fiindca o foloseste si postarea din forum.
    """
    loop = asyncio.get_running_loop()
    fetched = await loop.run_in_executor(None, partial(kakobuy_qc_albums, url, albums))
    sent = 0
    for i, photos in enumerate(fetched, 1):
        shown = photos[:QC_PER_ALBUM]
        files = []
        for j, src in enumerate(shown, 1):
            try:
                data = await loop.run_in_executor(None, partial(ys.fetch, src))
                small = await loop.run_in_executor(None, partial(shrink, data))
            except Exception as e:
                log.warning(f"Poza QC {i}-{j} a picat: {e}")
                continue
            files.append(discord.File(io.BytesIO(small), filename=f"qc{i}-{j}.jpg"))
        if not files:
            continue
        extra = "" if len(photos) <= QC_PER_ALBUM else f" of {len(photos)}"
        await dest.send(content=f"**QC album {i}/{len(fetched)}** - "
                                f"{len(files)}{extra} photos", files=files)
        sent += 1
    return sent


async def _delete_command_message(ctx: commands.Context):
    """Sterge mesajul cu comanda, ca sa ramana in canal doar albumele."""
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        log.warning("Nu am permisiunea Manage Messages ca sa sterg comanda .qc.")
    except discord.HTTPException as e:
        log.debug(f"Nu am putut sterge mesajul comenzii: {e}")


def setup_image_command(client: commands.Bot):
    @client.command(name="qc")
    async def qc_cmd(ctx: commands.Context, *, url: str = ""):
        """Albumele de QC ale unui produs, asa cum le-au facut cumparatorii.

        Fiecare album e setul unei comenzi, deci merge cate un mesaj per album:
        Discord randeaza cel mult 4 imagini intr-o galerie, iar amestecate n-ai
        sti care poza e din ce comanda.
        """
        url = url.strip().strip("<>")
        # Stergem inainte de fetch: randarea dureaza 10-20 s si nu are rost sa
        # stea comanda in canal tot timpul asta.
        await _delete_command_message(ctx)
        if not url.startswith("http"):
            await ctx.send("Usage: `.qc <product or agent link>`", delete_after=20)
            return

        async with ctx.typing():
            try:
                sent = await send_qc_albums(ctx.channel, url)
            except Exception as e:
                log.warning(f"QC esuat pentru {url}: {e}")
                await ctx.send(f"Couldn't get QC photos: `{e}`", delete_after=25)
                return

        if not sent:
            await ctx.send("No QC photos on that product yet.", delete_after=20)
            return

        log.info(f"[QC] {ctx.author}: {sent} albume pentru {url}")

    @client.command(name="convert")
    async def convert_cmd(ctx: commands.Context):
        """Poza din galeria telefonului -> link, de lipit in formularul .add.

        Atasamentul are deja un URL pe CDN-ul Discord, deci nu reincarcam nimic;
        doar il scoatem la vedere ca sa poata fi copiat.
        """
        atts = [a for a in ctx.message.attachments if _is_image(a)]
        if not atts:
            await ctx.send("Attach one or more images and I'll give you their links.",
                           delete_after=20)
            return
        # Blocul de cod e obligatoriu: un mesaj care contine doar un link de
        # imagine e inlocuit de Discord cu previzualizarea, si nu mai ai ce copia.
        # Fara delete_after - linkul trebuie sa ramana pana il copiaza.
        links = "\n".join(a.url for a in atts)
        await ctx.send(f"```\n{links}\n```")


    @client.command(name="image")
    async def image_cmd(ctx: commands.Context, *, url: str = ""):
        # Mesajul poate avea mai multe linkuri, pe linii separate, fiecare cu
        # propriul ".image" in fata - le luam pe toate dintr-o data.
        urls = [u.strip("<>,") for u in re.findall(r"https?://\S+", url)][:MAX_TOTAL]
        attachments = [a for a in ctx.message.attachments if _is_image(a)][:MAX_TOTAL]

        if not attachments and not urls:
            await ctx.send("Usage: `.image <image or Yupoo album link>` "
                           "or attach the images to your message. "
                           "Several links at once work too.", delete_after=15)
            return

        async with ctx.typing():
            loop = asyncio.get_running_loop()
            errors = []
            if attachments:
                try:
                    sources = await read_attachments(attachments)
                except Exception as e:
                    log.warning(f"Nu am putut citi atasamentele: {e}")
                    await ctx.send(f"Couldn't read the attached images: `{e}`", delete_after=20)
                    return
            else:
                sources, errors = await loop.run_in_executor(
                    None, partial(collect_many, urls))

            if not sources:
                why = f": `{errors[0]}`" if errors else ""
                await ctx.send(f"Couldn't find any image{why}", delete_after=20)
                return

            files, failed = [], 0
            for label, data in sources:
                try:
                    # In Discord punem logoul pe toate pozele trimise.
                    out = await process_async(data, with_logo=True)
                except Exception as e:
                    log.warning(f"Procesare esuata pentru {label}: {e}")
                    failed += 1
                    continue
                if len(out) > MAX_UPLOAD:
                    failed += 1
                    continue
                files.append(discord.File(io.BytesIO(out), filename=f"csfinds-{label}.jpg"))

            if not files:
                await ctx.send("Couldn't process the images.", delete_after=20)
                return

            skipped = failed + len(errors)
            note = f" ({skipped} skipped)" if skipped else ""
            await ctx.send(content=f"Done{note}", files=files)


# ---------- QC automat pe linkurile postate in forum ----------
URL_RE = re.compile(r"""https?://[^\s"'<>]+""")
# Un thread poate primi acelasi link de mai multe ori (raspunsuri, citate) si
# fiecare rulare inseamna 10-20 s de randare, deci tinem minte ce am servit.
_qc_done = set()


def setup_forum_qc(client: commands.Bot):
    """Cineva pune un link de produs intr-un post din forum -> albumele de QC.

    Doar in threaduri de forum: in canalele normale ar fi zgomot, iar in
    postul proaspat deschis QC-ul vine oricum din `.add`.
    """
    @client.listen("on_message")
    async def on_forum_link(message: discord.Message):
        if message.author.bot or not message.content:
            return
        thread = message.channel
        if not isinstance(thread, discord.Thread):
            return
        if not isinstance(thread.parent, discord.ForumChannel):
            return
        if message.content.startswith((".", "!")):
            return          # comenzile isi vad de treaba

        parsed = None
        for url in URL_RE.findall(message.content):
            parsed = linkgen.parse_any(url.strip("<>,"))
            if parsed:
                break
        if parsed is None:
            return

        platform, item_id, raw = parsed
        key = (thread.id, platform, item_id)
        if key in _qc_done:
            return
        _qc_done.add(key)

        try:
            async with thread.typing():
                sent = await send_qc_albums(thread, raw)
        except Exception as e:
            _qc_done.discard(key)       # a picat scraperul, sa se poata reincerca
            log.warning(f"QC automat esuat pentru {raw}: {e}")
            return
        if sent:
            log.info(f"[QC-forum] {sent} albume in #{thread} pentru {raw}")
