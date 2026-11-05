from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

from .wiki import (
    TOPIC_EXPAND,
    content_terms,
    focus_terms,
    get_pages_named,
    pages_mentioned_in,
    search_wiki,
    title_mentioned,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GAME_HINTS = (
    "怎么", "如何", "什么", "哪些", "哪里", "谁", "为什么", "哪", "吗", "么",
    "第一年", "开局", "新手", "赚钱", "利润", "金币", "作物", "种子", "村民",
    "礼物", "喜好", "送礼", "结婚", "钓鱼", "采矿", "采集", "战斗", "农场",
    "矿井", "社区中心", "节日", "动物", "鸡", "牛", "温室", "果树", "姜岛",
    "阿比盖尔", "彭妮", "海莉", "玛鲁", "艾米丽", "莉亚", "山姆", "塞巴斯蒂安",
    "亚历克斯", "哈维", "埃利奥特", "艾利欧特", "谢恩", "马龙", "wiki", "星露谷",
    "卖", "多少", "价格", "售价", "钱",
)

_MONEY_KEYS = ("赚钱", "利润", "收入", "金币", "发财", "售价", "多少钱")
_GIFT_KEYS = ("礼物", "喜好", "送礼", "喜欢", "最爱", "讨厌", "不喜欢")
_GIFT_SKIP = {
    "图片", "名称", "描述", "来源", "材料", "最爱", "喜欢", "一般", "不喜欢", "讨厌",
    "采矿", "钓鱼", "耕种", "采集", "战斗", "烹饪", "锻造", "礼物",
    "所有普遍最爱的礼物", "所有普遍喜欢的礼物", "所有普遍讨厌的礼物",
}


def _prefer_ollama() -> bool:
    """ASK_PREFER_OLLAMA=1 时必须先走本地 Ollama；只有模型不可用才回规则。默认关闭。"""
    return os.getenv("ASK_PREFER_OLLAMA", "0").strip().lower() in {"1", "true", "yes", "on"}


def _ollama_chat_ready() -> bool:
    try:
        from .rag import _model_ready, chat_model_name, ollama_model_names

        names = ollama_model_names()
        return bool(names) and _model_ready(names, chat_model_name())
    except Exception:
        return False


def _try_rag(question: str) -> dict | None:
    try:
        from .rag import answer_with_langchain

        return answer_with_langchain(question)
    except Exception:
        return None


def answer_question(question: str, offset: int = 0) -> dict:
    question = question.strip()
    off = _off_topic_reply(question)
    if off:
        return {"answer": off, "sources": [], "mode": "off"}

    prefer = _prefer_ollama()
    if prefer and _ollama_chat_ready():
        rag = _try_rag(question)
        if rag and rag.get("mode") == "langchain" and "正在把 Wiki" in (rag.get("answer") or ""):
            return rag
        if rag:
            grounded = _ground_rag_with_wiki(question, rag)
            # 纠正地点胡编时仍优先保留 Ollama 润色结果。
            if grounded and grounded.get("mode") in {"ollama", "langchain"}:
                return grounded
            if grounded is None:
                return rag
            if rag:
                return rag
        # RAG 没成：仍把 Wiki 原文要点交给 Ollama，避免只回 ai.py 兜底句。
        pages = _pages_for_answer(question)
        if not pages:
            pages = get_pages_named(content_terms(question))
        notes, used = _think(question, pages)
        if not notes and pages:
            notes, used = _notes_from_pages(question, pages)
        if notes:
            polished = _ollama_answer(question, notes)
            if polished:
                return {"answer": polished, "sources": _sources(used), "mode": "ollama"}
            return {"answer": notes, "sources": _sources(used), "mode": "wiki"}
        return {
            "answer": "本地模型开着，但 Wiki 里没检索到能回答这句的原文。换个作物、村民或地点名称再问。",
            "sources": [],
            "mode": "off",
        }
    catalog = _catalog_answer(question)
    if catalog:
        return catalog

    planted = _planting_answer(question)
    if planted:
        return planted

    if not prefer:
        rag = _try_rag(question)
        if rag:
            return rag

    return _wiki_fallback(question)

_PLACE_WORDS = (
    "海洋", "河流", "瀑布", "湖泊", "矿井", "下水道", "秘密森林", "沙漠", "姜岛", "农场", "夜市",
)


def _place_words_in(text: str) -> set[str]:
    return {word for word in _PLACE_WORDS if word in (text or "")}


def _notes_from_pages(question: str, pages: list[dict]) -> tuple[str, list[dict]]:
    """规则摘句失败时，仍把专页里和问题相关的句子交给 Ollama。"""
    if not pages:
        return "", []
    page = pages[0]
    name = _page_name(page)
    sentences = _wiki_sentences(_body(page))
    terms = [term for term in content_terms(question) if term != name] or content_terms(question)
    hints = ("获得", "得到", "可以在", "来源", "种子", "生长", "成熟", "售价", "钓到", "掉落", "购买")
    picked: list[str] = []
    for sentence in sentences:
        if name and name in sentence and any(hint in sentence for hint in hints):
            picked.append(sentence)
        elif any(term in sentence for term in terms) and any(hint in sentence for hint in hints):
            picked.append(sentence)
        if len(picked) >= 3:
            break
    if not picked:
        picked = sentences[:3]
    if not picked:
        return "", []
    return _ask_line(question) + "\n" + "\n".join(picked), [page]

def _wiki_fallback(question: str) -> dict:
    pages = _pages_for_answer(question)
    notes, used = _think(question, pages)
    if not notes:
        return {
            "answer": "Wiki 里没有读到和这句话对得上的内容。换个说法，或写上具体的作物、村民、地点。",
            "sources": [],
            "mode": "off",
        }

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if api_key:
        try:
            generated = _llm_answer(question, notes, api_key)
            return {"answer": generated, "sources": _sources(used), "mode": "llm"}
        except Exception:
            pass

    polished = _ollama_answer(question, notes)
    if polished:
        return {"answer": polished, "sources": _sources(used), "mode": "ollama"}

    return {"answer": notes, "sources": _sources(used), "mode": "wiki"}


def _ground_rag_with_wiki(question: str, rag: dict) -> dict | None:
    """Ollama 若把地图索引里的邻近地点也算进去，改用 Wiki 摘句 + Ollama 润色。"""
    answer = rag.get("answer") or ""
    if not any(key in question for key in ("钓", "抓", "怎么", "如何", "哪里", "获得", "获取")):
        return None
    pages = _pages_for_answer(question)
    notes, used = _think(question, pages)
    if not notes or not used:
        return None
    extra = _place_words_in(answer) - _place_words_in(notes)
    if not extra:
        return None
    polished = _ollama_answer(question, notes)
    if polished:
        return {"answer": polished, "sources": _sources(used), "mode": "ollama"}
    # 优先 Ollama 时不把未润色的规则句当成“已纠正”。
    return None

def _off_topic_reply(question: str) -> str | None:
    compact = re.sub(r"[\s\W_]+", "", question, flags=re.UNICODE)
    if len(question) < 2 or len(compact) < 2:
        return "请把问题写完整，例如「第一年怎么赚钱」或某个村民的名字。"
    if len(compact) >= 4 and len(set(compact.lower())) <= 2:
        return "这串字我看不出在问什么。说一件星露谷里的具体事就好，比如种田、送礼或下矿。"
    if re.fullmatch(r"[a-zA-Z0-9]{1,16}", compact) and compact.lower() not in {"stardew", "wiki", "joja"}:
        return "我只回答星露谷物语相关的问题。用中文说说你想查什么吧。"
    if not re.search(r"[\u4e00-\u9fff]", question) and not any(k.lower() in question.lower() for k in GAME_HINTS):
        return "这不太像星露谷的问题。可以问攻略、村民、作物或地点。"
    return None


def _sources(hits: list[dict]) -> list[dict]:
    return [
        {
            "title": h.get("display_title") or h["title"],
            "url": h["url"],
            "pageid": h["pageid"],
        }
        for h in hits[:6]
    ]


_TOPIC_PAGES = (
    ("春季作物", "农作物"),
    ("夏季作物", "农作物"),
    ("秋季作物", "农作物"),
    ("冬季作物", "农作物"),
    ("农作物", "农作物"),
    ("作物", "农作物"),
    ("果树", "果树"),
    ("水果", "水果"),
    ("村民", "居民"),
    ("居民", "居民"),
    ("节日", "节日"),
    ("怪物", "怪物"),
    ("动物", "动物"),
    ("家畜", "动物"),
    ("禽类", "动物"),
    ("鱼类", "鱼"),
    ("鱼", "鱼"),
)
_SECTION_SKIP = {"提示", "历史", "参考", "成就", "外部链接", "漏洞", "目录", "概览", "策略", "趣闻", "工匠物品"}
# 目录里常见的“机制小节”，不是可数的实体种类。
_KIND_SECTION_SKIP = {"照顾动物", "提示", "历史", "参考", "外部链接", "策略", "趣闻", "成就"}
_KIND_NAME_SKIP = {
    "最爱", "喜欢", "一般", "不喜欢", "讨厌", "日程", "家庭", "人际关系",
    "新农场", "额外宠物", "好感度", "可能的礼物", "友谊", "心情", "产出", "居住处", "食物",
    "动物被困在外面", "野生动物袭击", "动物的生育",
    "所有战斗等级", "危险怪物", "怪物列表", "怪物",
}
_KIND_NAME_MARKS = ("好感", "友谊", "心情", "产出", "居住", "食物", "礼物", "袭击", "生育", "被困", "战斗等级", "攻略")


def _summary_mode(question: str) -> str | None:
    if any(key in question for key in ("多少钱", "多少金", "售价", "价格", "多贵")) and "多少种" not in question and "几种" not in question:
        return None
    if any(
        key in question
        for key in (
            "多少种", "几种", "有几种", "有多少", "总共", "共有", "一共有",
            "能钓多少", "可以钓多少", "钓上多少", "能抓多少", "可以抓多少",
        )
    ):
        return "count"
    if any(
        key in question
        for key in (
            "哪些", "有哪些", "都有什么", "有什么", "列出",
            "可以钓", "能钓", "钓什么", "钓哪些", "能抓", "可以抓", "抓什么",
        )
    ):
        return "list"
    if re.search(r"(什么|哪些)[\u4e00-\u9fff]{1,6}", question) and _topic_page(question):
        return "list"
    if any(key in question for key in ("总结", "概括", "介绍一下", "简介")):
        return "overview"
    return None


def _topic_page(question: str) -> str | None:
    for key, page in _TOPIC_PAGES:
        if key == "鱼":
            if re.search(r"(?<!钓)鱼(?![竿饵店缸])", question):
                return page
            continue
        if key in question:
            return page
    if re.search(r"钓(?![鱼竿饵])", question) and not any(key in question for key in ("怎么钓", "如何钓", "钓鱼怎么", "钓鱼如何", "钓鱼等级", "钓鱼技能")):
        if any(key in question for key in ("多少", "哪些", "什么", "几种", "有几")):
            return "鱼"
    return None


# 问句里的地点/场合 → 条目正文里应对上的词。用于「海里」「矿井」这类带约束的数量/列表题。
_PLACE_FILTERS: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (("海里", "海洋", "海边", "海滩", "码头"), ("海洋", "海里", "海鱼", "深海", "海底", "海中", "海生", "海边"), "海里"),
    (("河里", "河流", "河边", "河鱼"), ("河流", "河里", "河鱼", "上游"), "河里"),
    (("湖里", "湖泊", "湖边", "湖鱼"), ("湖泊", "湖里", "池鱼"), "湖里"),
    (("淡水",), ("淡水", "河流", "河里", "湖泊", "湖里", "池鱼"), "淡水"),
    (("矿井", "矿山", "矿洞"), ("矿井",), "矿井"),
    (("沙漠", "绿洲"), ("沙漠",), "沙漠"),
    (("下水道",), ("下水道",), "下水道"),
    (("秘密森林",), ("秘密森林",), "秘密森林"),
    (("姜岛",), ("姜岛",), "姜岛"),
    (("夜市",), ("夜市",), "夜市"),
    (("农场", "池塘"), ("农场", "池塘"), "农场"),
)


def _asked_place_filters(question: str) -> list[tuple[tuple[str, ...], str]]:
    found: list[tuple[tuple[str, ...], str]] = []
    seen: set[str] = set()
    for triggers, marks, label in _PLACE_FILTERS:
        if not any(trigger in question for trigger in triggers):
            continue
        if label in seen:
            continue
        seen.add(label)
        found.append((marks, label))
    return found


def _asked_seasons(question: str) -> list[str]:
    found = []
    for name, mark in (("春季", "春"), ("夏季", "夏"), ("秋季", "秋"), ("冬季", "冬")):
        if mark in question and name not in found:
            found.append(name)
    return found


def _entry_matches(blob: str, marks: tuple[str, ...]) -> bool:
    return any(mark in blob for mark in marks)


def _filter_entries(
    entries: list[tuple[str, str]],
    *,
    seasons: list[str] | None = None,
    place_filters: list[tuple[tuple[str, ...], str]] | None = None,
) -> list[tuple[str, str]]:
    kept = entries
    if seasons:
        kept = [
            item
            for item in kept
            if any(season in (item[1] or "") for season in seasons)
        ]
    if place_filters:
        kept = [
            item
            for item in kept
            if any(_entry_matches(item[1] or "", marks) for marks, _label in place_filters)
        ]
    return kept

def _plain_lines(body: str) -> list[str]:
    return [re.sub(r"\s+", " ", line).strip() for line in body.splitlines() if line.strip()]


def _toc_entries(lines: list[str]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    section = ""
    for line in lines:
        match = re.match(r"^(\d+(?:\.\d+)*)\s+(\S+)$", line)
        if not match:
            continue
        number, name = match.group(1), match.group(2)
        if "." not in number:
            section = name
            continue
        if number.count(".") == 1 and name not in _SECTION_SKIP and not name.endswith("种子"):
            entries.append((name, section))
    return entries


def _bullet_names(lines: list[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if "•" not in line:
            continue
        for part in line.split("•"):
            name = part.strip()
            if re.fullmatch(r"[\u4e00-\u9fff]{2,6}", name) and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def _described_names(lines: list[str], window: int = 28) -> list[tuple[str, str]]:
    labels = set()
    for line in lines:
        match = re.match(r"^\d+(?:\.\d+)*\s+(\S+)$", line)
        if match:
            labels.add(match.group(1))
    names: list[tuple[str, str]] = []
    seen: set[str] = set()
    skip = _CATALOG_SKIP | _SECTION_SKIP | set(_SEASON_WORDS) | set(_METHOD_WORDS) | labels
    span = max(14, window)
    for index, line in enumerate(lines[:-1]):
        if line in {"食谱", "历史", "参考", "外部链接"} and names:
            break
        if line in seen or line in skip or not re.fullmatch(r"[\u4e00-\u9fff]{2,8}", line):
            continue
        if any(word in line for word in ("游戏", "目录", "职业", "模式", "收集")):
            continue
        nxt = lines[index + 1]
        if "。" not in nxt or len(nxt) < 6:
            continue
        # 表格条目的位置/季节在价格行后面；窗口要够长，但必须在下一条目标前停下。
        chunk = lines[index + 1 : index + 1 + span]
        stop = len(chunk)
        for cursor in range(1, len(chunk) - 1):
            part = chunk[cursor]
            follow = chunk[cursor + 1]
            if (
                re.fullmatch(r"[\u4e00-\u9fff]{2,8}", part)
                and part not in skip
                and "。" in follow
                and len(follow) >= 4
            ):
                stop = cursor
                break
        window_text = " ".join(chunk[:stop])
        seen.add(line)
        names.append((line, window_text))
    return names


def _table_names(lines: list[str]) -> list[tuple[str, str]]:
    names: list[tuple[str, str]] = []
    seen: set[str] = set()
    skip = _CATALOG_SKIP | _SECTION_SKIP | set(_SEASON_WORDS) | set(_METHOD_WORDS)
    for index, line in enumerate(lines):
        if line in seen or line in skip or not re.fullmatch(r"[\u4e00-\u9fff]{2,8}", line):
            continue
        if line.endswith(("树", "树苗", "幼苗", "种子", "果酒", "果酱", "果干")):
            continue
        window = " ".join(lines[index + 1 : index + 8])
        if not any(mark in window for mark in _SEASON_WORDS + _METHOD_WORDS + ("金", "耕种", "采集", "树", "山洞")):
            continue
        seen.add(line)
        names.append((line, window))
    return names


def _page_entries(title: str, body: str) -> list[tuple[str, str]]:
    lines = _plain_lines(body)
    if title == "居民":
        return [(name, "") for name in _bullet_names(lines)]
    if title == "鱼":
        return _described_names(lines, window=32)
    if title == "水果":
        return _table_names(lines)
    toc = _toc_entries(lines)
    if title in {"农作物", "果树", "节日", "怪物", "动物"} and len(toc) >= 3:
        return toc
    if len(toc) >= 5:
        return toc
    described = _described_names(lines, window=24)
    if len(described) >= 5:
        return described
    table = _table_names(lines)
    if len(table) >= 3:
        return table
    return toc


def _subject_page(question: str):
    text = question
    for word in ("多少种", "有多少", "几种", "有几种", "哪些", "有哪些", "都有什么", "有什么", "介绍一下", "总结一下", "概括一下", "简介", "列出", "总共", "共有", "一共有", "一下", "什么"):
        text = text.replace(word, " ")
    parts = [re.sub(r"^[有的]+|[的吗呢吧]+$", "", part) for part in re.findall(r"[\u4e00-\u9fff]{2,}", text)]
    parts = [part for part in parts if len(part) >= 2]
    parts.sort(key=len, reverse=True)
    for part in parts:
        found = get_pages_named([part])
        if found:
            return found[0]
    return None


def _looks_like_kind_name(name: str) -> bool:
    if not name or name in _KIND_NAME_SKIP:
        return False
    if name.endswith("事件") or "店" in name or "有售" in name or "宝箱" in name:
        return False
    if any(mark in name for mark in _KIND_NAME_MARKS):
        return False
    if len(name) >= 6 and any(mark in name for mark in ("如何", "怎么", "可以", "需要", "打开", "关闭")):
        return False
    return True


def _real_kinds(entries: list[tuple[str, str]], title: str = "") -> list[tuple[str, str]]:
    """只保留可数的实体名。目录里的机制小节（友谊、心情、新农场）不算一种。"""
    kept: list[tuple[str, str]] = []
    for name, section in entries:
        if not _looks_like_kind_name(name):
            continue
        if section in _KIND_SECTION_SKIP or section in _SECTION_SKIP:
            continue
        kept.append((name, section))
    if not title or len(title) < 2:
        return kept
    # 若存在「鸡舍动物 / 畜棚动物」这类带主题词的分类，优先只数这些，避免把照顾说明算进去。
    categories = [item for item in kept if item[1] and title in item[1] and item[1] != title]
    if len(categories) < 3:
        return kept
    extras: list[tuple[str, str]] = []
    if title == "动物":
        extras = [
            item
            for item in kept
            if item[1] == "宠物" and item[0] not in {"新农场", "额外宠物"}
        ]
    seen = {name for name, _section in categories}
    for item in extras:
        if item[0] not in seen:
            categories.append(item)
            seen.add(item[0])
    return categories

def _lead_text(body: str) -> str:
    for line in _plain_lines(body):
        if re.match(r"^\d", line) or "#" in line or "请见" in line or "请查看" in line:
            continue
        if "。" not in line:
            continue
        sentence = re.split(r"(?<=。)", line)[0].strip()
        if len(sentence) >= 18:
            return sentence[:180]
    return ""


def _wants_planting_summary(question: str) -> bool:
    if any(key in question for key in ("多少钱", "售价", "价格", "多贵")) and "种什么" not in question:
        return False
    return any(key in question for key in ("种什么", "种啥", "适合种", "种植什么", "种点什么"))


def _shop_crops(question: str, body: str) -> list[dict]:
    records = [crop for crop in _crop_records(body) if crop.get("days") and crop.get("sell") and crop.get("seed")]
    seasons = _asked_seasons(question)
    if seasons:
        records = [crop for crop in records if crop.get("season") in seasons]
    records = [crop for crop in records if crop.get("seed_where") == "杂货店"]
    records.sort(key=lambda crop: (int(crop["days"]), -_daily_gold(crop)))
    return records


def _crop_clause(crop: dict) -> str:
    return (
        f"{crop['name']}，{crop['days']}天成熟，{crop['seed_where']}种子{crop['seed']}金，普通售价{crop['sell']}金"
    )


def _planting_summary(question: str, body: str, limit: int = 4) -> str:
    crops = _shop_crops(question, body)[:limit]
    if len(crops) < 2:
        return ""
    seasons = _asked_seasons(question)
    season = "、".join(seasons) if seasons else ""
    who = "新手" if any(key in question for key in ("新手", "开局", "第一年")) else ""
    names = "、".join(crop["name"] for crop in crops)
    head = f"{season}{who}可以先种杂货店买得到、成熟比较快的{names}。"
    detail = "；".join(_crop_clause(crop) for crop in crops)
    return head + detail + "。这些作物只能在对应季节种，换季时还在地里就会枯死。"


def _planting_answer(question: str) -> dict | None:
    if not _wants_planting_summary(question):
        return None
    page = _crop_page([])
    if not page:
        return None
    text = _planting_summary(question, _body(page))
    if not text:
        return None
    return {"answer": _ask_line(question) + "\n" + text, "sources": _sources([page]), "mode": "wiki"}


def _overview_text(question: str, title: str, names: list[str], lead: str, body: str, unit: str) -> str:
    parts = []
    if lead:
        parts.append(lead)
    if names:
        shown = names[:5]
        if len(names) > len(shown):
            parts.append(f"这里一共 {len(names)} {unit}，例如{_join_names(shown)}。")
        else:
            parts.append(f"这里主要是{_join_names(shown)}。")
    if title in {"农作物", "作物"} or "作物" in question:
        tip = _planting_summary(question, body, limit=2)
        if tip:
            parts.append(tip)
    return "\n".join(part for part in parts if part)


def _catalog_answer(question: str) -> dict | None:
    mode = _summary_mode(question)
    if not mode:
        return None
    title = _topic_page(question)
    page = None
    if title:
        found = get_pages_named([title])
        page = found[0] if found else None
    if page is None:
        page = _subject_page(question)
        if page is None:
            return None
        title = page.get("title") or _page_name(page)
    body = _body(page)
    entries = _real_kinds(_page_entries(title, body), title=title or "")
    seasons = _asked_seasons(question)
    place_filters = _asked_place_filters(question)
    constrained = bool(seasons or place_filters)
    entries = _filter_entries(entries, seasons=seasons or None, place_filters=place_filters or None)
    # 问句带了地点/季节，却过滤不出条目：不要退回「整页总数」，交给后面的检索去答。
    if constrained and len(entries) < 2:
        return None
    label = _page_name(page) or title
    names = [name for name, _section in entries if name not in {label, title}]
    lead = _lead_text(body)
    unit = "个" if title == "居民" else "种"
    scope_bits = [label for _marks, label in place_filters]
    scope_bits.extend(seasons)
    scope = "、".join(scope_bits)
    if mode == "overview":
        if constrained and names:
            head = f"按{label}条目里和{scope}有关的内容，一共 {len(names)} {unit}，例如{_join_names(names[:5])}。"
            return {"answer": _ask_line(question) + "\n" + head, "sources": _sources([page]), "mode": "wiki"}
        sentence = _overview_text(question, title, names, lead, body, unit)
        if not sentence:
            return None
        return {"answer": _ask_line(question) + "\n" + sentence, "sources": _sources([page]), "mode": "wiki"}
    if len(names) < 2:
        return None
    shown = "、".join(names)
    if constrained:
        if mode == "count":
            sentence = f"按{label}条目里地点/季节写明与{scope}相关的逐项数完，一共 {len(names)} {unit}：{shown}。"
        else:
            sentence = f"按{label}条目，和{scope}相关的有 {len(names)} {unit}：{shown}。"
    elif mode == "count":
        sentence = f"按{label}这一页逐项数完，一共 {len(names)} {unit}：{shown}。"
    else:
        sentence = f"{label}这一页列出 {len(names)} {unit}：{shown}。"
    return {"answer": _ask_line(question) + "\n" + sentence, "sources": _sources([page]), "mode": "wiki"}

def _pages_for_answer(question: str) -> list[dict]:
    titles: list[str] = []
    for keywords, names in TOPIC_EXPAND:
        if any(key in question for key in keywords):
            titles.extend(names)
    if any(key in question for key in _MONEY_KEYS) or "第一年" in question or "开局" in question:
        titles.append("农作物")
    found: list[dict] = []
    seen: set[int] = set()
    for page in pages_mentioned_in(question) + get_pages_named(titles):
        if page["pageid"] in seen:
            continue
        found.append(page)
        seen.add(page["pageid"])
    for page in search_wiki(question, limit=6):
        if page["pageid"] in seen:
            continue
        found.append(page)
        seen.add(page["pageid"])
        if len(found) >= 6:
            break
    return found


def _page_name(page: dict) -> str:
    return page.get("display_title") or page.get("title") or ""


def _body(page: dict) -> str:
    return (page.get("body") or "").replace("\xa0", " ").replace("\u200b", "")


def _understand(question: str) -> dict:
    kind = "what"
    if any(key in question for key in ("哪里", "在哪", "哪儿", "什么地方", "位置")):
        kind = "where"
    elif any(key in question for key in ("为什么", "为啥", "原因")):
        kind = "why"
    elif any(key in question for key in ("怎么", "如何", "怎样")):
        kind = "how"
    elif any(key in question for key in ("多少钱", "多少", "价格", "售价", "多贵")):
        kind = "price"
    elif "谁" in question:
        kind = "who"
    if any(key in question for key in _GIFT_KEYS):
        kind = "gift"
    if any(key in question for key in ("赚钱", "利润", "发财", "收入")):
        kind = "money"
    seasons = []
    for name, mark in (("春季", "春"), ("夏季", "夏"), ("秋季", "秋"), ("冬季", "冬")):
        if mark in question:
            seasons.append(name)
    return {
        "kind": kind,
        "year1": any(key in question for key in ("第一年", "开局", "新手")),
        "seasons": seasons,
    }


def _ask_line(question: str) -> str:
    text = question.strip().rstrip("？?").strip()
    return f"你问的是{text}。"


_OBTAIN_KEYS = ("获取", "获得", "得到", "拿到", "入手", "掉落")


_COLOR_WORDS = ("蓝色", "红色", "绿色", "紫色", "黄色", "白色", "黑色", "橙色", "粉色", "金色", "棕色", "褐色")
_CATALOG_SKIP = {
    "图片", "名称", "描述", "获得方式", "季节", "食用效果", "售出价格", "工匠物品售价",
    "基础售价", "农耕人", "熊的知识", "历史", "目录", "概览", "注意", "参考",
    "不可食用", "任意季节", "耕种", "采集", "钓鱼", "采矿", "战斗",
}
_SEASON_WORDS = ("春季", "夏季", "秋季", "冬季", "任意季节")
_METHOD_WORDS = ("耕种", "采集", "钓鱼", "采矿", "战斗")


def _catalog_rows(body: str) -> list[tuple[str, list[str]]]:
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    rows: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if line in seen or line in _CATALOG_SKIP or not re.fullmatch(r"[\u4e00-\u9fff]{2,8}", line):
            continue
        nxt = lines[index + 1 : index + 6]
        window = " ".join(nxt)
        if not any(mark in window for mark in _SEASON_WORDS + _METHOD_WORDS):
            continue
        seen.add(line)
        rows.append((line, nxt))
    return rows


def _row_phrase(name: str, nxt: list[str]) -> str:
    method = ""
    seasons: list[str] = []
    for line in nxt:
        if not method and (line in _METHOD_WORDS or (line.endswith("树") and re.fullmatch(r"[\u4e00-\u9fff]{2,8}", line))):
            method = line
        for season in _SEASON_WORDS:
            if season in line and season not in seasons:
                seasons.append(season)
        if method and seasons:
            break
    if seasons and method:
        return f"{name}，{'、'.join(seasons)}{method}"
    if seasons:
        return f"{name}，{'、'.join(seasons)}"
    if method:
        return f"{name}，{method}"
    return name


def _reason_color_items(question: str, pages: list[dict]) -> tuple[str, list[dict]]:
    color = next((word for word in _COLOR_WORDS if word in question), None)
    if not color:
        return "", []
    named = [page for page in pages if title_mentioned(_page_name(page), question)]
    if not named:
        return "", []
    named.sort(key=lambda page: len(_page_name(page)), reverse=True)
    page = named[0]
    mark = color[0]
    picked = [(name, nxt) for name, nxt in _catalog_rows(_body(page)) if mark in name and name != _page_name(page)]
    if not picked:
        return "", []
    phrases = [_row_phrase(name, nxt) for name, nxt in picked[:8]]
    lead = f"{_page_name(page)}条目按名称列出物品。其中带「{mark}」的是{_join_names(phrases)}。"
    return _ask_line(question) + "\n" + lead, [page]


def _think(question: str, pages: list[dict]) -> tuple[str, list[dict]]:
    understood = _understand(question)
    colored = _reason_color_items(question, pages)
    if colored[0]:
        return colored
    if any(key in question for key in _OBTAIN_KEYS):
        obtained = _reason_obtain(question, pages)
        if obtained[0]:
            return obtained
    if understood["kind"] == "gift":
        gift = _reason_gifts(question, pages)
        if gift[0]:
            return gift
    if understood["kind"] == "money" or (understood["kind"] == "how" and "钱" in question):
        money = _reason_money(question, pages, understood)
        if money[0]:
            return money
    if understood["kind"] == "price":
        price = _reason_price(question, pages)
        if price[0]:
            return price
    return _reason_passage(question, pages, understood)


def _reason_obtain(question: str, pages: list[dict]) -> tuple[str, list[dict]]:
    terms = content_terms(question)
    if not terms:
        return "", []
    subject = max(terms, key=len)
    hints = ("种子", "获得", "捐赠", "打造", "掉落", "宝箱", "旅行货车", "配方", "生产器", "合成", "古代种子", "上古种子")
    follow = ("种子", "也可以", "打造", "宝箱", "配方")
    pool: list[dict] = []
    seen: set[int] = set()
    for page in list(pages) + search_wiki(subject, limit=6):
        if page["pageid"] in seen:
            continue
        seen.add(page["pageid"])
        pool.append(page)
    found: list[str] = []
    used: list[dict] = []
    for page in pool:
        sentences = _wiki_sentences(_body(page))
        for index, sentence in enumerate(sentences):
            if subject not in sentence or not any(hint in sentence for hint in hints):
                continue
            chunk = sentence
            if index + 1 < len(sentences):
                nxt = sentences[index + 1]
                if subject not in nxt and any(hint in nxt for hint in follow):
                    chunk = sentence + nxt
            if chunk in found:
                continue
            found.append(chunk)
            if page not in used:
                used.append(page)
            if len(found) >= 2:
                break
        if len(found) >= 2:
            break
    if not found:
        return "", []
    return (
        _ask_line(question) + f"\n这个问题在问怎么拿到{subject}。按 Wiki 里写到的获取办法：\n" + "\n".join(found),
        used,
    )


def _reason_gifts(question: str, pages: list[dict]) -> tuple[str, list[dict]]:
    person = None
    for page in pages:
        name = _page_name(page)
        if name and len(name) >= 2 and name in question:
            person = page
            break
    if not person:
        return "", []
    name = _page_name(person)
    body = _body(person)
    loves = _unique(_infobox_names(body, "最爱的礼物") + _section_names(body, "最爱", ("喜欢", "一般", "不喜欢", "讨厌")))
    likes = _unique(_infobox_names(body, "喜欢的礼物") + _section_names(body, "喜欢", ("一般", "不喜欢", "讨厌", "最爱")))
    hates = _unique(_infobox_names(body, "讨厌的礼物") + _section_names(body, "讨厌", ("日程", "人际关系", "电影", "爱心")))
    ask_hate = any(key in question for key in ("讨厌", "不喜欢")) and not any(
        key in question for key in ("喜欢", "最爱", "礼物", "喜好", "送礼")
    )
    parts = [_ask_line(question)]
    if ask_hate:
        if not hates:
            return "", []
        parts.append(f"所以看讨厌的礼物：{name}讨厌{_join_names(hates)}。送礼时避开这些。")
    else:
        if not loves and not likes:
            return "", []
        if loves:
            extra = "这里还包括所有普遍最爱的礼物。" if "所有普遍最爱的礼物" in body else ""
            parts.append(f"送礼要先看最爱，因为这是喜好里最高的一档。{name}最爱{_join_names(loves)}。{extra}")
        if likes and "最爱" not in question:
            parts.append(f"最爱没有的时候，再看喜欢：{_join_names(likes)}。")
        if hates and any(key in question for key in ("讨厌", "不喜欢")):
            parts.append(f"同时要避开讨厌的：{_join_names(hates)}。")
    if "生日" in question:
        birthday = re.search(r"生日\s*([春夏秋冬]季?\s*\d+\s*日)", body)
        if birthday:
            parts.append(f"生日是{birthday.group(1).replace(' ', '')}，这一天送礼更合适。")
    return "\n".join(part for part in parts if part), [person]


def _crop_page(pages: list[dict]) -> dict | None:
    page = next((item for item in pages if _page_name(item) in {"农作物", "作物"}), None)
    if page:
        return page
    named = get_pages_named(["农作物"])
    return named[0] if named else None


def _daily_gold(crop: dict) -> float:
    if not crop["sell"] or not crop["days"]:
        return 0
    return int(crop["sell"]) / max(int(crop["days"]), 1)


def _reason_money(question: str, pages: list[dict], understood: dict) -> tuple[str, list[dict]]:
    page = _crop_page(pages)
    if not page:
        return "", []
    records = [crop for crop in _crop_records(_body(page)) if _daily_gold(crop) > 0]
    if not records:
        return "", []
    seasons = understood["seasons"] or ["春季", "夏季", "秋季", "冬季"]
    year1 = understood["year1"]
    parts = [_ask_line(question)]
    if year1:
        parts.append("第一年一开始能稳定买到的是杂货店种子，草莓要等复活节。比较时只用 Wiki 里这两处买得到的作物，用普通售价除以成熟天数，看哪一种每天更值钱。")
    else:
        parts.append("这个问题比的是赚钱，所以用 Wiki 里的普通售价除以成熟天数，看哪一种每天更值钱。")
    for season in seasons:
        group = [crop for crop in records if crop["season"] == season]
        if year1:
            group = [crop for crop in group if crop["seed_where"] in {"杂货店", "复活节", "蛋节"}]
        group.sort(key=_daily_gold, reverse=True)
        if not group:
            continue
        best = group[0]
        sentence = _crop_sentence(best).rstrip("。")
        parts.append(f"{season}先看{best['name']}，大约每天 {round(_daily_gold(best))} 金（{sentence}）。")
        if len(group) > 1:
            nxt = group[1]
            parts.append(f"其次是{nxt['name']}，大约每天 {round(_daily_gold(nxt))} 金。")
    if len(parts) < 3:
        return "", []
    return "\n".join(parts), [page]


def _sell_from_item_page(name: str) -> str:
    found = get_pages_named([name])
    if not found:
        return ""
    body = _body(found[0])
    match = re.search(r"基础售价[\s\S]{0,120}?([1-9]\d{0,4})\s*金", body)
    if match:
        return match.group(1)
    match = re.search(r"(?<!\d)([1-9]\d{2,4})\s*金", body)
    return match.group(1) if match else ""


def _reason_price(question: str, pages: list[dict]) -> tuple[str, list[dict]]:
    lines: list[str] = []
    used: list[dict] = []
    page = _crop_page(pages)
    asked: list[dict] = []
    if page:
        asked = [crop for crop in _crop_records(_body(page)) if crop["name"] and crop["name"] in question]
        # 农作物总表有时读出 000；改从该作物专页取基础售价。
        for crop in asked:
            sell = str(crop.get("sell") or "")
            if not sell or sell.strip("0") == "":
                fixed = _sell_from_item_page(crop["name"])
                if fixed:
                    crop["sell"] = fixed
        used.append(page)
        for crop in asked:
            sentence = _crop_sentence(crop)
            if not sentence:
                continue
            extra = ""
            if crop["sell"] and crop["seed"] and crop["seed_where"] in {"杂货店", "Joja超市", "绿洲", "复活节", "蛋节"}:
                gap = int(crop["sell"]) - int(crop["seed"])
                extra = f"普通品质卖掉，比种子多 {gap} 金。" if gap >= 0 else f"普通售价比种子少 {abs(gap)} 金。"
            lines.append(sentence + extra)
            named = get_pages_named([crop["name"]])
            if named and named[0] not in used:
                used.append(named[0])

    # 果树果实等不在农作物表里：直接读专页基础售价。
    seen_names = {crop["name"] for crop in asked}
    candidates = list(pages) + get_pages_named(content_terms(question))
    for item in candidates:
        name = _page_name(item)
        if not name or name in seen_names or not title_mentioned(name, question):
            continue
        sell = _sell_from_item_page(name)
        if not sell:
            continue
        lines.append(f"{name}普通售价 {sell} 金。")
        seen_names.add(name)
        if item not in used:
            used.append(item)
    if not lines:
        return "", []
    return _ask_line(question) + "\n" + "按 Wiki 条目里的售价：" + "".join(lines), used
def _reason_passage(question: str, pages: list[dict], understood: dict) -> tuple[str, list[dict]]:
    page = _best_page(question, pages)
    if not page:
        return "", []
    name = _page_name(page)
    terms = focus_terms(question, [name])
    sentences = _wiki_sentences(_body(page))
    kind = understood["kind"]
    named = bool(name and name in question)
    if terms and not (kind == "what" and named):
        focused = [sentence for sentence in sentences if any(term in sentence for term in terms)]
        if focused:
            sentences = focused
    if kind == "where":
        hints = ("位于", "北边", "南边", "东边", "西边", "入口", "山上", "镇里", "往北", "往南", "在山", "在镇")
        lead = "这个问题在问地点，所以采用写明它在哪里的句子。"
    elif kind == "how":
        hints = ("可以", "需要", "先", "然后", "才能", "方法", "只要")
        lead = "这个问题在问做法，所以采用写明可以怎么做的句子。"
    elif kind == "why":
        hints = ("因为", "由于", "所以", "原因")
        lead = "这个问题在问原因，所以采用写明因为什么的句子。"
    elif kind == "who":
        hints = ("是", "居住", "经营", "负责")
        lead = "这个问题在问是谁，所以采用点出人物的句子。"
    else:
        hints = ()
        lead = f"我按这个问题去读了{name}。"
    if kind == "what":
        defining = [sentence for sentence in sentences if not sentence.startswith(("如果", "当", "然后", "多人", "为了"))]
        chosen = (defining or sentences)[:3]
    else:
        if hints:
            matched = [sentence for sentence in sentences if any(hint in sentence for hint in hints)]
            if matched:
                sentences = matched
        if not sentences:
            return "", []

        def place_score(sentence: str) -> int:
            score = 0
            if name and (f"{name}位于" in sentence or f"{name}坐落" in sentence):
                score += 30
            if "位于" in sentence or "坐落" in sentence:
                score += 8
            return score

        if kind == "where":
            exact = [
                sentence
                for sentence in sentences
                if name and (f"{name}位于" in sentence or f"{name}坐落" in sentence)
            ]
            if exact:
                sentences = exact[:1]
            else:
                sentences.sort(key=place_score, reverse=True)
                placed = [sentence for sentence in sentences if place_score(sentence) > 0]
                if placed:
                    sentences = placed
        else:
            terms_or_name = terms or ([name] if name else [])
            sentences.sort(
                key=lambda sentence: sum(sentence.count(term) * len(term) for term in terms_or_name),
                reverse=True,
            )
        chosen = sentences[:2]
    if not chosen:
        return "", []
    return _ask_line(question) + "\n" + lead + "\n" + "\n".join(chosen), [page]


def _summarize_read(question: str, pages: list[dict], offset: int = 0) -> tuple[str, list[dict]]:
    gift = _summarize_gifts(question, pages, offset)
    if gift[0]:
        return gift
    crops = _summarize_crops(question, pages, offset)
    if crops[0]:
        return crops
    return _summarize_lines(question, pages, offset)


def _join_names(names: list[str]) -> str:
    names = [name for name in names if name]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return "、".join(names[:-1]) + "和" + names[-1]


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


def _infobox_names(body: str, label: str) -> list[str]:
    match = re.search(re.escape(label) + r"[ \t]+([^\n]+)", body)
    if not match:
        return []
    names = []
    for part in re.split(r"\s+", match.group(1).strip()):
        part = part.strip("，,、")
        if len(part) < 2 or len(part) > 10 or part in _GIFT_SKIP or re.match(r"^\d", part):
            continue
        if any(mark in part for mark in "。“”！？：:-"):
            continue
        if part.startswith("当收到"):
            continue
        names.append(part)
    return names[:12]


def _section_names(body: str, label: str, end_labels: tuple[str, ...]) -> list[str]:
    match = re.search(rf"(?m)^[ \t]*{re.escape(label)}[ \t]*$", body)
    if not match:
        return []
    rest = body[match.end() :]
    end = len(rest)
    for other in end_labels:
        found = re.search(rf"(?m)^[ \t]*{re.escape(other)}[ \t]*$", rest)
        if found:
            end = min(end, found.start())
    rows = [line.strip() for line in rest[:end].splitlines() if line.strip()]
    names = []
    for index, line in enumerate(rows[:-1]):
        if line in _GIFT_SKIP or not 2 <= len(line) <= 10 or line.startswith("当收到"):
            continue
        if any(mark in line for mark in "。“”！？，,、（(：:-"):
            continue
        nxt = rows[index + 1]
        if "。" in nxt or "的" in nxt:
            names.append(line)
    return names[:12]


def _summarize_gifts(question: str, pages: list[dict], offset: int = 0) -> tuple[str, list[dict]]:
    if not any(key in question for key in _GIFT_KEYS):
        return "", []
    person = None
    for page in pages:
        name = _page_name(page)
        if name and len(name) >= 2 and name in question:
            person = page
            break
    if not person:
        return "", []
    name = _page_name(person)
    body = _body(person)
    loves = _unique(_infobox_names(body, "最爱的礼物") + _section_names(body, "最爱", ("喜欢", "一般", "不喜欢", "讨厌")))
    likes = _unique(_infobox_names(body, "喜欢的礼物") + _section_names(body, "喜欢", ("一般", "不喜欢", "讨厌", "最爱")))
    hates = _unique(_infobox_names(body, "讨厌的礼物") + _section_names(body, "讨厌", ("日程", "人际关系", "电影", "爱心")))
    ask_hate = any(key in question for key in ("讨厌", "不喜欢"))
    birthday = re.search(r"生日\s*([春夏秋冬]季?\s*\d+\s*日)", body)
    rounds: list[str] = []
    if ask_hate and "喜欢" not in question and "最爱" not in question:
        if hates:
            rounds.append(f"{name}讨厌{_join_names(hates)}。")
        if likes:
            rounds.append(f"另外，{name}还喜欢{_join_names(likes)}。")
        if loves:
            extra = "，普遍最爱的礼物也包括在内" if "所有普遍最爱的礼物" in body else ""
            rounds.append(f"{name}最爱{_join_names(loves)}{extra}。")
    else:
        if loves:
            extra = "，普遍最爱的礼物也包括在内" if "所有普遍最爱的礼物" in body else ""
            rounds.append(f"{name}最爱{_join_names(loves)}{extra}。")
        if likes:
            rounds.append(f"{name}也喜欢{_join_names(likes)}。")
        if hates:
            rounds.append(f"{name}讨厌{_join_names(hates)}。")
    if birthday and ("生日" in question or offset >= len(rounds)):
        rounds.append(f"生日是{birthday.group(1).replace(' ', '')}。")
    if not rounds:
        return "", []
    if offset >= len(rounds):
        return f"{name}的礼物喜好就这些。可以换个问法，比如生日或日程。", [person]
    note = "再问一次这句，我会接着说下一条。" if offset + 1 < len(rounds) else ""
    text = rounds[offset]
    if note:
        text = text + "\n" + note
    return text, [person]


def _crop_records(body: str) -> list[dict]:
    body = body.replace("\xa0", " ")
    first_days = body.find("共：")
    if first_days < 0:
        return []
    start = body.rfind("春季作物", 0, first_days)
    if start < 0:
        start = 0
    names = set(re.findall(r"(?m)^\s*\d+\.\d+\s*(\S+)\s*$", body[:start]))
    if not names:
        return []
    season = ""
    records = []
    current: list[str] | None = None
    current_name = ""
    current_season = ""

    def close() -> None:
        nonlocal current
        if not current:
            return
        block = "\n".join(current)
        days = re.search(r"共：\s*(\d+)\s*天", block)
        seed = re.search(r"(杂货店|Joja超市|绿洲|复活节|蛋节)\s*(\d+)", block)
        sell = re.search(r"(\d+)\s*金", block)
        if days or sell:
            records.append(
                {
                    "name": current_name,
                    "season": current_season,
                    "days": days.group(1) if days else "",
                    "seed": seed.group(2) if seed else "",
                    "seed_where": seed.group(1) if seed else "",
                    "sell": sell.group(1) if sell else "",
                }
            )
        current = None

    for raw in body[start:].splitlines():
        plain = re.sub(r"^[\d.\s]+", "", raw).strip()
        if plain in {"春季作物", "夏季作物", "秋季作物", "冬季作物", "特殊作物"}:
            close()
            season = plain[:2]
            continue
        if raw.strip() in names:
            close()
            current_name = raw.strip()
            current_season = season
            current = [raw]
            continue
        if current is not None:
            current.append(raw)
    close()
    return records


def _crop_sentence(crop: dict) -> str:
    bits = []
    if crop["season"]:
        bits.append(crop["season"])
    if crop["days"]:
        bits.append(f"共 {crop['days']} 天成熟")
    if crop["seed"]:
        where = crop["seed_where"]
        if where in {"复活节", "蛋节"}:
            bits.append(f"{where}买种子 {crop['seed']} 金")
        else:
            bits.append(f"{where}种子 {crop['seed']} 金")
    if crop["sell"]:
        bits.append(f"普通售价 {crop['sell']} 金")
    if len(bits) < 2:
        return ""
    return f"{crop['name']}，" + "，".join(bits) + "。"


def _summarize_crops(question: str, pages: list[dict], offset: int = 0) -> tuple[str, list[dict]]:
    page = next((item for item in pages if _page_name(item) in {"农作物", "作物"}), None)
    if not page:
        named = get_pages_named(["农作物"])
        page = named[0] if named else None
    if not page:
        return "", []
    records = _crop_records(_body(page))
    if not records:
        return "", []
    asked = [crop for crop in records if crop["name"] and crop["name"] in question]
    broad = any(key in question for key in ("赚钱", "利润", "收入", "发财", "金币", "第一年", "开局", "新手", "种植", "种什么"))
    if not asked and ("售价" in question or "多少钱" in question):
        broad = True
    if asked and not broad:
        lines = [line for crop in asked if (line := _crop_sentence(crop))]
        if not lines:
            return "", []
        return "按 Wiki 农作物条目，" + "".join(lines), [page]
    if not broad:
        return "", []
    wanted = set()
    if "春" in question:
        wanted.add("春季")
    if "夏" in question:
        wanted.add("夏季")
    if "秋" in question:
        wanted.add("秋季")
    if "冬" in question:
        wanted.add("冬季")
    if not wanted:
        wanted = {"春季", "夏季", "秋季", "冬季"}
    chosen = []
    for season in ("春季", "夏季", "秋季", "冬季"):
        if season not in wanted:
            continue
        group = [crop for crop in records if crop["season"] == season and crop["sell"] and crop["days"]]
        if any(key in question for key in ("第一年", "开局", "新手")):
            basic = [crop for crop in group if crop["seed_where"] in {"杂货店", "复活节", "蛋节"}]
            if basic:
                group = basic
        group.sort(key=lambda crop: int(crop["sell"]) / max(int(crop["days"]), 1), reverse=True)
        if offset < len(group):
            chosen.append(group[offset])
    if asked and offset == 0:
        for crop in asked:
            if crop not in chosen:
                chosen.insert(0, crop)
    lines = [line for crop in chosen if (line := _crop_sentence(crop))]
    if not lines:
        if offset:
            return "按售价和成熟天数，这几个季节已经列完了。可以指定一种作物或一个季节再问。", [page]
        return "", []
    if offset:
        lead = "接着上一问，每个季节再换一种："
    elif any(key in question for key in _MONEY_KEYS) or "第一年" in question:
        lead = "第一年想靠种田赚钱，按 Wiki 里的成熟天数和普通售价，每个季节可以先看这一种："
    else:
        lead = "按 Wiki 里的成熟天数和普通售价，每个季节先看这一种："
    more = "\n再问一次这句，会换成每个季节的下一种。" if offset == 0 else ""
    return lead + "\n" + "\n".join(lines) + more, [page]


def _usable_line(line: str) -> bool:
    if not 8 <= len(line) <= 80:
        return False
    if line.startswith(("目录", "信息", "关于本", "此页面", "主页面", "参见", "导航")):
        return False
    if re.fullmatch(r"[\d\s.金天/（）()]+", line):
        return False
    return len(re.findall(r"[\u4e00-\u9fff]", line)) >= 4


def _wiki_sentences(body: str) -> list[str]:
    paragraphs: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if buf:
            paragraphs.append("".join(buf))
            buf.clear()

    for raw in body.splitlines():
        compact = re.sub(r"\s+", "", raw.strip())
        if not compact:
            flush()
            continue
        if re.match(r"^\d", compact) or compact in {"目录", "概览", "历史", "参考", "外部链接", "策略", "趣闻"}:
            flush()
            continue
        if len(compact) < 8 or compact.startswith(("此页面", "关于")) or "请查看" in compact:
            flush()
            continue
        buf.append(compact)
    flush()
    sentences = []
    for para in paragraphs:
        for part in re.findall(r"[^。！？]{8,180}[。！？]", para):
            if part not in sentences:
                sentences.append(part)
    return sentences


def _best_page(question: str, pages: list[dict]) -> dict | None:
    named = [page for page in pages if title_mentioned(_page_name(page), question)]
    if named:
        named.sort(key=lambda page: len(_page_name(page)), reverse=True)
        return named[0]
    return pages[0] if pages else None


def _summarize_lines(question: str, pages: list[dict], offset: int = 0) -> tuple[str, list[dict]]:
    page = _best_page(question, pages)
    if not page:
        return "", []
    terms = focus_terms(question, [_page_name(page)])
    sentences = _wiki_sentences(_body(page))
    named = _page_name(page) and _page_name(page) in question
    if terms and not named:
        focused = [sentence for sentence in sentences if any(term in sentence for term in terms)]
        if len(focused) >= 2:
            sentences = focused
    start = max(offset, 0) * 2
    batch = sentences[start : start + 2]
    name = _page_name(page)
    if not batch:
        return f"{name}这一条里能直接摘出的句子已经说完了。可以换一个更具体的问法。", [page]
    if offset:
        lead = f"接着上一问，{name}条目后面还有："
    else:
        lead = f"根据 Wiki 的{name}条目："
    more = "\n再问一次这句，会接着往下说。" if start + 2 < len(sentences) else ""
    return lead + "\n" + "\n".join(batch) + more, [page]


_POLISH_SYSTEM = (
    "你是星露谷问答助手小谷。只根据要点写成两三句总结。"
    "先直接回答，再补要点里已经写明的名称和数字。"
    "不要写要点里没有的名称，不要写英文，不要另起一份名单。"
    "若【最近的对话】不是（无），结合它理解用户这句话的指代；答案里的名称和数字仍必须来自本次要点。"
)
_POLISH_USER = "【最近的对话】\n{history}\n\n问题：{question}\n\n要点：\n{notes}\n\n总结："


def _ollama_answer(question: str, notes: str, history: list | None = None) -> str | None:
    """检索库还没建好时，用本机 Ollama 的对话模型把 Wiki 要点写成答案。"""
    try:
        from langchain_core.output_parsers import StrOutputParser
        from langchain_core.prompts import ChatPromptTemplate

        from .rag import _chat, _model_ready, chat_model_name, history_block, ollama_model_names

        names = ollama_model_names()
        if names is None or not _model_ready(names, chat_model_name()):
            return None
        model = _chat()
        model.num_predict = 700
        model.num_ctx = 8192
        prompt = ChatPromptTemplate.from_messages(
            [("system", _POLISH_SYSTEM), ("human", _POLISH_USER)]
        )
        text = (prompt | model | StrOutputParser()).invoke(
            {"question": question, "notes": notes, "history": history_block(history)}
        ).strip()
    except Exception:
        return None
    if not text:
        return None
    from .rag import sticks_to_source

    if not sticks_to_source(text, notes):
        return None
    return text


def _ollama_answer_stream(question: str, notes: str, history: list | None = None):
    """流式版 _ollama_answer：返回 (holder, gen)。

    gen 逐块产出答案文本；遍历结束后 holder["ok"] / holder["text"]
    与同步版的校验结果一致。"""
    holder = {"ok": False, "text": ""}

    def dead():
        return iter(())

    try:
        from langchain_core.output_parsers import StrOutputParser
        from langchain_core.prompts import ChatPromptTemplate

        from .rag import _chat, _model_ready, chat_model_name, history_block, ollama_model_names

        names = ollama_model_names()
        if names is None or not _model_ready(names, chat_model_name()):
            return holder, dead()
        model = _chat()
        model.num_predict = 700
        model.num_ctx = 8192
        prompt = ChatPromptTemplate.from_messages(
            [("system", _POLISH_SYSTEM), ("human", _POLISH_USER)]
        )
        chain = prompt | model | StrOutputParser()
        variables = {"question": question, "notes": notes, "history": history_block(history)}
        buf: list[str] = []

        def gen():
            try:
                for chunk in chain.stream(variables):
                    buf.append(chunk)
                    yield chunk
            except Exception:
                return
            text = "".join(buf).strip()
            holder["text"] = text
            from .rag import sticks_to_source

            holder["ok"] = bool(text) and sticks_to_source(text, notes)

        return holder, gen()
    except Exception:
        return holder, dead()


def _stream_rag(question: str, history: list | None = None):
    """流式版 _try_rag。返回 None / dict（建索引提示）/ (sources, gen, holder)。"""
    try:
        from .rag import stream_answer_with_langchain

        return stream_answer_with_langchain(question, history)
    except Exception:
        return None


def stream_answer(question: str, offset: int = 0, history: list | None = None):
    """流式版 answer_question：产出事件 dict，kind ∈ {status, meta, delta, replace, done}。

    meta 携带 mode/sources；delta 是答案文本块；replace 表示后置校验改写，
    前端应用它整体替换已流出的文本。"""
    history = history or []
    question = question.strip()
    off = _off_topic_reply(question)
    if off:
        yield {"kind": "meta", "mode": "off", "sources": []}
        yield {"kind": "delta", "text": off}
        yield {"kind": "done"}
        return

    prefer = _prefer_ollama()
    if prefer and _ollama_chat_ready():
        yield {"kind": "status", "text": "小谷正在 Wiki 里检索相关资料…"}
        rag = _stream_rag(question, history)
        if isinstance(rag, dict):
            # 正在建索引：把提示语一次性发完。
            yield {
                "kind": "meta",
                "mode": rag.get("mode") or "langchain",
                "sources": rag.get("sources") or [],
            }
            yield {"kind": "delta", "text": rag.get("answer") or ""}
            yield {"kind": "done"}
            return
        if rag is not None:
            sources, gen, holder = rag
            yield {"kind": "meta", "mode": "langchain", "sources": sources}
            yield {"kind": "status", "text": "小谷正在根据资料写答案…"}
            for chunk in gen:
                yield {"kind": "delta", "text": chunk}
            if holder.get("ok"):
                text = holder["text"]
                rag_dict = {"answer": text, "sources": sources, "mode": "langchain"}
                grounded = _ground_rag_with_wiki(question, rag_dict)
                final_sources = sources
                if grounded and grounded.get("mode") in {"ollama", "langchain"}:
                    final_sources = grounded.get("sources") or sources
                    if (grounded.get("answer") or "") != text:
                        yield {
                            "kind": "replace",
                            "text": grounded.get("answer") or text,
                            "mode": grounded.get("mode") or "ollama",
                            "sources": final_sources,
                        }
                yield {"kind": "done", "sources": final_sources}
                return
        # RAG 没成：仍把 Wiki 原文要点交给 Ollama 流式润色。
        pages = _pages_for_answer(question)
        if not pages:
            pages = get_pages_named(content_terms(question))
        notes, used = _think(question, pages)
        if not notes and pages:
            notes, used = _notes_from_pages(question, pages)
        if notes:
            yield {"kind": "meta", "mode": "ollama", "sources": _sources(used)}
            yield {"kind": "status", "text": "小谷正在把 Wiki 要点写成答案…"}
            holder, gen = _ollama_answer_stream(question, notes, history)
            for chunk in gen:
                yield {"kind": "delta", "text": chunk}
            if holder.get("ok"):
                yield {"kind": "done"}
            else:
                yield {"kind": "replace", "text": notes, "mode": "wiki"}
                yield {"kind": "done"}
            return
        yield {"kind": "meta", "mode": "off", "sources": []}
        yield {
            "kind": "delta",
            "text": "本地模型开着，但 Wiki 里没检索到能回答这句的原文。换个作物、村民或地点名称再问。",
        }
        yield {"kind": "done"}
        return

    # 没开优先 Ollama：沿用原同步逻辑，一次性给出。
    yield {"kind": "status", "text": "小谷正在整理答案…"}
    result = _catalog_answer(question)
    if not result:
        result = _planting_answer(question)
    if not result:
        rag = _try_rag(question)
        if rag:
            result = rag
    if not result:
        result = _wiki_fallback(question)
    yield {
        "kind": "meta",
        "mode": result.get("mode") or "wiki",
        "sources": result.get("sources") or [],
    }
    yield {"kind": "delta", "text": result.get("answer") or ""}
    yield {"kind": "done"}


def _llm_answer(question: str, notes: str, api_key: str) -> str:
    payload = {
        "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "temperature": 0.1,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是星露谷问答助手小谷。只根据要点写成两三句总结。"
                    "先直接回答，再补要点里已经写明的名称和数字。"
                    "不要写要点里没有的名称，不要写英文，不要另起一份名单。"
                ),
            },
            {
                "role": "user",
                "content": f"用户原话：{question}\n\n已读出的要点：\n{notes}",
            },
        ],
    }
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    with httpx.Client(timeout=45.0) as client:
        r = client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
