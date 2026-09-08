"""
Conexiune si schema pentru MariaDB.

Inlocuieste jsonstore.py: in loc de fisiere JSON pe disc, posts/drafts/links
se tin acum intr-o baza de date MariaDB. Restul botului nu stie diferenta -
poststore.py si linkstore.py pastreaza exact aceleasi functii, doar ce fac
"in spate" s-a schimbat.

Config vine din .env: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD.
"""
import logging
import os
import threading
from pathlib import Path

import mysql.connector
from mysql.connector import pooling

log = logging.getLogger("welcome-bot.db")

BASE_DIR = Path(__file__).resolve().parent

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

DB_HOST = os.getenv("DB_HOST", "192.168.1.55")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "csfinds")
DB_USER = os.getenv("DB_USER", "csfinds")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")

_pool_lock = threading.Lock()
_pool = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    thread_id           BIGINT UNSIGNED PRIMARY KEY,
    forum_id            BIGINT UNSIGNED NULL,
    guild_id            BIGINT UNSIGNED NULL,
    links_message_id    BIGINT UNSIGNED NULL,
    title               TEXT NULL,
    name                TEXT NULL,
    price               VARCHAR(64) NULL,
    product_url         TEXT NULL,
    image_url           TEXT NULL,
    platform            VARCHAR(64) NULL,
    item_id             VARCHAR(128) NULL,
    weight              VARCHAR(64) NULL,
    created             VARCHAR(64) NULL,
    INDEX idx_posts_platform_item (platform, item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS drafts (
    id            VARCHAR(255) PRIMARY KEY,
    name          TEXT NULL,
    price         VARCHAR(64) NULL,
    weight        VARCHAR(64) NULL,
    product_url   TEXT NULL,
    photo         TEXT NULL,
    forum_id      BIGINT UNSIGNED NULL,
    forum_name    VARCHAR(255) NULL,
    created       VARCHAR(64) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS links (
    link_key      VARCHAR(255) PRIMARY KEY,   -- "platform:item_id"
    platform      VARCHAR(64) NOT NULL,
    item_id       VARCHAR(128) NOT NULL,
    url           TEXT NULL,
    price         VARCHAR(64) NULL,
    weight        VARCHAR(64) NULL,
    store_title   TEXT NULL,
    share         TEXT NULL,
    seen_count    INT UNSIGNED NOT NULL DEFAULT 0,
    first_seen    VARCHAR(64) NULL,
    last_seen     VARCHAR(64) NULL,
    INDEX idx_links_platform_item (platform, item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS link_images (
    id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    link_key    VARCHAR(255) NOT NULL,
    position    INT UNSIGNED NOT NULL DEFAULT 0,
    url         TEXT NOT NULL,
    FOREIGN KEY (link_key) REFERENCES links(link_key) ON DELETE CASCADE,
    INDEX idx_link_images_key (link_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS link_agent_links (
    link_key    VARCHAR(255) NOT NULL,
    agent       VARCHAR(64) NOT NULL,
    url         TEXT NOT NULL,
    PRIMARY KEY (link_key, agent),
    FOREIGN KEY (link_key) REFERENCES links(link_key) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS link_posts (
    id          BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    link_key    VARCHAR(255) NOT NULL,
    thread_id   VARCHAR(64) NOT NULL,
    forum       VARCHAR(255) NULL,
    title       TEXT NULL,
    guild_id    VARCHAR(64) NULL,
    posted      VARCHAR(64) NULL,
    FOREIGN KEY (link_key) REFERENCES links(link_key) ON DELETE CASCADE,
    UNIQUE KEY uq_link_thread (link_key, thread_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = pooling.MySQLConnectionPool(
                    pool_name="csfinds_pool",
                    pool_size=5,
                    host=DB_HOST,
                    port=DB_PORT,
                    database=DB_NAME,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    autocommit=True,
                    charset="utf8mb4",
                )
    return _pool


def get_conn():
    """O conexiune din pool. Apelantul trebuie sa o inchida (with/close())."""
    return get_pool().get_connection()


def init_schema():
    """Creeaza tabelele daca nu exista. Se apeleaza o data, la pornirea botului."""
    conn = get_conn()
    try:
        cur = conn.cursor()
        for statement in SCHEMA.split(";"):
            statement = statement.strip()
            if statement:
                cur.execute(statement)
        cur.close()
        log.info("[DB] Schema verificata/creata cu succes.")
    finally:
        conn.close()
