"""
Comanda .preturi - reciteste preturile de pe Kakobuy pentru posturile facute.

Preturile se invechesc: vanzatorul le schimba, iar titlul de pe forum ramane cu
cel de acum trei saptamani. Comanda compara ce scrie in titlu cu ce zice acum
pagina si iti arata diferentele.

Nu schimba nimic din prima: `.preturi` e doar raport. Abia `.preturi aplica`
redenumeste threadurile, fiindca vorbim de postari deja publicate, vazute de
oameni. Fiecare produs cere un randat de pagina (~2s), deci o trecere peste tot
forumul dureaza un minut-doua si merge in fundal.
"""
import asyncio
import logging
from functools import partial

import discord
from discord.ext import commands

import imagecmd
import linkstore
import poststore

log = logging.getLogger("welcome-bot.preturi")

MAX_PRODUSE = 60          # plasa de siguranta pentru o comanda data din greseala
PAUZA = 1.0               # secunde intre produse, ca sa nu batem Kakobuy


def _posturi_de_verificat() -> list:
    """Cate un post pe produs, cel mai recent, doar cele cu link utilizabil."""
    per_produs = {}
    for p in poststore.all_posts():
        if p.get("platform") and p.get("item_id") and p.get("product_url"):
            per_produs.setdefault((p["platform"], p["item_id"]), p)
    return list(per_produs.values())


def setup_price_command(client: commands.Bot):
    @client.command(name="preturi")
    @commands.has_permissions(manage_guild=True)
    async def preturi(ctx, actiune: str = ""):
        """.preturi = raport | .preturi aplica = redenumeste threadurile."""
        aplica = actiune.lower() in ("aplica", "apply")
        posturi = _posturi_de_verificat()[:MAX_PRODUSE]

        await ctx.send(f"Verific {len(posturi)} produse pe Kakobuy"
                       f"{' si aplic schimbarile' if aplica else ' (doar raport)'}..."
                       f" Dureaza vreo {len(posturi) * 2 // 60 + 1} minute.")

        loop = asyncio.get_running_loop()
        schimbate, disparute, la_fel = [], [], 0

        for post in posturi:
            item = await loop.run_in_executor(
                None, partial(imagecmd.kakobuy_item, post["product_url"]))
            await asyncio.sleep(PAUZA)

            nou = item["price"]
            vechi = (post.get("price") or "").strip()
            if not nou:
                disparute.append(post)
                continue
            if nou == vechi:
                la_fel += 1
                continue

            schimbate.append((post, vechi, nou))
            linkstore.record(post["platform"], post["item_id"], price=nou,
                             url=post["product_url"], images=item["images"],
                             store_title=item["title"])
            if aplica:
                await _redenumeste(client, post, nou)

        await ctx.send(embed=_raport(schimbate, disparute, la_fel, aplica))

    return preturi


async def _redenumeste(client, post: dict, pret_nou: str):
    """Titlul threadului si evidenta, la pretul nou. Restul postului nu se atinge."""
    titlu = poststore.build_title(post.get("name") or post.get("title"), pret_nou)
    try:
        thread = client.get_channel(post["thread_id"]) or \
            await client.fetch_channel(post["thread_id"])
        await thread.edit(name=titlu[:100])
    except Exception as e:
        log.warning(f"Nu am putut redenumi {post['thread_id']}: {e}")
        return
    post.update(title=titlu, price=pret_nou)
    poststore.save(post)
    log.info(f"[PRETURI] {post['thread_id']} -> {titlu}")


def _raport(schimbate: list, disparute: list, la_fel: int, aplica: bool):
    embed = discord.Embed(
        title="Price check" + (" - applied" if aplica else " - report only"),
        colour=discord.Colour.orange() if schimbate else discord.Colour.green())
    embed.add_field(name="Unchanged", value=str(la_fel), inline=True)
    embed.add_field(name="Changed", value=str(len(schimbate)), inline=True)
    embed.add_field(name="No price found", value=str(len(disparute)), inline=True)

    if schimbate:
        linii = [f"**{p.get('name') or p.get('title')}**: "
                 f"${vechi or '?'} -> ${nou}" for p, vechi, nou in schimbate[:15]]
        if len(schimbate) > 15:
            linii.append(f"...si inca {len(schimbate) - 15}")
        embed.add_field(name="Differences", value=chr(10).join(linii), inline=False)

    if not aplica and schimbate:
        embed.set_footer(text="Run .preturi aplica to rename the threads.")
    return embed
