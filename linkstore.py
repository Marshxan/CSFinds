"""
Evidenta linkurilor trecute prin canalul de tools.

Fiecare link scanat isi lasa aici tot ce a aflat botul despre el: pretul de pe
Kakobuy, pozele produsului, greutatea, titlul din magazin si linkurile de
agenti. Daca acelasi link e aruncat din nou, randul lui e completat, nu
duplicat, si pastreaza de cand il stim.

Un simplu JSON langa bot, ca posts.json: sunt sute de linkuri, nu merita o baza
de date. Cheia e "platforma:id" (ex. "weidian:7565891040"), nu URL-ul, fiindca
acelasi produs vine si ca link brut, si prin agent cu affcode.
"""
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import jsonstore

log = logging.getLogger("welcome-bot.links")

STORE_PATH = Path(__file__).resolve().parent / "links.json"
_lock = threading.Lock()          # panoul web si botul scriu din acelasi proces


def _read() -> dict:
    """Ridica StoreUnreadable daca fisierul e acolo dar nu se poate citi."""
    return jsonstore.read_json(STORE_PATH)


def _write(data: dict):
    jsonstore.write_json(STORE_PATH, data)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def key_for(platform: str, item_id: str) -> str:
    return f"{platform}:{item_id}"


def get(platform: str, item_id: str) -> dict:
    with _lock:
        return _read().get(key_for(platform, item_id), {})


def all_links() -> list:
    """Toate linkurile, cel mai recent vazut primul."""
    with _lock:
        data = _read()
    links = list(data.values())
    links.sort(key=lambda r: r.get("last_seen", ""), reverse=True)
    return links


def record(platform: str, item_id: str, **fields) -> dict:
    """Salveaza ce stim despre un link si intoarce randul intreg.

    Campurile goale nu suprascriu ce era salvat: daca azi Kakobuy nu da pretul,
    ramane cel de data trecuta. `first_seen` se pune o singura data.
    """
    key = key_for(platform, item_id)
    with _lock:
        data = _read()
        row = data.get(key, {})
        row.setdefault("first_seen", _now())
        row.update(platform=platform, item_id=item_id, last_seen=_now())
        for name, value in fields.items():
            if value or isinstance(value, (int, float)):
                row[name] = value
        row["seen_count"] = row.get("seen_count", 0) + 1
        data[key] = row
        _write(data)
    return row


def record_post(platform: str, item_id: str, thread_id, forum: str, title: str,
                guild_id=None):
    """Leaga postarea de forum de linkul din care a iesit."""
    key = key_for(platform, item_id)
    with _lock:
        data = _read()
        row = data.setdefault(key, {"first_seen": _now(), "platform": platform,
                                    "item_id": item_id})
        posts = row.setdefault("posts", [])
        # Acelasi thread poate fi editat mai tarziu: actualizam randul, nu adaugam.
        for p in posts:
            if str(p.get("thread_id")) == str(thread_id):
                p.update(forum=forum, title=title, posted=p.get("posted") or _now())
                if guild_id:
                    p["guild_id"] = str(guild_id)
                break
        else:
            posts.append({"thread_id": str(thread_id), "forum": forum, "title": title,
                          "guild_id": str(guild_id) if guild_id else None,
                          "posted": _now()})
        row["last_seen"] = _now()
        data[key] = row
        _write(data)


def posted_before(platform: str, item_id: str) -> list:
    """Postarile facute deja din produsul asta. Gol daca e prima oara."""
    return get(platform, item_id).get("posts") or []


def thread_url(post: dict) -> str:
    """Linkul catre thread, daca stim serverul. Altfel doar numele forumului."""
    if post.get("guild_id") and post.get("thread_id"):
        return f"https://discord.com/channels/{post['guild_id']}/{post['thread_id']}"
    return ""
