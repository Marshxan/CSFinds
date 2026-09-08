"""
Panou local pentru editat posturile de pe forum: titlu, link produs, poza.

Ruleaza in acelasi proces cu botul, pe bucla lui de evenimente. Asa poate
modifica direct postul pe Discord, fara nicio comunicare intre procese.

Se leaga doar pe 127.0.0.1, deci nu e vizibil din afara masinii. Nu are login:
cine ajunge la calculator ajunge si la panou.

    http://127.0.0.1:8787
"""
import asyncio
import html
import io
import re
import logging
import os
from functools import partial
from urllib.parse import quote, urlparse

import discord
from aiohttp import web

import addcmd
import brand
import imagecmd
import linkgen
import poststore
import yupoo_scrape as ys

log = logging.getLogger("welcome-bot.panel")

URL_RE = re.compile(r"""https?://[^\s"'<>]+""")

PANEL_HOST = os.getenv("PANEL_HOST", "127.0.0.1")
PANEL_PORT = int(os.getenv("PANEL_PORT", "8787"))

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#1a1c20;color:#e8eaed;
     font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#9aa0a6;margin:0 0 28px;font-size:14px}
a{color:#4aa8ff}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #2c2f34;vertical-align:top}
th{color:#9aa0a6;font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:.4px}
tr:hover td{background:#212429}
.thumb{width:52px;height:52px;object-fit:cover;border-radius:6px;background:#2c2f34}
.muted{color:#9aa0a6;font-size:13px}
label{display:block;margin:18px 0 6px;font-size:13px;color:#9aa0a6}
input[type=text]{width:100%;padding:10px 12px;border-radius:8px;
    border:1px solid #3a3e44;background:#212429;color:#e8eaed;font-size:14px}
input[type=text]:focus{outline:none;border-color:#4aa8ff}
button{margin-top:24px;padding:10px 20px;border:0;border-radius:8px;
    background:#3b7dd8;color:#fff;font-size:14px;font-weight:600;cursor:pointer}
button:hover{background:#4a8ce8}
.note{background:#212429;border-left:3px solid #3b7dd8;padding:12px 14px;
    border-radius:0 8px 8px 0;margin:20px 0;font-size:14px}
.err{border-left-color:#e05a5a}
.empty{color:#9aa0a6;padding:40px 0;text-align:center}
select{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #3a3e44;
    background:#212429;color:#e8eaed;font-size:14px}
button.go{background:#2e9e5b}button.go:hover{background:#37b86a}
textarea{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #3a3e44;
    background:#212429;color:#e8eaed;font:13px/1.5 ui-monospace,Consolas,monospace;resize:vertical}
textarea:focus{outline:none;border-color:#4aa8ff}
.pick{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}
.pick img{width:72px;height:72px;object-fit:cover;border-radius:6px;cursor:pointer;
    border:2px solid transparent;background:#2c2f34}
.pick img:hover{border-color:#4aa8ff}
.nav{margin:0 0 20px}
.nav a{display:inline-block;padding:7px 14px;border-radius:8px;background:#212429;
    color:#e8eaed;text-decoration:none;font-size:13px;margin-right:8px}
.nav a:hover{background:#2c2f34}
.nav a.on{background:#3b7dd8;color:#fff}
button.del{background:#3a3e44}button.del:hover{background:#4a4e54}
"""


def page(title: str, body: str) -> web.Response:
    doc = (f"<!doctype html><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(title)}</title><style>{CSS}</style>"
           f"<div class='wrap'>{body}</div>")
    return web.Response(text=doc, content_type="text/html")


def esc(v) -> str:
    return html.escape(str(v or ""))


def nav(activ: str) -> str:
    """Aceleasi butoane pe fiecare pagina, ca sa nu mai tii minte adresele."""
    file = [("/", "Posts"), ("/drafts", "Drafts"), ("/bulk", "+ Add links")]
    parti = []
    for href, eticheta in file:
        clasa = " class='on'" if href == activ else ""
        parti.append(f"<a href='{href}'{clasa}>{eticheta}</a>")
    return "<div class='nav'>" + "".join(parti) + "</div>"


# ---------- Proxy de imagini ----------
# Multe magazine (Yupoo, Weidian) refuza cererile fara Referer de pe domeniul
# lor, deci browserul nu poate incarca pozele direct din panou. Le aducem noi.
#
# Aici a fost o lista alba de gazde permise, dar pozele vin de peste tot - CDN-ul
# Weidian, Alibaba, Discord, cautarea de imagini, blogurile de sneakers - si
# fiecare gazda noua aparea ca imagine rupta pana o adaugam de mana. Asa ca
# permitem orice gazda PUBLICA si pazim ce conteaza cu adevarat: proxy-ul sa nu
# ajunga o poarta catre reteaua locala (router, imprimanta, alte servicii de pe
# calculatorul lui), si sa nu serveasca altceva decat imagini.
THUMB = re.compile(r"(photo\.yupoo\.com/[^/]+/[0-9a-f]+/)[^/]+\.jpg")
CDN_THUMB = ("geilicdn.com", "alicdn.com")
THUMB_W = 500             # latimea ceruta CDN-ului; originalele au si 2560px
MAX_IMG = 12 * 1024 * 1024

# Semnaturile de inceput de fisier. Ne uitam la ele, nu la Content-Type: un CDN
# care minte ar putea trimite HTML sau altceva ca "image/jpeg".
SEMNATURI = (
    (bytes([0xFF, 0xD8, 0xFF]), "image/jpeg"),
    (bytes([0x89]) + b"PNG" + bytes([0x0D, 0x0A, 0x1A, 0x0A]), "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def tip_imagine(data: bytes):
    """Tipul real al pozei, dupa primii octeti. None daca nu e imagine."""
    for magic, mime in SEMNATURI:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp":                       # HEIC / AVIF
        marca = data[8:12]
        if marca in (b"avif", b"avis"):
            return "image/avif"
        if marca in (b"heic", b"heix", b"mif1"):
            return "image/heic"
    if data[:5] == b"<?xml" or data[:4] == b"<svg":
        return "image/svg+xml"
    return None


def gazda_publica(host: str) -> bool:
    """False pentru localhost, retea interna sau adrese rezervate.

    Fara asta, oricine poate face panoul sa ceara http://192.168.0.1/... sau
    un serviciu care asculta doar pe calculatorul asta, si sa vada raspunsul.
    """
    import ipaddress
    import socket
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        # is_global e fals pentru loopback, retele private, link-local si rezervate.
        if not ip.is_global:
            return False
    return bool(infos)


def thumb_url(url: str) -> str:
    """Varianta mica a pozei. Originalele au si 7 MB bucata."""
    url = url or ""
    if THUMB.search(url):
        return THUMB.sub(lambda m: m.group(1) + "medium.jpg", url)
    # Weidian si Alibaba redimensioneaza din query string.
    host = urlparse(url).netloc.lower()
    if any(host.endswith(h) for h in CDN_THUMB) and "?" not in url:
        return f"{url}?w={THUMB_W}"
    return url


def img_src(url: str, thumb: bool = True) -> str:
    """Adresa prin proxy, de pus in <img src=...>."""
    if not url:
        return ""
    return "/img?u=" + quote(thumb_url(url) if thumb else url, safe="")


async def _link_discord_proaspat(client, url: str):
    """Linkurile de atasament Discord sunt semnate si expira dupa cateva ore.

    Poza n-a disparut insa: e tot atasata la postare. Cerem mesajul de deschidere
    al threadului, luam linkul nou si il salvam, ca sa nu mai ceara nimeni de doua
    ori acelasi lucru.
    """
    if client is None:
        return None
    for post in poststore.all_posts():
        if post.get("image_url") != url or not post.get("thread_id"):
            continue
        try:
            thread = (client.get_channel(post["thread_id"])
                      or await client.fetch_channel(post["thread_id"]))
            # Pe forum, mesajul de deschidere are acelasi id ca threadul.
            starter = await thread.fetch_message(thread.id)
            if starter.attachments:
                nou = starter.attachments[0].url
                post["image_url"] = nou
                poststore.save(post)
                log.info(f"[PANEL] link de poza reimprospatat pentru {post['thread_id']}")
                return nou
        except Exception as e:
            log.debug(f"Nu am putut reimprospata poza {post.get('thread_id')}: {e}")
        return None
    return None


def _serveste(data: bytes):
    """Raspunsul catre browser, cu tipul real al pozei, nu unul ghicit."""
    mime = tip_imagine(data)
    if mime is None:
        raise web.HTTPUnsupportedMediaType(text="that wasn't an image")
    if len(data) > MAX_IMG:
        raise web.HTTPRequestEntityTooLarge(max_size=MAX_IMG, actual_size=len(data))
    return web.Response(body=data, content_type=mime,
                        headers={"Cache-Control": "max-age=86400"})


async def image_proxy(request):
    url = request.query.get("u", "")
    host = urlparse(url).netloc.lower().split(":")[0]
    if not url.startswith(("https://", "http://")) or not host:
        raise web.HTTPForbidden(text="only http(s) links")

    loop = asyncio.get_running_loop()
    if not await loop.run_in_executor(None, partial(gazda_publica, host)):
        # Nu e o poza de pe internet, ci ceva din reteaua locala.
        raise web.HTTPForbidden(text="host not allowed")

    try:
        data = await loop.run_in_executor(
            None, partial(ys.fetch, url, "https://" + host + "/"))
    except Exception as e:
        proaspat = None
        if "discordapp" in host:
            proaspat = await _link_discord_proaspat(request.app.get("client"), url)
        if proaspat:
            try:
                data = await loop.run_in_executor(
                    None, partial(ys.fetch, proaspat,
                                  "https://" + urlparse(proaspat).netloc + "/"))
                return _serveste(data)
            except Exception as e2:
                e = e2
        log.debug(f"Poza nu s-a incarcat ({url}): {e}")
        raise web.HTTPBadGateway(text="couldn't fetch that image")
    return _serveste(data)


# ---------- pagini ----------
async def index(request):
    posts = poststore.all_posts()
    if not posts:
        body = (nav("/") + "<h1>Posts</h1><p class='sub'>Nothing posted yet.</p>"
                "<div class='empty'>Posts made with <code>.add</code> show up here.</div>")
        return page("Posts", body)

    rows = []
    for p in posts:
        rows.append(
            f"<tr>"
            f"<td><img class='thumb' src='{esc(img_src(p.get('image_url')))}' alt=''></td>"
            f"<td><a href='/post/{p['thread_id']}'>{esc(p.get('name') or p.get('title'))}</a>"
            f"<div class='muted'>{esc(p.get('created', '')[:16].replace('T', ' '))}</div></td>"
            f"<td class='muted'>{'$' + esc(p['price']) if p.get('price') else '-'}</td>"
            f"<td class='muted'>{esc(p.get('weight')) or '?'}g</td>"
            f"<td class='muted'>{esc(p.get('platform'))}</td>"
            f"</tr>")
    body = (nav("/") + f"<h1>Posts</h1><p class='sub'>{len(posts)} posts &middot; "
            f"click one to edit its name, price, weight, link or image</p>"
            f"<table><tr><th></th><th>Name</th><th>Price</th><th>Weight</th>"
            f"<th>Platform</th></tr>"
            f"{''.join(rows)}</table>")
    return page("Posts", body)


async def edit_form(request, message: str = "", error: bool = False,
                    disparut: bool = False):
    client = request.app["client"]
    post = poststore.get(request.match_info["thread_id"])
    if post is None:
        raise web.HTTPNotFound(text="No such post")

    note = ""
    if message:
        note = f"<div class='note{' err' if error else ''}'>{esc(message)}</div>"

    body = (
        f"<p class='sub'><a href='/'>&larr; all posts</a></p>"
        f"<h1>{esc(post.get('title'))}</h1>"
        f"<p class='sub'>Saving updates the Discord post right away. "
        f"The thread title is rebuilt as <code>Name ~ $Price</code>.</p>"
        f"{note}"
        f"<img class='thumb' id='big' style='width:180px;height:180px' "
        f"src='{esc(img_src(post.get('image_url')))}' alt=''>"
        f"<form method='post'>"
        f"<label>Name</label>"
        f"<input type='text' name='name' value='{esc(post.get('name'))}'>"
        f"<label>Price (without the $) &mdash; empty means no price in the title</label>"
        f"<input type='text' name='price' value='{esc(post.get('price'))}'>"
        f"<label>Product link (Taobao / Weidian / 1688)</label>"
        f"<input type='text' name='product_url' value='{esc(post.get('product_url'))}'>"
        f"<label>Weight in grams &mdash; optional</label>"
        f"<input type='text' name='weight' value='{esc(post.get('weight'))}'>"
        f"<label>Image link &mdash; leave as is to keep the current photo</label>"
        f"<input type='text' name='image_url' id='image_url' "
        f"value='{esc(post.get('image_url'))}'>"
        f"{selector_poze(poze_produs(post))}"
        f"<label>Category &mdash; moving re-posts it and deletes the old thread, "
        f"with its replies</label>"
        f"<select name='forum_id'>{forum_options(client, post.get('forum_id'))}</select>"
        f"<button type='submit' name='action' value='save'>Save changes</button>"
        f" <button type='submit' name='action' value='redo_bg' class='del'>"
        f"Redo background</button>"
        f" <button type='submit' name='action' value='move' class='del'>"
        f"Move to category</button>"
        + (" <button type='submit' name='action' value='forget' class='del'>"
           "Remove from panel</button>" if disparut else "")
        + "</form>")
    return page("Edit post", body)


def poze_produs(post: dict) -> list:
    """Pozele stiute ale produsului, din evidenta linkurilor."""
    import linkstore
    if not (post.get("platform") and post.get("item_id")):
        return []
    return (linkstore.get(post["platform"], post["item_id"]).get("images") or [])[:8]


def selector_poze(poze: list, camp: str = "image_url") -> str:
    """Miniaturi pe care dai click ca sa schimbi poza din campul de mai sus.

    Multe anunturi au si poze cu cutia in cadru; cutia e un obiect real, la fel
    de mare ca produsul, deci taierea fundalului nu o poate deosebi. Cel mai
    rapid e sa alegi alta poza, nu sa te lupti cu masca.
    """
    if len(poze) < 2:
        return ""
    thumbs = "".join(
        f"<img src='{esc(img_src(u))}' title='photo {i}' "
        f"onclick=\"document.getElementById('{camp}').value={u!r};"
        f"document.getElementById('big').src=this.src\">"
        for i, u in enumerate(poze, 1))
    return f"<div class='pick'>{thumbs}</div>"


async def _muta_postarea(request, post: dict, forum_id: str):
    """Muta postarea in alt forum.

    Discord nu stie sa mute un thread dintr-un forum in altul, deci il facem din
    nou in forumul cerut si il stergem pe cel vechi. Raspunsurile de la postarea
    veche (inclusiv pozele de QC) se pierd - de aceea butonul spune asta.
    """
    client = request.app["client"]
    forums = {str(f.id): f for f in panel_forums(client)}
    tinta = forums.get(str(forum_id))
    if tinta is None:
        return await edit_form(request, "Pick a category to move it to.", error=True)
    if str(forum_id) == str(post.get("forum_id")):
        return await edit_form(request, "It's already in that category.", error=True)

    vechi = post.get("thread_id")
    try:
        payload = await addcmd.prepare_post(post["product_url"], post["image_url"])
        if str(post.get("weight") or "").isdigit():
            payload = payload[:4] + (int(post["weight"]),)
        thread = await addcmd.create_post(tinta, post["title"], *payload,
                                          image_url=post["image_url"])
    except Exception as e:
        log.warning(f"Mutarea postarii {vechi} a esuat: {e}")
        return await edit_form(request, f"Couldn't re-post it: {e}", error=True)

    # Abia dupa ce postarea noua exista stergem vechea: daca pica ceva mai sus,
    # ramai cu cea veche intreaga, nu fara niciuna.
    try:
        thread_vechi = (client.get_channel(vechi) or await client.fetch_channel(vechi))
        await thread_vechi.delete()
    except Exception as e:
        log.warning(f"Postarea veche {vechi} nu a putut fi stearsa: {e}")

    poststore.remove(vechi)
    log.info(f"[PANEL] postare mutata din {post.get('forum_id')} in {tinta.id}: "
             f"{post['title']}")
    raise web.HTTPFound(f"/post/{thread.id}")


async def _reia_fundalul(request, post: dict):
    """Taie iar fundalul, cu modelul greu, si inlocuieste poza din postare.

    Butonul are rost doar cu alt model: refacerea cu cel rapid ar da exact
    aceeasi poza. Modelul greu prinde suporturile si umbrele lipite de produs,
    dar dureaza ~20s in loc de ~2s.
    """
    client = request.app["client"]
    url = post.get("image_url")
    if not url:
        return await edit_form(request, "There's no image link to work from.",
                               error=True)
    try:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, partial(ys.fetch, url))
        jpg = await imagecmd.process_async(data, with_logo=True,
                                           model=brand.HEAVY_MODEL)
        if len(jpg) > imagecmd.MAX_UPLOAD:
            return await edit_form(request, "The redone image is too big to upload.",
                                   error=True)
        thread = (client.get_channel(post["thread_id"])
                  or await client.fetch_channel(post["thread_id"]))
        starter = thread.get_partial_message(thread.id)
        await starter.edit(attachments=[
            discord.File(io.BytesIO(jpg), filename="csfinds.jpg")])
    except Exception as e:
        log.warning(f"Refacerea fundalului a esuat pentru {post['thread_id']}: {e}")
        return await edit_form(request, f"Couldn't redo the background: {e}",
                               error=True)

    log.info(f"[PANEL] fundal refacut cu {brand.HEAVY_MODEL} "
             f"pentru {post['thread_id']}")
    return await edit_form(
        request, f"Background redone with the heavy model ({brand.HEAVY_MODEL}). "
        "Check the post on Discord.")


async def edit_save(request):
    client = request.app["client"]
    post = poststore.get(request.match_info["thread_id"])
    if post is None:
        raise web.HTTPNotFound(text="No such post")

    form = await request.post()
    name = (form.get("name") or "").strip()
    price = (form.get("price") or "").strip().lstrip("$")
    title = poststore.build_title(name, price)
    product_url = (form.get("product_url") or "").strip()
    image_url = (form.get("image_url") or "").strip()
    weight_in = (form.get("weight") or "").strip().rstrip("g")
    new_weight = int(weight_in) if weight_in.isdigit() else None

    if not name:
        return await edit_form(request, "Name can't be empty.", error=True)

    if form.get("action") == "move":
        return await _muta_postarea(request, post, form.get("forum_id") or "")

    if form.get("action") == "redo_bg":
        return await _reia_fundalul(request, post)

    if form.get("action") == "forget":
        poststore.remove(post["thread_id"])
        log.info(f"[PANEL] rand sters pentru threadul disparut {post['thread_id']}")
        raise web.HTTPFound("/")

    thread = client.get_channel(post["thread_id"])
    if thread is None:
        try:
            thread = await client.fetch_channel(post["thread_id"])
        except Exception as e:
            # 10003 = Unknown Channel: threadul a fost sters de pe Discord, dar
            # randul a ramas aici. Nu e nimic de reparat, doar de uitat.
            if getattr(e, "code", None) == 10003 or "10003" in str(e):
                return await edit_form(
                    request, "That thread no longer exists on Discord. "
                    "Nothing here can be edited - remove it from the panel?",
                    error=True, disparut=True)
            return await edit_form(request, f"Can't reach that post on Discord: {e}",
                                   error=True)

    changed = []
    try:
        # 1. Titlul threadului.
        if title != post.get("title"):
            await thread.edit(name=title[:100])
            changed.append("title")

        # 2. Linkul produsului sau greutatea -> refacem mesajul cu butoanele.
        link_changed = bool(product_url) and product_url != post.get("product_url")
        weight_changed = new_weight != post.get("weight")
        if link_changed or weight_changed:
            platform, item_id = post.get("platform"), post.get("item_id")
            if link_changed:
                parsed = linkgen.parse_link(product_url)
                if parsed is None:
                    return await edit_form(
                        request, "That product link isn't Taobao / Weidian / 1688.",
                        error=True)
                platform, item_id, raw = parsed
                post.update(product_url=raw, platform=platform, item_id=item_id)
                changed.append("links")
            if weight_changed:
                post["weight"] = new_weight
                changed.append("weight")

            links = linkgen.build_agent_links(platform, item_id, post["product_url"])
            msg = thread.get_partial_message(post["links_message_id"])
            await msg.edit(embed=linkgen.build_embed(platform, item_id, post["weight"]),
                           view=linkgen.LinkView(links))

        # 3. Poza -> o reprocesam si inlocuim atasamentul mesajului de deschidere.
        if image_url and image_url != post.get("image_url"):
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, partial(ys.fetch, image_url))
            jpg = await imagecmd.process_async(data, with_logo=True)
            if len(jpg) > imagecmd.MAX_UPLOAD:
                return await edit_form(request, "That image is too big once processed.",
                                       error=True)
            # Pe un forum, mesajul de deschidere are acelasi id ca threadul.
            starter = thread.get_partial_message(thread.id)
            await starter.edit(attachments=[
                discord.File(io.BytesIO(jpg), filename="csfinds.jpg")])
            post["image_url"] = image_url
            changed.append("image")
    except discord.Forbidden:
        return await edit_form(request, "The bot doesn't have permission to edit that post.",
                               error=True)
    except Exception as e:
        log.warning(f"Editare esuata pentru {post['thread_id']}: {e}")
        return await edit_form(request, f"Couldn't save: {e}", error=True)

    post.update(title=title, name=name, price=price)
    poststore.save(post)
    log.info(f"[PANEL] Post {post['thread_id']} actualizat: {', '.join(changed) or 'nimic'}")

    msg = f"Saved - updated {', '.join(changed)}." if changed else "Nothing changed."
    return await edit_form(request, msg)


# ---------- Drafturi: produse importate de pe Yupoo, inca nepostate ----------
async def drafts_index(request):
    drafts = poststore.all_drafts()
    if not drafts:
        return page("Drafts", nav("/drafts")
                    + "<h1>Drafts</h1><p class='sub'>Nothing waiting.</p>"
                    "<div class='empty'>Use <a href='/bulk'>+ Add links</a> to paste "
                    "product links, or <code>.import &lt;yupoo category&gt;</code> "
                    "in Discord.</div>")
    rows = []
    for d in drafts:
        rows.append(
            f"<tr>"
            f"<td><img class='thumb' src='{esc(img_src(d.get('photo')))}' alt=''></td>"
            f"<td><a href='/draft/{esc(d['id'])}'>{esc(d.get('name'))}</a>"
            f"<div class='muted'>{esc(d.get('album_url'))}</div></td>"
            f"<td class='muted'>{'$' + esc(d['price']) if d.get('price') else '-'}</td>"
            f"<td class='muted'>{esc(d.get('forum_name')) or '<i>pick one</i>'}</td>"
            f"</tr>")
    banner = ""
    if BULK["ruleaza"]:
        banner = (f"<div class='note'>Fetching {BULK['gata']}/{BULK['total']} links... "
                  f"<a href='/drafts'>refresh</a></div>")
    elif BULK["sarite"]:
        banner = ("<div class='note err'>Skipped " + str(len(BULK["sarite"])) + ": "
                  + esc("; ".join(BULK["sarite"][:4])) + "</div>")
    body = (nav("/drafts")
            + f"<h1>Drafts</h1><p class='sub'>{len(drafts)} waiting</p>"
            + banner
            + f"<table><tr><th></th><th>Name</th><th>Price</th><th>Category</th></tr>"
              f"{''.join(rows)}</table>")
    return page("Drafts", body)


def forum_options(client, selected, by_name: str = "") -> str:
    """Dropdown-ul de categorii. `by_name` preselecteaza dupa nume cand draftul
    are doar numele forumului, nu si id-ul (importuri facute din afara botului)."""
    opts = ["<option value=''>-- pick a category --</option>"]
    wanted = (by_name or "").lower()
    for f in panel_forums(client):
        chosen = (str(f.id) == str(selected)) or (
            not selected and wanted and wanted in f.name.lower())
        opts.append(f"<option value='{f.id}'{' selected' if chosen else ''}>"
                    f"{esc(f.name)}</option>")
    return "".join(opts)


def panel_forums(client) -> list:
    """Forumurile din categoria de finds, din primul server pe care e botul."""
    import addcmd
    for guild in client.guilds:
        found = addcmd.forums_in_category(guild)
        if found:
            return found
    return []


def photo_picker(draft: dict) -> str:
    """Miniaturile celorlalte poze ale produsului. Click = o pui in campul de sus.

    .bulk salveaza primele 8 poze; prima e doar o alegere automata, si rar e cea
    mai buna. Fara asta ar trebui sa cauti linkul de mana in pagina magazinului.
    """
    poze = draft.get("photos") or []
    if len(poze) < 2:
        return ""
    thumbs = "".join(
        f"<img src='{esc(img_src(u))}' title='photo {i}' "
        f"onclick=\"document.getElementById('photo').value={u!r};"
        f"document.getElementById('big').src=this.src\">"
        for i, u in enumerate(poze, 1))
    return f"<div class='pick'>{thumbs}</div>"


async def draft_form(request, message: str = "", error: bool = False):
    draft = poststore.get_draft(request.match_info["draft_id"])
    if draft is None:
        raise web.HTTPNotFound(text="No such draft")
    client = request.app["client"]

    note = (f"<div class='note{' err' if error else ''}'>{esc(message)}</div>"
            if message else "")
    body = (
        f"<p class='sub'><a href='/drafts'>&larr; all drafts</a></p>"
        f"<h1>{esc(draft.get('name'))}</h1>"
        f"<p class='sub'>Set the name, price and category, then post it.</p>"
        f"{note}"
        f"<img class='thumb' id='big' style='width:180px;height:180px' "
        f"src='{esc(img_src(draft.get('photo')))}' alt=''>"
        f"<form method='post'>"
        f"<label>Name</label>"
        f"<input type='text' name='name' value='{esc(draft.get('name'))}'>"
        f"<label>Price (without the $)</label>"
        f"<input type='text' name='price' value='{esc(draft.get('price'))}'>"
        f"<label>Weight in grams &mdash; optional, it can't be looked up</label>"
        f"<input type='text' name='weight' value='{esc(draft.get('weight'))}'>"
        f"<label>Category</label>"
        f"<select name='forum_id'>"
        f"{forum_options(client, draft.get('forum_id'), draft.get('forum_name'))}"
        f"</select>"
        f"<label>Product link</label>"
        f"<input type='text' name='product_url' value='{esc(draft.get('product_url'))}'>"
        f"<label>Image link</label>"
        f"<input type='text' name='photo' id='photo' value='{esc(draft.get('photo'))}'>"
        f"{photo_picker(draft)}"
        f"<button type='submit' name='action' value='save'>Save</button> "
        f"<button type='submit' name='action' value='post' class='go'>Post to Discord</button> "
        f"<button type='submit' name='action' value='delete' class='del'>Delete</button>"
        f"</form>")
    return page("Edit draft", body)


async def draft_save(request):
    draft = poststore.get_draft(request.match_info["draft_id"])
    if draft is None:
        raise web.HTTPNotFound(text="No such draft")
    client = request.app["client"]
    form = await request.post()
    action = form.get("action", "save")

    if action == "delete":
        poststore.remove_draft(draft["id"])
        raise web.HTTPFound("/drafts")

    draft.update(
        name=(form.get("name") or "").strip(),
        price=(form.get("price") or "").strip().lstrip("$"),
        product_url=(form.get("product_url") or "").strip(),
        photo=(form.get("photo") or "").strip(),
        weight=(form.get("weight") or "").strip().rstrip("g"),
        forum_id=(form.get("forum_id") or "").strip() or None,
    )
    forums = {str(f.id): f for f in panel_forums(client)}
    picked = forums.get(str(draft["forum_id"]))
    draft["forum_name"] = picked.name if picked else ""
    poststore.save_draft(draft)

    if action != "post":
        return await draft_form(request, "Saved.")

    # ---- publicare ----
    if not draft["name"]:
        return await draft_form(request, "Give it a name first.", error=True)
    if picked is None:
        return await draft_form(request, "Pick a category first.", error=True)
    if linkgen.parse_link(draft["product_url"]) is None:
        return await draft_form(request, "That product link isn't Taobao / Weidian / 1688.",
                                error=True)

    import addcmd
    title = addcmd.build_title([draft["name"], draft["price"]] if draft["price"]
                               else [draft["name"]])
    try:
        payload = await addcmd.prepare_post(draft["product_url"], draft["photo"])
        if str(draft.get("weight", "")).isdigit():
            payload = payload[:4] + (int(draft["weight"]),)
        thread = await addcmd.create_post(picked, title, *payload, image_url=draft["photo"])
    except discord.Forbidden:
        return await draft_form(request, f"No permission to post in {picked.name}.",
                                error=True)
    except Exception as e:
        log.warning(f"Publicare draft {draft['id']} esuata: {e}")
        return await draft_form(request, f"Couldn't post it: {e}", error=True)

    poststore.remove_draft(draft["id"])
    log.info(f"[PANEL] Draft {draft['id']} publicat in #{picked.name}")
    raise web.HTTPFound("/drafts")


def setup_panel(client):
    """Porneste panoul odata cu botul, pe aceeasi bucla de evenimente."""
    async def start_panel():
        # on_ready se poate declansa de mai multe ori (reconectari) - pornim o data.
        if getattr(client, "_panel_running", False):
            return
        client._panel_running = True

        app = web.Application()
        app["client"] = client
        app.router.add_get("/", index)
        app.router.add_get("/img", image_proxy)
        app.router.add_get("/drafts", drafts_index)
        app.router.add_get("/bulk", bulk_form)
        app.router.add_post("/bulk", bulk_start)
        app.router.add_get("/draft/{draft_id}", draft_form)
        app.router.add_post("/draft/{draft_id}", draft_save)
        app.router.add_get("/post/{thread_id}", edit_form)
        app.router.add_post("/post/{thread_id}", edit_save)

        runner = web.AppRunner(app)
        await runner.setup()
        try:
            await web.TCPSite(runner, PANEL_HOST, PANEL_PORT).start()
        except OSError as e:
            log.error(f"Panoul nu a pornit pe {PANEL_HOST}:{PANEL_PORT}: {e}")
            client._panel_running = False
            return
        log.info(f"[PANEL] http://{PANEL_HOST}:{PANEL_PORT}")

    client.add_listener(start_panel, "on_ready")


# ---------- Adaugare in masa ----------
# Acelasi lucru ca .bulk din Discord, dar din panou: lipesti linkurile, botul ia
# pozele si preturile, si le completezi pe rand. Aducerea dureaza ~3s pe produs,
# prea mult pentru un raspuns HTTP, deci merge in fundal si pagina de drafturi
# arata cat s-a facut.
BULK = {"total": 0, "gata": 0, "noi": 0, "sarite": [], "ruleaza": False}


async def _bulk_worker(client, linkuri: list, forum_id: str):
    import imagecmd
    import linkgen
    import linkstore
    import scrapecmd

    loop = asyncio.get_running_loop()
    forums = panel_forums(client)
    fortat = next((f for f in forums if str(f.id) == str(forum_id)), None)
    try:
        for url in linkuri:
            parsed = await loop.run_in_executor(
                None, partial(linkgen.parse_any_deep, url))
            if not parsed:
                BULK["sarite"].append(f"{url[:45]} - not a store link")
                BULK["gata"] += 1
                continue
            platform, item_id, raw = parsed
            try:
                item = await loop.run_in_executor(
                    None, partial(imagecmd.kakobuy_item, raw))
            except Exception as e:
                BULK["sarite"].append(f"{platform}:{item_id} - {e}")
                BULK["gata"] += 1
                continue

            if not item["images"]:
                BULK["sarite"].append(f"{platform}:{item_id} - no photos found")
                BULK["gata"] += 1
                continue

            ghicit = fortat or scrapecmd.route_forum(item["title"], forums)
            BULK["noi"] += poststore.save_draft({
                "id": f"{platform}-{item_id}",
                "name": item["title"],
                "price": item["price"],
                "weight": item["weight"] or "",
                "product_url": raw,
                "photo": item["images"][0],
                "photos": item["images"][:8],     # ca sa poti alege alta in formular
                "forum_id": ghicit.id if ghicit else None,
                "forum_name": ghicit.name if ghicit else "",
            })
            linkstore.record(platform, item_id, url=raw, price=item["price"],
                             images=item["images"], store_title=item["title"],
                             weight=item["weight"])
            BULK["gata"] += 1
    finally:
        BULK["ruleaza"] = False
        log.info(f"[BULK] gata: {BULK['noi']} drafturi noi, "
                 f"{len(BULK['sarite'])} sarite")


async def bulk_form(request):
    client = request.app["client"]
    stare = ""
    if BULK["ruleaza"]:
        stare = (f"<div class='note'>Fetching {BULK['gata']}/{BULK['total']}... "
                 f"<a href='/bulk'>refresh</a></div>")
    body = (
        nav("/bulk") + f"<h1>Add links</h1>"
        f"<p class='sub'>Paste product links, one per line &middot; "
        f"<a href='/drafts'>drafts</a></p>{stare}"
        f"<form method='post' action='/bulk'>"
        f"<label>Links &mdash; Kakobuy, Weidian, Taobao, 1688 or any agent link</label>"
        f"<textarea name='links' rows='10' placeholder='https://item.kakobuy.com/...&#10;"
        f"https://weidian.com/item.html?itemID=...'></textarea>"
        f"<label>Category for all of them &mdash; leave empty to guess from the name</label>"
        f"<select name='forum_id'>{forum_options(client, '')}</select>"
        f"<button type='submit'>Fetch photos and prices</button>"
        f"</form>")
    return page("Add links", body)


async def bulk_start(request):
    form = await request.post()
    linkuri = list(dict.fromkeys(
        u.strip("<>,") for u in URL_RE.findall(form.get("links") or "")))
    if not linkuri:
        return page("Add links", "<h1>Add links</h1>"
                    "<p class='sub'>No links found in that text.</p>"
                    "<p><a href='/bulk'>back</a></p>")
    if BULK["ruleaza"]:
        return web.HTTPFound("/drafts")

    BULK.update(total=len(linkuri), gata=0, noi=0, sarite=[], ruleaza=True)
    asyncio.create_task(_bulk_worker(request.app["client"], linkuri,
                                     form.get("forum_id") or ""))
    return web.HTTPFound("/drafts")
