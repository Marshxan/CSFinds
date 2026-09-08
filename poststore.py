"""
Evidenta posturilor create cu .add, ca sa poata fi editate mai tarziu din panou.

Tinut acum in MariaDB (tabelele `posts` si `drafts`), nu in JSON. Toate
functiile de mai jos pastreaza exact acelasi nume, aceiasi parametri si
aceeasi forma de rezultat ca inainte, ca restul botului (addcmd, editcmd,
webpanel, bulkcmd etc.) sa nu aiba nevoie de nicio modificare.
"""
import logging
import threading
from datetime import datetime, timezone

import db

log = logging.getLogger("welcome-bot.store")

_lock = threading.Lock()          # panoul web si botul scriu din acelasi proces

POST_COLUMNS = ["thread_id", "forum_id", "guild_id", "links_message_id",
                 "title", "name", "price", "product_url", "image_url",
                 "platform", "item_id", "weight", "created"]

DRAFT_COLUMNS = ["id", "name", "price", "weight", "product_url", "photo",
                  "forum_id", "forum_name", "created"]


def _row_to_post(row: dict) -> dict:
    """Normalizeaza randul din DB la forma pe care o astepta restul botului
    (thread_id/forum_id/guild_id/links_message_id ca int, ca inainte)."""
    if row is None:
        return None
    post = dict(row)
    for key in ("thread_id", "forum_id", "guild_id", "links_message_id"):
        if post.get(key) is not None:
            post[key] = int(post[key])
    return post


def _fill_name_price(post: dict):
    """Completeaza in memorie numele si pretul pentru posturile vechi."""
    if post and post.get("title") and not post.get("name"):
        post["name"], post["price"] = split_title(post["title"])


def all_posts() -> list:
    """Toate posturile, cel mai nou primul."""
    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(f"SELECT {', '.join(POST_COLUMNS)} FROM posts")
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()
    posts = [_row_to_post(r) for r in rows]
    for p in posts:
        _fill_name_price(p)
    posts.sort(key=lambda p: p.get("created", "") or "", reverse=True)
    return posts


def get(thread_id) -> dict:
    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                f"SELECT {', '.join(POST_COLUMNS)} FROM posts WHERE thread_id = %s",
                (int(thread_id),),
            )
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
    post = _row_to_post(row)
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
    record = dict(record)
    record.setdefault("created", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    # Posturile facute inainte de campurile separate au doar titlul: le
    # completam la prima salvare, ca panoul sa aiba ce edita.
    if record.get("title") and not record.get("name"):
        name, price = split_title(record["title"])
        record.setdefault("name", name)
        record.setdefault("price", price)

    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT created FROM posts WHERE thread_id = %s",
                (int(record["thread_id"]),),
            )
            existing = cur.fetchone()
            if existing and existing.get("created"):
                record["created"] = existing["created"]

            cols = [c for c in POST_COLUMNS if c in record]
            values = [record[c] for c in cols]
            placeholders = ", ".join(["%s"] * len(cols))
            update_clause = ", ".join(f"{c} = VALUES({c})" for c in cols if c != "thread_id")
            sql = (
                f"INSERT INTO posts ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON DUPLICATE KEY UPDATE {update_clause}"
            )
            cur.execute(sql, values)
            cur.close()
        finally:
            conn.close()


def remove(thread_id):
    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM posts WHERE thread_id = %s", (int(thread_id),))
            cur.close()
        finally:
            conn.close()


# ---------- Drafturi: produse trase de pe Yupoo, inca nepostate ----------

def _row_to_draft(row: dict) -> dict:
    if row is None:
        return None
    draft = dict(row)
    if draft.get("forum_id") is not None:
        draft["forum_id"] = int(draft["forum_id"])
    return draft


def all_drafts() -> list:
    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(f"SELECT {', '.join(DRAFT_COLUMNS)} FROM drafts")
            rows = cur.fetchall()
            cur.close()
        finally:
            conn.close()
    drafts = [_row_to_draft(r) for r in rows]
    drafts.sort(key=lambda d: d.get("created", "") or "", reverse=True)
    return drafts


def get_draft(draft_id) -> dict:
    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                f"SELECT {', '.join(DRAFT_COLUMNS)} FROM drafts WHERE id = %s",
                (str(draft_id),),
            )
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
    return _row_to_draft(row)


def save_draft(record: dict) -> bool:
    """-> True daca e nou. Cheia e id-ul albumului, deci un reimport nu duplica."""
    record = dict(record)
    key = str(record["id"])
    record["id"] = key
    record.setdefault("created", datetime.now(timezone.utc).isoformat(timespec="seconds"))

    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT created FROM drafts WHERE id = %s", (key,))
            existing = cur.fetchone()
            is_new = existing is None
            if existing and existing.get("created"):
                record["created"] = existing["created"]

            cols = [c for c in DRAFT_COLUMNS if c in record]
            values = [record[c] for c in cols]
            placeholders = ", ".join(["%s"] * len(cols))
            update_clause = ", ".join(f"{c} = VALUES({c})" for c in cols if c != "id")
            sql = (
                f"INSERT INTO drafts ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON DUPLICATE KEY UPDATE {update_clause}"
            )
            cur.execute(sql, values)
            cur.close()
        finally:
            conn.close()
    return is_new


def remove_draft(draft_id):
    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM drafts WHERE id = %s", (str(draft_id),))
            cur.close()
        finally:
            conn.close()
