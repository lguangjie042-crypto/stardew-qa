from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, unquote

import httpx

from .catalog import ALIASES, CATEGORY_ORDER, CATEGORY_ROOTS, all_hub_titles, hub_group
from .db import ROOT, connect

WIKI_API = "https://zh.stardewvalleywiki.com/mediawiki/api.php"
WIKI_SITE = "https://zh.stardewvalleywiki.com"
EN_WIKI = "https://stardewvalleywiki.com"
USER_AGENT = "StardewQA/0.1 (student wiki reader; https://zh.stardewvalleywiki.com)"

CORE_TITLES_FILE = ROOT / "data" / "core_titles.txt"
ZH_TITLES = {
    "Combat/Skill": "战斗技能",
    "Farming/Skill": "耕作技能",
    "Fishing/Skill": "钓鱼技能",
    "Foraging/Skill": "采集技能",
    "Mining/Skill": "采矿技能",
}

GROUPS_FILE = ROOT / "data" / "wiki_groups.json"
_groups_cache: dict[str, str] | None = None

TOPIC_EXPAND = [
    (["赚钱", "利润", "收入", "金币", "金", "发财"], ["金钱", "农作物", "春季作物", "夏季作物", "秋季作物", "种子", "采矿", "钓鱼", "动物", "制作", "蜜蜂房", "酒桶", "罐头瓶", "农场"]),
    (["第一年", "年1", "开局", "新手"], ["农作物", "春季作物", "社区中心", "矿井", "农场", "工具", "金钱"]),
    (["礼物", "送礼", "喜好"], ["礼物", "友谊", "村民"]),
    (["结婚", "约会"], ["结婚", "可结婚对象", "友谊"]),
]


ANIMAL_TITLES = {
    "鸡", "奶牛", "牛", "猪", "山羊", "绵羊", "兔子", "恐龙", "马", "宠物",
    "动物", "鸡舍", "谷仓", "鸭", "鸵鸟", "羊", "猫", "狗",
}


def classify_page(title: str, body: str = "") -> str:
    t = (title or "").strip()
    head = (body or "")[:900]
    person_marks = ["生日", "居住于", "最爱的礼物", "家庭成员", "诊所预约", "可结婚对象"]
    if sum(1 for mark in person_marks if mark in head) >= 2 or (
        "生日" in head and ("居住于" in head or "最爱的礼物" in head)
    ):
        return "人物"
    if t in ANIMAL_TITLES or t.endswith("鸡舍") or t.endswith("谷仓"):
        return "动物"
    if "动物" in t and "制品" not in t:
        return "动物"
    title_rules = [
        ("技能", ["技能", "/Skill"]),
        ("农作物", ["作物", "种子", "果树", "温室"]),
        ("事件", ["节日", "夜市", "花舞", "展览会", "冰雪", "望月", "冬日星", "心事件"]),
        ("地点", ["鹈鹕镇", "矿井", "沙漠", "姜岛", "海滩", "农场", "洞穴", "博物馆", "仓库", "诊所", "超市", "农舍", "小屋", "下水道", "墓园", "温泉", "森林", "山路"]),
        ("物品", ["工具", "武器", "制作", "烹饪", "酒桶", "罐头", "蜜蜂房"]),
        ("机制", ["友谊", "礼物", "金钱", "时间", "日历", "天气", "结婚", "婚姻", "社区中心"]),
        ("人物", ["村民"]),
    ]
    for category, keys in title_rules:
        if any(key in t for key in keys):
            return category
    if "生日" in head:
        return "人物"
    return "其他"


def group_for_title(title: str) -> str | None:
    global _groups_cache
    if _groups_cache is None:
        if GROUPS_FILE.exists():
            _groups_cache = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
        else:
            _groups_cache = {}
    return _groups_cache.get(title) or hub_group(title)


def set_group_map(groups: dict[str, str]) -> None:
    global _groups_cache
    _groups_cache = dict(groups)
    GROUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GROUPS_FILE.write_text(json.dumps(_groups_cache, ensure_ascii=False), encoding="utf-8")


def category_of(title: str, body: str = "") -> str:
    grouped = group_for_title(title)
    if grouped:
        return grouped
    return classify_page(title, body)


def recategorize_all() -> None:
    conn = connect()
    rows = conn.execute("SELECT pageid, title, display_title, body FROM wiki_pages").fetchall()
    for row in rows:
        grouped = group_for_title(row["title"] or "") or group_for_title(row["display_title"] or "")
        category = grouped or classify_page(row["display_title"] or row["title"], row["body"] or "")
        conn.execute(
            "UPDATE wiki_pages SET category=? WHERE pageid=?",
            (category, row["pageid"]),
        )
    conn.commit()
    conn.close()


def _abs_wiki_url(url: str) -> str:
    url = html_lib.unescape((url or "").strip())
    if url.startswith("/wiki/go/") or url.startswith("#"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://zh.stardewvalleywiki.com" + url
    return url


def _local_article_href(url: str) -> str | None:
    absolute = _abs_wiki_url(url)
    base = WIKI_SITE + "/"
    if not absolute.startswith(base):
        return None
    rest = absolute[len(base):]
    if rest.startswith(("mediawiki", "w/", "index.php", "load.php")):
        return None
    path, _, frag = rest.partition("#")
    path = path.split("?")[0]
    title = unquote(path).replace("_", " ").strip()
    if not title or title.startswith(
        ("文件:", "File:", "分类:", "Category:", "特殊:", "Special:", "模板:", "Template:", "User:", "用户:")
    ):
        return None
    local = "/wiki/go/" + quote(title, safe="/")
    if frag:
        local += "#" + frag
    return local


def html_to_text(raw_html: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", "", raw_html, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", "", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|tr|h[1-6]|li|dt|dd|table|blockquote)>", "\n", text, flags=re.I)
    text = re.sub(r"<th[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<td[^>]*>", "  ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def display_name(raw: str, fallback: str) -> str:
    name = html_to_text(raw or "")
    return name or fallback


def rewrite_wiki_html(raw_html: str) -> str:
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", raw_html or "", flags=re.I | re.S)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.I | re.S)
    html = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", html, flags=re.I)
    html = re.sub(r"href=\"javascript:[^\"]*\"", "href=\"#\"", html, flags=re.I)

    def src_repl(match: re.Match) -> str:
        url = match.group(1)
        return f'src="{_english_image_url(url) or _abs_wiki_url(url)}"'

    def href_repl(match: re.Match) -> str:
        url = match.group(1)
        image = _english_image_url(url)
        if image and (_is_file_page(url) or "/mediawiki/images/" in _abs_wiki_url(url)):
            return f'href="{_zh_fallback_url(url) or image}"'
        local = _local_article_href(url)
        if local:
            return f'href="{local}"'
        return f'href="{_abs_wiki_url(url)}"'

    def srcset_repl(match: re.Match) -> str:
        parts = []
        for piece in match.group(1).split(","):
            piece = piece.strip()
            if not piece:
                continue
            bits = piece.split()
            bits[0] = _english_image_url(bits[0]) or _abs_wiki_url(bits[0])
            parts.append(" ".join(bits))
        return 'srcset="' + ", ".join(parts) + '"'

    html = re.sub(r'src="([^"]+)"', src_repl, html, flags=re.I)
    html = re.sub(r'srcset="([^"]+)"', srcset_repl, html, flags=re.I)
    html = re.sub(r'href="([^"]+)"', href_repl, html, flags=re.I)
    html = _responsive_map_boxes(html)
    return _add_zh_fallback(_point_pictures_at_files(html))


def _responsive_map_boxes(html: str) -> str:
    """把地点信息框里的固定像素地图改成可随屏宽缩放。"""

    def repl(match: re.Match) -> str:
        style = match.group(1) or ""
        pin_style = match.group(2) or ""
        pin_inner = match.group(3) or ""
        map_img = match.group(4) or ""
        width_match = re.search(r"width\s*:\s*(\d+)px", style, flags=re.I)
        height_match = re.search(r"height\s*:\s*(\d+)px", style, flags=re.I)
        width = int(width_match.group(1)) if width_match else 400
        height = int(height_match.group(1)) if height_match else 244
        top_match = re.search(r"top\s*:\s*([\d.]+)px", pin_style, flags=re.I)
        left_match = re.search(r"left\s*:\s*([\d.]+)px", pin_style, flags=re.I)
        top = float(top_match.group(1)) if top_match else 0.0
        left = float(left_match.group(1)) if left_match else 0.0
        box_style = (
            f"position:relative;width:100%;max-width:{width}px;"
            f"aspect-ratio:{width}/{height};height:auto"
        )
        new_pin = (
            f'top:{top / height * 100:.4f}%;left:{left / width * 100:.4f}%;'
            f"position:absolute;transform:translate(-50%,-50%)"
        )
        if "class=" in map_img:
            map_img = re.sub(r'\bclass="([^"]*)"', r'class="\1 map-bg"', map_img, count=1, flags=re.I)
        else:
            map_img = map_img.replace("<img", '<img class="map-bg"', 1)
        map_img = re.sub(r'\s(width|height)="\d+"', "", map_img, flags=re.I)
        return (
            f'<div class="mapcontainer" style="{box_style}">'
            f'<div class="maplocation" style="{new_pin}">{pin_inner}</div>'
            f"{map_img}</div>"
        )

    return re.sub(
        r'<div class="mapcontainer"\s+style="([^"]*)">\s*'
        r'<div class="maplocation"\s+style="([^"]*)">(.*?)</div>\s*'
        r'(<img\b[^>]*>)\s*</div>',
        repl,
        html,
        flags=re.I | re.S,
    )


def _english_image_url(url: str) -> str | None:
    """图片只取文件。中文维基和英文维基的图是同一批，统一用英文站的图片地址。"""
    absolute = _abs_wiki_url(html_lib.unescape(url or ""))
    path = absolute.split("?", 1)[0].split("#", 1)[0]
    marker = "/mediawiki/images/"
    index = path.find(marker)
    if index >= 0:
        return EN_WIKI + marker + path[index + len(marker) :]
    if not _is_file_page(absolute):
        return None
    name = unquote(path.rsplit("/", 1)[-1]).replace(" ", "_")
    for prefix in ("File:", "file:", "Image:", "image:", "文件:", "图像:"):
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    if not name:
        return None
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    return f"{EN_WIKI}{marker}{digest[0]}/{digest[:2]}/{quote(name, safe='()')}"


_ZH_SUFFIX = re.compile(r"_ZH(?=\.[A-Za-z0-9]+$)", re.I)


def _without_zh(filename: str) -> str | None:
    renamed = _ZH_SUFFIX.sub("", unquote(filename))
    if not renamed or renamed == unquote(filename):
        return None
    return renamed


def _image_file_url(filename: str) -> str:
    digest = hashlib.md5(filename.encode("utf-8")).hexdigest()
    return f"{EN_WIKI}/mediawiki/images/{digest[0]}/{digest[:2]}/{quote(filename, safe='()')}"


def _zh_fallback_url(url: str) -> str | None:
    """中文截图文件名带 _ZH，英文维基用去掉 _ZH 的同名文件。"""
    absolute = _english_image_url(url) or ""
    marker = "/mediawiki/images/"
    if marker not in absolute:
        return None
    tail = unquote(absolute.split(marker, 1)[1].split("?", 1)[0])
    parts = [part for part in tail.split("/") if part]
    if parts and parts[0] == "thumb" and len(parts) >= 4:
        filename = parts[3]
    elif len(parts) >= 3:
        filename = parts[-1]
    else:
        return None
    if "px-" in filename and filename.split("px-", 1)[0].isdigit():
        filename = filename.split("px-", 1)[1]
    plain = _without_zh(filename)
    if not plain:
        return None
    return _image_file_url(plain)


def _add_zh_fallback(html: str) -> str:
    def repl(match: re.Match) -> str:
        tag = match.group(0)
        src = re.search(r'\bsrc="([^"]+)"', tag, flags=re.I)
        if not src:
            return tag
        fallback = _zh_fallback_url(src.group(1))
        if not fallback:
            return tag
        tag = re.sub(r'\s+srcset="[^"]*"', "", tag, flags=re.I)
        handler = (
            "this.onerror=null;this.removeAttribute('srcset');this.src='"
            + fallback
            + "';var a=this.closest('a');if(a)a.href=this.src"
        )
        if tag.endswith("/>"):
            return tag[:-2] + f' onerror="{handler}" />'
        return tag[:-1] + f' onerror="{handler}">'

    return re.sub(r"<img\b[^>]*>", repl, html, flags=re.I)


def _is_file_page(url: str) -> bool:
    absolute = unquote(_abs_wiki_url(url)).split("?", 1)[0].split("#", 1)[0]
    name = absolute.rsplit("/", 1)[-1].replace("_", " ")
    lowered = name.lower()
    return lowered.startswith(("file:", "image:")) or name.startswith(("文件:", "图像:"))


def _point_pictures_at_files(html: str) -> str:
    """图片外层链接原本指向中文维基的文件页。官网打不开时，点图片就落到 503。改为直接打开这张图。"""

    def repl(match: re.Match) -> str:
        open_tag, inner, close = match.group(1), match.group(2), match.group(3)
        href = re.search(r'href="([^"]+)"', open_tag, flags=re.I)
        image = re.search(r'<img\b[^>]*\bsrc="([^"]+)"', inner, flags=re.I)
        if not href or not image or not _is_file_page(href.group(1)):
            return match.group(0)
        src = image.group(1)
        if "magnify" in unquote(src).lower():
            return match.group(0)
        start, end = href.start(1), href.end(1)
        return open_tag[:start] + src + open_tag[end:] + inner + close

    html = re.sub(r"(<a\b[^>]*>)(.*?)(</a>)", repl, html, flags=re.I | re.S)
    return _point_magnify_at_last_image(html)


def _point_magnify_at_last_image(html: str) -> str:
    """放大按钮里没有图片，只链到文件页。跟上前面那张已经能打开的图。"""
    last_src = ""

    def repl(match: re.Match) -> str:
        nonlocal last_src
        tag = match.group(0)
        if tag.lower().startswith("<img"):
            src = re.search(r'\bsrc="([^"]+)"', tag, flags=re.I)
            if src and "magnify" not in unquote(src.group(1)).lower():
                last_src = src.group(1)
            return tag
        href = re.search(r'href="([^"]+)"', tag, flags=re.I)
        if not href or not last_src or not _is_file_page(href.group(1)):
            return tag
        start, end = href.start(1), href.end(1)
        return tag[:start] + last_src + tag[end:]

    return re.sub(r"<img\b[^>]*>|<a\b[^>]*>.*?</a>", repl, html, flags=re.I | re.S)


def fetch_rendered_html(title: str) -> str:
    with _client() as client:
        data = client.get(
            WIKI_API,
            params={
                "action": "parse",
                "page": title,
                "prop": "text",
                "format": "json",
                "disableeditsection": "1",
                "disablelimitreport": "1",
                "uselang": "zh-cn",
            },
        ).json()
    raw = data.get("parse", {}).get("text", {}).get("*", "")
    return rewrite_wiki_html(raw)


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=120.0,
        follow_redirects=True,
        trust_env=False,
    )


def load_core_titles() -> list[str]:
    if not CORE_TITLES_FILE.exists():
        return []
    titles = []
    for line in CORE_TITLES_FILE.read_text(encoding="utf-8").splitlines():
        title = line.strip()
        if title and not title.startswith("#"):
            titles.append(title)
    return titles


def _wanted_title(title: str) -> bool:
    if not title or title.startswith(("Category:", "File:", "Template:", "分类:", "文件:", "模板:")):
        return False
    if title.startswith("模组:") and title != "模组:目录":
        return False
    head = title.split("/", 1)[0]
    if "/" in title and head in {"合作", "模组"}:
        return False
    return True


def _category_members(client: httpx.Client, category: str, cmtype: str) -> list[str]:
    titles: list[str] = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category if category.startswith("Category:") else f"Category:{category}",
        "cmtype": cmtype,
        "cmlimit": "500",
        "format": "json",
    }
    while True:
        data = client.get(WIKI_API, params=params).json()
        members = data.get("query", {}).get("categorymembers", [])
        titles.extend(m["title"] for m in members)
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
        params["cmcontinue"] = cont
        time.sleep(0.12)
    return titles


def walk_category_titles(client: httpx.Client, category: str, seen: set[str] | None = None) -> list[str]:
    seen = seen if seen is not None else set()
    key = category.removeprefix("Category:")
    if key in seen:
        return []
    seen.add(key)
    titles = [title for title in _category_members(client, key, "page") if _wanted_title(title)]
    for sub in _category_members(client, key, "subcat"):
        titles.extend(walk_category_titles(client, sub, seen))
    return titles


def collect_group_map(client: httpx.Client) -> dict[str, str]:
    groups: dict[str, str] = {}
    for category, group in CATEGORY_ROOTS:
        for title in walk_category_titles(client, category):
            groups[title] = group
    for title in all_hub_titles():
        groups[title] = hub_group(title) or groups.get(title) or "其他"
    for alias, target in ALIASES.items():
        if target in groups and alias not in groups:
            groups[alias] = groups[target]
    return groups


def resolve_titles(client: httpx.Client, titles: list[str]) -> list[str]:
    unique = []
    seen = set()
    for title in titles:
        if title not in seen:
            seen.add(title)
            unique.append(title)
    resolved: list[str] = []
    for i in range(0, len(unique), 40):
        batch = unique[i : i + 40]
        data = None
        for attempt in range(3):
            try:
                data = client.get(
                    WIKI_API,
                    params={
                        "action": "query",
                        "titles": "|".join(batch),
                        "redirects": "1",
                        "format": "json",
                    },
                ).json()
                break
            except httpx.HTTPError:
                time.sleep(1.0 * (attempt + 1))
        if not data:
            continue
        for page in data.get("query", {}).get("pages", {}).values():
            if page.get("missing") is not None:
                continue
            resolved.append(page["title"])
        time.sleep(0.1)
    return resolved


def _parse_page(client: httpx.Client, title: str) -> dict | None:
    params = {
        "action": "parse",
        "page": title,
        "prop": "text|displaytitle",
        "format": "json",
        "disableeditsection": "1",
        "disablelimitreport": "1",
        "uselang": "zh-cn",
    }
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            data = client.get(WIKI_API, params=params).json()
        except httpx.HTTPError as exc:
            last_error = exc
            time.sleep(1.0 * (attempt + 1))
            continue
        parsed = data.get("parse")
        if parsed and parsed.get("text", {}).get("*"):
            return parsed
        last_error = RuntimeError(data.get("error", {}).get("info") or "empty parse")
        time.sleep(0.6)
    if last_error:
        return None
    return None


def record_from_parse(parsed: dict) -> dict | None:
    wiki_title = parsed.get("title") or ""
    raw_html = parsed.get("text", {}).get("*", "") if isinstance(parsed.get("text"), dict) else ""
    if not wiki_title or not raw_html or not _wanted_title(wiki_title):
        return None
    body = html_to_text(raw_html)
    if len(body) < 40:
        return None
    summary = re.sub(r"\s+", " ", body)[:280].strip()
    display_title = ZH_TITLES.get(wiki_title) or display_name(parsed.get("displaytitle", ""), wiki_title)
    return {
        "pageid": parsed["pageid"],
        "title": wiki_title,
        "display_title": display_title,
        "category": category_of(display_title or wiki_title, body),
        "url": f"{WIKI_SITE}/{quote(wiki_title)}",
        "summary": summary,
        "body": body[:400000],
        "html": rewrite_wiki_html(raw_html),
        "lastrevid": parsed.get("revid"),
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def fetch_pages(client: httpx.Client, titles: list[str], *, resolve: bool = True) -> list[dict]:
    pages: list[dict] = []
    ready = resolve_titles(client, titles) if resolve else titles
    for title in ready:
        parsed = _parse_page(client, title)
        if not parsed:
            continue
        record = record_from_parse(parsed)
        if record:
            pages.append(record)
        time.sleep(0.12)
    return pages


# 这三页的数据表能拆成物品行。category 用英文，subcategory 沿用维基小节标题。
_ENTITY_PAGES = {
    "武器": "Weapon",
    "鱼": "Fish",
    "友谊": "Villager",
}
_ENTITY_SKIP_SECTIONS = {
    "目录", "参考", "历史", "趣闻", "小故障",
    "武器属性", "弹弓弹药", "武器等级和卖出价格",
    "食谱", "生鱼片利润", "钓鱼小游戏", "运动模式",
}
_SKIP_COLUMNS = {"图片", "图像"}


def _cell_text(fragment: str) -> str:
    fragment = re.sub(
        r"<span\b[^>]*display\s*:\s*none[^>]*>.*?</span>",
        " ",
        fragment,
        flags=re.I | re.S,
    )
    fragment = re.sub(r"<br\s*/?>", " ", fragment, flags=re.I)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    fragment = html_lib.unescape(fragment).replace("\xa0", " ")
    return re.sub(r"\s+", " ", fragment).strip()


def _top_level_pieces(html: str, tag: str) -> list[tuple[str, str]]:
    """返回某一标签的最外层片段，单元格里的嵌套表格不会被拆开。"""
    pattern = re.compile(rf"<(/?){tag}\b([^>]*)>", re.I)
    depth = 0
    start = None
    attrs = ""
    pieces: list[tuple[str, str]] = []
    for match in pattern.finditer(html):
        closing = match.group(1) == "/"
        if not closing:
            if depth == 0:
                start = match.end()
                attrs = match.group(2)
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0 and start is not None:
                pieces.append((attrs, html[start:match.start()]))
                start = None
    return pieces


def _headed_tables(html: str) -> list[tuple[str, str, str]]:
    token = re.compile(r"<(/?)(h[1-6]|table)\b([^>]*)>", re.I)
    heading = ""
    depth = 0
    start = None
    attrs = ""
    found: list[tuple[str, str, str]] = []
    for match in token.finditer(html):
        closing = match.group(1) == "/"
        kind = match.group(2).lower()
        if kind.startswith("h"):
            if closing or depth:
                continue
            close = re.search(r"</h[1-6]>", html[match.end():], re.I)
            if close:
                heading = _cell_text(html[match.end(): match.end() + close.start()])
            continue
        if not closing:
            if depth == 0:
                start = match.end()
                attrs = match.group(3)
            depth += 1
        elif depth:
            depth -= 1
            if depth == 0 and start is not None:
                found.append((heading, attrs, html[start:match.start()]))
                start = None
    return found


def _row_cells(row_html: str) -> list[tuple[str, str]]:
    token = re.compile(r"<(/?)(th|td|table)\b([^>]*)>", re.I)
    table_depth = 0
    cell_depth = 0
    start = None
    kind = ""
    cells: list[tuple[str, str]] = []
    for match in token.finditer(row_html):
        closing = match.group(1) == "/"
        tag = match.group(2).lower()
        if tag == "table":
            if not closing:
                table_depth += 1
            elif table_depth:
                table_depth -= 1
            continue
        if table_depth:
            continue
        if not closing:
            if cell_depth == 0:
                start = match.end()
                kind = tag
            cell_depth += 1
        elif cell_depth:
            cell_depth -= 1
            if cell_depth == 0 and start is not None:
                cells.append((kind, _cell_text(row_html[start:match.start()])))
                start = None
    return cells


def _table_records(inner: str) -> tuple[list[str], list[dict[str, str]]]:
    headers: list[str] = []
    records: list[dict[str, str]] = []
    for _attrs, row_html in _top_level_pieces(inner, "tr"):
        cells = _row_cells(row_html)
        if not cells:
            continue
        texts = [text for _tag, text in cells]
        tags = {tag for tag, _text in cells}
        if not headers and tags == {"th"}:
            headers = texts
            continue
        if not headers:
            continue
        row: dict[str, str] = {}
        for index, text in enumerate(texts):
            if index >= len(headers) or not text:
                continue
            header = headers[index]
            if not header or header in _SKIP_COLUMNS:
                continue
            row[header] = text
        if row:
            records.append(row)
    return headers, records


def _entity_table(category: str, heading: str, headers: list[str]) -> bool:
    if heading in _ENTITY_SKIP_SECTIONS:
        return False
    if category == "Weapon":
        return "名称" in headers and "伤害" in headers
    if category == "Fish":
        return "名称" in headers and bool({"价格", "售出价格"} & set(headers))
    if category == "Villager":
        return "村民" in headers and "最爱" in headers
    return False


def entities_from_page(page: dict) -> list[dict]:
    category = _ENTITY_PAGES.get(page.get("title") or "")
    html = page.get("html") or ""
    if not category or not html:
        return []
    entities: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for heading, attrs, inner in _headed_tables(html):
        if "wikitable" not in attrs.lower():
            continue
        headers, records = _table_records(inner)
        if not _entity_table(category, heading, headers):
            continue
        name_header = "村民" if category == "Villager" else "名称"
        subcategory = None if category == "Villager" else (heading or None)
        for record in records:
            name = record.get(name_header, "").strip()
            if not name or name == name_header or len(name) > 40:
                continue
            key = (subcategory or "", name)
            if key in seen:
                continue
            seen.add(key)
            attributes = {key: value for key, value in record.items() if key != name_header}
            entities.append({
                "name": name,
                "category": category,
                "subcategory": subcategory,
                "attributes": json.dumps(attributes, ensure_ascii=False, separators=(",", ":")) if attributes else None,
            })
    return entities


def _store_page_entities(conn, pages: list[dict]) -> int:
    grouped: dict[str, list[dict]] = {}
    for page in pages:
        rows = entities_from_page(page)
        if rows:
            grouped[rows[0]["category"]] = rows
    stored = 0
    for category, rows in grouped.items():
        conn.execute("DELETE FROM wiki_entities WHERE category = ?", (category,))
        conn.executemany(
            """
            INSERT INTO wiki_entities (name, category, subcategory, attributes)
            VALUES (:name, :category, :subcategory, :attributes)
            """,
            rows,
        )
        stored += len(rows)
    return stored


def upsert_pages(pages: list[dict]) -> int:
    conn = connect()
    for p in pages:
        conn.execute(
            """
            INSERT INTO wiki_pages (pageid, title, display_title, category, url, summary, body, html, lastrevid, synced_at)
            VALUES (:pageid, :title, :display_title, :category, :url, :summary, :body, :html, :lastrevid, :synced_at)
            ON CONFLICT(pageid) DO UPDATE SET
                title=excluded.title,
                display_title=excluded.display_title,
                category=excluded.category,
                url=excluded.url,
                summary=excluded.summary,
                body=excluded.body,
                html=excluded.html,
                lastrevid=excluded.lastrevid,
                synced_at=excluded.synced_at
            """,
            p,
        )
    _store_page_entities(conn, pages)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0]
    conn.close()
    return count


def save_parsed_batch(items: list[dict]) -> int:
    pages = [record for item in items if (record := record_from_parse(item))]
    if not pages:
        return 0
    upsert_pages(pages)
    return len(pages)


def mark_synced() -> None:
    conn = connect()
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('last_sync', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
    )
    conn.commit()
    conn.close()


def incomplete_layout_titles() -> list[str]:
    conn = connect()
    rows = conn.execute(
        """
        SELECT title FROM wiki_pages
        WHERE html IS NULL
           OR html = ''
           OR length(html) = 400000
           OR instr(html, 'mw-parser-output') = 0
        ORDER BY title
        """
    ).fetchall()
    conn.close()
    return [row["title"] for row in rows]


def refresh_incomplete_layouts() -> dict:
    titles = incomplete_layout_titles()
    if not titles:
        return {"fetched": 0, "total": wiki_count(), "missing": []}
    with _client() as client:
        pages = fetch_pages(client, titles)
    total = upsert_pages(pages)
    got = {page["title"] for page in pages}
    return {
        "fetched": len(pages),
        "total": total,
        "missing": [title for title in titles if title not in got],
    }


def sync_official_wiki() -> dict:
    titles = load_core_titles()
    with _client() as client:
        try:
            groups = collect_group_map(client)
        except httpx.HTTPError:
            groups = {title: hub_group(title) or "其他" for title in all_hub_titles()}
        set_group_map(groups)
        titles.extend(groups)
        pages = fetch_pages(client, titles)
    total = upsert_pages(pages) if pages else wiki_count()
    conn = connect()
    for en_title, zh_title in ZH_TITLES.items():
        conn.execute(
            "UPDATE wiki_pages SET display_title=? WHERE title=?",
            (zh_title, en_title),
        )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('last_sync', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
    )
    conn.commit()
    conn.close()
    recategorize_all()
    clear_title_cache()
    return {"fetched": len(pages), "total": total, "source": WIKI_SITE}


def wiki_count() -> int:
    conn = connect()
    n = conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0]
    conn.close()
    return n


def last_sync() -> str | None:
    conn = connect()
    row = conn.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
    conn.close()
    return row["value"] if row else None


def count_pages(q: str | None = None, category: str | None = None) -> int:
    if q:
        return len(list_pages(q=q, category=category, limit=100000))
    conn = connect()
    if category:
        n = conn.execute("SELECT COUNT(*) FROM wiki_pages WHERE category=?", (category,)).fetchone()[0]
    else:
        n = conn.execute("SELECT COUNT(*) FROM wiki_pages").fetchone()[0]
    conn.close()
    return n


def list_pages(q: str | None = None, category: str | None = None, limit: int = 40, offset: int = 0) -> list:
    conn = connect()
    if q:
        where = []
        args: list = []
        if category:
            where.append("category = ?")
            args.append(category)
        sql = """
            SELECT pageid, title, COALESCE(NULLIF(display_title, ''), title) AS display_title,
                   category, url, summary, body
            FROM wiki_pages
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        ranked = rank_pages(q, rows, limit + offset)
        for item in ranked:
            item.pop("body", None)
        return ranked[offset : offset + limit]
    where = []
    args = []
    if category:
        where.append("category = ?")
        args.append(category)
    sql = """
        SELECT pageid, title, COALESCE(NULLIF(display_title, ''), title) AS display_title,
               category, url, summary
        FROM wiki_pages
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(NULLIF(display_title, ''), title) LIMIT ? OFFSET ?"
    args.extend([limit, offset])
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return rows


def grouped_pages(per_category: int = 6) -> dict[str, list]:
    groups = {name: [] for name in CATEGORY_ORDER}
    conn = connect()
    rows = conn.execute(
        """
        SELECT pageid, title, COALESCE(NULLIF(display_title, ''), title) AS display_title,
               category, url, summary
        FROM wiki_pages
        ORDER BY COALESCE(NULLIF(display_title, ''), title)
        """
    ).fetchall()
    conn.close()
    for row in rows:
        cat = row["category"] or "其他"
        if cat not in groups:
            cat = "其他"
        if len(groups[cat]) < per_category:
            groups[cat].append(row)
    return {k: v for k, v in groups.items() if v}


def get_pages_named(names: list[str]) -> list[dict]:
    conn = connect()
    found: list[dict] = []
    seen: set[int] = set()
    for name in names:
        wanted = resolve_title(name)
        for key in (wanted, name):
            if not key:
                continue
            row = conn.execute(
                """
                SELECT pageid, title, COALESCE(NULLIF(display_title, ''), title) AS display_title,
                       category, url, summary, body
                FROM wiki_pages
                WHERE title = ? OR display_title = ?
                """,
                (key, key),
            ).fetchone()
            if row and int(row["pageid"]) not in seen:
                found.append(dict(row))
                seen.add(int(row["pageid"]))
                break
    conn.close()
    return found


def pages_mentioned_in(question: str) -> list[dict]:
    conn = connect()
    rows = conn.execute(
        """
        SELECT pageid, title, COALESCE(NULLIF(display_title, ''), title) AS display_title,
               category, url, summary, body
        FROM wiki_pages
        """
    ).fetchall()
    conn.close()
    # 先把口语别名换成条目名，便于「三文鱼」对上「鲑鱼」。
    expanded = expand_question_aliases(question)
    found = []
    for row in rows:
        name = row["display_title"] or row["title"]
        if title_mentioned(name, question) or title_mentioned(name, expanded):
            found.append(dict(row))
    found.sort(key=lambda item: len(item["display_title"] or item["title"]), reverse=True)
    return found

def retrieve_for_question(question: str, limit: int = 10) -> list[dict]:
    extra_names: list[str] = []
    for keywords, titles in TOPIC_EXPAND:
        if any(key in question for key in keywords):
            extra_names.extend(titles)
    prefer_named = bool(extra_names)
    result: list[dict] = []
    seen: set[int] = set()
    for page in pages_mentioned_in(question) + get_pages_named(extra_names):
        if page["pageid"] in seen:
            continue
        result.append(page)
        seen.add(page["pageid"])
    skip_people = prefer_named and not any(k in question for k in ("礼物", "喜好", "送礼", "结婚", "村民"))
    for hit in search_wiki(question, limit=30):
        if hit["pageid"] in seen:
            continue
        if skip_people and classify_page(hit.get("display_title") or hit["title"], hit.get("body") or "") == "人物":
            continue
        result.append(hit)
        seen.add(hit["pageid"])
        if len(result) >= limit:
            break
    return result[:limit]


def resolve_title(title: str) -> str:
    cleaned = unquote(title or "").replace("_", " ").strip()
    return ALIASES.get(cleaned, cleaned)


def get_page_by_title(title: str):
    wanted = resolve_title(title)
    conn = connect()
    row = conn.execute(
        """
        SELECT pageid FROM wiki_pages
        WHERE title=? OR display_title=? OR title=? OR display_title=?
        """,
        (wanted, wanted, title, title),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return get_page(int(row["pageid"]))


def relink_stored_html() -> int:
    conn = connect()
    rows = conn.execute("SELECT pageid, html FROM wiki_pages WHERE html IS NOT NULL AND html != ''").fetchall()
    changed = 0
    for row in rows:
        html = rewrite_wiki_html(row["html"] or "")
        if html != (row["html"] or ""):
            conn.execute("UPDATE wiki_pages SET html=? WHERE pageid=?", (html, row["pageid"]))
            changed += 1
    conn.commit()
    conn.close()
    return changed


def get_page(pageid: int):
    conn = connect()
    row = conn.execute("SELECT * FROM wiki_pages WHERE pageid=?", (pageid,)).fetchone()
    conn.close()
    if not row:
        return None
    data = dict(row)
    html = data.get("html") or ""
    if "mw-parser-output" not in html:
        data["html"] = ""
    else:
        data["html"] = rewrite_wiki_html(html)
    return data


_QUERY_STOPS = (
    "为什么",
    "哪些",
    "哪个",
    "哪里",
    "怎么",
    "如何",
    "什么",
    "请问",
    "可以",
    "一下",
    "这个",
    "那个",
    "还有",
    "是否",
    "吗",
    "呢",
    "吧",
    "的",
    "了",
    "更快",
    "比较",
    "获取",
    "获得",
    "得到",
    "拿到",
    "入手",
    "喜欢",
    "是",
    # 价格/数量问法，避免「苹果售价多少」整段粘在一起对不上条目名。
    "多少钱",
    "多少金",
    "卖多少",
    "卖钱",
    "售价",
    "价格",
    "多贵",
    "多少种",
    "有多少种",
    "有几种",
    "多少",
    "几种",
    "有几",
    # 来源/地点问法，避免「香蕉从哪获得」粘成「香蕉从哪」。
    "从哪里",
    "从哪儿",
    "从哪",
    "在哪里",
    "在哪儿",
    "在哪",
    "哪里",
    "哪儿",
    "何处",
)

_TERM_EDGES = set("有在和与或把被给对从到很也都还就不没个些着过吗呢吧")
# 短问句里再按虚词切开，避免「香蕉从哪获得」整段粘连。
_SOFT_SPLITS = set("的了吗呢吧啊呀嘛着过和与或把被给对从到在向往比很也还就不没个些有是于跟")

_title_cache: list[str] | None = None
_title_set_cache: set[str] | None = None


def _trim_term(term: str) -> str:
    while len(term) > 2 and term[-1] in _TERM_EDGES:
        term = term[:-1]
    while len(term) > 2 and term[0] in _TERM_EDGES:
        term = term[1:]
    return term


def clear_title_cache() -> None:
    global _title_cache, _title_set_cache
    _title_cache = None
    _title_set_cache = None


def _wiki_title_list() -> list[str]:
    """缓存站内条目标题，短问句用来精确拆词。"""
    global _title_cache, _title_set_cache
    if _title_cache is not None:
        return _title_cache
    conn = connect()
    rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(display_title, ''), title) AS name
        FROM wiki_pages
        """
    ).fetchall()
    conn.close()
    titles = []
    for row in rows:
        name = (row["name"] or "").strip()
        # 含「鱼」这类单字条目；过长标题不参与拆词。
        if 1 <= len(name) <= 12 and re.fullmatch(r"[\u4e00-\u9fffA-Za-z0-9·：:]+", name):
            titles.append(name)
    titles.sort(key=len, reverse=True)
    _title_cache = titles
    _title_set_cache = set(titles)
    return titles


def _wiki_title_set() -> set[str]:
    if _title_set_cache is None:
        _wiki_title_list()
    return _title_set_cache or set()


def _titles_in_text(text: str, limit: int = 6) -> list[str]:
    found: list[str] = []
    for title in _wiki_title_list():
        if title not in text:
            continue
        if any(title != other and title in other for other in found):
            continue
        found.append(title)
        if len(found) >= limit:
            break
    return found


def _soft_split_chunks(text: str) -> list[str]:
    chunks: list[str] = []
    buf: list[str] = []
    for ch in text:
        if ch in _SOFT_SPLITS or not (ch.isalnum() or "\u4e00" <= ch <= "\u9fff"):
            if buf:
                chunks.append("".join(buf))
                buf.clear()
            continue
        buf.append(ch)
    if buf:
        chunks.append("".join(buf))
    return chunks


def _fine_pieces(chunk: str) -> list[str]:
    """长碎片优先拆出其中的 Wiki 标题；没有标题才保留短前缀。"""
    chunk = _trim_term(chunk)
    if not chunk:
        return []
    titled = _titles_in_text(chunk, limit=4)
    if titled:
        return titled
    if len(chunk) == 1:
        return [chunk] if chunk in _wiki_title_set() else []
    if len(chunk) <= 4:
        return [chunk]
    # 仍偏长时只留前 2～4 字试探，避免「钓上来三文鱼」切出一堆废词。
    return [chunk[:4], chunk[:3], chunk[:2]]


def content_terms(q: str) -> list[str]:
    """把短问句拆细：先抓 Wiki 条目标题，再按停用词/虚词切开。"""
    raw_q = (q or "").strip()
    terms: list[str] = []
    known = _wiki_title_set()

    def add(term: str) -> None:
        term = _trim_term(term)
        if not term or term in terms:
            return
        if not (len(term) >= 2 or term in known):
            return
        # 丢掉已被更长词覆盖的短词，如「阿比盖尔」下的「盖尔」。
        if any(term != other and term in other for other in terms):
            return
        terms[:] = [other for other in terms if not (other != term and other in term)]
        terms.append(term)

    # 1) 问句里直接出现的条目名（最长优先）
    for title in _titles_in_text(raw_q):
        add(title)

    # 2) 停用词挖掉后，再按虚词细切
    text = raw_q
    for word in sorted(_QUERY_STOPS, key=len, reverse=True):
        text = text.replace(word, " ")
    for chunk in _soft_split_chunks(text):
        for piece in _fine_pieces(chunk):
            add(piece)

    # 3) 口语别名
    for alias, canon in ALIASES.items():
        if alias in raw_q:
            add(canon)

    if not terms:
        compact = "".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", raw_q))
        if len(compact) >= 2:
            add(compact[:8])
    return terms[:12]

def _saturated(text: str, term: str, weight: float) -> float:
    found = text.count(term)
    if not found:
        return 0.0
    return weight * len(term) * (found * 2.2) / (found + 1.6)


def _about_bonus(body: str, term: str) -> float:
    """A line that is the term itself, then the words right after it, outranks a passing mention."""
    bonus = 0.0
    loose = 0
    pattern = re.compile(rf"(?m)^[ \t\d.]*{re.escape(term)}[ \t]*$")
    for match in pattern.finditer(body):
        window = body[match.end() : match.end() + 400]
        if any(mark in window for mark in ("种子", "售价", "一种", "共：", "生日", "居住于", "描述")):
            bonus += 520
        else:
            loose += 1
    return bonus + min(loose, 2) * 36


def _leftover_is_particle(longer: str, shorter: str) -> bool:
    rest = longer.replace(shorter, "", 1)
    return bool(rest) and all(ch in "在是的了吗呢吧与和或有" or ch.isspace() for ch in rest)


# 「怎么钓上来鲑鱼」「苹果售价多少」里，标题外多出来的动作/问法词不算误匹配。
_TITLE_ACTION = set("钓抓捕捞买卖种种找获用给把被对从上下到来到")
_TITLE_ASK_TAILS = (
    "售价", "价格", "多少钱", "多少金", "卖多少", "卖钱", "多贵", "多少",
    "怎么钓", "怎么抓", "怎么获得", "怎么种", "从哪里", "从哪儿", "从哪",
    "在哪里", "在哪儿", "在哪", "哪里", "哪儿", "获得", "获取", "得到",
)

def _leftover_is_noise(longer: str, shorter: str) -> bool:
    rest = longer.replace(shorter, "", 1)
    if not rest:
        return True
    for tail in sorted(_TITLE_ASK_TAILS, key=len, reverse=True):
        rest = rest.replace(tail, "")
    if not rest.strip():
        return True
    if all(ch in "在是的了吗呢吧与和或有" or ch.isspace() for ch in rest):
        return True
    if all(ch in _TITLE_ACTION or ch.isspace() for ch in rest):
        return True
    return False

def expand_question_aliases(question: str) -> str:
    text = question or ""
    for alias, canon in sorted(ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if alias in text and canon not in text:
            text = text.replace(alias, canon)
    return text


def title_mentioned(name: str, question: str) -> bool:
    if not name or len(name) < 2:
        return False
    text = question or ""
    # 别名直接点名条目时也算提到了（三文鱼 → 鲑鱼）。
    if name not in text:
        if not any(alias in text and canon == name for alias, canon in ALIASES.items()):
            return False
        text = expand_question_aliases(text)
        if name not in text:
            return False
    for term in content_terms(question) + content_terms(expand_question_aliases(question)):
        if term == name:
            return True
        if len(term) > len(name) and name in term and not _leftover_is_noise(term, name):
            return False
    return True

def focus_terms(q: str, names: list[str] | None = None) -> list[str]:
    terms = content_terms(q)
    titled: list[str] = []
    for name in names or []:
        if title_mentioned(name, q or "") and name not in titled:
            titled.append(name)
    titled.sort(key=len, reverse=True)
    kept: list[str] = []
    for name in titled:
        if any(name != other and name in other for other in kept):
            continue
        kept.append(name)
    merged = kept + [
        term
        for term in terms
        if term not in kept and not any(name != term and name in term and not _leftover_is_particle(term, name) for name in kept)
    ]
    return merged[:8]


def rank_pages(q: str, rows, limit: int) -> list[dict]:
    names: list[str] = []
    for row in rows:
        item = dict(row)
        name = item.get("display_title") or item.get("title") or ""
        if name and name not in names:
            names.append(name)
    terms = focus_terms(q, names)
    if not terms:
        return []
    scored: list[tuple[int, float, str, dict]] = []
    for row in rows:
        item = dict(row)
        title = item.get("title") or ""
        shown = item.get("display_title") or title
        body = (item.get("body") or "").replace("\xa0", " ")
        matched = 0
        score = 0.0
        for term in terms:
            title_text = shown if shown == title else f"{shown}\n{title}"
            part = _saturated(title_text, term, 14) + _saturated(body, term, 4)
            part += _about_bonus(body, term)
            if shown == term or title == term:
                part += 800
            elif term in shown or term in title:
                part += 120
            if part:
                matched += 1
                score += part
        if score <= 0:
            continue
        score *= 0.4 + 0.6 * (matched / len(terms))
        # 标题本身就是问句里的词时，排在只是正文里提到这个词的页前面。
        exact = 1 if shown in terms or title in terms else 0
        scored.append((exact, score, shown, item))
    scored.sort(key=lambda pair: (-pair[0], -pair[1], pair[2]))
    return [item for _, _, _, item in scored[:limit]]


def search_wiki(q: str, limit: int = 6) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    conn = connect()
    rows = conn.execute(
        """
        SELECT pageid, title, COALESCE(NULLIF(display_title, ''), title) AS display_title,
               category, url, summary, body
        FROM wiki_pages
        """
    ).fetchall()
    conn.close()
    expanded = expand_question_aliases(q)
    hits = rank_pages(expanded, rows, limit)
    if hits or expanded == q:
        return hits
    return rank_pages(q, rows, limit)