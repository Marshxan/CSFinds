"""
Comanda .postdrafts - publica drafturile pe rand, la interval fix.

Un import mare lasa zeci-sute de drafturi in panou. Publicarea lor una cate una
din panou dureaza o zi, iar postarea lor toate odata ar bloca botul in rate
limit si ar inunda forumul in cateva secunde. Asa ca le trimitem la interval:

    .postdrafts            arata ce s-ar posta, fara sa posteze
    .postdrafts 20         posteaza urmatoarele 20, cate una la 15 secunde
    .postdrafts 20 #forum  toate in forumul asta, in loc de cel ghicit
    .stopposting           opreste seria in curs

Categoria: cea salvata pe draft, altfel ghicita din nume ca la .import.
Drafturile fara categorie si fara potrivire sunt sarite, nu aruncate aiurea
intr-un forum.

Publicarea sterge draftul, la fel ca butonul din panou: ce e postat nu mai e
draft. Daca botul e oprit la mijloc, ce a apucat sa posteze ramane postat, iar
restul asteapta in panou.
"""
import asyncio
import logging
import re

import discord
from discord.ext import commands

import addcmd
import linkstore
import poststore
import scrapecmd

log = logging.getLogger("welcome-bot.queue")

INTERVAL = 15             # secunde intre postari
MAX_PE_COMANDA = 200      # plasa de siguranta

_ruleaza = {"activ": False, "opreste": False}


def _categorie(draft: dict, forums: list, fortat):
    """Forumul in care merge draftul, sau None daca nu stim."""
    if fortat:
        return fortat
    if draft.get("forum_id"):
        gasit = next((f for f in forums if str(f.id) == str(draft["forum_id"])), None)
        if gasit:
            return gasit
    return scrapecmd.route_forum(draft.get("name") or "", forums)


def setup_post_queue(client: commands.Bot):
    @client.command(name="postdrafts")
    @commands.has_permissions(manage_guild=True)
    async def postdrafts(ctx: commands.Context, *, rest: str = ""):
        # Numarul si canalul pot veni in orice ordine: ".postdrafts 20 #forum",
        # ".postdrafts #forum 20", sau doar unul din ele. Un argument tipizat ar
        # fi refuzat ".postdrafts #forum" cu o eroare de conversie.
        curat = re.sub(r"(?<![0-9])[0-9]{17,20}(?![0-9])", " ",
                       addcmd.CHANNEL_RE.sub(" ", rest))
        numere = re.findall(r"[0-9]{1,4}", curat)
        cate = int(numere[0]) if numere else 0

        if _ruleaza["activ"]:
            await ctx.send("A batch is already running. Use `.stopposting` first.")
            return

        forums = addcmd.forums_in_category(ctx.guild)
        if not forums:
            await ctx.send("No forums found in the finds category.")
            return

        # Acceptam si "#forum" (mentiune), si id-ul brut lipit din link, si
        # linkul intreg de Discord - toate trei ajung la acelasi canal.
        # Acceptam "#forum" (mentiune), id-ul brut, sau linkul intreg de Discord.
        # Din link luam ULTIMUL id: primul e al serverului, al doilea al canalului.
        fortat = None
        m = addcmd.CHANNEL_RE.search(rest)
        ids = [m.group(1)] if m else re.findall(r"(?<![0-9])([0-9]{17,20})", rest)
        if ids:
            ales = ctx.guild.get_channel(int(ids[-1]))
            if isinstance(ales, discord.ForumChannel):
                fortat = ales
            elif ales is None:
                await ctx.send("I can't see that channel on this server.")
                return
            else:
                await ctx.send(f"**{ales}** isn't a forum channel.")
                return

        drafturi = poststore.all_drafts()
        gata = [(d, _categorie(d, forums, fortat)) for d in drafturi]
        postabile = [(d, f) for d, f in gata if f is not None]
        fara_categorie = [d for d, f in gata if f is None]

        if not cate:
            pe_forum = {}
            for _, f in postabile:
                pe_forum[f.name] = pe_forum.get(f.name, 0) + 1
            embed = discord.Embed(
                title="Nothing posted yet - this is a preview",
                description=(f"{len(postabile)} drafts ready, "
                             f"one every {INTERVAL}s "
                             f"(~{len(postabile) * INTERVAL // 60 + 1} min for all)."),
                colour=discord.Colour.blurple())
            if pe_forum:
                embed.add_field(
                    name="Where they'd go",
                    value=chr(10).join(f"#{k}: {v}" for k, v in
                                       sorted(pe_forum.items(), key=lambda x: -x[1])),
                    inline=False)
            if fara_categorie:
                embed.add_field(name=f"No category guessed ({len(fara_categorie)})",
                                value="Set one in the panel, or pass a #forum.",
                                inline=False)
            embed.set_footer(text="Run .postdrafts <how many> to start.")
            await ctx.send(embed=embed)
            return

        lot = postabile[:min(cate, MAX_PE_COMANDA)]
        _ruleaza.update(activ=True, opreste=False)
        await ctx.send(f"Posting {len(lot)} drafts, one every {INTERVAL}s. "
                       f"`.stopposting` to stop.")

        postate, esuate = 0, []
        try:
            for i, (draft, forum) in enumerate(lot):
                if _ruleaza["opreste"]:
                    break
                if i:
                    await asyncio.sleep(INTERVAL)
                try:
                    await _publica(ctx, draft, forum)
                    postate += 1
                except Exception as e:
                    log.warning(f"Draftul {draft.get('id')} nu s-a postat: {e}")
                    esuate.append(f"{(draft.get('name') or '')[:30]}: {e}")
        finally:
            _ruleaza["activ"] = False

        embed = discord.Embed(
            title=f"Posted {postate} of {len(lot)}",
            colour=discord.Colour.green() if not esuate else discord.Colour.orange())
        if _ruleaza["opreste"]:
            embed.description = "Stopped early. The rest are still in the panel."
        if esuate:
            embed.add_field(name=f"Failed {len(esuate)}",
                            value=chr(10).join(f"- {x}" for x in esuate[:6])[:1000],
                            inline=False)
        await ctx.send(embed=embed)
        log.info(f"[QUEUE] {ctx.author}: {postate} postate, {len(esuate)} esuate")

    @client.command(name="stopposting")
    @commands.has_permissions(manage_guild=True)
    async def stopposting(ctx: commands.Context):
        if not _ruleaza["activ"]:
            await ctx.send("Nothing is running.")
            return
        _ruleaza["opreste"] = True
        await ctx.send("Stopping after the current post.")

    return postdrafts


async def _publica(ctx, draft: dict, forum: discord.ForumChannel):
    """Acelasi drum ca butonul de publicare din panou."""
    titlu = addcmd.build_title([draft["name"], draft["price"]] if draft.get("price")
                               else [draft["name"]])
    payload = await addcmd.prepare_post(draft["product_url"], draft["photo"])
    if str(draft.get("weight") or "").isdigit():
        payload = payload[:4] + (int(draft["weight"]),)
    thread = await addcmd.create_post(forum, titlu, *payload,
                                      image_url=draft["photo"])
    poststore.remove_draft(draft["id"])

    parsed = None
    try:
        import linkgen
        parsed = linkgen.parse_any(draft["product_url"])
    except Exception:
        pass
    if parsed:
        linkstore.record_post(parsed[0], parsed[1], thread.id, forum.name, titlu,
                              guild_id=ctx.guild.id)
    log.info(f"[QUEUE] postat '{titlu}' in #{forum}")
