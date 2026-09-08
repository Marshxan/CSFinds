"""
Comanda .scrapp — listeaza albumele dintr-o categorie Yupoo.

    .scrapp <link categorie>        toate paginile categoriei
    .scrapp <link categorie> 2      doar pagina 2

Titlurile si linkurile sunt deja in pagina de categorie, asa ca o listare
completa costa o cerere per pagina, nu una per album.

Iese un rezumat in chat plus un .txt cu tot, de unde poti lua linkurile
pentru .add sau .image.
"""
import asyncio
import io
import logging
import os
import re
import time
from functools import partial

import discord
from discord.ext import commands

import addcmd
import poststore
import yupoo_scrape as ys

log = logging.getLogger("welcome-bot.scrape")

MAX_PAGES = 10            # plafon, ca o categorie uriasa sa nu tina botul ocupat
PAGE_DELAY = 0.5          # pauza intre pagini, sa nu ne blocheze Yupoo
PREVIEW = 10              # cate albume aratam direct in chat


def page_url(base: str, page_no: int) -> str:
    if page_no == 1:
        return base
    return f"{base}{'&' if '?' in base else '?'}page={page_no}"


def scrape_category(url: str, only_page: int = None):
    """-> (albume, nr pagini parcurse, nr total pagini). Blocheaza, deci in executor."""
    first = ys.fetch_html(page_url(url, only_page or 1))
    total_pages = ys.category_page_count(first)
    albums = ys.category_albums(first, url)

    if only_page is not None:
        return albums, 1, total_pages

    seen = {a["url"].split("?")[0] for a in albums}
    pages_done = 1
    for page_no in range(2, min(total_pages, MAX_PAGES) + 1):
        time.sleep(PAGE_DELAY)
        try:
            html = ys.fetch_html(page_url(url, page_no))
        except Exception as e:
            log.warning(f"Pagina {page_no} a picat: {e}")
            break
        fresh = [a for a in ys.category_albums(html, url)
                 if a["url"].split("?")[0] not in seen]
        if not fresh:
            break
        seen.update(a["url"].split("?")[0] for a in fresh)
        albums.extend(fresh)
        pages_done += 1
    return albums, pages_done, total_pages


def as_txt(albums: list, source: str) -> bytes:
    lines = [f"# {source}", f"# {len(albums)} albume", ""]
    for i, a in enumerate(albums, 1):
        lines.append(f"{i:3d}. {a['title']}")
        lines.append(f"     {a['url'].split('?')[0]}")
    return "\n".join(lines).encode("utf-8")


# ---------- .autopost: din albume Yupoo direct in forum ----------
# Rutare pe cuvinte din titlu. Fragmentul din stanga trebuie sa apara in numele
# forumului, asa ca merge si daca redenumesti canalele cu emoji in fata.
ROUTES = [
    ("boots",        ("boot",)),
    ("slides",       ("slide", "sandal", "slipper", "flip flop")),
    ("shoes",        ("shoe", "sneaker", "trainer", "runner", "dunk", "jordan")),
    ("polos",        ("polo",)),
    ("long",         ("long-sleeve", "long sleeve", "longsleeve", "hoodie",
                      "sweatshirt", "crewneck", "jacket", "zip", "windrunner", "windbreaker")),
    ("shirts",       ("tee", "t-shirt", "shirt", "short-sleeve", "short sleeve")),
    # Fragmentul se cauta in numele forumului, deci o ruta fara forum potrivit
    # nu strica nimic: produsul ramane nerutat si e sarit, nu pus aiurea.
    ("pant",         ("jean", "denim", "trouser", "pant", "short", "cargo",
                      "sweatpant", "jogger")),
    ("accessories",  ("bag", "backpack", "cap", "hat", "beanie", "belt", "sock",
                      "scarf", "glove", "wallet", "necklace", "glasses",
                      "keychain", "key chain")),
    ("room",         ("lamp", "poster", "rug", "decor", "pillow", "blanket")),
]

IMPORT_MAX = 50            # plafon dur pe rulare
IMPORT_DEFAULT = 20
PANEL_URL = os.getenv("PANEL_HOST", "127.0.0.1") + ":" + os.getenv("PANEL_PORT", "8787")
BOUNDARY = chr(92) + "b"   # granita de cuvant pentru regex


def _has_word(text: str, word: str) -> bool:
    """Potrivire la inceput de cuvant, nu oriunde in sir.

    Fara asta "runner" prindea in "Windrunner" si trimitea o geaca la Shoes.
    Lasam coada libera, ca "long-sleeve" sa prinda si "long-sleeved".
    """
    return re.search(BOUNDARY + re.escape(word), text) is not None


def route_forum(name: str, forums: list):
    """Forumul potrivit pentru un titlu de album, sau None daca nu-l recunoastem."""
    low = name.lower()
    for fragment, words in ROUTES:
        if any(_has_word(low, w) for w in words):
            for f in forums:
                if fragment in f.name.lower():
                    return f
    return None


# Multi vanzatori pun tabelul de marimi ca prima poza a albumului, sau chiar ca
# el ca si coperta. Sunt imagini pe fond negru, cu text colorat: masurate, au
# peste 95% pixeli aproape negri, in timp ce pozele de produs stau sub 25%.
# Pragul e la mijloc, cu marja de ambele parti.
NEGRU = 60                # sub atat pe toate canalele = pixel "negru"
PRAG_TABEL = 0.85         # cat de negru trebuie sa fie ca sa-l sarim
VERIFICA = 4              # cate poze incercam pana ne lasam


def _e_tabel(url: str) -> bool:
    """True daca poza pare tabel de marimi (aproape complet neagra)."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(ys.fetch(url))).convert("RGB")
        im.thumbnail((48, 48))
        px = list(im.getdata())
        return sum(1 for r, g, b in px if max(r, g, b) < NEGRU) / len(px) >= PRAG_TABEL
    except Exception as e:
        log.debug(f"Nu am putut masura poza {url}: {e}")
        return False


def alege_coperta(cover: str, photos: list):
    """Prima poza care arata a produs, nu a tabel de marimi.

    Incercam cel mult VERIFICA poze: daca un album chiar are patru tabele la
    rand, nu merita inca patru descarcari ca sa aflam.
    """
    candidati = ([cover] if cover else []) + [u for u in photos if u != cover]
    for u in candidati[:VERIFICA]:
        if not _e_tabel(u):
            return u
    return cover or (photos[0] if photos else None)


def album_details(url: str) -> dict:
    """Ce ne trebuie dintr-un album ca sa-l putem posta. Blocheaza -> in executor."""
    html = ys.fetch_html(url if "?" in url else url + "?uid=1")
    album_id = url.rstrip("/").split("/")[-1].split("?")[0]
    name, price = ys.split_album_title(ys.album_title(html, album_id))
    photos = ys.photo_urls(html)
    # Coperta aleasa de vanzator, nu prima poza din grila - sunt poze diferite,
    # iar coperta e cea care apare in lista de categorii. Daca insa e tabelul de
    # marimi, trecem la prima poza adevarata de produs.
    cover = alege_coperta(ys.album_cover(html), photos)
    return {
        "url": url,
        "name": name,
        "price": price,
        "product_url": ys.album_product_link(html),
        "photo": cover,
        "photos": photos[:8],      # ca sa poti alege alta din panou
    }


# ---------- .doc: fisa produselor, gata de copiat ----------
# Preturile de pe Yupoo sunt in euro; le dam in dolari cu un curs fix, scris si
# in antetul fisierului ca sa se vada cu ce s-a calculat.
EUR_USD = float(os.getenv("EUR_USD", "1.08"))


def usd(price: str) -> str:
    try:
        return f"${round(float(price) * EUR_USD, 2):g}"
    except (TypeError, ValueError):
        return "?"


def as_doc(items: list, source: str) -> bytes:
    """Formatul cerut: nume, link, pret in dolari, plus linkul pozei."""
    out = [f"# {source}", f"# {len(items)} products",
           f"# prices converted from EUR at {EUR_USD}", ""]
    for it in items:
        out += [
            f"The name of the product : {it.get('name', '')}",
            f"link : {it.get('product_url', '')}",
            f"price in usd : {usd(it.get('price'))}",
            f"picture : {it.get('photo', '')}",
            "",
        ]
    return chr(10).join(out).encode("utf-8")


def setup_scrape_command(client: commands.Bot):
    @client.command(name="doc")
    async def doc_cmd(ctx: commands.Context, url: str = "", limit: int = None):
        """Fisa produselor ca text: nume, link, pret in dolari, poza.

        Fara link -> exporta ce e deja in baza de drafturi.
        Cu link de categorie Yupoo -> o citeste pe loc, fara sa salveze nimic.
        """
        url = url.strip().strip("<>")
        limit = min(limit or IMPORT_DEFAULT, IMPORT_MAX)

        async with ctx.typing():
            if not url:
                items, source = poststore.all_drafts(), "drafts"
            elif "yupoo.com" in url:
                loop = asyncio.get_running_loop()
                try:
                    albums, _, _ = await loop.run_in_executor(
                        None, partial(scrape_category, url))
                except Exception as e:
                    await ctx.send(f"Couldn't read that category: `{e}`", delete_after=25)
                    return
                items = []
                for album in albums[:limit]:
                    try:
                        d = await loop.run_in_executor(
                            None, partial(album_details, album["url"]))
                    except Exception as e:
                        log.warning(f"Album sarit la .doc: {e}")
                        continue
                    if d["product_url"]:
                        items.append(d)
                    await asyncio.sleep(0.3)
                source = url
            else:
                await ctx.send("Usage: `.doc` for the drafts, or "
                               "`.doc <yupoo category link> [how many]`", delete_after=25)
                return

        if not items:
            await ctx.send("Nothing to write up yet. Run `.import` first.", delete_after=20)
            return

        file = discord.File(io.BytesIO(as_doc(items, source)), filename="products.txt")
        await ctx.send(f"{len(items)} products - prices converted from EUR at {EUR_USD}",
                       file=file)
        log.info(f"[DOC] {ctx.author}: {len(items)} produse din {source}")

    @client.command(name="import", aliases=["imports"])
    async def import_cmd(ctx: commands.Context, url: str = "", *, rest: str = ""):
        """Trage albumele unei categorii Yupoo in baza de drafturi.

        Nu posteaza nimic: numele, pretul si categoria se aleg dupa aceea din
        panoul local, unde pot fi corectate inainte de publicare.
        """
        url = url.strip().strip("<>")
        if not url.startswith("http") or "yupoo.com" not in url:
            await ctx.send("Usage: `.import <yupoo category link> [#forum] [how many]`" + chr(10) +
                           "Without a forum, each product is routed by its title.",
                           delete_after=25)
            return

        forums = addcmd.forums_in_category(ctx.guild)

        # Optional: #forum -> toate drafturile primesc categoria asta, in loc de
        # cea ghicita din titlu.
        forced = None
        m = addcmd.CHANNEL_RE.search(rest)
        if m:
            picked = ctx.guild.get_channel(int(m.group(1)))
            if not isinstance(picked, discord.ForumChannel):
                await ctx.send("That channel isn't a forum.", delete_after=20)
                return
            forced = picked
        nums = re.findall("[0-9]{1,3}", addcmd.CHANNEL_RE.sub("", rest))
        limit = min(int(nums[0]) if nums else IMPORT_DEFAULT, IMPORT_MAX)

        async with ctx.typing():
            loop = asyncio.get_running_loop()
            try:
                albums, _, _ = await loop.run_in_executor(
                    None, partial(scrape_category, url))
            except Exception as e:
                await ctx.send(f"Couldn't read that category: `{e}`", delete_after=25)
                return

            added, again, skipped = 0, 0, []
            for album in albums[:limit]:
                try:
                    d = await loop.run_in_executor(
                        None, partial(album_details, album["url"]))
                except Exception as e:
                    skipped.append(f"{album['title'][:36]} - {e}")
                    continue
                if not d["product_url"] or not d["photo"]:
                    skipped.append(f"{d['name'][:36]} - no store link in the album")
                    continue

                # Categoria ghicita din titlu e doar o propunere; se schimba din panou.
                guess = forced or route_forum(d["name"], forums)
                is_new = poststore.save_draft({
                    "id": album["url"].rstrip("/").split("/")[-1].split("?")[0],
                    "name": d["name"],
                    "price": d["price"],
                    "product_url": d["product_url"],
                    "photo": d["photo"],
                    "album_url": album["url"].split("?")[0],
                    "forum_id": guess.id if guess else None,
                    "forum_name": guess.name if guess else "",
                })
                added += is_new
                again += not is_new
                await asyncio.sleep(0.3)

        embed = discord.Embed(
            title=f"Imported {added} products",
            description=(f"They're waiting in the panel, where you set the name, "
                         "price and category before posting." + chr(10) +
                         f"http://{PANEL_URL}/drafts"),
            color=discord.Color.from_rgb(0, 162, 232))
        if again:
            embed.add_field(name="Already imported", value=str(again), inline=True)
        if skipped:
            embed.add_field(name=f"Skipped {len(skipped)}",
                            value=chr(10).join(f"- {x}" for x in skipped[:6])[:1000],
                            inline=False)
        await ctx.send(embed=embed)
        log.info(f"[IMPORT] {ctx.author}: {added} noi, {again} deja, {len(skipped)} sarite")

    @client.command(name="brands", aliases=["categories"])
    async def brands_cmd(ctx: commands.Context, url: str = ""):
        """Brandurile din meniul lateral al unui magazin Yupoo."""
        url = url.strip().strip("<>")
        if not url.startswith("http") or "yupoo.com" not in url:
            await ctx.send("Usage: `.brands <yupoo link>`", delete_after=20)
            return

        async with ctx.typing():
            loop = asyncio.get_running_loop()
            try:
                html = await loop.run_in_executor(None, partial(ys.fetch_html, url))
                cats = ys.sidebar_categories(html, url)
            except Exception as e:
                log.warning(f"Brands esuat pentru {url}: {e}")
                await ctx.send(f"Couldn't read that store: `{e}`", delete_after=25)
                return

        if not cats:
            await ctx.send("No brands in that store's sidebar.", delete_after=20)
            return

        embed = discord.Embed(
            title=f"{len(cats)} brands",
            description="\n".join(f"**{c['name'] or 'unnamed'}**\n{c['url']}"
                                  for c in cats)[:4000],
            color=discord.Color.from_rgb(0, 162, 232))
        embed.set_footer(text="Run .scrapp on any of these to list its albums")
        await ctx.send(embed=embed)
        log.info(f"[BRANDS] {ctx.author}: {len(cats)} branduri din {url}")


    @client.command(name="scrapp", aliases=["scrape"])
    async def scrapp_cmd(ctx: commands.Context, url: str = "", page: int = None):
        url = url.strip().strip("<>")
        if not url.startswith("http") or "yupoo.com" not in url:
            await ctx.send("Usage: `.scrapp <yupoo category link> [page]`",
                           delete_after=20)
            return

        async with ctx.typing():
            loop = asyncio.get_running_loop()
            try:
                albums, done, total = await loop.run_in_executor(
                    None, partial(scrape_category, url, page))
            except Exception as e:
                log.warning(f"Scrape esuat pentru {url}: {e}")
                await ctx.send(f"Couldn't read that category: `{e}`", delete_after=25)
                return

        if not albums:
            await ctx.send("No albums found there. Is that a category link?",
                           delete_after=20)
            return

        shown = albums[:PREVIEW]
        body = "\n".join(f"**{i}.** {a['title'] or 'untitled'}\n{a['url'].split('?')[0]}"
                         for i, a in enumerate(shown, 1))
        embed = discord.Embed(
            title=f"{len(albums)} albums found",
            description=body[:4000],
            color=discord.Color.from_rgb(0, 162, 232))
        note = f"page {page} of {total}" if page else f"{done} of {total} pages"
        if not page and total > MAX_PAGES:
            note += f" (capped at {MAX_PAGES})"
        embed.set_footer(text=f"{note} - full list in the file below")

        file = discord.File(io.BytesIO(as_txt(albums, url)), filename="albums.txt")
        await ctx.send(embed=embed, file=file)
        log.info(f"[SCRAPP] {ctx.author}: {len(albums)} albume din {url}")
