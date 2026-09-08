"""
Comanda .bulk - mai multe linkuri de produs deodata, direct in drafturi.

.import face asta pentru o categorie Yupoo. Aici pleci de la linkuri de
magazin sau de agent (Kakobuy, Weidian, Taobao, 1688, OOPBuy...), oricate,
scrise unul sub altul sau puse intr-un .txt atasat:

    .bulk
    https://item.kakobuy.com/item/details?url=...
    https://weidian.com/item.html?itemID=...
    https://www.taobao.com/...

Pentru fiecare link botul ia pretul, pozele si numele din magazin de pe
Kakobuy, si lasa un draft in panou. Nu posteaza nimic: numele si categoria le
alegi tu acolo, ca la .import. Numele din magazin e chinezesc sau plin de
coduri, deci e doar o propunere.

Produsele deja postate sunt semnalate, nu sarite: uneori chiar vrei sa
repostezi.
"""
import asyncio
import logging
import os
import re
import time
from functools import partial

import discord
from discord.ext import commands

import addcmd
import imagecmd
import linkgen
import linkstore
import poststore
import scrapecmd
import toolscmd

log = logging.getLogger("welcome-bot.bulk")

MAX_LINKURI = 40          # o comanda data din greseala nu tine botul ocupat o ora
PAUZA = 0.5               # secunde intre produse, in firul fiecarui worker

# Fiecare produs porneste un Chrome prin scriptul de Kakobuy: ~3s in care
# botul doar asteapta. Cate 3 deodata taie timpul la o treime fara sa umple
# memoria; pe un PC slab pune BULK_WORKERS=1 in .env si e ca inainte.
WORKERS = max(1, int(os.getenv("BULK_WORKERS", "3")))
URL_RE = re.compile(r"""https?://[^\s"'<>]+""")


async def _citeste_linkuri(ctx, rest: str) -> list:
    """Linkurile din mesaj plus cele dintr-un .txt atasat, fara duplicate."""
    brute = URL_RE.findall(rest)
    for att in ctx.message.attachments:
        if att.filename.lower().endswith((".txt", ".csv")):
            try:
                brute += URL_RE.findall((await att.read()).decode("utf-8", "ignore"))
            except Exception as e:
                log.warning(f"Nu am putut citi {att.filename}: {e}")
    return list(dict.fromkeys(u.strip("<>,") for u in brute))


def setup_bulk_command(client: commands.Bot):
    @client.command(name="bulk", aliases=["bulkadd"])
    async def bulk(ctx: commands.Context, *, rest: str = ""):
        """.bulk <linkuri> - oricate linkuri de produs, deodata, in drafturi."""
        linkuri = await _citeste_linkuri(ctx, rest)
        if not linkuri:
            await ctx.send(
                "Usage: `.bulk` followed by product links, one per line "
                "(or attach a .txt with them)." + chr(10) +
                "Works with Kakobuy, Weidian, Taobao, 1688 and agent links.",
                delete_after=30)
            return

        prea_multe = len(linkuri) > MAX_LINKURI
        linkuri = linkuri[:MAX_LINKURI]
        forums = addcmd.forums_in_category(ctx.guild)

        # Categoria fortata, daca dai un #forum in comanda (ca la .import).
        forced = None
        m = addcmd.CHANNEL_RE.search(rest)
        if m:
            picked = ctx.guild.get_channel(int(m.group(1)))
            if isinstance(picked, discord.ForumChannel):
                forced = picked

        status = await ctx.send(
            f"Fetching {len(linkuri)} products from Kakobuy"
            f"{' (capped from more)' if prea_multe else ''}, {WORKERS} at a time... "
            f"about {len(linkuri) * 4 // WORKERS // 60 + 1} min.")

        loop = asyncio.get_running_loop()
        sem = asyncio.Semaphore(WORKERS)
        gata = 0

        async def _adu(url: str) -> dict:
            """Un produs: link -> draft. -> ce s-a intamplat, pentru raport."""
            nonlocal gata
            async with sem:
                # parse_any_deep desface intai linkurile scurte (ikako.vip &co),
                # deci face o cerere de retea - de aia pe thread separat.
                parsed = await loop.run_in_executor(
                    None, partial(linkgen.parse_any_deep, url))
                if not parsed:
                    gata += 1
                    return {"sarit": f"{url[:40]} - not a store link"}
                platform, item_id, raw = parsed

                # Produs vazut deja (alt .bulk, .import, tools): datele sunt in
                # evidenta, deci sarim randarea paginii - de la ~3s la instant.
                stiut = linkstore.get(platform, item_id)
                if stiut.get("images"):
                    item = {"title": stiut.get("store_title") or "",
                            "price": stiut.get("price") or "",
                            "images": stiut.get("images") or [],
                            "weight": stiut.get("weight")}
                    din_cache = True
                else:
                    item = await loop.run_in_executor(
                        None, partial(imagecmd.kakobuy_item, raw))
                    din_cache = False
                    await asyncio.sleep(PAUZA)

                gata += 1
                if not item["images"]:
                    return {"sarit": f"{platform}:{item_id} - no photos found"}

                nume = (item["title"] or "").strip()
                ghicit = forced or scrapecmd.route_forum(nume, forums)
                is_new = poststore.save_draft({
                    "id": f"{platform}-{item_id}",
                    "name": nume,
                    "price": item["price"],
                    "weight": item["weight"] or "",
                    "product_url": raw,
                    "photo": item["images"][0],
                    "forum_id": ghicit.id if ghicit else None,
                    "forum_name": ghicit.name if ghicit else "",
                })
                if not din_cache:
                    linkstore.record(platform, item_id, url=raw, price=item["price"],
                                     images=item["images"], store_title=item["title"],
                                     weight=item["weight"])
                return {
                    "id": f"{platform}-{item_id}",
                    "nou": is_new,
                    "postat": (nume or f"{platform}:{item_id}")
                              if linkstore.posted_before(platform, item_id) else None,
                }

        async def _progres(mesaj):
            """Rescrie mesajul de start la fiecare 5s: altfel stai orbeste minute."""
            while True:
                await asyncio.sleep(5)
                try:
                    await mesaj.edit(content=f"Fetching products... {gata}/{len(linkuri)}")
                except Exception:
                    return

        start = time.monotonic()
        ceas = asyncio.create_task(_progres(status))
        try:
            # Un produs care crapa (Chrome picat, retea) nu trebuie sa arunce
            # la gunoi celelalte 39: il trecem la sarite si mergem mai departe.
            rezultate = await asyncio.gather(*(_adu(u) for u in linkuri),
                                             return_exceptions=True)
        finally:
            ceas.cancel()

        for i, r in enumerate(rezultate):
            if isinstance(r, BaseException):
                log.warning(f"[BULK] {linkuri[i]} a picat: {r}")
                rezultate[i] = {"sarit": f"{linkuri[i][:40]} - {type(r).__name__}"}

        noi = sum(1 for r in rezultate if r.get("nou"))
        deja_draft = sum(1 for r in rezultate if "nou" in r and not r["nou"])
        deja_postate = [r["postat"] for r in rezultate if r.get("postat")]
        sarite = [r["sarit"] for r in rezultate if r.get("sarit")]
        durata = time.monotonic() - start

        try:
            await status.delete()
        except Exception:
            pass
        # Cardurile de postare: acelasi lucru ca in canalul de tools, ca sa
        # poti publica de pe telefon fara sa ajungi la panou.
        proaspete = [d for d in (poststore.get_draft(r["id"]) for r in rezultate
                                 if r.get("id")) if d]
        view = DraftPicker(proaspete, ctx.author.id) if proaspete else None
        await ctx.send(embed=_raport(noi, deja_draft, deja_postate, sarite, durata),
                       view=view)
        log.info(f"[BULK] {ctx.author}: {noi} noi, {deja_draft} deja in drafturi, "
                 f"{len(sarite)} sarite, in {durata:.0f}s")

    @client.command(name="short", aliases=["aff", "share"])
    async def short(ctx: commands.Context, *, rest: str = ""):
        """.short <linkuri> - linkurile scurte de afiliat (ikako.vip).

        Kakobuy le creeaza pe server, legate de contul logat - de aceea nu se
        pot compune din id-ul produsului, ci se cer pagina cu pagina.
        """
        linkuri = await _citeste_linkuri(ctx, rest)
        if not linkuri:
            await ctx.send("Usage: `.short` followed by product links, one per line.",
                           delete_after=25)
            return

        linkuri = linkuri[:MAX_LINKURI]
        await ctx.send(f"Generating {len(linkuri)} affiliate links...")

        loop = asyncio.get_running_loop()
        randuri = []
        for url in linkuri:
            parsed = await loop.run_in_executor(
                None, partial(linkgen.parse_any_deep, url))
            if not parsed:
                randuri.append(f"{url[:40]} - not a store link")
                continue
            platform, item_id, raw = parsed
            item = await loop.run_in_executor(
                None, partial(imagecmd.kakobuy_item, raw, None, True))
            if item["share"]:
                linkstore.record(platform, item_id, url=raw, share=item["share"],
                                 price=item["price"], images=item["images"],
                                 store_title=item["title"])
                randuri.append(item["share"])
            else:
                randuri.append(f"{platform}:{item_id} - couldn't get a short link")
            await asyncio.sleep(PAUZA)

        # Fara embed: asa poti copia linkurile direct, fara sa le desparta nimic.
        await ctx.send(chr(10).join(randuri)[:1900])
        log.info(f"[SHORT] {ctx.author}: {len(randuri)} linkuri")

    return bulk


def _raport(noi: int, deja_draft: int, deja_postate: list, sarite: list,
            durata: float = 0):
    embed = discord.Embed(
        title=f"Queued {noi} products",
        description=("Pick one below to post it straight from here - name, price "
                     "and category are in the form." + chr(10) +
                     "They're also waiting in the panel: "
                     f"http://{scrapecmd.PANEL_URL}/drafts"),
        color=discord.Color.from_rgb(0, 162, 232))
    if deja_draft:
        embed.add_field(name="Already in drafts", value=str(deja_draft), inline=True)
    if durata:
        embed.add_field(name="Took", value=f"{durata:.0f}s", inline=True)
    if deja_postate:
        embed.add_field(
            name=f"⚠️ Already on the forum ({len(deja_postate)})",
            value=chr(10).join(f"- {n[:50]}" for n in deja_postate[:6])[:1000],
            inline=False)
    if sarite:
        embed.add_field(name=f"Skipped {len(sarite)}",
                        value=chr(10).join(f"- {x}" for x in sarite[:6])[:1000],
                        inline=False)
    return embed


# ---------- Postarea drafturilor direct din Discord ----------
# Panoul web se leaga pe 127.0.0.1, deci de pe telefon nu exista. Aici scoatem
# fix cardul din canalul de tools (butoane de agenti, galerie de poze, "Post to
# forum"), doar ca pentru produsele proaspat aduse de .bulk.
PREVIEW_COUNT = 4


class DraftPostModal(toolscmd.PostModal):
    """Formularul din tools, plus stersul draftului dupa ce postarea a reusit."""

    def __init__(self, draft: dict, forums: list, image_url: str, parsed: tuple):
        super().__init__(draft["product_url"], forums, image_url,
                         str(draft.get("price") or ""))
        self.draft_id = draft["id"]
        self.parsed = parsed
        self.name.default = (draft.get("name") or "")[:90]
        if str(draft.get("weight") or "").isdigit():
            self.weight.default = str(draft["weight"])

    async def on_submit(self, interaction: discord.Interaction):
        platform, item_id = self.parsed[0], self.parsed[1]
        inainte = len(linkstore.posted_before(platform, item_id))
        await super().on_submit(interaction)
        # PostModal isi raspunde singur si nu spune daca a iesit; singurul semn
        # sigur ca postarea exista e un rand nou in evidenta.
        if len(linkstore.posted_before(platform, item_id)) > inainte:
            poststore.remove_draft(self.draft_id)
            log.info(f"[BULK] draft {self.draft_id} postat din Discord")


class DraftCard(discord.ui.View):
    """Butoanele de agenti + alegerea pozei + publicarea, pentru un draft."""

    def __init__(self, draft: dict, links: dict, photos: list, parsed: tuple):
        super().__init__(timeout=toolscmd.FORM_TIMEOUT)
        self.draft, self.photos, self.parsed = draft, photos, parsed
        for label, emoji, row in linkgen.BUTTONS:
            self.add_item(discord.ui.Button(
                label=label, emoji=linkgen.CUSTOM_EMOJIS.get(label, emoji),
                url=links[label], row=row))
        if len(photos) > 1:
            self.add_item(_PhotoPick(self, photos))

    def _forums(self, interaction) -> list:
        return addcmd.forums_in_category(interaction.guild)

    async def deschide(self, interaction: discord.Interaction, image_url: str):
        if not toolscmd.is_owner(interaction.user):
            return await interaction.response.send_message(
                "You don't have permission to use this.", ephemeral=True)
        forums = self._forums(interaction)
        if not forums:
            return await interaction.response.send_message(
                "No forums found in the Arc Finds category.", ephemeral=True)
        await interaction.response.send_modal(
            DraftPostModal(self.draft, forums, image_url, self.parsed))

    @discord.ui.button(label="Post to forum", emoji="\N{OUTBOX TRAY}",
                       style=discord.ButtonStyle.success, row=3)
    async def post(self, interaction: discord.Interaction, _):
        await self.deschide(interaction, self.photos[0] if self.photos else "")

    # Panoul are un buton de sters draftul; fara el, un produs pe care nu-l vrei
    # ramane in coada la nesfarsit si il tot vezi in lista.
    @discord.ui.button(label="Delete draft", emoji="\N{WASTEBASKET}", style=discord.ButtonStyle.danger,
                       row=3)
    async def sterge(self, interaction: discord.Interaction, button):
        if not toolscmd.is_owner(interaction.user):
            return await interaction.response.send_message(
                "You don't have permission to use this.", ephemeral=True)
        if button.label != "Really delete?":
            button.label = "Really delete?"
            return await interaction.response.edit_message(view=self)
        poststore.remove_draft(self.draft["id"])
        log.info(f"[DRAFTS] draft {self.draft['id']} sters din Discord")
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content="Draft deleted.", embeds=[], view=self)


class _PhotoPick(discord.ui.Select):
    def __init__(self, card: DraftCard, photos: list):
        super().__init__(placeholder="Pick a photo and post it...", row=2,
                         options=[discord.SelectOption(label=f"Photo {i}", value=str(i - 1))
                                  for i in range(1, len(photos) + 1)])
        self.card = card

    async def callback(self, interaction: discord.Interaction):
        await self.card.deschide(interaction, self.card.photos[int(self.values[0])])


class DraftPicker(discord.ui.View):
    """Lista produselor aduse de .bulk; alegi unul si primesti cardul lui."""

    def __init__(self, drafturi: list, author_id: int):
        super().__init__(timeout=toolscmd.FORM_TIMEOUT)
        self.drafturi = {d["id"]: d for d in drafturi}
        self.author_id = author_id
        self.add_item(_DraftSelect(self, drafturi[:25]))


class _DraftSelect(discord.ui.Select):
    def __init__(self, picker: DraftPicker, drafturi: list):
        super().__init__(
            placeholder=f"Post one of the {len(drafturi)} products...",
            options=[discord.SelectOption(
                label=((d.get("name") or "untitled")[:95]),
                description=(f"${d['price']}" if d.get("price") else None),
                value=d["id"]) for d in drafturi])
        self.picker = picker

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.picker.author_id:
            return await interaction.response.send_message(
                "Run `.bulk` yourself to post these.", ephemeral=True)
        # Draftul poate fi editat intre timp din panou, deci il citim din nou.
        draft = poststore.get_draft(self.values[0]) or self.picker.drafturi[self.values[0]]
        parsed = linkgen.parse_link(draft["product_url"])
        if parsed is None:
            return await interaction.response.send_message(
                "That product link isn't Taobao / Weidian / 1688 anymore.",
                ephemeral=True)
        platform, item_id, raw = parsed
        links = linkgen.build_agent_links(platform, item_id, raw)
        stiut = linkstore.get(platform, item_id)
        photos = [p for p in ([draft.get("photo")] + (stiut.get("images") or []))
                  if p][:PREVIEW_COUNT]
        photos = list(dict.fromkeys(photos))

        embed = linkgen.build_embed(platform, item_id,
                                    draft.get("weight") or stiut.get("weight"))
        embed.set_footer(text=(f"{draft.get('name') or 'untitled'} - pick one of the "
                               f"{len(photos)} photos below")[:2048])
        embeds = [embed]
        if len(photos) > 1:
            # Embed-uri cu acelasi `url` sunt grupate de Discord intr-o galerie.
            embed.url = raw
            for src in photos:
                extra = discord.Embed(url=raw)
                extra.set_image(url=src)
                embeds.append(extra)
        elif photos:
            embed.set_image(url=photos[0])

        await interaction.response.send_message(
            embeds=embeds, view=DraftCard(draft, links, photos, parsed),
            ephemeral=True)


# ---------- .drafts: coada de drafturi, oricand ----------
# Cardurile de mai sus vin lipite de mesajul lui .bulk si expira odata cu el:
# dupa o zi, singurul loc unde mai ajungeai la drafturi era panoul web. Comanda
# asta le scoate din nou la lumina, cu acelasi selector.
DRAFTS_PER_PAGE = 25          # limita Discord pentru un meniu de selectie


def _cauta_drafturi(text: str) -> list:
    """Drafturile care se potrivesc cu textul cautat (nume, pret, categorie)."""
    drafturi = poststore.all_drafts()
    text = (text or "").strip().lower()
    if not text:
        return drafturi
    return [d for d in drafturi
            if text in " ".join(str(d.get(k) or "") for k in
                                ("name", "price", "forum_name", "product_url")).lower()]


def setup_drafts_command(client: commands.Bot):
    @client.command(name="drafts", aliases=["draft"])
    async def drafts(ctx: commands.Context, *, cauta: str = ""):
        """`.drafts [text]` - produsele aduse si nepostate inca."""
        gasite = _cauta_drafturi(cauta)
        if not gasite:
            return await ctx.send(
                f"Nothing matching `{cauta}` in the drafts."
                if cauta else
                "No drafts waiting. Use `.bulk <links>` or `.import <yupoo category>`.")

        aratate = gasite[:DRAFTS_PER_PAGE]
        embed = discord.Embed(
            title=f"{len(gasite)} drafts waiting",
            description="Pick one below: you get its photos, the agent links and "
                        "the form to post it.",
            color=discord.Color.from_rgb(0, 162, 232))
        if len(gasite) > len(aratate):
            embed.add_field(
                name="Too many to list",
                value=f"Showing the first {len(aratate)}. Narrow it down with "
                      f"`.drafts <name>`.", inline=False)
        fara_categorie = sum(1 for d in gasite if not d.get("forum_id"))
        if fara_categorie:
            embed.add_field(name="Without a category", value=str(fara_categorie),
                            inline=True)
        await ctx.send(embed=embed, view=DraftPicker(aratate, ctx.author.id))
        log.info(f"[DRAFTS] {ctx.author}: {len(gasite)} drafturi pentru '{cauta}'")

    return drafts
