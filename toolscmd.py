"""
Canalul de tools: arunci un link si botul face restul.

Orice link de Taobao / Weidian / 1688 - sau de agent (KakoBuy, OOPBuy, CSSBuy...) -
postat in canalul de tools primeste automat butoanele de agenti si greutatea.
Daca vrei sa-l si publici pe forum, apesi butonul si completezi doar numele.

Poza vine singura: atasata la mesaj daca ai pus una, altfel botul o ia de pe
pagina produsului prin Kakobuy. Deci minimul e un link si un nume.
"""
import asyncio
import logging
import os
import re
from functools import partial

import discord
from discord.ext import commands, tasks

import addcmd
import imagecmd
import linkgen
import linkstore

log = logging.getLogger("welcome-bot.tools")

TOOLS_CHANNEL_ID = int(os.getenv("TOOLS_CHANNEL_ID", "1544320705980661800"))
OWNER_ROLE_ID = int(os.getenv("OWNER_ROLE_ID", "1543695086037237841"))
FORM_TIMEOUT = 900          # 15 minute ca sa apuci sa apesi butonul
PREVIEW_COUNT = 4           # cate poze de produs aratam la alegere
PREVIEW_TIMEOUT = 40        # daca Kakobuy nu raspunde repede, mergem mai departe

URL_RE = re.compile(r"""https?://[^\s"'<>]+""")


def is_owner(member) -> bool:
    return any(r.id == OWNER_ROLE_ID for r in getattr(member, "roles", []))


class PostModal(discord.ui.Modal, title="Post to forum"):
    """Doar ce nu se poate ghici: numele, si optional pretul si poza."""

    def __init__(self, product_url: str, forums: list, image_url: str = "",
                 price: str = ""):
        super().__init__()
        self.product_url = product_url
        # Tinem poza aleasa si pe instanta: `default` pe TextInput nu e trimis
        # inapoi de Discord daca omul nu atinge campul, si atunci ajungeam sa
        # cautam automat poza produsului si sa punem prima, nu pe cea aleasa.
        self.preset_image = image_url
        self.name = discord.ui.TextInput(placeholder="CDG PLAY Tee", max_length=90)
        # Pretul vine de pe pagina Kakobuy (minimul si maximul intre modele),
        # dar ramane editabil: uneori vrei sa ascunzi o varianta scumpa.
        self.price = discord.ui.TextInput(placeholder="14", max_length=20,
                                          default=price, required=False)
        self.preset_price = price
        # Greutatea nu se poate lua automat (Doppel e in spatele unui CAPTCHA),
        # deci o scrii daca o stii; altfel postul arata "Unknown", ca pana acum.
        self.weight = discord.ui.TextInput(placeholder="850", max_length=10, required=False)
        self.image = discord.ui.TextInput(
            placeholder="leave empty and I'll pull it from the store page",
            default=image_url, max_length=500, required=False)
        self.forum = discord.ui.Select(
            options=[discord.SelectOption(label=f.name[:100], value=str(f.id))
                     for f in forums[:25]])

        self.add_item(discord.ui.Label(text="Product name", component=self.name))
        self.add_item(discord.ui.Label(text="Price (optional)", component=self.price))
        self.add_item(discord.ui.Label(text="Weight in grams (optional)",
                                       component=self.weight))
        self.add_item(discord.ui.Label(text="Image link", component=self.image))
        self.add_item(discord.ui.Label(text="Forum", component=self.forum))

    async def on_submit(self, interaction: discord.Interaction):
        forum = interaction.guild.get_channel(int(self.forum.values[0]))
        if not isinstance(forum, discord.ForumChannel):
            await interaction.response.send_message("That forum no longer exists.",
                                                    ephemeral=True)
            return
        typed = self.image.value.strip() or self.preset_image
        if typed:
            try:
                image_url = addcmd.clean_url(typed)
            except ValueError as e:
                await interaction.response.send_message(
                    f"That image link won't work: {e}.", ephemeral=True)
                return
        else:
            image_url = ""

        await interaction.response.defer(thinking=True, ephemeral=True)

        # Fara poza data de om, o luam de pe pagina produsului. Taobao/Weidian
        # blocheaza cererile server-side, deci trece prin Kakobuy (Playwright);
        # dureaza 10-20s, de aceea o facem abia acum, nu la fiecare link postat.
        if not image_url:
            try:
                loop = asyncio.get_running_loop()
                photos = await loop.run_in_executor(
                    None, partial(imagecmd.kakobuy_images, self.product_url))
                image_url = photos[0] if photos else ""
            except Exception as e:
                log.warning(f"Nu am putut lua poza produsului: {e}")
                await interaction.followup.send(
                    f"I couldn't fetch the product photo: `{e}`" + chr(10) +
                    "Attach a photo to your message, or paste an image link in the form.",
                    ephemeral=True)
                return
            if not image_url:
                await interaction.followup.send(
                    "No photos found on that product page. Attach one instead.",
                    ephemeral=True)
                return

        # Ca si la poza, `default` nu se intoarce daca omul n-a atins campul.
        parts = [self.name.value.strip()]
        price = self.price.value.strip() or self.preset_price
        if price:
            parts.append(price)
        title = addcmd.build_title(parts)

        try:
            payload = await addcmd.prepare_post(self.product_url, image_url)
            manual = self.weight.value.strip().rstrip("g")
            if manual.isdigit():          # ce scrie omul bate ce stie botul
                payload = payload[:4] + (int(manual),)
            thread = await addcmd.create_post(forum, title, *payload, image_url=image_url)
        except discord.Forbidden:
            await interaction.followup.send(
                f"I can't post in **{forum.name}** (I need Create Posts / Attach Files).",
                ephemeral=True)
            return
        except Exception as e:
            log.warning(f"Postare din tools esuata: {e}")
            await interaction.followup.send(f"Couldn't post it: `{e}`", ephemeral=True)
            return

        parsed = linkgen.parse_any(self.product_url)
        if parsed:
            linkstore.record_post(parsed[0], parsed[1], thread.id, forum.name, title,
                                  guild_id=interaction.guild.id)

        log.info(f"[TOOLS] {interaction.user} a postat '{title}' in #{forum}")
        await interaction.followup.send(
            embed=addcmd.post_summary(thread, forum, title, payload[3], payload[4]),
            ephemeral=True)


class PhotoSelect(discord.ui.Select):
    """Alegerea unei poze deschide direct formularul, deja completat cu ea."""

    def __init__(self, photos: list, product_url: str, price: str = ""):
        super().__init__(
            placeholder="Pick a photo and post it...",
            options=[discord.SelectOption(label=f"Photo {i}", value=str(i - 1))
                     for i in range(1, len(photos) + 1)],
            row=2)
        self.photos = photos
        self.product_url = product_url
        self.price = price

    async def callback(self, interaction: discord.Interaction):
        if not is_owner(interaction.user):
            await interaction.response.send_message(
                "You don't have permission to use this.", ephemeral=True)
            return
        forums = addcmd.forums_in_category(interaction.guild)
        if not forums:
            await interaction.response.send_message(
                "No forums found in the Arc Finds category.", ephemeral=True)
            return
        await interaction.response.send_modal(
            PostModal(self.product_url, forums,
                      self.photos[int(self.values[0])], self.price))


class ToolsView(discord.ui.View):
    """Butoanele de agenti plus butonul de publicare."""

    def __init__(self, links: dict, product_url: str, image_url: str = "",
                 photos: list = None, price: str = ""):
        super().__init__(timeout=FORM_TIMEOUT)
        self.product_url = product_url
        self.image_url = image_url
        self.price = price
        for label, emoji, row in linkgen.BUTTONS:
            self.add_item(discord.ui.Button(
                label=label, emoji=linkgen.CUSTOM_EMOJIS.get(label, emoji),
                url=links[label], row=row))
        if photos:
            self.add_item(PhotoSelect(photos, product_url, price))

    @discord.ui.button(label="Post to forum", emoji="\N{OUTBOX TRAY}",
                       style=discord.ButtonStyle.success, row=3)
    async def post(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_owner(interaction.user):
            await interaction.response.send_message(
                "You don't have permission to use this button.", ephemeral=True)
            return
        forums = addcmd.forums_in_category(interaction.guild)
        if not forums:
            await interaction.response.send_message(
                "No forums found in the Arc Finds category.", ephemeral=True)
            return
        await interaction.response.send_modal(
            PostModal(self.product_url, forums, self.image_url, self.price))


SESSION_CHECK_HOURS = 12


def setup_tools_channel(client: commands.Bot):
    @tasks.loop(hours=SESSION_CHECK_HOURS)
    async def kakobuy_watch():
        """Sesiunea Kakobuy expira in tacere: pretul iese gol si pozele lipsesc,
        fara nicio eroare. Verificam periodic si spunem in canalul de tools."""
        loop = asyncio.get_running_loop()
        ok, motiv = await loop.run_in_executor(None, imagecmd.kakobuy_session_ok)
        if ok:
            log.info("[KAKOBUY] sesiune buna")
            return
        log.warning(f"[KAKOBUY] {motiv}")
        channel = client.get_channel(TOOLS_CHANNEL_ID)
        if channel:
            await channel.send(
                "⚠️ **Kakobuy login expired** - prices and photos will come back "
                "empty until it's renewed." + chr(10) +
                "Run `node scraper/kakobuy-login.js` in the kfinds.net folder.")

    # Pornirea se face din on_ready, nu de aici: setup_tools_channel ruleaza la
    # import, cand inca nu exista bucla de evenimente, iar `.start()` o cere.
    # Fiind pornita din on_ready, bucla nu mai are nevoie de `before_loop`.
    @client.listen("on_ready")
    async def _porneste_watch():
        if not kakobuy_watch.is_running():
            kakobuy_watch.start()

    @client.listen("on_message")
    async def on_tools_message(message: discord.Message):
        if message.author.bot or message.channel.id != TOOLS_CHANNEL_ID:
            return
        if not is_owner(message.author):
            return
        # Comenzile normale isi vad de treaba; aici prindem doar linkurile simple.
        if message.content.startswith((".", "!")):
            return

        # Intai fara retea, ca sa nu deschidem o conexiune pentru fiecare mesaj
        # din canal. Abia daca niciun link nu e recunoscut incercam sa desfacem
        # linkurile scurte de agent (ikako.vip &co).
        gasite = [u.strip("<>,") for u in URL_RE.findall(message.content)]
        parsed = next((p for p in map(linkgen.parse_any, gasite) if p), None)
        if parsed is None and gasite:
            loop = asyncio.get_running_loop()
            for url in gasite:
                parsed = await loop.run_in_executor(
                    None, partial(linkgen.parse_any_deep, url))
                if parsed:
                    break
        if parsed is None:
            return

        platform, item_id, raw = parsed
        links = linkgen.build_agent_links(platform, item_id, raw)

        weight = None
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                weight = await linkgen.fetch_weight(session, platform, item_id)
        except Exception as e:
            log.debug(f"Greutatea nu a venit: {e}")

        photo = next((a.url for a in message.attachments if imagecmd._is_image(a)), "")

        loop = asyncio.get_running_loop()
        # Un singur randat de pagina da si pretul, si pozele: amandoua stau in
        # starea paginii. Galeria randata poate lipsi (uneori apare un captcha
        # in loc), starea nu.
        item = await loop.run_in_executor(None, partial(imagecmd.kakobuy_item, raw))
        price = item["price"]

        # Fara poza atasata, aratam pozele produsului ca sa poata alege una.
        photos = []
        if not photo:
            photos = item["images"][:PREVIEW_COUNT]
            if not photos:      # rezerva: citim galeria randata, ca inainte
                try:
                    photos = await loop.run_in_executor(None, partial(
                        imagecmd.kakobuy_images, raw, PREVIEW_COUNT, PREVIEW_TIMEOUT))
                except Exception as e:
                    log.info(f"Nu am putut lua pozele produsului: {e}")

        embed = linkgen.build_embed(platform, item_id, weight)
        embeds = [embed]

        # Acelasi produs aruncat a doua oara: spunem unde e deja, ca sa nu ajunga
        # de doua ori pe forum. Nu blocam nimic - uneori chiar vrei sa repostezi.
        already = linkstore.posted_before(platform, item_id)
        if already:
            lines = []
            for p in already[-3:]:
                url = linkstore.thread_url(p)
                when = (p.get("posted") or "")[:10]
                name = p.get("title") or "post"
                lines.append(f"[{name}]({url}) in #{p.get('forum')} ({when})"
                             if url else f"**{name}** in #{p.get('forum')} ({when})")
            embed.add_field(
                name="⚠️ Already posted"
                     + (f" ({len(already)}x)" if len(already) > 1 else ""),
                value="\n".join(lines), inline=False)
        if photo:
            embed.set_footer(text="Photo picked up from your message")
        elif photos:
            embed.set_footer(text=f"Pick one of the {len(photos)} photos below")
            # Embed-uri cu acelasi `url` sunt grupate de Discord intr-o galerie.
            embed.url = raw
            for src in photos:
                extra = discord.Embed(url=raw)
                extra.set_image(url=src)
                embeds.append(extra)

        # Tot ce am aflat despre link intra in links.json: data viitoare cand
        # apare acelasi produs, avem deja pretul si pozele salvate.
        linkstore.record(platform, item_id, url=raw, price=price,
                         images=item["images"], store_title=item["title"],
                         weight=weight or item["weight"], agent_links=links)

        await message.reply(embeds=embeds,
                            view=ToolsView(links, raw, photo, photos, price),
                            mention_author=False)
        log.info(f"[TOOLS] {message.author}: {platform} {item_id}"
                 f"{' + poza' if photo else ''}{' + $' + price if price else ''}")
