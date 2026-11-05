from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from fastapi import Request

from .db import connect

AVATARS = [
    {"id": "farmer", "emoji": "👨‍🌾", "name": "农夫"},
    {"id": "cow", "emoji": "🐄", "name": "奶牛"},
    {"id": "chicken", "emoji": "🐔", "name": "鸡"},
    {"id": "pig", "emoji": "🐷", "name": "猪"},
    {"id": "goat", "emoji": "🐐", "name": "羊"},
    {"id": "duck", "emoji": "🦆", "name": "鸭"},
    {"id": "dino", "emoji": "🦖", "name": "恐龙"},
    {"id": "horse", "emoji": "🐴", "name": "马"},
    {"id": "fish", "emoji": "🐟", "name": "鱼"},
    {"id": "lobster", "emoji": "🦞", "name": "龙虾"},
    {"id": "gem", "emoji": "💎", "name": "宝石"},
    {"id": "star", "emoji": "⭐", "name": "星之果实"},
    {"id": "crop", "emoji": "🌾", "name": "麦穗"},
    {"id": "parsnip", "emoji": "🥕", "name": "防风草"},
    {"id": "strawberry", "emoji": "🍓", "name": "草莓"},
    {"id": "blueberry", "emoji": "🫐", "name": "蓝莓"},
    {"id": "grape", "emoji": "🍇", "name": "葡萄"},
    {"id": "pumpkin", "emoji": "🎃", "name": "南瓜"},
    {"id": "melon", "emoji": "🍈", "name": "甜瓜"},
    {"id": "sunflower", "emoji": "🌻", "name": "向日葵"},
    {"id": "flower", "emoji": "🌸", "name": "花舞"},
    {"id": "mushroom", "emoji": "🍄", "name": "蘑菇"},
    {"id": "honey", "emoji": "🍯", "name": "蜂蜜"},
    {"id": "beer", "emoji": "🍺", "name": "啤酒"},
    {"id": "coffee", "emoji": "☕", "name": "咖啡"},
    {"id": "egg", "emoji": "🥚", "name": "鸡蛋"},
    {"id": "cheese", "emoji": "🧀", "name": "奶酪"},
    {"id": "axe", "emoji": "🪓", "name": "斧头"},
    {"id": "pick", "emoji": "⛏️", "name": "镐"},
    {"id": "rod", "emoji": "🎣", "name": "鱼竿"},
    {"id": "sword", "emoji": "⚔️", "name": "剑"},
    {"id": "house", "emoji": "🏠", "name": "农舍"},
    {"id": "bus", "emoji": "🚌", "name": "巴士"},
    {"id": "wizard", "emoji": "🧙", "name": "法师"},
    {"id": "ring", "emoji": "💍", "name": "戒指"},
    {"id": "moon", "emoji": "🌙", "name": "月亮"},
    {"id": "junimo", "emoji": "🟢", "name": "祝尼魔"},
    {"id": "cat", "emoji": "🐱", "name": "猫"},
    {"id": "dog", "emoji": "🐶", "name": "狗"},
]


def user_handle(user_id: int | None) -> str:
    if not user_id:
        return ""
    return f"@{int(user_id):04d}"


def avatar_emoji(avatar_id: str | None) -> str:
    for item in AVATARS:
        if item["id"] == avatar_id:
            return item["emoji"]
    return "👨‍🌾"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), 120000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hexhash = stored.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode(), 120000)
    return hmac.compare_digest(digest.hex(), hexhash)


def is_admin(user) -> bool:
    return bool(user) and (user["role"] or "") == "admin"


def get_user_by_id(user_id: int):
    conn = connect()
    row = conn.execute(
        """
        SELECT id, username, created_at, avatar, recovery_hash, role,
               COALESCE(disabled, 0) AS disabled
        FROM users WHERE id=?
        """,
        (user_id,),
    ).fetchone()
    conn.close()
    return row


def get_user_by_name(username: str):
    conn = connect()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return row


def current_user(request: Request):
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = get_user_by_id(int(user_id))
    if not user or int(user["disabled"] or 0):
        request.session.pop("user_id", None)
        return None
    return user


def register_user(username: str, password: str, recovery: str = "") -> tuple[dict | None, str | None]:
    username = username.strip()
    recovery = recovery.strip()
    if not (2 <= len(username) <= 16):
        return None, "用户名需要 2 到 16 个字符"
    if len(password) < 6:
        return None, "密码至少 6 位"
    if len(recovery) < 4:
        return None, "请设置至少 4 位安全口令，用来找回密码"
    if get_user_by_name(username):
        return None, "这个用户名已经被占用"
    conn = connect()
    conn.execute(
        """
        INSERT INTO users(username, password_hash, created_at, avatar, recovery_hash, role, disabled)
        VALUES (?, ?, ?, ?, ?, 'user', 0)
        """,
        (
            username,
            hash_password(password),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "farmer",
            hash_password(recovery),
        ),
    )
    conn.commit()
    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return {"id": user_id, "username": username}, None


def get_user_by_handle(handle: str):
    raw = handle.strip().lstrip("@")
    if not raw.isdigit():
        return None
    return get_user_by_id(int(raw))


def login_user(username: str, password: str):
    name = username.strip()
    user = get_user_by_name(name) or get_user_by_handle(name)
    if not user or not verify_password(password, user["password_hash"]):
        return None
    return user


def change_username(user_id: int, new_name: str) -> str | None:
    new_name = new_name.strip()
    if not (2 <= len(new_name) <= 16):
        return "用户名需要 2 到 16 个字符"
    existing = get_user_by_name(new_name)
    if existing and existing["id"] != user_id:
        return "这个用户名已经被占用"
    conn = connect()
    old = conn.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    old_name = old["username"] if old else ""
    conn.execute("UPDATE users SET username=? WHERE id=?", (new_name, user_id))
    conn.execute("UPDATE posts SET nickname=? WHERE user_id=?", (new_name, user_id))
    conn.execute(
        "UPDATE comments SET nickname=? WHERE user_id=? AND nickname=?",
        (new_name, user_id, old_name),
    )
    conn.commit()
    conn.close()
    return None


def set_avatar(user_id: int, avatar_id: str) -> bool:
    if avatar_id not in {item["id"] for item in AVATARS}:
        return False
    conn = connect()
    conn.execute("UPDATE users SET avatar=? WHERE id=?", (avatar_id, user_id))
    conn.commit()
    conn.close()
    return True


def change_password(user_id: int, old_password: str, new_password: str) -> str | None:
    conn = connect()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        conn.close()
        return "账号不存在"
    if not verify_password(old_password, row["password_hash"]):
        conn.close()
        return "当前密码不对"
    if len(new_password) < 6:
        conn.close()
        return "新密码至少 6 位"
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user_id))
    conn.commit()
    conn.close()
    return None


def set_recovery(user_id: int, recovery: str) -> str | None:
    recovery = recovery.strip()
    if len(recovery) < 4:
        return "安全口令至少 4 位"
    conn = connect()
    conn.execute("UPDATE users SET recovery_hash=? WHERE id=?", (hash_password(recovery), user_id))
    conn.commit()
    conn.close()
    return None


def reset_password(username: str, recovery: str, new_password: str) -> str | None:
    user = get_user_by_name(username.strip())
    if not user or not user["recovery_hash"]:
        return "找不到这个账号，或还没设置安全口令"
    if not verify_password(recovery.strip(), user["recovery_hash"]):
        return "安全口令不对"
    if len(new_password) < 6:
        return "新密码至少 6 位"
    conn = connect()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new_password), user["id"]))
    conn.commit()
    conn.close()
    return None
