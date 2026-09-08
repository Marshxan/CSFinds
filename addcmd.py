"""
Comanda .add — deschide un post nou in forum cu poza branduita si linkurile de agenti.

    .add "<link produs>" "<link poza>" "<nume>" "<pret>"

Linkurile pot fi date in orice ordine: cel de Taobao/Weidian/1688 e produsul,
celalalt e poza. Poza poate fi si atasata la mesaj, in loc de link.

Forumul se alege dintr-un dropdown cu categoriile din Arc Finds. Daca dai comanda
direct in forum sau mentionezi unul cu #nume, sare peste dropdown.

Titlul postului iese ca in forum: "CDG PLAY Tee ~ $14".
"""
import asyncio
import io
import logging
import os
import re
from functools import partial

import discord
from discord.ext import commands

import imagecmd
import linkgen
import poststore
import yupoo_scrape as ys

log = logging.getLogger("welcome-bot.add")

# Categoria din care se aleg forumurile in dropdown (Arc Finds).
FINDS_CATEGORY_ID = int(os.getenv("FINDS_CATEGORY_ID", "1543853861737857144"))
# Acelasi rol ca in bot.py: cine il are poate folosi panoul, oricine ar fi postat mesajul.
OWNER_ROLE_ID = int(os.getenv("OWNER_ROLE_ID", "1543695086037237841"))
PICKER_TIMEOUT = 180
ADD_BUTTON_ID = "csfinds:add"          # fix, ca butonul sa mearga si dupa restart

# URL-ul se opreste la ghilimele si <>, altfel inghite delimitatorul si
# stricam si linkul, si perechile de ghilimele ramase pentru titlu.
URL_RE = re.compile(r"""https?://[^\s"'<>]+""")
CHANNEL_RE = re.compile(r"<#(\d+)>")
QUOTED_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'|(\S+)')


def split_args(text: str):
    """-> (linkuri, id canal mentionat, bucati de text ramase)."""
    channel_id = None
    m = CHANNEL_RE.search(text)
    if m:
        channel_id = int(m.group(1))
        text = text.replace(m.group(0), " ")

    urls = [u.rstrip(",.") for u in URL_RE.findall(text)]
    text = URL_RE.sub(" ", text).replace("<", " ").replace(">", " ")

    parts = [(a or b or c).strip() for a, b, c in QUOTED_RE.findall(text)]
    return urls, channel_id, [p for p in parts if p]


def build_title(parts: list) -> str:
    """['CDG PLAY Tee', '14'] -> 'CDG PLAY Tee ~ $14'. O singura bucata = titlu gata facut."""
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    name, price = " ".join(parts[:-1]), parts[-1]
    if not price.startswith("$"):
        price = "$" + price.lstrip("$")
    return f"{name} ~ {price}"


def clean_url(raw: str) -> str:
    """Curata un link lipit de pe telefon. Ridica ValueError cu un motiv citibil.

    Copierea de pe mobil strica linkuri: taie caractere invizibile inauntru sau
    inlocuieste punctul din domeniu cu spatiu ("media discordapp.net"). Prindem
    asta aici, altfel urllib arunca "URL can't contain control characters", care
    nu-i spune nimanui ce sa faca.
    """
    url = "".join(ch for ch in raw.strip().strip("<>") if ch.isprintable())
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("it doesn't start with `http`")
    if any(ch.isspace() for ch in url):
        raise ValueError("it has a space in it - it got mangled when copied. "
                         "Send `.convert` with the photo attached and copy that link")
    return url


def pick_links(urls: list):
    """-> (link produs, link poza). Produsul e cel pe care il recunoaste linkgen."""
    product = image = None
    for u in urls:
        if linkgen.parse_link(u) is not None:
            if product is None:
                product = u
        elif image is None:
            image = u
    return product, image


async def resolve_forum(ctx: commands.Context, channel_id):
    """Forumul mentionat, altfel cel in care s-a dat comanda (direct sau dintr-un post)."""
    if channel_id is not None:
        channel = ctx.guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await ctx.guild.fetch_channel(channel_id)
            except Exception as e:
                log.warning(f"Nu pot obtine canalul {channel_id}: {e}")
                return None
        return channel if isinstance(channel, discord.ForumChannel) else None

    channel = ctx.channel
    if isinstance(channel, discord.Thread):
        channel = channel.parent          # comanda data intr-un post din forum
    return channel if isinstance(channel, discord.ForumChannel) else None


def forums_in_category(guild: discord.Guild) -> list:
    """Forumurile din categoria Arc Finds, in ordinea din server."""
    category = guild.get_channel(FINDS_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        return []
    return [c for c in category.channels if isinstance(c, discord.ForumChannel)]


async def create_post(forum: discord.ForumChannel, title: str, jpg: bytes,
                      platform: str, item_id: str, links: dict, weight,
                      image_url: str = ""):
    """Deschide postul: poza in mesajul de start, linkurile de agenti ca raspuns."""
    created = await forum.create_thread(
        name=title[:100],
        file=discord.File(io.BytesIO(jpg), filename="csfinds.jpg"),
    )
    links_msg = await created.thread.send(
        embed=linkgen.build_embed(platform, item_id, weight),
        view=linkgen.LinkView(links),
    )
    # Retinem postul ca sa poata fi editat din panoul web. Id-ul mesajului cu
    # linkuri nu se poate deduce mai tarziu, deci se salveaza acum.
    poststore.save({
        "thread_id": created.thread.id,
        "forum_id": forum.id,
        "guild_id": forum.guild.id,
        "links_message_id": links_msg.id,
        "title": title,
        "product_url": links.get("Raw", ""),
        "image_url": image_url,
        "platform": platform,
        "item_id": item_id,
        "weight": weight,
    })
    # QC-ul se ia randand pagina Kakobuy (10-20 s), deci merge in fundal:
    # postul e gata imediat, iar albumele pica in thread cand sunt.
    asyncio.create_task(post_qc(created.thread, links.get("Raw", "")))
    return created.thread


async def post_qc(thread, url: str):
    """Albumele de QC ale produsului, trimise in postul proaspat deschis.

    Ruleaza detasat de comanda: daca produsul n-are QC sau scraperul pica,
    postul ramane cum e si doar se logheaza.
    """
    if not url:
        return
    try:
        sent = await imagecmd.send_qc_albums(thread, url)
    except Exception as e:
        log.warning(f"QC pentru {url} a picat: {e}")
        return
    if sent:
        log.info(f"[ADD] {sent} albume QC in #{thread}")


async def prepare_post(product_url: str, image_url: str = "", data: bytes = None):
    """Poza branduita + linkurile de agenti. -> (jpg, platform, item_id, links, weight)."""
    loop = asyncio.get_running_loop()
    if data is None:
        data = await loop.run_in_executor(None, partial(ys.fetch, image_url))
    jpg = await imagecmd.process_async(data, with_logo=True)
    if len(jpg) > imagecmd.MAX_UPLOAD:
        raise RuntimeError("the processed image is too large for Discord")

    platform, item_id, raw = linkgen.parse_link(product_url)
    links = linkgen.build_agent_links(platform, item_id, raw)
    weight = None
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            weight = await linkgen.fetch_weight(session, platform, item_id)
    except Exception as e:
        log.debug(f"Nu am putut lua greutatea: {e}")
    return jpg, platform, item_id, links, weight


def post_summary(thread, forum, title: str, links: dict, weight) -> discord.Embed:
    """Confirmarea de la final: ce s-a postat, unde, si cu ce link."""
    embed = discord.Embed(title="Posted", color=linkgen.EMBED_COLOR)
    name, sep, price = title.rpartition(" ~ ")
    if not sep:                      # titlu fara pret -> totul e nume
        name, price = title, ""
    embed.add_field(name="Name", value=name, inline=True)
    if price:
        embed.add_field(name="Price", value=price, inline=True)
    embed.add_field(name="Weight", value=f"{weight}g" if weight else "Unknown", inline=True)
    embed.add_field(name="Forum", value=forum.mention, inline=True)
    embed.add_field(name="Post", value=thread.mention, inline=True)
    embed.add_field(name="Product", value=links["Raw"], inline=False)
    embed.add_field(name="QC", value=links["QC"], inline=False)
    return embed


class ForumSelect(discord.ui.Select):
    def __init__(self, forums: list):
        super().__init__(
            placeholder="Choose a forum...",
            options=[discord.SelectOption(label=f.name[:100], value=str(f.id))
                     for f in forums[:25]],          # Discord permite max 25 optiuni
        )

    async def callback(self, interaction: discord.Interaction):
        view: "ForumPicker" = self.view
        forum = interaction.guild.get_channel(int(self.values[0]))
        if not isinstance(forum, discord.ForumChannel):
            await interaction.response.send_message("That forum no longer exists.", ephemeral=True)
            return

        # Postarea dureaza cateva secunde; blocam butonul ca sa nu iasa doua posturi.
        self.disabled = True
        await interaction.response.edit_message(content=f"Posting in **{forum.name}**...",
                                                view=view)
        try:
            thread = await create_post(forum, view.title, view.jpg, view.platform,
                                       view.item_id, view.links, view.weight,
                                       image_url=view.image_url)
        except discord.Forbidden:
            await interaction.edit_original_response(
                content=f"I'm not allowed to post in **{forum.name}** "
                        "(I need Create Posts / Attach Files).", view=None)
            return
        except discord.HTTPException as e:
            log.warning(f"Nu am putut crea postul: {e}")
            await interaction.edit_original_response(
                content=f"Couldn't create the post: `{e.text or e}`", view=None)
            return

        log.info(f"[ADD] {interaction.user} a postat '{view.title}' in #{forum}")
        await interaction.edit_original_response(
            content=None, view=None,
            embed=post_summary(thread, forum, view.title, view.links, view.weight))
        view.stop()


class ForumPicker(discord.ui.View):
    """Dropdown-ul cu forumurile. Doar cine a dat comanda poate alege."""

    def __init__(self, author_id: int, forums: list, title: str, jpg: bytes,
                 platform: str, item_id: str, links: dict, weight,
                 image_url: str = ""):
        super().__init__(timeout=PICKER_TIMEOUT)
        self.author_id = author_id
        self.title = title
        self.jpg = jpg
        self.image_url = image_url
        self.platform = platform
        self.item_id = item_id
        self.links = links
        self.weight = weight
        self.add_item(ForumSelect(forums))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only whoever ran the command can pick the forum.", ephemeral=True)
            return False
        return True


class AddModal(discord.ui.Modal, title="Add product"):
    """Formularul de la buton: tot ce trebuie pentru post, inclusiv forumul.

    discord.py 2.7 permite si select-uri in modal (prin ui.Label), asa ca
    forumul se alege direct aici - fara un al doilea pas cu dropdown.
    """

    def __init__(self, forums: list):
        super().__init__()
        self.product = discord.ui.TextInput(
            placeholder="Taobao / Weidian / 1688", max_length=500)
        self.image = discord.ui.TextInput(
            placeholder="https://photo.yupoo.com/...", max_length=500)
        self.product_name = discord.ui.TextInput(
            placeholder="CDG PLAY Tee", max_length=90)
        self.price = discord.ui.TextInput(placeholder="14", max_length=20)
        self.forum = discord.ui.Select(
            options=[discord.SelectOption(label=f.name[:100], value=str(f.id))
                     for f in forums[:25]])          # Discord permite max 25 optiuni

        self.add_item(discord.ui.Label(text="Product link", component=self.product))
        self.add_item(discord.ui.Label(text="Image link", component=self.image))
        self.add_item(discord.ui.Label(text="Product name", component=self.product_name))
        self.add_item(discord.ui.Label(text="Price", component=self.price))
        self.add_item(discord.ui.Label(text="Forum", component=self.forum))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            product_url = clean_url(self.product.value)
            image_url = clean_url(self.image.value)
        except ValueError as e:
            await interaction.response.send_message(
                f"That link won't work: {e}.", ephemeral=True)
            return

        if linkgen.parse_link(product_url) is None:
            await interaction.response.send_message(
                "That product link isn't Taobao / Weidian / 1688.", ephemeral=True)
            return

        forum = interaction.guild.get_channel(int(self.forum.values[0]))
        if not isinstance(forum, discord.ForumChannel):
            await interaction.response.send_message(
                "That forum no longer exists.", ephemeral=True)
            return

        # Procesarea pozei dureaza cateva secunde - Discord da timeout la 3s.
        await interaction.response.defer(thinking=True, ephemeral=True)
        title = build_title([self.product_name.value.strip(), self.price.value.strip()])
        try:
            payload = await prepare_post(product_url, image_url)
        except Exception as e:
            log.warning(f"Nu am putut pregati produsul: {e}")
            await interaction.followup.send(f"Couldn't prepare the image: `{e}`", ephemeral=True)
            return

        try:
            thread = await create_post(forum, title, *payload, image_url=image_url)
        except discord.Forbidden:
            await interaction.followup.send(
                f"I'm not allowed to post in **{forum.name}** "
                "(I need Create Posts / Attach Files).", ephemeral=True)
            return
        except discord.HTTPException as e:
            log.warning(f"Nu am putut crea postul: {e}")
            await interaction.followup.send(
                f"Couldn't create the post: `{e.text or e}`", ephemeral=True)
            return

        log.info(f"[ADD] {interaction.user} a postat '{title}' in #{forum}")
        await interaction.followup.send(
            embed=post_summary(thread, forum, title, payload[3], payload[4]),
            ephemeral=True)


class AddStarter(discord.ui.View):
    """Panoul cu butonul. Persistent: timeout=None + custom_id fix, ca sa mearga
    si dupa ce se repornește botul, fara sa mai dea nimeni comanda."""

    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if any(r.id == OWNER_ROLE_ID for r in getattr(interaction.user, "roles", [])):
            return True
        if await interaction.client.is_owner(interaction.user):
            return True
        await interaction.response.send_message(
            "You don't have permission to use this button.", ephemeral=True)
        return False

    @discord.ui.button(label="Add product", emoji="➕",
                       style=discord.ButtonStyle.success, custom_id=ADD_BUTTON_ID)
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        forums = forums_in_category(interaction.guild)
        if not forums:
            await interaction.response.send_message(
                "No forums found in the Arc Finds category.", ephemeral=True)
            return
        await interaction.response.send_modal(AddModal(forums))


def setup_add_command(client: commands.Bot):
    @client.listen("on_ready")
    async def register_panel():
        """Fara asta, butonul dintr-un panou vechi nu mai raspunde dupa restart."""
        client.add_view(AddStarter())

    @client.command(name="addpanel")
    @commands.has_permissions(manage_guild=True)
    async def add_panel(ctx: commands.Context):
        """Pune un panou permanent: oricine are rolul de owner apasa butonul,
        fara sa mai scrie nimic. Mesajul ramane, nu se sterge."""
        await linkgen.delete_command_message(ctx)
        embed = discord.Embed(
            title="Add a product",
            description="Click the button below to post a product to the finds forums.",
            color=linkgen.EMBED_COLOR)
        await ctx.send(embed=embed, view=AddStarter())
        log.info(f"[ADDPANEL] {ctx.author} a pus panoul in #{ctx.channel}")

    @client.command(name="add")
    async def add_cmd(ctx: commands.Context, *, args: str = ""):
        urls, channel_id, parts = split_args(args)
        product_url, image_url = pick_links(urls)
        attachments = [a for a in ctx.message.attachments if imagecmd._is_image(a)]
        title = build_title(parts)

        # `.add` gol -> buton + formular. Cu argumente -> merge direct, ca inainte.
        if not args.strip() and not ctx.message.attachments:
            await linkgen.delete_command_message(ctx)
            await ctx.send("Click the button to add a product:",
                           view=AddStarter())
            return

        usage = ('Usage: `.add "<product link>" "<image link>" "<name>" "<price>"`\n'
                 "Or just send `.add` on its own and fill in the form.")
        if product_url is None:
            await ctx.send(f"Missing the Taobao / Weidian / 1688 link.\n{usage}",
                           delete_after=25)
            return
        if image_url is None and not attachments:
            await ctx.send(f"Missing the image.\n{usage}", delete_after=25)
            return
        if not title:
            await ctx.send(f"Missing the product name.\n{usage}", delete_after=25)
            return

        forum = await resolve_forum(ctx, channel_id)
        if forum is None and not forums_in_category(ctx.guild):
            await ctx.send("No forums found in the Arc Finds category. "
                           "Run the command inside a forum or mention one with #name.",
                           delete_after=25)
            return

        async with ctx.typing():
            loop = asyncio.get_running_loop()

            # 1. Poza: atasament sau link, apoi acelasi tratament ca la .image.
            try:
                if attachments:
                    data = await attachments[0].read()
                else:
                    data = await loop.run_in_executor(None, partial(ys.fetch, image_url))
                jpg = await imagecmd.process_async(data, with_logo=True)
            except Exception as e:
                log.warning(f"Nu am putut pregati poza: {e}")
                await ctx.send(f"Couldn't prepare the image: `{e}`", delete_after=25)
                return
            if len(jpg) > imagecmd.MAX_UPLOAD:
                await ctx.send("The processed image is too large for Discord.", delete_after=20)
                return

            # 2. Linkurile de agenti, exact ca la .link.
            platform, item_id, raw = linkgen.parse_link(product_url)
            links = linkgen.build_agent_links(platform, item_id, raw)
            weight = None
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    weight = await linkgen.fetch_weight(session, platform, item_id)
            except Exception as e:
                log.debug(f"Nu am putut lua greutatea: {e}")

            # 3. Fara forum explicit -> lasam owner-ul sa aleaga din dropdown.
            if forum is None:
                await linkgen.delete_command_message(ctx)
                await ctx.send(
                    f"**{title}** - which forum should it go in?",
                    view=ForumPicker(ctx.author.id, forums_in_category(ctx.guild),
                                     title, jpg, platform, item_id, links, weight,
                                     image_url=image_url or ""))
                return

            try:
                thread = await create_post(forum, title, jpg, platform, item_id,
                                           links, weight, image_url=image_url or "")
            except discord.Forbidden:
                await ctx.send("I'm not allowed to post in that forum "
                               "(I need Create Posts / Attach Files).", delete_after=25)
                return
            except discord.HTTPException as e:
                log.warning(f"Nu am putut crea postul: {e}")
                await ctx.send(f"Couldn't create the post: `{e.text or e}`", delete_after=25)
                return

        await linkgen.delete_command_message(ctx)
        log.info(f"[ADD] {ctx.author} a postat '{title}' in #{forum}")
        await ctx.send(embed=post_summary(thread, forum, title, links, weight),
                       delete_after=60)
