import os
import sys
import asyncio
import logging
from pathlib import Path

import discord
from discord.ext import commands, tasks

# ---------- Config ----------
BASE_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

TOKEN = os.getenv("DISCORD_TOKEN")
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "1543777272400580608"))
LOGO_PATH = BASE_DIR / os.getenv("WELCOME_IMAGE", "logo-lung.png")
OWNER_ROLE_ID = int(os.getenv("OWNER_ROLE_ID", "1543695086037237841"))

# ---------- Single instance lock ----------
# Impiedica rularea a doua instante simultan (altfel welcome-ul se trimite de 2x).
import socket
_lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _lock_sock.bind(("127.0.0.1", 50517))
    _lock_sock.listen(1)
except OSError:
    print("[STOP] Botul ruleaza deja intr-un alt proces. Ies.")
    sys.exit(3)

if not TOKEN:
    print("[FATAL] Lipseste DISCORD_TOKEN. Pune-l in fisierul .env de langa bot.py")
    sys.exit(1)

# ---------- Logging (consola + fisier) ----------
# Consola Windows e pe cp1252 si crapa la emoji din numele canalelor -> fortam UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("welcome-bot")


def _log_crash(exc_type, exc, tb):
    """Fara asta, o eroare la pornire se pierde: procesul e lansat din GUI si
    stderr nu ajunge nicaieri, iar in log ramane ultima linie inselatoare."""
    log.critical("Botul s-a oprit cu o eroare netratata",
                 exc_info=(exc_type, exc, tb))


sys.excepthook = _log_crash

# ---------- Bot ----------
intents = discord.Intents.default()
intents.members = True          # OBLIGATORIU pentru on_member_join
intents.message_content = True

client = commands.Bot(command_prefix=commands.when_mentioned_or("!", "."), intents=intents)

# O functie stricata dintr-un modul nu trebuie sa doboare tot botul: welcome-ul
# si comenzile de baza merg si fara panoul web sau fara comanda de preturi.
# Ce pica aici e raportat in log si la .cmds, iar restul porneste normal.
FEATURES_ESUATE = {}


def _porneste(nume: str, setup, *args):
    try:
        setup(client, *args)
    except Exception as e:
        FEATURES_ESUATE[nume] = f"{type(e).__name__}: {e}"
        log.exception(f"[SETUP] '{nume}' nu a pornit; botul merge mai departe fara el")


from linkgen import setup_link_command
_porneste("linkuri", setup_link_command)

from imagecmd import setup_image_command, setup_forum_qc
_porneste("image", setup_image_command)
_porneste("qc pe forum", setup_forum_qc)

from addcmd import setup_add_command
_porneste("add", setup_add_command)

from scrapecmd import setup_scrape_command
_porneste("scrape", setup_scrape_command)

from toolscmd import setup_tools_channel
_porneste("canalul de tools", setup_tools_channel)

from pricecmd import setup_price_command
_porneste("preturi", setup_price_command)

from bulkcmd import setup_bulk_command
_porneste("bulk", setup_bulk_command)

from postqueue import setup_post_queue
_porneste("coada de postare", setup_post_queue)

from editcmd import setup_edit_command
_porneste("edit", setup_edit_command)

from editcmd import setup_fixbg_command, setup_posts_command
_porneste("fixbg", setup_fixbg_command)
_porneste("lista postarilor", setup_posts_command)

from bulkcmd import setup_drafts_command
_porneste("lista drafturilor", setup_drafts_command)

from webpanel import setup_panel
_porneste("panoul web", setup_panel)


# ---------- Access control: doar rolul de owner poate folosi comenzile ----------
@client.check
async def owner_role_only(ctx: commands.Context):
    if ctx.guild is None:
        raise commands.CheckFailure("Commands only work inside the server.")
    if await client.is_owner(ctx.author):
        return True
    if any(r.id == OWNER_ROLE_ID for r in getattr(ctx.author, "roles", [])):
        return True
    raise commands.CheckFailure("You don't have permission to use this command.")


@client.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, (commands.CheckFailure, commands.MissingPermissions)):
        # Fara rol -> botul tace complet.
        log.info(f"Comanda refuzata pentru {ctx.author} ({ctx.author.id}): {ctx.command}")
        return
    log.exception(f"Eroare la comanda {ctx.command}: {error}")
    await ctx.send("Something went wrong while running that command.", delete_after=10)


async def get_welcome_channel():
    """Ia canalul din cache; daca lipseste, il cere de la API."""
    channel = client.get_channel(WELCOME_CHANNEL_ID)
    if channel is None:
        try:
            channel = await client.fetch_channel(WELCOME_CHANNEL_ID)
        except Exception as e:
            log.error(f"Nu pot obtine canalul {WELCOME_CHANNEL_ID}: {e}")
            return None
    return channel


def build_embed(member):
    embed = discord.Embed(
        title="Welcome to ChinaSide Finds! 👋",
        description=(
            f"Hey, welcome to ChinaSide Finds, {member.mention}! 🥳\n\n"
            "**New to reps?** Open a ticket on the server!"
        ),
        color=discord.Color.from_rgb(0, 162, 232),
    )
    if LOGO_PATH.exists():
        embed.set_image(url="attachment://" + LOGO_PATH.name)
    return embed


async def send_welcome(member, attempt=1):
    channel = await get_welcome_channel()
    if channel is None:
        return

    try:
        embed = build_embed(member)
        files = [discord.File(LOGO_PATH, filename=LOGO_PATH.name)] if LOGO_PATH.exists() else []
        await channel.send(content=f"Hey {member.mention}!", embed=embed, files=files)
        log.info(f"[TRIMIS] Welcome pentru {member} ({member.id})")
    except discord.Forbidden:
        log.error("Botul nu are permisiuni (View Channel / Send Messages / Attach Files / Embed Links) pe canalul de welcome.")
    except (discord.HTTPException, OSError) as e:
        log.warning(f"Eroare la trimitere (incercarea {attempt}): {e}")
        if attempt < 3:
            await asyncio.sleep(5 * attempt)
            await send_welcome(member, attempt + 1)
        else:
            log.error(f"Am renuntat dupa 3 incercari pentru {member}.")


@client.event
async def on_ready():
    log.info(f"[ONLINE] Conectat ca {client.user} | {len(client.guilds)} servere")
    channel = await get_welcome_channel()
    if channel is None:
        log.error("ATENTIE: canalul de welcome nu a fost gasit. Verifica WELCOME_CHANNEL_ID.")
    else:
        log.info(f"Canal de welcome: #{channel} ({channel.id})")
    if not LOGO_PATH.exists():
        log.warning(f"Imaginea {LOGO_PATH} nu exista - trimit embed fara imagine.")
    if FEATURES_ESUATE:
        for nume, motiv in FEATURES_ESUATE.items():
            log.error(f"[SETUP] '{nume}' e oprit: {motiv}")
    client.add_view(HelpMenu())      # butoanele .cmds merg si dupa restart
    if not heartbeat.is_running():
        heartbeat.start()


@client.event
async def on_resumed():
    log.info("[RECONECTAT] Sesiunea a fost reluata.")


@client.event
async def on_disconnect():
    log.warning("[DECONECTAT] Se incearca reconectarea automata...")


@client.event
async def on_member_join(member):
    log.info(f"[DETECTAT] A intrat: {member} ({member.id})")
    await send_welcome(member)


@client.event
async def on_error(event, *args, **kwargs):
    log.exception(f"Eroare netratata in evenimentul {event}")


@tasks.loop(minutes=30)
async def heartbeat():
    log.info(f"[ALIVE] latenta {client.latency * 1000:.0f} ms | servere: {len(client.guilds)}")


# Comanda de test: !testwelcome [@user ...]
@client.command()
@commands.has_permissions(manage_guild=True)
async def testwelcome(ctx, members: commands.Greedy[discord.Member] = None):
    """Fara argument -> welcome pentru cel care a dat comanda.
    Cu unul sau mai multi membri mentionati -> welcome pentru fiecare."""
    targets = members or [ctx.author]
    for member in targets:
        await send_welcome(member)
    names = ", ".join(m.display_name for m in targets)
    await ctx.send(f"Test message sent for: {names}", delete_after=10)


# Comanda .commands - lista tuturor comenzilor. Textul e scris de mana, nu
# generat din docstring-uri: aici conteaza cum se folosesc, nu ce fac in cod.
COMMAND_HELP = [
    ("Posting products", "📦", [
        ("`.addpanel`",
         "Put a permanent panel with the **Add product** button in this channel. "
         "Anyone with the owner role can use it, no command needed. *(Manage Server)*"),
        ("`.add`",
         "Open the add-product form right away: product link, image link, name, "
         "price and the forum to post in."),
        ("`.edit <post link>`",
         "Edit a posted product from your phone: name, price, product link, "
         "weight, photo - plus move it to another category, redo the background "
         "or delete it."),
        ('`.add "<product link>" "<image link>" "<name>" "<price>"`',
         "Post without the form. Links work in any order, the image can be "
         "attached instead, and `#forum` picks the forum."),
    ]),
    ("Images", "🖼️", [
        ("`.image <link>`",
         "Background removed, white backdrop, CS Finds logo. Takes several links "
         "or attachments at once (up to 10), and Yupoo album links."),
        ("`.qc <product or agent link>`",
         "The QC photos buyers took, straight from the product page."),
        ("`.convert`",
         "Attach photos and get their links back - for filling the **Image link** "
         "field when you're on your phone."),
    ]),
    ("Yupoo", "🛍️", [
        ("`.brands <yupoo link>`",
         "List the brands from a store's sidebar, with a link for each one."),
        ("`.import <category link> [how many]`",
         "Pull products from a Yupoo category into the local panel, where you "
         "set the name, price and category before posting them."),
        ("`.doc [category link]`",
         "A copy-paste sheet for each product - name, link, price in USD and "
         "the picture link. No link means the imported drafts."),
        ("`.scrapp <category link> [page]`",
         "List every album in a Yupoo category - titles and links - as a text "
         "file you can pull links from for `.add` and `.image`."),
    ]),
    ("Agent links", "🔗", [
        ("The tools channel",
         "Just drop a Taobao / Weidian / 1688 link there - or an agent link like "
         "KakoBuy - and the bot answers with every agent button and the weight. "
         "Attach a photo and hit **Post to forum** to publish it; you only fill "
         "in the name."),
        ("`.link <Taobao / Weidian / 1688 link>`",
         "Buttons for every agent, plus the recommended agent and the weight."),
    ]),
    # Panoul web se leaga pe 127.0.0.1, deci de pe telefon (si de pe VPS) nu
    # exista. Grupul asta e panoul intreg, mutat in comenzi.
    ("The panel, in Discord", "📋", [
        ("`.posts [name]`",
         "Everything posted on the forums. Pick one from the menu and you get the "
         "same edit screen as `.edit`: name, price, link, weight, photo, category."),
        ("`.drafts [name]`",
         "Products fetched but not posted yet. Pick one to see its photos and the "
         "agent links, then post it or delete it."),
        ("`.bulk <links>`",
         "Any number of product links at once - or a `.txt` with them - straight "
         "into the drafts. Prices, photos and names come from the store."),
        ("`.short <links>`",
         "The short affiliate links (ikako.vip) for those products."),
        ("`.fixbg [#forum]`",
         "Go through the posts and redo the photos where the background was cut "
         "badly. `.fixbg check` only counts them, without changing anything; "
         "`.fixbg all` redoes every photo with the slow, accurate model (~20s "
         "each) - use it when the quick pass keeps missing bad cuts."),
    ]),
    ("Setup", "⚙️", [
        ("`.setupemojis`",
         "Link the server emojis to the agent buttons and upload the missing ones "
         "from the `emojis/` folder. *(Manage Server)*"),
        ("`.testwelcome [@user]`",
         "Send a test welcome message. *(Manage Server)*"),
    ]),
]


def section_embed(index: int) -> discord.Embed:
    name, emoji, entries = COMMAND_HELP[index]
    embed = discord.Embed(
        title=f"{emoji} {name}",
        description=chr(10).join(f"{cmd}\n{desc}\n" for cmd, desc in entries),
        color=discord.Color.from_rgb(0, 162, 232))
    embed.set_footer(text="Every command needs the owner role")
    return embed


class SectionButton(discord.ui.Button):
    def __init__(self, index: int):
        name, emoji, entries = COMMAND_HELP[index]
        super().__init__(label=name, emoji=emoji, custom_id=f"cmds:{index}",
                         style=discord.ButtonStyle.secondary,
                         row=index // 3)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        # Ephemeral: fiecare isi vede propriul meniu, canalul nu se umple.
        await interaction.response.send_message(
            embed=section_embed(self.index), ephemeral=True)


class HelpMenu(discord.ui.View):
    """Persistent: un mesaj cu .cmds poate fi pinuit si merge dupa restart."""

    def __init__(self):
        super().__init__(timeout=None)
        for i in range(len(COMMAND_HELP)):
            self.add_item(SectionButton(i))


# discord.py inregistreaza singur o comanda "help"; o scoatem, altfel alias-ul
# de mai jos arunca CommandRegistrationError si botul nici nu porneste.
client.remove_command("help")


@client.command(name="commands", aliases=["cmds", "help"])
async def list_commands(ctx: commands.Context):
    """Meniul de ajutor: cate un buton per categorie."""
    total = sum(len(entries) for _, _, entries in COMMAND_HELP)
    embed = discord.Embed(
        title="What I can do",
        description=(f"{total} things, grouped below. Tap a button to see that "
                     f"group - only you will see the answer."),
        color=discord.Color.from_rgb(0, 162, 232))
    embed.add_field(
        name="Fastest way to post something",
        value=("Drop a Taobao / Weidian / KakoBuy link in the tools channel - "
               "attach a photo too and you only have to type the name."),
        inline=False)
    await ctx.send(embed=embed, view=HelpMenu())


async def main():
    """O singura sesiune; discord.py se reconecteaza singur.
    Daca totusi pica de tot, iesim cu cod 1 si start-bot.bat reporneste procesul
    (un client discord.py inchis nu poate fi refolosit)."""
    try:
        async with client:
            await client.start(TOKEN, reconnect=True)
    except discord.LoginFailure:
        log.critical("Token invalid. Opresc.")
        sys.exit(2)
    except Exception as e:
        log.exception(f"Botul s-a oprit neasteptat: {e}")
        sys.exit(1)
    log.warning("Clientul s-a inchis.")
    sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Oprit manual.")
