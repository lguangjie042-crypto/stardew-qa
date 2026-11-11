from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from .ai import answer_question, stream_answer, _prefer_ollama
from markupsafe import Markup, escape

from .auth import (
    AVATARS,
    avatar_emoji,
    change_password,
    change_username,
    current_user,
    get_user_by_handle,
    is_admin,
    login_user,
    register_user,
    reset_password,
    set_avatar,
    set_recovery,
    user_handle,
)
from .db import connect, init_db
from .catalog import CATEGORY_ORDER, SECTIONS
from .rag import (
    chat_model_name,
    embed_model_name,
    index_is_current,
    ollama_model_names,
    _model_ready,
)
from .wiki import (
    count_pages,
    get_page,
    get_page_by_title,
    last_sync,
    list_pages,
    recategorize_all,
    sync_official_wiki,
    wiki_count,
)

APP_DIR = Path(__file__).resolve().parent
NCM_DIR = Path.home() / "Desktop" / "ncm"
_NCM_MEDIA = {".mp3", ".jpg", ".jpeg", ".png", ".webp", ".gif"}
load_dotenv(APP_DIR.parent / ".env")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
def mentionize(text: str) -> Markup:
    safe = str(escape(text or ""))
    linked = re.sub(
        r"@(\d{1,6})",
        lambda m: f'<a class="handle" href="/u/{int(m.group(1)):04d}">@{int(m.group(1)):04d}</a>',
        safe,
    )
    return Markup(linked.replace("\n", "<br>"))


templates.env.globals["avatar_emoji"] = avatar_emoji
templates.env.globals["AVATARS"] = AVATARS
templates.env.globals["user_handle"] = user_handle
templates.env.globals["mentionize"] = mentionize


def forum_when(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(timezone(timedelta(hours=8)))
    clock = f"{local.hour:02d}:{local.minute:02d}"
    if local.year == datetime.now(timezone(timedelta(hours=8))).year:
        return f"{local.month}月{local.day}日 {clock}"
    return f"{local.year}年{local.month}月{local.day}日 {clock}"


def excerpt(value: str, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


templates.env.filters["forum_when"] = forum_when
templates.env.filters["excerpt"] = excerpt

app = FastAPI(title="星露谷问答社")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET", "stardew-qa-local-secret"),
    same_site="lax",
)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def _ncm_media_files() -> list[Path]:
    if not NCM_DIR.is_dir():
        return []
    return [
        path
        for path in NCM_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in _NCM_MEDIA
    ]


def ncm_track_urls() -> list[str]:
    tracks = [path for path in _ncm_media_files() if path.suffix.lower() == ".mp3"]
    tracks.sort(key=lambda path: path.name.casefold())
    return ["/ncm/" + quote(path.name) for path in tracks]


@app.get("/ncm/{name}")
def ncm_media(name: str):
    if name != Path(name).name:
        raise HTTPException(status_code=404)
    root = NCM_DIR.resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file() or path.suffix.lower() not in _NCM_MEDIA:
        raise HTTPException(status_code=404)
    return FileResponse(path)


@app.on_event("startup")
def startup() -> None:
    init_db()
    recategorize_all()

    def _warm() -> None:
        try:
            from .rag import warm_index

            warm_index()
        except Exception:
            pass

    if os.environ.get("VERCEL"):
        return
    import threading

    threading.Thread(target=_warm, daemon=True).start()


def ctx(request: Request, **extra):
    user = current_user(request)
    data = {
        "request": request,
        "wiki_count": wiki_count(),
        "last_sync": last_sync(),
        "user": user,
        "is_admin": is_admin(user),
        "categories": CATEGORY_ORDER,
        "path": request.url.path,
        "ncm_tracks": ncm_track_urls(),
    }
    data.update(extra)
    return data


def require_admin(request: Request, next_url: str):
    user = current_user(request)
    if not user:
        return None, RedirectResponse(f"/me?next={quote(next_url)}", status_code=303)
    if not is_admin(user):
        return None, RedirectResponse("/me", status_code=303)
    return user, None


def login_required(request: Request, next_url: str):
    if current_user(request):
        return None
    return RedirectResponse(f"/me?next={quote(next_url)}", status_code=303)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_next(url: str, fallback: str) -> str:
    if url.startswith("/") and not url.startswith("//"):
        return url
    return fallback


def wants_stay(request: Request) -> bool:
    return request.headers.get("x-stay") == "1"


def owns(user, owner_id) -> bool:
    if not user or owner_id is None:
        return False
    return int(owner_id) == int(user["id"])


def cabinet(user_id: int):
    conn = connect()
    posts = conn.execute(
        "SELECT id, title, created_at FROM posts WHERE user_id=? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    favorites = conn.execute(
        """
        SELECT p.id, p.title, p.created_at
        FROM post_saves s
        JOIN posts p ON p.id = s.post_id
        WHERE s.user_id=?
        ORDER BY s.created_at DESC, p.id DESC
        """,
        (user_id,),
    ).fetchall()
    conn.close()
    return posts, favorites


def chat_peers(conn, user_id: int):
    return conn.execute(
        """
        SELECT u.id, u.username, COALESCE(u.avatar, 'farmer') AS avatar
        FROM users u
        WHERE u.id IN (
            SELECT CASE WHEN sender_id = ? THEN recipient_id ELSE sender_id END
            FROM messages
            WHERE sender_id = ? OR recipient_id = ?
        )
        ORDER BY u.username
        """,
        (user_id, user_id, user_id),
    ).fetchall()


def comment_root_id(conn, comment_id: int) -> int:
    seen = set()
    current = comment_id
    while current and current not in seen:
        seen.add(current)
        row = conn.execute("SELECT id, parent_id FROM comments WHERE id=?", (current,)).fetchone()
        if not row or not row["parent_id"]:
            return int(row["id"]) if row else comment_id
        current = int(row["parent_id"])
    return comment_id


def comment_forest(rows):
    nodes = {}
    for row in rows:
        item = dict(row)
        item["children"] = []
        item["reply_count"] = 0
        item["reply_to"] = ""
        nodes[item["id"]] = item
    roots = []
    for item in nodes.values():
        parent_id = item.get("parent_id")
        parent = nodes.get(parent_id) if parent_id else None
        if parent:
            item["reply_to"] = parent["nickname"]
            parent["children"].append(item)
        else:
            roots.append(item)

    def count(node):
        total = 0
        for child in node["children"]:
            total += 1 + count(child)
        node["reply_count"] = total
        return total

    for root in roots:
        count(root)
    return roots


REPLY_MARK = re.compile(r"^回复\s*@(.+?)[：:]\s*(.+)$", re.S)


@app.get("/")
def home(request: Request):
    return templates.TemplateResponse("landing.html", ctx(request))


@app.get("/wiki")
def wiki_index(request: Request, q: str = "", cat: str = "", p: int = 1, sync: str = ""):
    page_no = p if p > 0 else 1
    page_size = 60
    pages = []
    total = 0
    if q:
        total = count_pages(q=q, category=cat or None)
        pages = list_pages(
            q=q,
            category=cat or None,
            limit=page_size,
            offset=(page_no - 1) * page_size,
        )
    return templates.TemplateResponse(
        "index.html",
        ctx(
            request,
            sections=SECTIONS,
            pages=pages,
            q=q,
            cat=cat,
            p=page_no,
            total=total,
            page_size=page_size,
            sync_error="只有管理员可以同步官方 Wiki" if sync == "forbidden" else "",
        ),
    )


@app.get("/wiki/go/{title:path}")
def wiki_go(request: Request, title: str):
    page = get_page_by_title(title)
    if not page:
        return templates.TemplateResponse(
            "wiki_missing.html",
            ctx(request, title=title),
            status_code=404,
        )
    return RedirectResponse(f"/wiki/{page['pageid']}", status_code=302)


@app.get("/wiki/{pageid}")
def wiki_detail(request: Request, pageid: int):
    page = get_page(pageid)
    if not page:
        raise HTTPException(404, "没有这条 Wiki 资料")
    return templates.TemplateResponse("wiki_detail.html", ctx(request, page=page))


@app.post("/wiki/sync")
def wiki_sync(request: Request):
    user = current_user(request)
    if not user:
        return RedirectResponse("/me?next=/wiki", status_code=303)
    if not is_admin(user):
        return RedirectResponse("/wiki?sync=forbidden", status_code=303)
    sync_official_wiki()
    return RedirectResponse("/wiki", status_code=303)


def forum_posts(conn, viewer_id: int | None, date: str, author: str, content: str):
    params: list = []
    liked = "0 AS liked, 0 AS saved"
    if viewer_id:
        liked = (
            "EXISTS(SELECT 1 FROM post_likes l WHERE l.post_id=p.id AND l.user_id=?) AS liked, "
            "EXISTS(SELECT 1 FROM post_saves s WHERE s.post_id=p.id AND s.user_id=?) AS saved"
        )
        params.extend([viewer_id, viewer_id])
    sql = f"""
        SELECT p.id, p.nickname, p.title, p.body, p.created_at, p.user_id,
               COALESCE(u.username, p.nickname) AS username,
               COALESCE(u.avatar, 'farmer') AS avatar,
               (SELECT COUNT(*) FROM comments c WHERE c.post_id = p.id) AS comments,
               {liked}
        FROM posts p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE 1=1
    """
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        sql += " AND substr(p.created_at, 1, 10) = ?"
        params.append(date)
    else:
        date = ""
    author = (author or "").strip()[:32]
    if author:
        sql += " AND (COALESCE(u.username, p.nickname) LIKE ? OR p.nickname LIKE ?)"
        like = f"%{author}%"
        params.extend([like, like])
    content = (content or "").strip()[:80]
    if content:
        sql += " AND (p.title LIKE ? OR p.body LIKE ?)"
        like = f"%{content}%"
        params.extend([like, like])
    sql += " ORDER BY p.id DESC"
    return conn.execute(sql, params).fetchall(), date, author, content


@app.get("/forum")
def forum(request: Request, date: str = "", author: str = "", content: str = "", shared: str = ""):
    user = current_user(request)
    conn = connect()
    posts, date, author, content = forum_posts(conn, user["id"] if user else None, date, author, content)
    peers = chat_peers(conn, user["id"]) if user else []
    conn.close()
    query = []
    if date:
        query.append(f"date={quote(date)}")
    if author:
        query.append(f"author={quote(author)}")
    if content:
        query.append(f"content={quote(content)}")
    next_url = "/forum" + (f"?{'&'.join(query)}" if query else "")
    notice = "已分享到私聊" if shared else ""
    return templates.TemplateResponse(
        "forum.html",
        ctx(
            request,
            posts=posts,
            peers=peers,
            date=date,
            author=author,
            content=content,
            next_url=next_url,
            notice=notice,
        ),
    )


@app.get("/forum/new")
def forum_new(request: Request):
    redirect = login_required(request, "/forum/new")
    if redirect:
        return redirect
    return templates.TemplateResponse("forum_new.html", ctx(request))


@app.post("/forum/new")
def forum_create(request: Request, title: str = Form(...), body: str = Form(...)):
    redirect = login_required(request, "/forum/new")
    if redirect:
        return redirect
    user = current_user(request)
    title = title.strip()[:80]
    body = body.strip()[:4000]
    if not title or not body:
        return RedirectResponse("/forum/new", status_code=303)
    conn = connect()
    conn.execute(
        "INSERT INTO posts(user_id, nickname, title, body, created_at) VALUES (?, ?, ?, ?, ?)",
        (user["id"], user["username"], title, body, now_iso()),
    )
    conn.commit()
    post_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return RedirectResponse(f"/forum/{post_id}", status_code=303)


def load_post(conn, post_id: int, viewer_id: int | None):
    liked = "0 AS liked, 0 AS saved"
    params: list = []
    if viewer_id:
        liked = (
            "EXISTS(SELECT 1 FROM post_likes l WHERE l.post_id=p.id AND l.user_id=?) AS liked, "
            "EXISTS(SELECT 1 FROM post_saves s WHERE s.post_id=p.id AND s.user_id=?) AS saved"
        )
        params.extend([viewer_id, viewer_id])
    params.append(post_id)
    return conn.execute(
        f"""
        SELECT p.*, COALESCE(u.username, p.nickname) AS username,
               COALESCE(u.avatar, 'farmer') AS avatar,
               {liked}
        FROM posts p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE p.id=?
        """,
        params,
    ).fetchone()


@app.get("/forum/{post_id}")
def forum_post(request: Request, post_id: int, thread: int = 0, shared: str = "", back: str = ""):
    user = current_user(request)
    conn = connect()
    post = load_post(conn, post_id, user["id"] if user else None)
    if not post:
        conn.close()
        raise HTTPException(404, "帖子不存在")
    back_url = back if back.startswith("/") and not back.startswith("//") else ""
    comments = conn.execute(
        """
        SELECT c.*, COALESCE(u.username, c.nickname) AS username,
               COALESCE(u.avatar, 'farmer') AS avatar
        FROM comments c
        LEFT JOIN users u ON u.id = c.user_id
        WHERE c.post_id=?
        ORDER BY c.id
        """,
        (post_id,),
    ).fetchall()
    peers = chat_peers(conn, user["id"]) if user else []
    conn.close()
    return templates.TemplateResponse(
        "post.html",
        ctx(
            request,
            post=post,
            roots=comment_forest(comments),
            peers=peers,
            next_url=f"/forum/{post_id}" + (f"?back={quote(back_url, safe='')}" if back_url else ""),
            back=back_url,
            open_thread=thread,
            notice="已分享到私聊" if shared else "",
        ),
    )


@app.post("/forum/{post_id}/like")
def toggle_like(request: Request, post_id: int, next: str = Form("/forum")):
    redirect = login_required(request, f"/forum/{post_id}")
    if redirect:
        return redirect
    user = current_user(request)
    conn = connect()
    if not conn.execute("SELECT id FROM posts WHERE id=?", (post_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "帖子不存在")
    existing = conn.execute(
        "SELECT 1 FROM post_likes WHERE user_id=? AND post_id=?",
        (user["id"], post_id),
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM post_likes WHERE user_id=? AND post_id=?", (user["id"], post_id))
    else:
        conn.execute(
            "INSERT INTO post_likes(user_id, post_id, created_at) VALUES (?, ?, ?)",
            (user["id"], post_id, now_iso()),
        )
    conn.commit()
    conn.close()
    return RedirectResponse(safe_next(next, "/forum"), status_code=303)


@app.post("/forum/{post_id}/save")
def toggle_save(request: Request, post_id: int, next: str = Form("/forum")):
    redirect = login_required(request, f"/forum/{post_id}")
    if redirect:
        return redirect
    user = current_user(request)
    conn = connect()
    if not conn.execute("SELECT id FROM posts WHERE id=?", (post_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "帖子不存在")
    existing = conn.execute(
        "SELECT 1 FROM post_saves WHERE user_id=? AND post_id=?",
        (user["id"], post_id),
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM post_saves WHERE user_id=? AND post_id=?", (user["id"], post_id))
    else:
        conn.execute(
            "INSERT INTO post_saves(user_id, post_id, created_at) VALUES (?, ?, ?)",
            (user["id"], post_id, now_iso()),
        )
    conn.commit()
    conn.close()
    return RedirectResponse(safe_next(next, "/forum"), status_code=303)


@app.post("/forum/{post_id}/share")
def share_post(request: Request, post_id: int, to: int = Form(...), next: str = Form("/forum")):
    stay = wants_stay(request)
    user = current_user(request)
    if not user:
        if stay:
            return JSONResponse({"ok": False}, status_code=401)
        return RedirectResponse(f"/me?next=/forum/{post_id}", status_code=303)
    if to == user["id"]:
        if stay:
            return JSONResponse({"ok": False})
        return RedirectResponse(safe_next(next, "/forum"), status_code=303)
    conn = connect()
    post = conn.execute("SELECT id, title FROM posts WHERE id=?", (post_id,)).fetchone()
    if not post:
        conn.close()
        raise HTTPException(404, "帖子不存在")
    peer = conn.execute("SELECT id FROM users WHERE id=?", (to,)).fetchone()
    talked = conn.execute(
        """
        SELECT 1 FROM messages
        WHERE (sender_id=? AND recipient_id=?) OR (sender_id=? AND recipient_id=?)
        """,
        (user["id"], to, to, user["id"]),
    ).fetchone()
    sent = False
    if peer and talked:
        conn.execute(
            """
            INSERT INTO messages(sender_id, recipient_id, body, post_id, created_at, seen)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (user["id"], to, f"分享帖子：{post['title']}", post_id, now_iso()),
        )
        conn.commit()
        sent = True
    conn.close()
    if stay:
        return JSONResponse({"ok": sent})
    dest = safe_next(next, f"/forum/{post_id}")
    if not sent:
        return RedirectResponse(dest, status_code=303)
    join = "&" if "?" in dest else "?"
    return RedirectResponse(f"{dest}{join}shared=1", status_code=303)


@app.post("/forum/{post_id}/comments")
def add_comment(
    request: Request,
    post_id: int,
    body: str = Form(...),
    nickname: str = Form(""),
    parent_id: str = Form(""),
):
    redirect = login_required(request, f"/forum/{post_id}")
    if redirect:
        return redirect
    user = current_user(request)
    body = body.strip()[:1200]
    nickname = nickname.strip()[:16] or user["username"]
    if not body:
        return RedirectResponse(f"/forum/{post_id}", status_code=303)
    parent = int(parent_id) if parent_id.isdigit() else None
    original = body
    matched = REPLY_MARK.match(body)
    conn = connect()
    if not conn.execute("SELECT id FROM posts WHERE id=?", (post_id,)).fetchone():
        conn.close()
        raise HTTPException(404, "帖子不存在")
    if parent is None and matched:
        target_name = matched.group(1).strip()
        hit = conn.execute(
            """
            SELECT id FROM comments
            WHERE post_id=? AND nickname=?
            ORDER BY id DESC LIMIT 1
            """,
            (post_id, target_name),
        ).fetchone()
        if hit:
            parent = int(hit["id"])
            body = matched.group(2).strip()[:1200]
        else:
            body = original
    if parent is not None:
        owns = conn.execute(
            "SELECT id FROM comments WHERE id=? AND post_id=?",
            (parent, post_id),
        ).fetchone()
        if not owns:
            parent = None
            body = original
    if not body:
        conn.close()
        return RedirectResponse(f"/forum/{post_id}", status_code=303)
    conn.execute(
        """
        INSERT INTO comments(post_id, user_id, nickname, body, created_at, parent_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (post_id, user["id"], nickname, body, now_iso(), parent),
    )
    conn.commit()
    new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    root = comment_root_id(conn, int(new_id)) if parent else None
    conn.close()
    if root and root != int(new_id):
        return RedirectResponse(f"/forum/{post_id}?thread={root}", status_code=303)
    return RedirectResponse(f"/forum/{post_id}", status_code=303)


def delete_comment_tree(conn, post_id: int, comment_id: int) -> bool:
    rows = conn.execute(
        "SELECT id, parent_id FROM comments WHERE post_id=?",
        (post_id,),
    ).fetchall()
    children: dict[int | None, list[int]] = {}
    known: set[int] = set()
    for row in rows:
        known.add(row["id"])
        children.setdefault(row["parent_id"], []).append(row["id"])
    if comment_id not in known:
        return False
    stack = [comment_id]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(children.get(current, []))
    conn.execute(
        f"DELETE FROM comments WHERE id IN ({','.join('?' for _ in seen)})",
        tuple(seen),
    )
    return True


@app.post("/forum/{post_id}/delete")
def remove_post(request: Request, post_id: int, next: str = Form("")):
    stay = wants_stay(request)
    user = current_user(request)
    if not user:
        if stay:
            return JSONResponse({"ok": False}, status_code=401)
        return RedirectResponse(f"/me?next=/forum/{post_id}", status_code=303)
    conn = connect()
    row = conn.execute("SELECT id, user_id FROM posts WHERE id=?", (post_id,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "帖子不存在")
    if not is_admin(user) and not owns(user, row["user_id"]):
        conn.close()
        if stay:
            return JSONResponse({"ok": False})
        return RedirectResponse(f"/forum/{post_id}", status_code=303)
    conn.execute("UPDATE messages SET post_id=NULL WHERE post_id=?", (post_id,))
    conn.execute("DELETE FROM posts WHERE id=?", (post_id,))
    conn.commit()
    conn.close()
    if stay:
        return JSONResponse({"ok": True})
    return RedirectResponse(safe_next(next, "/forum") if next else "/forum", status_code=303)


@app.post("/forum/{post_id}/comments/{comment_id}/delete")
def remove_comment(request: Request, post_id: int, comment_id: int):
    stay = wants_stay(request)
    user = current_user(request)
    if not user:
        if stay:
            return JSONResponse({"ok": False}, status_code=401)
        return RedirectResponse(f"/me?next=/forum/{post_id}", status_code=303)
    conn = connect()
    row = conn.execute(
        "SELECT id, user_id FROM comments WHERE id=? AND post_id=?",
        (comment_id, post_id),
    ).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "评论不存在")
    if not is_admin(user) and not owns(user, row["user_id"]):
        conn.close()
        if stay:
            return JSONResponse({"ok": False})
        return RedirectResponse(f"/forum/{post_id}", status_code=303)
    if not delete_comment_tree(conn, post_id, comment_id):
        conn.close()
        raise HTTPException(404, "评论不存在")
    conn.commit()
    conn.close()
    if stay:
        return JSONResponse({"ok": True})
    return RedirectResponse(f"/forum/{post_id}", status_code=303)


@app.post("/forum/{post_id}/unsave")
def unsave_post(request: Request, post_id: int):
    stay = wants_stay(request)
    user = current_user(request)
    if not user:
        if stay:
            return JSONResponse({"ok": False}, status_code=401)
        return RedirectResponse("/me?next=/me", status_code=303)
    conn = connect()
    cur = conn.execute(
        "DELETE FROM post_saves WHERE user_id=? AND post_id=?",
        (user["id"], post_id),
    )
    removed = cur.rowcount > 0
    conn.commit()
    conn.close()
    if stay:
        return JSONResponse({"ok": removed})
    if not removed:
        return RedirectResponse("/me", status_code=303)
    return RedirectResponse("/me?removed=1", status_code=303)


@app.get("/me")
def me_page(
    request: Request,
    next: str = "/",
    error: str = "",
    notice: str = "",
    removed: str = "",
    deleted: str = "",
    shared: str = "",
):
    user = current_user(request)
    posts, favorites = cabinet(user["id"]) if user else ([], [])
    share_peers = []
    if user:
        conn = connect()
        share_peers = [
            {
                "id": int(row["id"]),
                "name": row["username"],
                "avatar": avatar_emoji(row["avatar"]),
            }
            for row in chat_peers(conn, user["id"])
        ]
        conn.close()
    return templates.TemplateResponse(
        "me.html",
        ctx(
            request,
            posts=posts,
            favorites=favorites,
            next_url=next,
            error=error,
            notice=notice,
            removed=removed == "1",
            deleted=deleted == "1",
            shared=shared == "1",
            share_peers=share_peers,
        ),
    )


@app.post("/me/register")
def me_register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    recovery: str = Form(""),
    next: str = Form("/"),
):
    user, error = register_user(username, password, recovery)
    if error:
        return templates.TemplateResponse(
            "me.html",
            ctx(request, posts=[], next_url=next, error=error, notice=""),
            status_code=400,
        )
    request.session["user_id"] = user["id"]
    return RedirectResponse(next or "/me", status_code=303)


@app.post("/me/login")
def me_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    user = login_user(username, password)
    if not user:
        return templates.TemplateResponse(
            "me.html",
            ctx(request, posts=[], next_url=next, error="用户名或密码不对", notice=""),
            status_code=400,
        )
    if int(user["disabled"] or 0):
        return templates.TemplateResponse(
            "me.html",
            ctx(request, posts=[], next_url=next, error="这个账号已停用", notice=""),
            status_code=400,
        )
    request.session["user_id"] = user["id"]
    return RedirectResponse(next or "/me", status_code=303)


@app.post("/me/logout")
def me_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/me", status_code=303)


@app.get("/admin")
def admin_page(request: Request, error: str = "", notice: str = ""):
    _user, redirect = require_admin(request, "/admin")
    if redirect:
        return redirect
    conn = connect()
    accounts = conn.execute(
        """
        SELECT u.id, u.username, u.created_at, u.role,
               COALESCE(u.disabled, 0) AS disabled,
               (SELECT COUNT(*) FROM posts p WHERE p.user_id = u.id) AS post_count
        FROM users u
        ORDER BY u.id
        """
    ).fetchall()
    conn.close()
    return templates.TemplateResponse(
        "admin.html",
        ctx(request, accounts=accounts, error=error, notice=notice),
    )


@app.post("/admin/users/{user_id}")
def admin_user_state(request: Request, user_id: int, disabled: int = Form(...)):
    user, redirect = require_admin(request, "/admin")
    if redirect:
        return redirect
    if disabled not in (0, 1):
        return RedirectResponse(f"/admin?error={quote('参数不对')}", status_code=303)
    conn = connect()
    target = conn.execute(
        "SELECT id, role FROM users WHERE id=?",
        (user_id,),
    ).fetchone()
    if not target:
        conn.close()
        return RedirectResponse(f"/admin?error={quote('账号不存在')}", status_code=303)
    if target["id"] == user["id"] or (target["role"] or "") == "admin":
        conn.close()
        return RedirectResponse(f"/admin?error={quote('不能停用这个管理员账号')}", status_code=303)
    conn.execute("UPDATE users SET disabled=? WHERE id=?", (disabled, user_id))
    conn.commit()
    conn.close()
    notice = "已停用" if disabled else "已恢复"
    return RedirectResponse(f"/admin?notice={quote(notice)}", status_code=303)


@app.post("/me/avatar")
def me_avatar(request: Request, avatar: str = Form(...)):
    redirect = login_required(request, "/me")
    if redirect:
        return redirect
    user = current_user(request)
    set_avatar(user["id"], avatar)
    return RedirectResponse("/me", status_code=303)


@app.get("/me/settings")
def me_settings(request: Request, error: str = "", notice: str = ""):
    redirect = login_required(request, "/me/settings")
    if redirect:
        return redirect
    return templates.TemplateResponse(
        "settings.html",
        ctx(request, error=error, notice=notice),
    )


@app.post("/me/username")
def me_username(request: Request, username: str = Form(...)):
    redirect = login_required(request, "/me/settings")
    if redirect:
        return redirect
    user = current_user(request)
    error = change_username(user["id"], username)
    if error:
        return RedirectResponse(f"/me/settings?error={quote(error)}", status_code=303)
    return RedirectResponse(f"/me/settings?notice={quote('用户名已更新，序号不变')}", status_code=303)


@app.get("/u/{tag}")
def public_profile(request: Request, tag: str):
    user = get_user_by_handle(tag)
    if not user:
        raise HTTPException(404, "没有这个账号")
    conn = connect()
    posts = conn.execute(
        "SELECT id, title, created_at FROM posts WHERE user_id=? ORDER BY id DESC",
        (user["id"],),
    ).fetchall()
    conn.close()
    return templates.TemplateResponse(
        "profile.html",
        ctx(request, profile=user, posts=posts),
    )


def activity_notices(conn, user_id: int):
    mention = re.compile(rf"(?<!\d)@(?:{int(user_id):04d}|{int(user_id)})(?!\d)")
    comments = conn.execute(
        """
        SELECT c.id, c.post_id, c.body, c.created_at, c.parent_id,
               p.title AS title, p.user_id AS post_owner,
               COALESCE(u.username, c.nickname) AS name,
               COALESCE(u.avatar, 'farmer') AS avatar,
               parent.user_id AS parent_owner
        FROM comments c
        JOIN posts p ON p.id = c.post_id
        LEFT JOIN users u ON u.id = c.user_id
        LEFT JOIN comments parent ON parent.id = c.parent_id
        WHERE c.user_id IS NULL OR c.user_id != ?
        ORDER BY c.id DESC
        """,
        (user_id,),
    ).fetchall()
    items = []
    for row in comments:
        body = row["body"] or ""
        mentioned = bool(mention.search(body))
        on_my_post = row["parent_id"] is None and row["post_owner"] == user_id
        reply_to_me = row["parent_owner"] is not None and row["parent_owner"] == user_id
        if not (mentioned or on_my_post or reply_to_me):
            continue
        matched = REPLY_MARK.match(body.strip())
        spoken = matched.group(2).strip() if matched else body.strip()
        items.append(
            {
                "name": row["name"],
                "avatar": row["avatar"],
                "line": "@了你" if mentioned else spoken,
                "created_at": row["created_at"],
                "post_id": row["post_id"],
                "title": row["title"],
                "thread": comment_root_id(conn, row["id"]),
            }
        )
    posts = conn.execute(
        """
        SELECT p.id, p.title, p.body, p.created_at,
               COALESCE(u.username, p.nickname) AS name,
               COALESCE(u.avatar, 'farmer') AS avatar
        FROM posts p
        LEFT JOIN users u ON u.id = p.user_id
        WHERE p.user_id IS NULL OR p.user_id != ?
        ORDER BY p.id DESC
        """,
        (user_id,),
    ).fetchall()
    for row in posts:
        if not mention.search(row["body"] or ""):
            continue
        items.append(
            {
                "name": row["name"],
                "avatar": row["avatar"],
                "line": "@了你",
                "created_at": row["created_at"],
                "post_id": row["id"],
                "title": row["title"],
                "thread": None,
            }
        )
    items.sort(key=lambda item: item["created_at"] or "", reverse=True)
    return items


def _stamp_unread(conn, table: str, user_id: int, items) -> bool:
    row = conn.execute(
        f"SELECT read_at FROM {table} WHERE user_id=?",
        (user_id,),
    ).fetchone()
    if row is None:
        return bool(items)
    stamp = row["read_at"] or ""
    return any((item["created_at"] or "") > stamp for item in items)


def _mark_stamp(conn, table: str, user_id: int) -> None:
    conn.execute(
        f"""
        INSERT INTO {table}(user_id, read_at) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET read_at=excluded.read_at
        """,
        (user_id, now_iso()),
    )
    conn.commit()


def notices_unread(conn, user_id: int) -> bool:
    return _stamp_unread(conn, "notice_reads", user_id, activity_notices(conn, user_id))


def mark_notices_read(conn, user_id: int) -> None:
    _mark_stamp(conn, "notice_reads", user_id)


def like_notices(conn, user_id: int):
    rows = conn.execute(
        """
        SELECT l.created_at, l.post_id, p.title,
               u.username AS name, COALESCE(u.avatar, 'farmer') AS avatar
        FROM post_likes l
        JOIN posts p ON p.id = l.post_id
        JOIN users u ON u.id = l.user_id
        WHERE p.user_id = ? AND l.user_id != ?
        ORDER BY l.created_at DESC
        """,
        (user_id, user_id),
    ).fetchall()
    return [
        {
            "name": row["name"],
            "avatar": row["avatar"],
            "line": "赞了你",
            "created_at": row["created_at"],
            "post_id": row["post_id"],
            "title": row["title"],
            "thread": None,
        }
        for row in rows
    ]


def likes_unread(conn, user_id: int) -> bool:
    return _stamp_unread(conn, "like_reads", user_id, like_notices(conn, user_id))


def mark_likes_read(conn, user_id: int) -> None:
    _mark_stamp(conn, "like_reads", user_id)


def conversations(conn, user_id: int):
    rows = conn.execute(
        """
        SELECT m.id, m.sender_id, m.recipient_id, m.body, m.post_id, m.created_at, m.seen,
               u.id AS peer_id, u.username AS peer_name, COALESCE(u.avatar, 'farmer') AS peer_avatar
        FROM messages m
        JOIN users u ON u.id = CASE WHEN m.sender_id = ? THEN m.recipient_id ELSE m.sender_id END
        WHERE m.sender_id = ? OR m.recipient_id = ?
        ORDER BY m.id DESC
        """,
        (user_id, user_id, user_id),
    ).fetchall()
    fresh = {
        row["peer_id"]
        for row in rows
        if row["sender_id"] != user_id and int(row["seen"] or 0) == 0
    }
    seen = set()
    items = []
    for row in rows:
        if row["peer_id"] in seen:
            continue
        seen.add(row["peer_id"])
        item = dict(row)
        item["unread"] = row["peer_id"] in fresh
        items.append(item)
    return items


@app.get("/notices")
def notices_page(request: Request, next: str = "/chat"):
    back = safe_next(next, "/chat")
    redirect = login_required(request, f"/notices?next={quote(back)}")
    if redirect:
        return redirect
    user = current_user(request)
    conn = connect()
    items = activity_notices(conn, user["id"])
    mark_notices_read(conn, user["id"])
    conn.close()
    return templates.TemplateResponse(
        "notices.html",
        ctx(request, items=items, back=back, heading="消息", empty="还没有消息。", list_path="/notices"),
    )


@app.get("/likes")
def likes_page(request: Request, next: str = "/chat"):
    back = safe_next(next, "/chat")
    redirect = login_required(request, f"/likes?next={quote(back)}")
    if redirect:
        return redirect
    user = current_user(request)
    conn = connect()
    items = like_notices(conn, user["id"])
    mark_likes_read(conn, user["id"])
    conn.close()
    return templates.TemplateResponse(
        "notices.html",
        ctx(request, items=items, back=back, heading="点赞", empty="还没有点赞。", list_path="/likes"),
    )


@app.get("/chat")
def chat_home(request: Request):
    redirect = login_required(request, "/chat")
    if redirect:
        return redirect
    user = current_user(request)
    conn = connect()
    threads = conversations(conn, user["id"])
    unread = notices_unread(conn, user["id"])
    liked = likes_unread(conn, user["id"])
    conn.close()
    return templates.TemplateResponse(
        "chat.html",
        ctx(request, threads=threads, peer=None, messages=[], unread=unread, likes_unread=liked),
    )


@app.get("/chat/{peer_id}")
def chat_thread(request: Request, peer_id: int):
    redirect = login_required(request, f"/chat/{peer_id}")
    if redirect:
        return redirect
    user = current_user(request)
    if peer_id == user["id"]:
        return RedirectResponse("/chat", status_code=303)
    conn = connect()
    peer = conn.execute(
        "SELECT id, username, COALESCE(avatar, 'farmer') AS avatar FROM users WHERE id=?",
        (peer_id,),
    ).fetchone()
    if not peer:
        conn.close()
        raise HTTPException(404, "没有这个账号")
    conn.execute(
        "UPDATE messages SET seen=1 WHERE recipient_id=? AND sender_id=?",
        (user["id"], peer_id),
    )
    conn.commit()
    messages = conn.execute(
        """
        SELECT m.*, p.title AS post_title
        FROM messages m
        LEFT JOIN posts p ON p.id = m.post_id
        WHERE (m.sender_id=? AND m.recipient_id=?) OR (m.sender_id=? AND m.recipient_id=?)
        ORDER BY m.id
        """,
        (user["id"], peer_id, peer_id, user["id"]),
    ).fetchall()
    threads = conversations(conn, user["id"])
    unread = notices_unread(conn, user["id"])
    liked = likes_unread(conn, user["id"])
    conn.close()
    return templates.TemplateResponse(
        "chat.html",
        ctx(request, threads=threads, peer=peer, messages=messages, unread=unread, likes_unread=liked),
    )


@app.post("/chat/{peer_id}")
def chat_send(request: Request, peer_id: int, body: str = Form(...)):
    redirect = login_required(request, f"/chat/{peer_id}")
    if redirect:
        return redirect
    user = current_user(request)
    body = body.strip()[:1200]
    if peer_id == user["id"] or not body:
        return RedirectResponse(f"/chat/{peer_id}", status_code=303)
    conn = connect()
    peer = conn.execute("SELECT id FROM users WHERE id=?", (peer_id,)).fetchone()
    if not peer:
        conn.close()
        raise HTTPException(404, "没有这个账号")
    conn.execute(
        """
        INSERT INTO messages(sender_id, recipient_id, body, post_id, created_at, seen)
        VALUES (?, ?, ?, NULL, ?, 0)
        """,
        (user["id"], peer_id, body, now_iso()),
    )
    conn.commit()
    conn.close()
    return RedirectResponse(f"/chat/{peer_id}", status_code=303)


@app.post("/me/password")
def me_password(
    request: Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
):
    redirect = login_required(request, "/me/settings")
    if redirect:
        return redirect
    user = current_user(request)
    error = change_password(user["id"], old_password, new_password)
    if error:
        return RedirectResponse(f"/me/settings?error={quote(error)}", status_code=303)
    return RedirectResponse(f"/me/settings?notice={quote('密码已更新')}", status_code=303)


@app.post("/me/recovery")
def me_recovery(request: Request, recovery: str = Form(...)):
    redirect = login_required(request, "/me/settings")
    if redirect:
        return redirect
    user = current_user(request)
    error = set_recovery(user["id"], recovery)
    if error:
        return RedirectResponse(f"/me/settings?error={quote(error)}", status_code=303)
    return RedirectResponse(f"/me/settings?notice={quote('安全口令已保存')}", status_code=303)


@app.get("/me/forgot")
def forgot_page(request: Request, error: str = ""):
    if current_user(request):
        return RedirectResponse("/me", status_code=303)
    return templates.TemplateResponse("forgot.html", ctx(request, error=error, notice=""))


@app.post("/me/forgot")
def forgot_submit(
    request: Request,
    username: str = Form(...),
    recovery: str = Form(...),
    new_password: str = Form(...),
):
    error = reset_password(username, recovery, new_password)
    if error:
        return templates.TemplateResponse(
            "forgot.html",
            ctx(request, error=error, notice=""),
            status_code=400,
        )
    return templates.TemplateResponse(
        "forgot.html",
        ctx(request, error="", notice="密码已重置，请用新密码登录。"),
    )


@app.get("/ask")
def ask_page():
    return RedirectResponse("/wiki", status_code=302)


class AskTurn(BaseModel):
    q: str = Field(min_length=1, max_length=500)
    a: str = Field(max_length=2000)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    history: list[AskTurn] = Field(default_factory=list)


class AskFeedback(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(max_length=6000)
    mode: str = Field(max_length=20)
    helpful: bool


@app.post("/api/ask")
def api_ask(request: Request, payload: AskBody):
    question = payload.question.strip()
    previous = request.session.get("ask_last") or {}
    offset = int(previous.get("n") or 0) + 1 if previous.get("q") == question else 0
    request.session["ask_last"] = {"q": question, "n": offset}
    return answer_question(question, offset)


@app.post("/api/ask/stream")
def api_ask_stream(request: Request, payload: AskBody):
    question = payload.question.strip()
    previous = request.session.get("ask_last") or {}
    offset = int(previous.get("n") or 0) + 1 if previous.get("q") == question else 0
    request.session["ask_last"] = {"q": question, "n": offset}
    history = [{"q": turn.q, "a": turn.a} for turn in payload.history][-3:]

    import json

    def gen():
        try:
            for event in stream_answer(question, offset, history):
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        except Exception:
            yield (
                "data: "
                + json.dumps(
                    {"kind": "error", "message": "回答生成中断，请再问一次。"},
                    ensure_ascii=False,
                )
                + "\n\n"
            )

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/ask/feedback")
def api_ask_feedback(payload: AskFeedback):
    conn = connect()
    conn.execute(
        "INSERT INTO ask_feedback(question, answer, mode, helpful, created_at) VALUES(?,?,?,?,?)",
        (
            payload.question.strip()[:500],
            payload.answer[:6000],
            payload.mode[:20],
            1 if payload.helpful else 0,
            now_iso(),
        ),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/ask/status")
def api_ask_status():
    status = {
        "ollama": False,
        "embed_ready": False,
        "chat_ready": False,
        "index_ready": False,
        "last_sync": last_sync(),
        "wiki_count": wiki_count(),
        "prefer_ollama": _prefer_ollama(),
    }
    try:
        names = ollama_model_names()
        status["ollama"] = names is not None
        if names:
            status["embed_ready"] = _model_ready(names, embed_model_name())
            status["chat_ready"] = _model_ready(names, chat_model_name())
    except Exception:
        pass
    try:
        status["index_ready"] = index_is_current()
    except Exception:
        pass
    return status
