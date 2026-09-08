"""
Evidenta linkurilor trecute prin canalul de tools.

Tinut acum in MariaDB (tabelele `links`, `link_images`, `link_agent_links`,
`link_posts`), nu in links.json. Fiecare link scanat isi lasa aici tot ce a
aflat botul despre el: pretul de pe Kakobuy, pozele produsului, greutatea,
titlul din magazin si linkurile de agenti. Daca acelasi link e aruncat din
nou, randul lui e completat, nu duplicat, si pastreaza de cand il stim.

Cheia e "platforma:id" (ex. "weidian:7565891040"), nu URL-ul, fiindca acelasi
produs vine si ca link brut, si prin agent cu affcode.

Toate functiile de mai jos pastreaza exact acelasi nume, parametri si forma
de rezultat ca in versiunea pe JSON, ca restul botului sa nu se schimbe.
"""
import logging
import threading
from datetime import datetime, timezone

import db

log = logging.getLogger("welcome-bot.links")

_lock = threading.Lock()          # panoul web si botul scriu din acelasi proces

LINK_COLUMNS = ["link_key", "platform", "item_id", "url", "price", "weight",
                 "store_title", "share", "seen_count", "first_seen", "last_seen"]


def key_for(platform: str, item_id: str) -> str:
    return f"{platform}:{item_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_row(cur, key: str) -> dict:
    """Reconstruieste dict-ul complet al unui link (cu images/agent_links/posts)
    din tabelele copil, la fel cum arata randul in vechiul links.json."""
    cur.execute(f"SELECT {', '.join(LINK_COLUMNS)} FROM links WHERE link_key = %s", (key,))
    row = cur.fetchone()
    if not row:
        return {}
    result = dict(row)
    result.pop("link_key", None)

    cur.execute(
        "SELECT url FROM link_images WHERE link_key = %s ORDER BY position ASC",
        (key,),
    )
    result["images"] = [r["url"] for r in cur.fetchall()]

    cur.execute(
        "SELECT agent, url FROM link_agent_links WHERE link_key = %s",
        (key,),
    )
    result["agent_links"] = {r["agent"]: r["url"] for r in cur.fetchall()}

    cur.execute(
        "SELECT thread_id, forum, title, guild_id, posted "
        "FROM link_posts WHERE link_key = %s ORDER BY id ASC",
        (key,),
    )
    result["posts"] = cur.fetchall()

    return result


def get(platform: str, item_id: str) -> dict:
    key = key_for(platform, item_id)
    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor(dictionary=True)
            row = _load_row(cur, key)
            cur.close()
        finally:
            conn.close()
    return row


def all_links() -> list:
    """Toate linkurile, cel mai recent vazut primul."""
    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("SELECT link_key FROM links")
            keys = [r["link_key"] for r in cur.fetchall()]
            links = [_load_row(cur, k) for k in keys]
            cur.close()
        finally:
            conn.close()
    links.sort(key=lambda r: r.get("last_seen", "") or "", reverse=True)
    return links


def record(platform: str, item_id: str, **fields) -> dict:
    """Salveaza ce stim despre un link si intoarce randul intreg.

    Campurile goale nu suprascriu ce era salvat: daca azi Kakobuy nu da pretul,
    ramane cel de data trecuta. `first_seen` se pune o singura data.
    """
    key = key_for(platform, item_id)
    now = _now()

    # images si agent_links sunt tinute separat (tabele copil), restul
    # campurilor merg direct pe randul din `links`.
    images = fields.pop("images", None)
    agent_links = fields.pop("agent_links", None)

    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor(dictionary=True)

            cur.execute(
                "SELECT first_seen, seen_count, price, weight, store_title, share, url "
                "FROM links WHERE link_key = %s",
                (key,),
            )
            existing = cur.fetchone() or {}

            row = dict(existing)
            row.setdefault("first_seen", now)
            row["first_seen"] = existing.get("first_seen") or now
            row["last_seen"] = now
            row["platform"] = platform
            row["item_id"] = item_id
            row["seen_count"] = (existing.get("seen_count") or 0) + 1

            for name, value in fields.items():
                if value or isinstance(value, (int, float)):
                    row[name] = value

            row["link_key"] = key
            cols = [c for c in LINK_COLUMNS if c in row]
            values = [row[c] for c in cols]
            placeholders = ", ".join(["%s"] * len(cols))
            update_clause = ", ".join(f"{c} = VALUES({c})" for c in cols if c != "link_key")
            cur.execute(
                f"INSERT INTO links ({', '.join(cols)}) VALUES ({placeholders}) "
                f"ON DUPLICATE KEY UPDATE {update_clause}",
                values,
            )

            if images:
                cur.execute("DELETE FROM link_images WHERE link_key = %s", (key,))
                cur.executemany(
                    "INSERT INTO link_images (link_key, position, url) VALUES (%s, %s, %s)",
                    [(key, i, url) for i, url in enumerate(images)],
                )

            if agent_links:
                for agent, url in agent_links.items():
                    cur.execute(
                        "INSERT INTO link_agent_links (link_key, agent, url) "
                        "VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE url = VALUES(url)",
                        (key, agent, url),
                    )

            full_row = _load_row(cur, key)
            cur.close()
        finally:
            conn.close()
    return full_row


def record_post(platform: str, item_id: str, thread_id, forum: str, title: str,
                guild_id=None):
    """Leaga postarea de forum de linkul din care a iesit."""
    key = key_for(platform, item_id)
    now = _now()

    with _lock:
        conn = db.get_conn()
        try:
            cur = conn.cursor(dictionary=True)

            # Asigura ca exista un rand parinte in `links` (ca in jsonstore,
            # unde setdefault crea randul daca nu exista deja).
            cur.execute("SELECT link_key FROM links WHERE link_key = %s", (key,))
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO links (link_key, platform, item_id, first_seen, last_seen, seen_count) "
                    "VALUES (%s, %s, %s, %s, %s, 0)",
                    (key, platform, item_id, now, now),
                )

            cur.execute(
                "SELECT id FROM link_posts WHERE link_key = %s AND thread_id = %s",
                (key, str(thread_id)),
            )
            existing = cur.fetchone()

            if existing:
                cur.execute(
                    "UPDATE link_posts SET forum = %s, title = %s, "
                    "guild_id = COALESCE(%s, guild_id) WHERE id = %s",
                    (forum, title, str(guild_id) if guild_id else None, existing["id"]),
                )
            else:
                cur.execute(
                    "INSERT INTO link_posts (link_key, thread_id, forum, title, guild_id, posted) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (key, str(thread_id), forum, title,
                     str(guild_id) if guild_id else None, now),
                )

            cur.execute("UPDATE links SET last_seen = %s WHERE link_key = %s", (now, key))
            cur.close()
        finally:
            conn.close()


def posted_before(platform: str, item_id: str) -> list:
    """Postarile facute deja din produsul asta. Gol daca e prima oara."""
    return get(platform, item_id).get("posts") or []


def thread_url(post: dict) -> str:
    """Linkul catre thread, daca stim serverul. Altfel doar numele forumului."""
    if post.get("guild_id") and post.get("thread_id"):
        return f"https://discord.com/channels/{post['guild_id']}/{post['thread_id']}"
    return ""
