from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "app.db"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS wiki_pages (
            pageid INTEGER PRIMARY KEY,
            title TEXT NOT NULL UNIQUE,
            url TEXT NOT NULL,
            summary TEXT NOT NULL,
            body TEXT NOT NULL,
            lastrevid INTEGER,
            synced_at TEXT NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS wiki_fts USING fts5(
            title,
            body,
            content='wiki_pages',
            content_rowid='pageid'
        );

        CREATE TRIGGER IF NOT EXISTS wiki_ai AFTER INSERT ON wiki_pages BEGIN
            INSERT INTO wiki_fts(rowid, title, body) VALUES (new.pageid, new.title, new.body);
        END;
        CREATE TRIGGER IF NOT EXISTS wiki_ad AFTER DELETE ON wiki_pages BEGIN
            INSERT INTO wiki_fts(wiki_fts, rowid, title, body)
            VALUES('delete', old.pageid, old.title, old.body);
        END;
        CREATE TRIGGER IF NOT EXISTS wiki_au AFTER UPDATE ON wiki_pages BEGIN
            INSERT INTO wiki_fts(wiki_fts, rowid, title, body)
            VALUES('delete', old.pageid, old.title, old.body);
            INSERT INTO wiki_fts(rowid, title, body) VALUES (new.pageid, new.title, new.body);
        END;

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            disabled INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            nickname TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            user_id INTEGER REFERENCES users(id),
            nickname TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS post_likes (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, post_id)
        );

        CREATE TABLE IF NOT EXISTS post_saves (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, post_id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL REFERENCES users(id),
            recipient_id INTEGER NOT NULL REFERENCES users(id),
            body TEXT NOT NULL,
            post_id INTEGER REFERENCES posts(id),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ask_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT '',
            helpful INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    wiki_cols = [row[1] for row in conn.execute("PRAGMA table_info(wiki_pages)")]
    if "display_title" not in wiki_cols:
        conn.execute("ALTER TABLE wiki_pages ADD COLUMN display_title TEXT")
    if "category" not in wiki_cols:
        conn.execute("ALTER TABLE wiki_pages ADD COLUMN category TEXT")
    if "html" not in wiki_cols:
        conn.execute("ALTER TABLE wiki_pages ADD COLUMN html TEXT")
    user_cols = [row[1] for row in conn.execute("PRAGMA table_info(users)")]
    if "avatar" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT DEFAULT 'farmer'")
    if "recovery_hash" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN recovery_hash TEXT")
    user_cols = [row[1] for row in conn.execute("PRAGMA table_info(users)")]
    if "role" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    if "disabled" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN disabled INTEGER NOT NULL DEFAULT 0")
    conn.execute("UPDATE users SET role='user' WHERE role IS NULL OR role=''")
    conn.execute("UPDATE users SET disabled=0 WHERE disabled IS NULL")
    conn.execute("UPDATE users SET role='admin' WHERE id=2 AND username='Sheldon'")
    post_cols = [row[1] for row in conn.execute("PRAGMA table_info(posts)")]
    if "user_id" not in post_cols:
        conn.execute("ALTER TABLE posts ADD COLUMN user_id INTEGER")
    comment_cols = [row[1] for row in conn.execute("PRAGMA table_info(comments)")]
    if "user_id" not in comment_cols:
        conn.execute("ALTER TABLE comments ADD COLUMN user_id INTEGER")
    if "parent_id" not in comment_cols:
        conn.execute("ALTER TABLE comments ADD COLUMN parent_id INTEGER")
    conn.commit()
    conn.close()
