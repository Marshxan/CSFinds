"""
Evidenta posturilor create cu .add, ca sa poata fi editate mai tarziu din panou.

Un simplu JSON langa bot: cate zeci-sute de posturi, nu merita o baza de date.
Cheia e id-ul threadului, care e si id-ul mesajului de deschidere pe forum.
"""
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import jsonstore

log = logging.getLogger("welcome-bot.store")

STORE_PATH = Path(__file__).resolve().parent / "posts.json"
_lock = threading.Lock()          # panoul web si botul scriu din acelasi proces


def _read() -> dict:
    """Ridica StoreUnreadable daca fisierul e acolo dar nu se poate citi."""
    return jsonstore.read_json(STORE_PATH)


def _write(data: dict):
    jsonstore.write_json(STORE_PATH, data)


def all_posts() -> list:
    """Toate posturile, cel mai nou primul."""
    with _lock:
        data = _read()
    posts = list(data.values())
    for p in posts:
        _fill_name_price(p)
    posts.sort(key=lambda p: p.get("created", ""), reverse=True)
    return posts


def _fill_name_price(post: dict):
    """Completeaza in memorie numele si pretul pentru posturile vechi."""
    if post and post.get("title") and not post.get("name"):
        post["name"], post["price"] = split_title(post["title"])


def get(thread_id) -> dict:
    with _lock:
        post = _read().get(str(thread_id))
    _fill_name_price(post)
    return post


def split_title(title: str) -> tuple:
    """'Windrunner Jacket ~ $30' -> ('Windrunner Jacket', '30').

    Titlurile vechi n-au numele si pretul separate; le scoatem de aici ca sa
    poata fi editate ca doua campuri in panou.
    """
    name, sep, price = (title or "").rpartition(" ~ $")
    if not sep:
        return (title or "").strip(), ""
    return name.strip(), price.strip()


def build_title(name: str, price: str) -> str:
    """Inversul lui split_title. Fara pret, titlul e doar numele."""
    name = (name or "").strip()
    price = (price or "").strip().lstrip("$")
    return f"{name} ~ ${price}" if price else name


def save(record: dict):
    with _lock:
        data = _read()
        key = str(record["thread_id"])
        record.setdefault("created", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        # Posturile facute inainte de campurile separate au doar titlul: le
        # completam la prima salvare, ca panoul sa aiba ce edita.
        if record.get("title") and not record.get("name"):
            name, price = split_title(record["title"])
            record.setdefault("name", name)
            record.setdefault("price", price)
        data[key] = {**data.get(key, {}), **record}
        _write(data)


def remove(thread_id):
    with _lock:
        data = _read()
        if data.pop(str(thread_id), None) is not None:
            _write(data)


# ---------- Drafturi: produse trase de pe Yupoo, inca nepostate ----------
# Stau separat de posturi: un draft n-are thread, iar utilizatorul ii alege
# numele, pretul si categoria din panou inainte sa ajunga pe Discord.
DRAFTS_PATH = STORE_PATH.with_name("drafts.json")


def _read_drafts() -> dict:
    return jsonstore.read_json(DRAFTS_PATH)


def _write_drafts(data: dict):
    jsonstore.write_json(DRAFTS_PATH, data)


def all_drafts() -> list:
    with _lock:
        data = _read_drafts()
    drafts = list(data.values())
    drafts.sort(key=lambda d: d.get("created", ""), reverse=True)
    return drafts


def get_draft(draft_id) -> dict:
    with _lock:
        return _read_drafts().get(str(draft_id))


def save_draft(record: dict) -> bool:
    """-> True daca e nou. Cheia e id-ul albumului, deci un reimport nu duplica."""
    with _lock:
        data = _read_drafts()
        key = str(record["id"])
        is_new = key not in data
        record.setdefault("created", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        data[key] = {**data.get(key, {}), **record}
        _write_drafts(data)
        return is_new


def remove_draft(draft_id):
    with _lock:
        data = _read_drafts()
        if data.pop(str(draft_id), None) is not None:
            _write_drafts(data)
