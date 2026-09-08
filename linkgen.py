"""
Comanda .link — converteste un link Taobao/Weidian/1688 in linkuri de agenti
si arata agentul recomandat + greutatea (daca exista pe Doppel).
"""
import os
import re
import json
import time
import logging
from urllib.parse import quote, unquote, urlparse, parse_qs

from pathlib import Path

import discord
from discord.ext import commands

log = logging.getLogger("welcome-bot.link")

# ---------- Config (poti pune codurile in .env) ----------
AFF = {
    "kakobuy":  os.getenv("AFF_KAKOBUY", ""),
    "superbuy": os.getenv("AFF_SUPERBUY", ""),
    "oopbuy":   os.getenv("AFF_OOPBUY", ""),
    "cssbuy":   os.getenv("AFF_CSSBUY", ""),
    "joyagoo":  os.getenv("AFF_JOYAGOO", ""),
    "litbuy":   os.getenv("AFF_LITBUY", ""),
    "sugargoo": os.getenv("AFF_SUGARGOO", ""),
}
REPORT_CHANNEL_ID = int(os.getenv("REPORT_CHANNEL_ID", "1544054180748992593"))
BEST_AGENT = os.getenv("BEST_AGENT", "KakoBuy")
DOPPEL_REF = os.getenv("DOPPEL_REF", "wHQx8Gp_")

# Emoji custom pentru butoane. Se completeaza automat cu .setupemojis
# (uploadeaza pozele din folderul emojis/ ca emoji pe server).
EMOJI_DIR = Path(__file__).resolve().parent / "emojis"
EMOJI_FILE = Path(__file__).resolve().parent / "emojis.json"


def load_emojis() -> dict:
    try:
        return json.loads(EMOJI_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_emojis(data: dict):
    EMOJI_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


CUSTOM_EMOJIS = load_emojis()
EMBED_COLOR = discord.Color.from_rgb(0, 162, 232)

# ---------- Parsare link ----------
# platforma -> (cod joyagoo, cod oopbuy/litbuy)
PLATFORMS = {
    "taobao":  ("taobao", "TAOBAO", "1"),
    "weidian": ("weidian", "WEIDIAN", "weidian"),
    "1688":    ("1688", "ALI_1688", "0"),
}


def parse_link(url: str):
    """-> (platform, item_id, raw_url) sau None."""
    url = url.strip().strip("<>")
    if not url.startswith("http"):
        url = "https://" + url
    host = (urlparse(url).netloc or "").lower()
    qs = parse_qs(urlparse(url).query)

    def q(*keys):
        for k in keys:
            if k in qs and qs[k]:
                return qs[k][0]
        return None

    if "weidian.com" in host:
        item_id = q("itemID", "itemId", "itemid")
        platform = "weidian"
    elif "1688.com" in host:
        m = re.search(r"offer/(\d+)", url)
        item_id = m.group(1) if m else q("id")
        platform = "1688"
    elif "taobao.com" in host or "tmall.com" in host:
        item_id = q("id")
        platform = "taobao"
    else:
        return None

    if not item_id or not item_id.isdigit():
        return None

    # link curat, fara parametri de tracking
    if platform == "taobao":
        raw = f"https://item.taobao.com/item.htm?id={item_id}"
    elif platform == "weidian":
        raw = f"https://weidian.com/item.html?itemID={item_id}"
    else:
        raw = f"https://detail.1688.com/offer/{item_id}.html"
    return platform, item_id, raw


def build_agent_links(platform: str, item_id: str, raw: str) -> dict:
    enc = quote(raw, safe="")          # o data encodat
    enc2 = quote(enc, safe="")         # dublu encodat (oopbuy / sugargoo)
    joya_plat = PLATFORMS[platform][1]
    oop_plat = PLATFORMS[platform][2]
    ts = int(time.time() * 1000)

    links = {
        "KakoBuy":  f"https://item.kakobuy.com/item/details?url={enc}",
        "SuperBuy": ("https://www.superbuy.com/en/page/buy/?nTag=Home-search&from=search-input"
                     f"&url={enc}&trackPayload=pc_header_search_goods"),
        "OOPBuy":   f"https://oopbuy.com/product/{oop_plat}/{item_id}?originKeywordUrl={enc2}",
        "CSSBuy":   f"https://www.cssbuy.com/shop/goodsDetail?type={platform}&id={item_id}&t={ts}",
        "JoyaGoo":  f"https://joyagoo.com/product?id={item_id}&platform={joya_plat}&productUrl=&productPwd=",
        "LitBuy":   f"https://litbuy.com/product/{oop_plat}/{item_id}?linkSearch=true",
        "SugarGoo": f"https://www.sugargoo.com/products?productLink={enc2}",
    }
    # coduri de afiliere (se adauga doar daca sunt setate in .env)
    if AFF["kakobuy"]:
        links["KakoBuy"] += f"&affcode={AFF['kakobuy']}"
    if AFF["superbuy"]:
        links["SuperBuy"] += f"&partnercode={AFF['superbuy']}"
    if AFF["oopbuy"]:
        links["OOPBuy"] += f"&inviteCode={AFF['oopbuy']}"
    if AFF["cssbuy"]:
        links["CSSBuy"] += f"&promotionCode={AFF['cssbuy']}"
    if AFF["joyagoo"]:
        links["JoyaGoo"] += f"&ref={AFF['joyagoo']}"
    if AFF["litbuy"]:
        links["LitBuy"] += f"&ref={AFF['litbuy']}"
    if AFF["sugargoo"]:
        links["SugarGoo"] += f"&memberId={AFF['sugargoo']}"

    links["Raw"] = raw
    links["QC"] = doppel_url(platform, item_id)
    return links


def doppel_url(platform: str, item_id: str) -> str:
    url = f"https://doppel.fit/item/{platform}/{item_id}"
    return url + f"?ref={DOPPEL_REF}" if DOPPEL_REF else url


# ---------- Desfacerea linkurilor de agent ----------
# Un link de KakoBuy/OOPBuy/etc. ascunde inauntru linkul original de magazin.
# Il scoatem ca sa putem regenera toti agentii dintr-un link de agent.
WRAPPED_PARAMS = ("url", "productLink", "originKeywordUrl", "productUrl",
                  "keyword", "searchUrl", "goodsUrl")
AGENT_HOSTS = ("kakobuy.com", "superbuy.com", "oopbuy.com", "cssbuy.com",
               "joyagoo.com", "litbuy.com", "sugargoo.com", "hoobuy.com",
               "mulebuy.com", "allchinabuy.com", "basetao.com", "ponybuy.com")
# oopbuy/litbuy: /product/<platforma>/<id>
AGENT_PATH = re.compile(r"/product/([A-Za-z0-9_]+)/(\d+)")


def _store_from(platform_code: str, item_id: str):
    """Codul de platforma folosit de agenti -> link curat de magazin."""
    code = (platform_code or "").lower()
    if code in ("taobao", "1", "tb"):
        return f"https://item.taobao.com/item.htm?id={item_id}"
    if code in ("weidian", "wd"):
        return f"https://weidian.com/item.html?itemID={item_id}"
    if code in ("ali_1688", "1688", "0"):
        return f"https://detail.1688.com/offer/{item_id}.html"
    return None


def unwrap_agent_link(url: str):
    """Link de agent -> link de magazin. None daca nu recunoastem nimic."""
    host = (urlparse(url).netloc or "").lower()
    if not any(h in host for h in AGENT_HOSTS):
        return None

    qs = parse_qs(urlparse(url).query)

    # 1. Cazul obisnuit: linkul original sta intr-un parametru, encodat o data
    #    sau de doua ori.
    for key in WRAPPED_PARAMS:
        for variant in (key, key.lower()):
            if variant in qs and qs[variant]:
                inner = qs[variant][0]
                for _ in range(2):
                    if parse_link(inner) is not None:
                        return inner
                    inner = unquote(inner)
                if parse_link(inner) is not None:
                    return inner

    # 2. CSSBuy / JoyaGoo: platforma si id-ul stau in parametri separati.
    plat = (qs.get("type") or qs.get("platform") or [None])[0]
    item = (qs.get("id") or qs.get("goodsId") or [None])[0]
    if plat and item:
        return _store_from(plat, item)

    # 3. OOPBuy / LitBuy: platforma si id-ul sunt in calea URL-ului.
    m = AGENT_PATH.search(urlparse(url).path)
    if m:
        return _store_from(m.group(1), m.group(2))
    return None


def parse_any(url: str):
    """parse_link, dar accepta si linkuri de agent."""
    direct = parse_link(url)
    if direct is not None:
        return direct
    inner = unwrap_agent_link(url)
    return parse_link(inner) if inner else None


# Linkurile scurte de agent (ikako.vip/xxxxx de la Kakobuy, si echivalentele
# celorlalti) nu spun nimic despre produs: trebuie urmarita redirectarea.
# Nu tinem o lista de scurtatori - orice link pe care nu-l recunoastem primeste
# o incercare, si asa merge si cu agentii care apar maine.
SHORT_TIMEOUT = 12


def expand_link(url: str) -> str:
    """Linkul final, dupa redirectari. Intoarce originalul daca nu merge."""
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")})
        with urllib.request.urlopen(req, timeout=SHORT_TIMEOUT) as r:
            return r.geturl() or url
    except Exception as e:
        log.debug(f"Nu am putut desface linkul {url}: {e}")
        return url


def parse_any_deep(url: str):
    """Ca parse_any, dar desface intai linkurile scurte. Face cerere de retea,
    deci ruleaza pe thread separat, nu in bucla de evenimente."""
    gasit = parse_any(url)
    if gasit is not None:
        return gasit
    lung = expand_link(url)
    return parse_any(lung) if lung != url else None


# ---------- Greutate de pe Doppel ----------
async def fetch_weight(session, platform: str, item_id: str):
    """Intoarce mereu None. Pastrata ca sa nu se schimbe apelantii.

    Greutatea o are doar Doppel, de la oameni care au cumparat deja produsul.
    API-ul lor sta in spatele unui CAPTCHA Turnstile ("Confirm you're human")
    si raspunde criptat AES-GCM; nici macar un browser Playwright nu trece.
    Nu incercam sa ocolim asta.

    Vechea implementare batea doua endpointuri inexistente si pierdea ~9s la
    FIECARE postare, ca sa intoarca tot None. Greutatea se completeaza acum de
    mana, in formularul de postare sau in panou.
    """
    return None


def _dig(obj, key):
    """Cauta recursiv o cheie intr-un JSON."""
    if isinstance(obj, dict):
        if key in obj and obj[key] not in (None, "", 0):
            return obj[key]
        for v in obj.values():
            found = _dig(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _dig(v, key)
            if found is not None:
                return found
    return None


# ---------- UI ----------
BUTTONS = [
    # (label, emoji, row)
    ("KakoBuy", "🖤", 0), ("SuperBuy", "🖤", 0), ("OOPBuy", "🖤", 0),
    ("CSSBuy", "🖤", 0), ("JoyaGoo", "🖤", 0),
    ("LitBuy", "🖤", 1), ("SugarGoo", "🖤", 1),
    ("Raw", "🔗", 1), ("QC", "📸", 1),
]


class ReportModal(discord.ui.Modal, title="Report this link"):
    reason = discord.ui.TextInput(
        label="What's wrong?",
        style=discord.TextStyle.paragraph,
        placeholder="Wrong link, product removed, wrong weight...",
        max_length=500,
    )

    def __init__(self, raw_url: str):
        super().__init__()
        self.raw_url = raw_url

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.client.get_channel(REPORT_CHANNEL_ID) if REPORT_CHANNEL_ID else None
        if channel is None and REPORT_CHANNEL_ID:
            try:
                channel = await interaction.client.fetch_channel(REPORT_CHANNEL_ID)
            except Exception as e:
                log.error(f"Nu pot obtine canalul de reporturi {REPORT_CHANNEL_ID}: {e}")
        if channel is None:
            log.warning(f"Report fara canal: {self.raw_url} | {self.reason.value}")
        else:
            embed = discord.Embed(title="⚠️ Report link", color=discord.Color.orange(),
                                  description=self.reason.value)
            embed.add_field(name="Link", value=self.raw_url, inline=False)
            embed.set_footer(text=f"reported by {interaction.user} ({interaction.user.id})")
            await channel.send(embed=embed)
        await interaction.response.send_message("Thanks! Your report has been sent.", ephemeral=True)


class ReportButton(discord.ui.Button):
    def __init__(self, raw_url: str):
        super().__init__(label="Report", emoji="⚠️", style=discord.ButtonStyle.danger, row=1)
        self.raw_url = raw_url

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ReportModal(self.raw_url))


class LinkView(discord.ui.View):
    def __init__(self, links: dict):
        super().__init__(timeout=None)
        for label, emoji, row in BUTTONS:
            self.add_item(discord.ui.Button(
                label=label,
                emoji=CUSTOM_EMOJIS.get(label, emoji),
                url=links[label],
                row=row,
            ))
        self.add_item(ReportButton(links["Raw"]))


def build_embed(platform: str, item_id: str, weight):
    embed = discord.Embed(color=EMBED_COLOR)
    embed.add_field(name="Recommended Agent", value=f"**{BEST_AGENT}**", inline=False)
    embed.add_field(name="Status", value="🟢 Active", inline=True)
    embed.add_field(
        name="Weight",
        value=f"⚖️ {weight}g" if weight else "⚖️ Unknown",
        inline=True,
    )
    return embed


# ---------- Comanda ----------
async def delete_command_message(ctx: commands.Context):
    """Sterge mesajul cu comanda, ca sa ramana doar raspunsul botului."""
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        log.warning("Nu am permisiunea Manage Messages ca sa sterg comanda.")
    except discord.HTTPException as e:
        log.debug(f"Nu am putut sterge mesajul comenzii: {e}")



def setup_link_command(client: commands.Bot):
    @client.command(name="link")
    async def link_cmd(ctx: commands.Context, *, url: str = ""):
        await delete_command_message(ctx)

        parsed = parse_link(url) if url else None
        if parsed is None:
            await ctx.send("Usage: `.link <Taobao / Weidian / 1688 link>`", delete_after=15)
            return

        platform, item_id, raw = parsed
        links = build_agent_links(platform, item_id, raw)

        weight = None
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                weight = await fetch_weight(session, platform, item_id)
        except Exception as e:
            log.debug(f"Nu am putut lua greutatea: {e}")

        await ctx.send(embed=build_embed(platform, item_id, weight), view=LinkView(links))


    @client.command(name="setupemojis")
    @commands.has_permissions(manage_guild=True)
    async def setup_emojis(ctx: commands.Context):
        """Leaga emoji-urile de pe server la butoane (dupa nume) si urca din
        folderul emojis/ doar ce lipseste. Numele = numele agentului (kakobuy, qc...)."""
        valid = {label for label, _, _ in BUTTONS} | {"Report"}
        existing = {e.name.lower(): e for e in ctx.guild.emojis}
        linked, added, skipped, failed = [], [], [], []

        # 1. Emoji deja urcate pe server -> le legam direct.
        for label in sorted(valid):
            emoji = existing.get(label.lower())
            if emoji is not None:
                CUSTOM_EMOJIS[label] = str(emoji)
                linked.append(label)

        # 2. Ce a ramas nelegat -> incercam sa urcam din folderul emojis/.
        if EMOJI_DIR.exists():
            for path in sorted(EMOJI_DIR.iterdir()):
                if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                    continue
                label = next((v for v in valid if v.lower() == path.stem.lower()), None)
                if label is None:
                    skipped.append(f"{path.name} (unknown name)")
                    continue
                if label in CUSTOM_EMOJIS:
                    continue
                if path.stat().st_size > 256 * 1024:
                    failed.append(f"{path.name} (over 256 KB)")
                    continue
                try:
                    emoji = await ctx.guild.create_custom_emoji(
                        name=label.lower(), image=path.read_bytes(),
                        reason=f"setupemojis de la {ctx.author}")
                except discord.HTTPException as e:
                    failed.append(f"{path.name} ({e.text or e})")
                    continue
                CUSTOM_EMOJIS[label] = str(emoji)
                added.append(label)

        save_emojis(CUSTOM_EMOJIS)
        missing = sorted(valid - set(CUSTOM_EMOJIS))
        msg = [f"**Emojis linked:** {len(CUSTOM_EMOJIS)}/{len(valid)}"]
        if linked:
            msg.append("Found on server: " + ", ".join(linked))
        if added:
            msg.append("Uploaded: " + ", ".join(added))
        if missing:
            msg.append("Still missing (using default emoji): " + ", ".join(missing))
        if skipped:
            msg.append("Skipped: " + ", ".join(skipped))
        if failed:
            msg.append("Failed: " + ", ".join(failed))
        await ctx.send(chr(10).join(msg))
