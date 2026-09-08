"""
Migrare unica: importa posts.json, drafts.json si links.json in MariaDB.

Ruleaza o singura data, dupa ce ai creat baza de date si tabelele (vezi
schema.sql) si ai completat DB_HOST/DB_USER/DB_PASSWORD in .env.

    python migrate_json_to_db.py

E sigur de rulat de mai multe ori: foloseste INSERT ... ON DUPLICATE KEY
UPDATE, deci nu duplica randuri daca il rulezi din nou din greseala.
"""
import json
import sys
from pathlib import Path

# Scriptul e in scripts/, dar db.py si restul modulelor sunt in radacina
# proiectului - il adaugam in sys.path ca sa poata fi importat de aici.
PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

import db

DATA_DIR = PROJECT_DIR / "data"


def load(name):
    path = DATA_DIR / name
    if not path.exists():
        print(f"[!] {name} nu exista, sar peste.")
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def migrate_posts(cur, posts: dict):
    cols = ["thread_id", "forum_id", "guild_id", "links_message_id", "title",
            "name", "price", "product_url", "image_url", "platform",
            "item_id", "weight", "created"]
    n = 0
    for row in posts.values():
        values = [row.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        update_clause = ", ".join(f"{c} = VALUES({c})" for c in cols if c != "thread_id")
        cur.execute(
            f"INSERT INTO posts ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {update_clause}",
            values,
        )
        n += 1
    print(f"[OK] {n} posturi migrate.")


def migrate_drafts(cur, drafts: dict):
    cols = ["id", "name", "price", "weight", "product_url", "photo",
            "forum_id", "forum_name", "created"]
    n = 0
    for row in drafts.values():
        values = [row.get(c) for c in cols]
        placeholders = ", ".join(["%s"] * len(cols))
        update_clause = ", ".join(f"{c} = VALUES({c})" for c in cols if c != "id")
        cur.execute(
            f"INSERT INTO drafts ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {update_clause}",
            values,
        )
        n += 1
    print(f"[OK] {n} drafturi migrate.")


def migrate_links(cur, links: dict):
    cols = ["link_key", "platform", "item_id", "url", "price", "weight",
            "store_title", "share", "seen_count", "first_seen", "last_seen"]
    n_links = n_images = n_agents = n_posts = 0

    for key, row in links.items():
        values = [
            key, row.get("platform"), row.get("item_id"), row.get("url"),
            row.get("price"), row.get("weight"), row.get("store_title"),
            row.get("share"), row.get("seen_count", 0),
            row.get("first_seen"), row.get("last_seen"),
        ]
        placeholders = ", ".join(["%s"] * len(cols))
        update_clause = ", ".join(f"{c} = VALUES({c})" for c in cols if c != "link_key")
        cur.execute(
            f"INSERT INTO links ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON DUPLICATE KEY UPDATE {update_clause}",
            values,
        )
        n_links += 1

        images = row.get("images") or []
        if images:
            cur.execute("DELETE FROM link_images WHERE link_key = %s", (key,))
            cur.executemany(
                "INSERT INTO link_images (link_key, position, url) VALUES (%s, %s, %s)",
                [(key, i, url) for i, url in enumerate(images)],
            )
            n_images += len(images)

        agent_links = row.get("agent_links") or {}
        for agent, url in agent_links.items():
            cur.execute(
                "INSERT INTO link_agent_links (link_key, agent, url) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE url = VALUES(url)",
                (key, agent, url),
            )
            n_agents += 1

        for post in row.get("posts") or []:
            cur.execute(
                "INSERT INTO link_posts (link_key, thread_id, forum, title, guild_id, posted) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE forum = VALUES(forum), title = VALUES(title), "
                "guild_id = VALUES(guild_id), posted = VALUES(posted)",
                (key, str(post.get("thread_id")), post.get("forum"),
                 post.get("title"),
                 str(post.get("guild_id")) if post.get("guild_id") else None,
                 post.get("posted")),
            )
            n_posts += 1

    print(f"[OK] {n_links} linkuri, {n_images} poze, {n_agents} linkuri agenti, "
          f"{n_posts} legaturi post migrate.")


def main():
    print(f"[..] Conectare la MariaDB pe {db.DB_HOST}:{db.DB_PORT}/{db.DB_NAME} ...")
    try:
        db.init_schema()
    except Exception as e:
        print(f"[FATAL] Nu m-am putut conecta / crea schema: {e}")
        sys.exit(1)

    posts = load("posts.json")
    drafts = load("drafts.json")
    links = load("links.json")

    conn = db.get_conn()
    try:
        cur = conn.cursor()
        migrate_posts(cur, posts)
        migrate_drafts(cur, drafts)
        migrate_links(cur, links)
        cur.close()
    finally:
        conn.close()

    print("\n[GATA] Migrare completa. posts.json / drafts.json / links.json "
          "raman neatinse pe disc ca backup - poti sa le arhivezi separat.")


if __name__ == "__main__":
    main()
