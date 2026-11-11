import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.db import connect, init_db
from app.main import app

init_db()
suffix = "社"
a_name = f"甲{suffix}"
b_name = f"乙{suffix}"

# reuse if a previous run created them
conn = connect()
conn.execute(
    """
    DELETE FROM messages
    WHERE sender_id IN (SELECT id FROM users WHERE username IN (?, ?))
       OR recipient_id IN (SELECT id FROM users WHERE username IN (?, ?))
    """,
    (a_name, b_name, a_name, b_name),
)
conn.execute("DELETE FROM post_likes")
conn.execute("DELETE FROM post_saves")
conn.execute("DELETE FROM comments WHERE nickname IN (?, ?) OR body LIKE '楼层%'", (a_name, b_name))
conn.execute("DELETE FROM posts WHERE title=?", ("楼层测试帖",))
conn.commit()
conn.close()

client_a = TestClient(app)
client_b = TestClient(app)

def ensure(client, name):
    page = client.post(
        "/me/register",
        data={"username": name, "password": "123456", "recovery": "口令口令", "next": "/forum"},
        follow_redirects=True,
    )
    if page.status_code == 400:
        client.post(
            "/me/login",
            data={"username": name, "password": "123456", "next": "/forum"},
            follow_redirects=True,
        )

ensure(client_a, a_name)
ensure(client_b, b_name)

made = client_a.post(
    "/forum/new",
    data={"title": "楼层测试帖", "body": "主楼正文"},
    follow_redirects=False,
)
assert made.status_code == 303, made.status_code
post_url = made.headers["location"]
post_id = int(post_url.rsplit("/", 1)[-1])

root = client_b.post(
    f"/forum/{post_id}/comments",
    data={"body": "楼层一级", "nickname": "路人乙"},
    follow_redirects=False,
)
assert root.status_code == 303
assert "thread=" not in root.headers["location"]

nested = client_a.post(
    f"/forum/{post_id}/comments",
    data={"body": "回复@路人乙：楼层二级", "nickname": a_name},
    follow_redirects=False,
)
assert nested.status_code == 303
assert "thread=" in nested.headers["location"]

deeper = client_b.post(
    f"/forum/{post_id}/comments",
    data={"body": "楼层三级", "nickname": "路人乙", "parent_id": ""},
    follow_redirects=True,
)
page = client_a.get(f"/forum/{post_id}")
assert page.status_code == 200
assert "楼层一级" in page.text
assert "1条回复" in page.text or "2条回复" in page.text
assert "@路人乙" in page.text
assert "帖子记在个人账号下" not in client_a.get("/forum").text
assert "查找" in client_a.get("/forum").text
assert "聊天" in page.text

conn = connect()
rows = conn.execute(
    "SELECT id, nickname, body, parent_id FROM comments WHERE post_id=? ORDER BY id",
    (post_id,),
).fetchall()
assert rows[0]["parent_id"] is None
assert rows[1]["parent_id"] == rows[0]["id"]
assert rows[1]["body"] == "楼层二级"
second_root = client_b.post(
    f"/forum/{post_id}/comments",
    data={"body": "楼层另一条一级", "nickname": "路人乙"},
    follow_redirects=False,
)
assert "thread=" not in second_root.headers["location"]

liked = client_b.post(f"/forum/{post_id}/like", data={"next": "/forum"}, follow_redirects=True)
assert 'class="icon-btn on heart"' in liked.text or "on heart" in liked.text
saved = client_b.post(f"/forum/{post_id}/save", data={"next": "/me"}, follow_redirects=True)
assert "收藏" in saved.text
assert "楼层测试帖" in saved.text

user_a = conn.execute("SELECT id FROM users WHERE username=?", (a_name,)).fetchone()
user_b = conn.execute("SELECT id FROM users WHERE username=?", (b_name,)).fetchone()
profile = client_b.get(f"/u/{user_a['id']:04d}")
assert "私聊" in profile.text
sent = client_b.post(f"/chat/{user_a['id']}", data={"body": "在吗"}, follow_redirects=True)
assert "在吗" in sent.text
shared = client_a.post(
    f"/forum/{post_id}/share",
    data={"to": user_b["id"], "next": "/forum"},
    follow_redirects=True,
)
assert "已分享到私聊" in shared.text
inbox = client_b.get(f"/chat/{user_a['id']}")
assert "楼层测试帖" in inbox.text

found = client_a.get("/forum", params={"author": a_name})
assert "楼层测试帖" in found.text
missing = client_a.get("/forum", params={"author": "不存在的人xyz"})
assert "楼层测试帖" not in missing.text
conn.close()
print("social-ok", post_id)

conn = connect()
ids = [row[0] for row in conn.execute("SELECT id FROM users WHERE username IN (?, ?)", (a_name, b_name))]
if ids:
    marks = ",".join("?" * len(ids))
    conn.execute(
        f"DELETE FROM messages WHERE sender_id IN ({marks}) OR recipient_id IN ({marks})",
        ids + ids,
    )
    conn.execute(f"DELETE FROM post_likes WHERE user_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM post_saves WHERE user_id IN ({marks})", ids)
    conn.execute(
        f"DELETE FROM comments WHERE user_id IN ({marks}) OR post_id IN (SELECT id FROM posts WHERE user_id IN ({marks}))",
        ids + ids,
    )
    conn.execute(f"DELETE FROM posts WHERE user_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM users WHERE id IN ({marks})", ids)
    conn.commit()
conn.close()
