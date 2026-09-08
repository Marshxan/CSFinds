-- Ruleaza asta o singura data pe serverul MariaDB (192.168.1.55), ca root
-- sau alt user cu drept de CREATE DATABASE / CREATE USER.
--
--   mysql -h 192.168.1.55 -P 3306 -u root -p < schema.sql
--
-- Schimba parola de mai jos (CHANGE_ME) inainte sa rulezi, si pune aceeasi
-- parola in .env la DB_PASSWORD.

CREATE DATABASE IF NOT EXISTS csfinds
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'csfinds'@'%' IDENTIFIED BY 'CHANGE_ME';
GRANT ALL PRIVILEGES ON csfinds.* TO 'csfinds'@'%';
FLUSH PRIVILEGES;

USE csfinds;

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
