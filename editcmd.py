"""
`.edit <link postare>` - editatul postarilor direct din Discord, de pe telefon.

Aceleasi lucruri ca in panoul web (webpanel.edit_save), doar ca imbracate in
modal si butoane: panoul se leaga pe 127.0.0.1, deci de pe telefon nu exista.
"""
import asyncio
import io
import logging
import re
from functools import partial

import discord
from discord.ext import commands
from PIL import Image

import addcmd
import brand
import imagecmd
import linkgen
import linkstore
import poststore
import yupoo_scrape as ys

log = logging.getLogger("welcome-bot.edit")

# https://discord.com/channels/<guild>/<thread>[/<mesaj>] sau doar id-ul.
LINK_RE = re.compile(r"(?:channels/\d+/)?(\d{15,25})")


def _thread_id(argument: str):
    m = LINK_RE.search(argument or "")
    return int(m.group(1)) if m else None


def _forums(client) -> list:
    for guild in client.guilds:
        found = addcmd.forums_in_category(guild)
        if found:
            return found
    return []


async def _get_thread(client, thread_id):
    return client.get_channel(thread_id) or await client.fetch_channel(thread_id)


def _rezumat(post: dict, client) -> discord.Embed:
    forum = client.get_channel(post.get("forum_id"))
    embed = discord.Embed(
        title=post.get("title") or "(no title)",
        color=linkgen.EMBED_COLOR,
        description=f"[Open the post](https://discord.com/channels/"
                    f"{post.get('guild_id')}/{post['thread_id']})")
    embed.add_field(name="Name", value=post.get("name") or "-", inline=True)
    embed.add_field(name="Price",
                    value=f"${post['price']}" if post.get("price") else "-",
                    inline=True)
    embed.add_field(name="Weight",
                    value=f"{post['weight']}g" if post.get("weight") else "-",
                    inline=True)
    embed.add_field(name="Product link", value=post.get("product_url") or "-",
                    inline=False)
    embed.add_field(name="Category", value=forum.name if forum else "-", inline=True)
    if post.get("image_url"):
        embed.set_thumbnail(url=post["image_url"])
    return embed


async def _aplica(post: dict, client, *, name, price, product_url, image_url,
                  weight_in) -> str:
    """Salveaza modificarile pe Discord. -> textul de raspuns. Ridica la eroare."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Name can't be empty.")
    price = (price or "").strip().lstrip("$")
    title = poststore.build_title(name, price)
    product_url = (product_url or "").strip()
    image_url = (image_url or "").strip()
    weight_in = (weight_in or "").strip().rstrip("g")
    new_weight = int(weight_in) if weight_in.isdigit() else None

    thread = await _get_thread(client, post["thread_id"])
    changed = []

    if title != post.get("title"):
        await thread.edit(name=title[:100])
        changed.append("title")

    link_changed = bool(product_url) and product_url != post.get("product_url")
    weight_changed = new_weight != post.get("weight")
    if link_changed or weight_changed:
        platform, item_id = post.get("platform"), post.get("item_id")
        if link_changed:
            parsed = linkgen.parse_link(product_url)
            if parsed is None:
                raise ValueError("That product link isn't Taobao / Weidian / 1688.")
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

    if image_url and image_url != post.get("image_url"):
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, partial(ys.fetch, image_url))
        jpg = await imagecmd.process_async(data, with_logo=True)
        if len(jpg) > imagecmd.MAX_UPLOAD:
            raise ValueError("That image is too big once processed.")
        starter = thread.get_partial_message(thread.id)
        await starter.edit(attachments=[
            discord.File(io.BytesIO(jpg), filename="csfinds.jpg")])
        post["image_url"] = image_url
        changed.append("image")

    post.update(title=title, name=name, price=price)
    poststore.save(post)
    log.info(f"[EDIT] Post {post['thread_id']} actualizat: {', '.join(changed) or 'nimic'}")
    return f"Saved - updated {', '.join(changed)}." if changed else "Nothing changed."


class DetailsModal(discord.ui.Modal, title="Edit product"):
    """Discord da voie la 5 campuri intr-un modal - exact cate are si panoul."""

    def __init__(self, view: "EditView"):
        super().__init__()
        post = view.post
        self.view_ref = view
        self.name_in = discord.ui.TextInput(
            label="Name", default=post.get("name") or "", max_length=90)
        self.price_in = discord.ui.TextInput(
            label="Price ($)", default=post.get("price") or "", required=False,
            max_length=10)
        self.link_in = discord.ui.TextInput(
            label="Product link", default=post.get("product_url") or "",
            required=False, style=discord.TextStyle.paragraph)
        self.weight_in = discord.ui.TextInput(
            label="Weight (g)",
            default=str(post["weight"]) if post.get("weight") else "",
            required=False, max_length=6)
        self.image_in = discord.ui.TextInput(
            label="Image link (leave as is to keep the photo)",
            default=post.get("image_url") or "", required=False,
            style=discord.TextStyle.paragraph)
        for item in (self.name_in, self.price_in, self.link_in, self.weight_in,
                     self.image_in):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        # Reprocesarea pozei dureaza mai mult decat cele 3s ale interactiunii.
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = self.view_ref
        try:
            mesaj = await _aplica(
                view.post, interaction.client,
                name=self.name_in.value, price=self.price_in.value,
                product_url=self.link_in.value, image_url=self.image_in.value,
                weight_in=self.weight_in.value)
        except discord.Forbidden:
            return await interaction.followup.send(
                "The bot can't edit that post.", ephemeral=True)
        except Exception as e:
            log.warning(f"Editare esuata pentru {view.post['thread_id']}: {e}")
            return await interaction.followup.send(f"Couldn't save: {e}",
                                                   ephemeral=True)
        await interaction.followup.send(mesaj, ephemeral=True)
        await view.refresh(interaction)


class MoveSelect(discord.ui.Select):
    def __init__(self, view: "EditView", forums: list):
        super().__init__(placeholder="Move to another category...",
                         options=[discord.SelectOption(
                             label=f.name[:100], value=str(f.id),
                             default=str(f.id) == str(view.post.get("forum_id")))
                             for f in forums[:25]])
        self.view_ref = view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = self.view_ref
        post = view.post
        tinta = interaction.client.get_channel(int(self.values[0]))
        if tinta is None or str(tinta.id) == str(post.get("forum_id")):
            return await interaction.followup.send("It's already in that category.",
                                                   ephemeral=True)
        vechi = post.get("thread_id")
        try:
            # Discord nu muta threaduri intre forumuri: il refacem si il stergem
            # pe cel vechi. Raspunsurile (inclusiv QC-ul) se pierd.
            payload = await addcmd.prepare_post(post["product_url"], post["image_url"])
            if str(post.get("weight") or "").isdigit():
                payload = payload[:4] + (int(post["weight"]),)
            thread = await addcmd.create_post(tinta, post["title"], *payload,
                                              image_url=post["image_url"])
        except Exception as e:
            log.warning(f"Mutarea postarii {vechi} a esuat: {e}")
            return await interaction.followup.send(f"Couldn't re-post it: {e}",
                                                   ephemeral=True)
        try:
            vechi_thread = await _get_thread(interaction.client, vechi)
            await vechi_thread.delete()
        except Exception as e:
            log.warning(f"Postarea veche {vechi} nu a putut fi stearsa: {e}")
        poststore.remove(vechi)
        log.info(f"[EDIT] postare mutata in {tinta.id}: {post['title']}")
        view.post = poststore.get(thread.id) or view.post
        await interaction.followup.send(
            f"Moved to **{tinta.name}**. Replies and QC photos from the old post "
            "are gone.", ephemeral=True)
        await view.refresh(interaction)


def poze_stiute(post: dict) -> list:
    """Pozele stiute ale produsului, din evidenta linkurilor (ca in panou)."""
    if not (post.get("platform") and post.get("item_id")):
        return []
    poze = linkstore.get(post["platform"], post["item_id"]).get("images") or []
    # Cea pusa acum prima, ca sa se vada de la ce pleci.
    poze = [p for p in ([post.get("image_url")] + list(poze)) if p]
    return list(dict.fromkeys(poze))[:24]


class PhotoSelect(discord.ui.Select):
    """Panoul arata miniaturile produsului si schimbi poza dintr-un click.

    In Discord nu se pot pune poze intr-un meniu, dar linkurile sunt aceleasi:
    alegi "Photo 2" si postarea se retaie cu ea. Fara asta ar trebui sa copiezi
    linkul pozei de mana in formular.
    """

    def __init__(self, view: "EditView", poze: list):
        super().__init__(placeholder="Change the photo...", row=2,
                         options=[discord.SelectOption(
                             label=f"Photo {i}" + (" (current)" if i == 1 else ""),
                             value=str(i - 1)) for i in range(1, len(poze) + 1)])
        self.view_ref = view
        self.poze = poze

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        view = self.view_ref
        post = view.post
        aleasa = self.poze[int(self.values[0])]
        if aleasa == post.get("image_url"):
            return await interaction.followup.send("That's already the photo.",
                                                   ephemeral=True)
        try:
            mesaj = await _aplica(
                post, interaction.client, name=post.get("name"),
                price=post.get("price"), product_url=post.get("product_url"),
                image_url=aleasa,
                weight_in=str(post.get("weight") or ""))
        except Exception as e:
            log.warning(f"Schimbarea pozei a esuat pentru {post['thread_id']}: {e}")
            return await interaction.followup.send(f"Couldn't change it: {e}",
                                                   ephemeral=True)
        await interaction.followup.send(mesaj, ephemeral=True)
        await view.refresh(interaction)


class EditView(discord.ui.View):
    def __init__(self, post: dict, author_id: int, forums: list):
        super().__init__(timeout=600)
        self.post = post
        self.author_id = author_id
        self.message = None
        if forums:
            self.add_item(MoveSelect(self, forums))
        poze = poze_stiute(post)
        if len(poze) > 1:
            self.add_item(PhotoSelect(self, poze))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Run `.edit` yourself to edit this post.", ephemeral=True)
            return False
        return True

    async def refresh(self, interaction: discord.Interaction):
        """Reimprospateaza rezumatul de sus dupa o salvare."""
        self.post = poststore.get(self.post["thread_id"]) or self.post
        try:
            await interaction.message.edit(
                embed=_rezumat(self.post, interaction.client), view=self)
        except Exception:
            pass

    @discord.ui.button(label="Edit details", style=discord.ButtonStyle.primary,
                       emoji="✏️", row=1)
    async def edit_details(self, interaction: discord.Interaction, _):
        await interaction.response.send_modal(DetailsModal(self))

    @discord.ui.button(label="Redo background", style=discord.ButtonStyle.secondary,
                       emoji="🪄", row=1)
    async def redo_bg(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True, thinking=True)
        post = self.post
        if not post.get("image_url"):
            return await interaction.followup.send("There's no image link to work from.",
                                                   ephemeral=True)
        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, partial(ys.fetch, post["image_url"]))
            jpg = await imagecmd.process_async(data, with_logo=True,
                                               model=brand.HEAVY_MODEL)
            if len(jpg) > imagecmd.MAX_UPLOAD:
                return await interaction.followup.send(
                    "The redone image is too big to upload.", ephemeral=True)
            thread = await _get_thread(interaction.client, post["thread_id"])
            await thread.get_partial_message(thread.id).edit(attachments=[
                discord.File(io.BytesIO(jpg), filename="csfinds.jpg")])
        except Exception as e:
            log.warning(f"Refacerea fundalului a esuat pentru {post['thread_id']}: {e}")
            return await interaction.followup.send(f"Couldn't redo the background: {e}",
                                                   ephemeral=True)
        log.info(f"[EDIT] fundal refacut cu {brand.HEAVY_MODEL} pentru {post['thread_id']}")
        await interaction.followup.send(
            f"Background redone with the heavy model ({brand.HEAVY_MODEL}).",
            ephemeral=True)

    # Threadul poate fi sters de mana de pe Discord, si atunci randul ramane aici
    # si strica listele. Panoul are butonul asta; il aducem si in bot.
    @discord.ui.button(label="Forget (thread is gone)",
                       style=discord.ButtonStyle.secondary, row=3)
    async def uita(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await _get_thread(interaction.client, self.post["thread_id"])
        except Exception as e:
            if not (getattr(e, "code", None) == 10003 or "10003" in str(e)):
                return await interaction.followup.send(
                    f"Couldn't check that thread: {e}", ephemeral=True)
        else:
            return await interaction.followup.send(
                "That thread still exists - use `Delete post` if you want it gone.",
                ephemeral=True)
        poststore.remove(self.post["thread_id"])
        log.info(f"[EDIT] rand sters pentru threadul disparut {self.post['thread_id']}")
        for item in self.children:
            item.disabled = True
        await interaction.followup.send("Removed it from the panel.", ephemeral=True)
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Delete post", style=discord.ButtonStyle.danger,
                       emoji="🗑️", row=1)
    async def delete_post(self, interaction: discord.Interaction, button):
        if button.label != "Really delete?":
            button.label = "Really delete?"
            return await interaction.response.edit_message(view=self)
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            thread = await _get_thread(interaction.client, self.post["thread_id"])
            await thread.delete()
        except Exception as e:
            log.warning(f"Stergerea postarii {self.post['thread_id']} a esuat: {e}")
            return await interaction.followup.send(f"Couldn't delete it: {e}",
                                                   ephemeral=True)
        poststore.remove(self.post["thread_id"])
        log.info(f"[EDIT] postare stearsa: {self.post.get('title')}")
        for item in self.children:
            item.disabled = True
        await interaction.followup.send("Post deleted.", ephemeral=True)
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
        self.stop()


# ---------- .posts: lista postarilor, ca prima pagina a panoului ----------
# Panoul are un tabel cu tot ce e postat si dai click pe un rand ca sa-l editezi.
# Aici: cauti dupa nume, alegi din meniu si primesti exact ecranul de la `.edit`.
POSTS_PER_PAGE = 25           # limita Discord pentru un meniu de selectie


def cauta_postari(text: str) -> list:
    """Postarile care se potrivesc cu textul (nume, pret, link, platforma)."""
    posts = poststore.all_posts()
    text = (text or "").strip().lower()
    if not text:
        return posts
    return [p for p in posts
            if text in " ".join(str(p.get(k) or "") for k in
                                ("name", "title", "price", "platform",
                                 "product_url")).lower()]


class PostSelect(discord.ui.Select):
    def __init__(self, posts: list, author_id: int, forums: list):
        super().__init__(
            placeholder=f"Edit one of the {len(posts)} posts...",
            options=[discord.SelectOption(
                label=(p.get("name") or p.get("title") or "untitled")[:95],
                description=(f"${p['price']}" if p.get("price") else None),
                value=str(p["thread_id"])) for p in posts])
        self.author_id = author_id
        self.forums = forums

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            return await interaction.response.send_message(
                "Run `.posts` yourself to edit these.", ephemeral=True)
        # Postarea poate fi schimbata intre timp, deci o citim din nou.
        post = poststore.get(int(self.values[0]))
        if post is None:
            return await interaction.response.send_message(
                "That post isn't in the panel anymore.", ephemeral=True)
        view = EditView(post, interaction.user.id, self.forums)
        await interaction.response.send_message(
            embed=_rezumat(post, interaction.client), view=view, ephemeral=True)


class PostsView(discord.ui.View):
    def __init__(self, posts: list, author_id: int, forums: list):
        super().__init__(timeout=600)
        self.add_item(PostSelect(posts, author_id, forums))


def setup_posts_command(client: commands.Bot):
    @client.command(name="posts", aliases=["list"])
    async def posts(ctx: commands.Context, *, cauta: str = ""):
        """`.posts [text]` - ce e postat pe forumuri, si editezi din lista."""
        gasite = cauta_postari(cauta)
        if not gasite:
            return await ctx.send(
                f"Nothing matching `{cauta}`." if cauta else
                "Nothing posted yet. Posts made with `.add` show up here.")

        aratate = gasite[:POSTS_PER_PAGE]
        randuri = []
        for p in aratate[:10]:
            pret = f"${p['price']}" if p.get("price") else "-"
            forum = client.get_channel(p.get("forum_id"))
            randuri.append(
                f"[{(p.get('name') or p.get('title') or 'untitled')[:45]}]"
                f"(https://discord.com/channels/{p.get('guild_id')}/{p['thread_id']})"
                f" - {pret}" + (f" - {forum.name}" if forum else ""))

        embed = discord.Embed(
            title=f"{len(gasite)} posts" + (f" matching '{cauta}'" if cauta else ""),
            description=chr(10).join(randuri)[:4000],
            color=linkgen.EMBED_COLOR)
        if len(gasite) > len(randuri):
            embed.set_footer(
                text=f"Showing {len(randuri)} of {len(gasite)}. The menu holds "
                     f"{len(aratate)} - narrow it down with `.posts <name>`.")
        await ctx.send(embed=embed,
                       view=PostsView(aratate, ctx.author.id, _forums(client)))
        log.info(f"[POSTS] {ctx.author}: {len(gasite)} postari pentru '{cauta}'")

    return posts


def setup_edit_command(client: commands.Bot):
    @client.command(name="edit")
    async def edit(ctx: commands.Context, target: str = None):
        """`.edit <link postare>` sau `.edit` chiar in postare."""
        thread_id = _thread_id(target) if target else getattr(ctx.channel, "id", None)
        if thread_id is None:
            return await ctx.send("Send the post link: `.edit <link>`")

        post = poststore.get(thread_id)
        if post is None and target:
            # Un link de mesaj duce la id-ul mesajului, nu al threadului; pe
            # forum, threadul e canalul in care sta mesajul.
            try:
                msg_channel = await _get_thread(client, thread_id)
                post = poststore.get(getattr(msg_channel, "id", 0))
            except Exception:
                post = None
        if post is None:
            return await ctx.send(
                "I don't have that post in the panel - it has to be one posted "
                "with `.add`. Open the post and copy its link from the top.")

        view = EditView(post, ctx.author.id, _forums(client))
        view.message = await ctx.send(embed=_rezumat(post, client), view=view)


# ---------------------------------------------------------------- .fixbg
# Modelul rapid rateaza uneori taietura si postarea ramane cu produsul facut
# ferfenita (gri deschis pe fundal alb: vezi brand.RETRY_AREA). Pana acum
# singurul leac era butonul "Redo background", postare cu postare. Comanda asta
# le trece pe toate, se uita la fiecare taietura si o reface doar pe cea stricata.
FIXBG_PAUZA = 1.0        # secunde intre postari, ca sa nu lovim rate limitul
FIXBG_CANDIDATI = 6      # cate poze de rezerva incercam pentru un produs
FIXBG_NOTA_BUNA = 0.95   # nota de la care ne oprim din cautat


# Doua feluri de taietura ratata, si se vad la numere diferite:
#  - produsul facut ferfenita (haina gri pe alb): aria mastii sub brand.RETRY_AREA;
#  - muscaturi din contur (jumatate de tricou lipsa): masca e mare, dar
#    recuperarea de pe fundalul alb ii adauga mult.
# Prima se repara cu modelul greu, a doua o repara chiar pasul de recuperare -
# in ambele cazuri poza trebuie incarcata din nou pe Discord.
FIXBG_CRESTERE = 1.06    # cat sa adauge recuperarea ca sa fie muscatura, nu contur


def _e_stricata(data: bytes) -> bool:
    """True daca poza asta iese prost prin taietura veche."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    aria, crestere = brand.diagnostic(im)
    return aria < brand.RETRY_AREA or crestere >= FIXBG_CRESTERE


def _poza_de_rezerva(post: dict) -> tuple:
    """(bytes, link) pentru o poza vie a produsului, cand cea din postare a murit.

    Magazinele chinezesti isi rescriu pozele si linkul salvat da 404 - masurat,
    11 postari din 215. Produsul insa e acelasi, deci luam alta poza a lui:
    intai una din evidenta (instant), si abia daca nici aia nu mai traieste
    cerem pagina din nou prin Kakobuy (~15s). -> (None, "") daca nu iese nimic.
    """
    platform, item_id = post.get("platform"), post.get("item_id")
    incercate = {post.get("image_url")}

    def _prima_vie(poze):
        """Cea mai buna poza vie a produsului dintre primele cateva.

        Pozele unui produs nu sunt toate ale produsului: printre ele stau
        bannere, tabele de marimi si colaje cu scris (masurat: la Yeezy Foam,
        prima poza vie era un colaj si postarea iesea cu un titlu chinezesc in
        loc de papuc). Le dam tuturor o nota si o luam pe cea mai buna, nu pe
        prima care trece un prag: la o pereche de papuci toate notele sunt mici
        pe drept, si tot vrem sa alegem intre ele.
        """
        cea_buna, nota_buna = (None, ""), -1.0
        for u in poze[:FIXBG_CANDIDATI]:
            if not u or u in incercate:
                continue
            incercate.add(u)
            try:
                data = ys.fetch(u)
            except Exception:
                continue
            try:
                nota = brand.nota_produs(Image.open(io.BytesIO(data)).convert("RGB"))
            except Exception:
                nota = 0.0
            if nota > nota_buna:
                cea_buna, nota_buna = (data, u), nota
            if nota >= FIXBG_NOTA_BUNA:
                break               # nu mai are rost sa le incercam pe toate
        return cea_buna

    if platform and item_id:
        data, url = _prima_vie(linkstore.get(platform, item_id).get("images") or [])
        if data:
            return data, url

    if not post.get("product_url"):
        return None, ""
    item = imagecmd.kakobuy_item(post["product_url"])
    if not item["images"]:
        return None, ""
    if platform and item_id:
        linkstore.record(platform, item_id, url=post["product_url"],
                         images=item["images"])
    return _prima_vie(item["images"])


def setup_fixbg_command(client: commands.Bot):
    @client.command(name="fixbg")
    async def fixbg(ctx: commands.Context, *, rest: str = ""):
        """`.fixbg` - reface pozele taiate prost. Optional un #forum, sau `check`.

        Fara argumente trece prin toate postarile din evidenta. Cu `check` doar
        le numara, fara sa atinga nimic - util inainte de o rulare lunga.
        """
        doar_numar = "check" in rest.lower()
        # `all` sare peste orice ghicit si reface tot cu modelul bun. E singura
        # varianta sigura: poza unui produs asezat pe o cutie de carton de
        # aceeasi culoare iese mancata, si niciun semnal ieftin nu o deosebeste
        # de o taietura buna - dar modelul greu o taie corect (verificat pe UGG
        # Tasman). Dureaza ~20s de postare, deci se cere anume.
        tot = "all" in rest.lower().split()
        m = addcmd.CHANNEL_RE.search(rest)
        forum_id = int(m.group(1)) if m else None

        posts = [p for p in poststore.all_posts() if p.get("image_url")]
        if forum_id:
            posts = [p for p in posts if p.get("forum_id") == forum_id]
        if not posts:
            return await ctx.send("No posts with an image to work on.")

        if tot and doar_numar:
            return await ctx.send("`all` and `check` mean opposite things - "
                                  "pick one.")
        status = await ctx.send(
            f"Redoing all {len(posts)} posts with the heavy model... about "
            f"{len(posts) * 22 // 60 + 1} min. Nothing else will be slow, but "
            f"it's a long run." if tot else
            f"Checking {len(posts)} posts... about {len(posts) * 2 // 60 + 1} min"
            + ("." if doar_numar else ", plus ~25s for each one I redo."))

        loop = asyncio.get_running_loop()
        stricate = reparate = inlocuite = 0
        esuate, moarte = [], []
        for i, post in enumerate(posts, 1):
            try:
                poza_noua = ""
                try:
                    data = await loop.run_in_executor(
                        None, partial(ys.fetch, post["image_url"]))
                except Exception:
                    # Linkul pozei a murit la magazin. Produsul e acelasi, deci
                    # cautam alta poza a lui in loc sa raportam doar "404".
                    data, poza_noua = await loop.run_in_executor(
                        None, partial(_poza_de_rezerva, post))
                    if not data:
                        moarte.append(post.get("title") or str(post["thread_id"]))
                        continue

                stricata = tot or await loop.run_in_executor(
                    None, partial(_e_stricata, data))
                stricate += bool(stricata)
                # Poza inlocuita se reincarca oricum: cea din postare e taiata
                # dintr-un link care nu mai exista, deci nu se poate reface altfel.
                if not (stricata or poza_noua) or doar_numar:
                    continue

                jpg = await imagecmd.process_async(
                    data, with_logo=True,
                    model=brand.HEAVY_MODEL if stricata else None)
                if len(jpg) > imagecmd.MAX_UPLOAD:
                    raise RuntimeError("prea mare pentru upload")
                thread = await _get_thread(client, post["thread_id"])
                await thread.get_partial_message(thread.id).edit(attachments=[
                    discord.File(io.BytesIO(jpg), filename="csfinds.jpg")])
                if poza_noua:
                    post["image_url"] = poza_noua
                    poststore.save(post)
                    inlocuite += 1
                reparate += bool(stricata)
                log.info(f"[FIXBG] refacut {post['thread_id']}: {post.get('title')}"
                         + (f" (poza inlocuita: {poza_noua})" if poza_noua else ""))
            except Exception as e:
                esuate.append(f"{post.get('title') or post['thread_id']} - {e}")
                log.warning(f"[FIXBG] {post['thread_id']} a picat: {e}")
            await asyncio.sleep(FIXBG_PAUZA)
            if i % 10 == 0:
                gata = (f"{stricate} botched so far" if doar_numar
                        else f"{reparate} redone, {inlocuite} photos replaced")
                try:
                    await status.edit(content=f"Checking... {i}/{len(posts)}, {gata}.")
                except Exception:
                    pass

        embed = discord.Embed(
            color=linkgen.EMBED_COLOR,
            title="Backgrounds redone" if tot else "Backgrounds checked",
            description=((f"{len(posts)} posts, all redone with "
                          f"{brand.HEAVY_MODEL}, {reparate} replaced.") if tot else
                         f"{len(posts)} posts, {stricate} with a botched cut"
                         + ("" if doar_numar else
                            f", {reparate} redone" +
                            (f", {inlocuite} with a dead photo swapped for a live one"
                             if inlocuite else "") + ".")))
        if doar_numar:
            embed.add_field(name="Nothing changed",
                            value="Run `.fixbg` without `check` to redo them.",
                            inline=False)
        if moarte:
            embed.add_field(
                name=f"No live photo left ({len(moarte)})",
                value=("The listing is gone from the store, so there's nothing to "
                       "cut. Give them a new photo with `.edit`." + chr(10)
                       + chr(10).join(f"- {x[:50]}" for x in moarte[:8]))[:1000],
                inline=False)
        if esuate:
            embed.add_field(name=f"Failed {len(esuate)}",
                            value=chr(10).join(f"- {x}" for x in esuate[:8])[:1000],
                            inline=False)
        try:
            await status.delete()
        except Exception:
            pass
        await ctx.send(embed=embed)
