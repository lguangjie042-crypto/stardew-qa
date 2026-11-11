"""小谷的问答：小段检索，取出相关原文后一次生成答案。"""

from __future__ import annotations

import os
import re
import shutil
import threading
from pathlib import Path

import httpx
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .db import DATA_DIR, connect
from .wiki import last_sync, wiki_count

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CHROMA_DIR = DATA_DIR / "chroma"
PARENT_DIR = DATA_DIR / "parent_store"
STAMP_PATH = DATA_DIR / "chroma.stamp"
COLLECTION = "stardew_wiki"

# 子段只进向量库。按段落和句子切开，保留重叠，不用按字符硬切。
CHILD_CHUNK_SIZE = 400
CHILD_CHUNK_OVERLAP = 80
CHILD_K = 8
# 命中后只把和问题相关的段落送进模型。篇数和字数都有上限，避免 CPU/显卡把整篇再读一遍。
MAX_PARENTS = 3
CONTEXT_BUDGET = 3600
EXCERPT_CHARS = 1200
NUM_CTX = 4096
NO_DATA = "暂无相关资料"

# 系统提示固定在代码里，不从环境变量读取。
SYSTEM_PROMPT = """你是问答助手「小谷」。只根据本次给出的参考资料，写成一段简短总结。
先用一两句话直接回答问题，再补两三句资料里的名称、地点、售价或天数。
问「多少种」「有几种」「有多少」时，第一句必须给出数字（或明确说资料里数不清），不要改成介绍玩法、技能或小游戏。只数【词条】里的名字。地点、季节、售价、用途是该词条的子字段，不能把子字段当成一种。栏目标题、弹药、属性说明、售价公式都不是一件物品。资料若分开写了「可以拿到」和「拿不到」，两种数量都要说。
问「有哪些」「可以钓什么」「能抓什么」时，先给数量或短名单，不要改成介绍该活动怎么玩。名单只用【词条】名。
用户提到的地点、季节、方式是过滤条件，只回答符合这些条件的内容，不要回答更大范围的总览。
给了【最近的对话】时，结合它理解用户这句话的指代（比如「那夏天呢」指上一轮的话题）；答案中的名称和数字仍必须来自本次参考资料。
资料里没有的名称和数字不要写，不要写英文，不要编造数量。
资料里已经出现了用户问的名称时，不要回答「暂无相关资料」。"""
_ANSWER_USER = """把参考资料总结成答案。只使用资料里出现过的名称和数字。

【最近的对话】
{history}

用户问题：{question}

【参考资料】
{context}"""


def history_block(history: list | None) -> str:
    """把最近几轮问答渲染成提示词里的【最近的对话】块；没有历史时写（无）。"""
    turns = [t for t in (history or []) if isinstance(t, dict) and t.get("q") and t.get("a")]
    if not turns:
        return "（无）"
    lines: list[str] = []
    for turn in turns[-3:]:
        q = str(turn.get("q") or "").strip()[:200]
        a = str(turn.get("a") or "").strip()[:400]
        lines.append(f"问：{q}\n答：{a}")
    return "\n".join(lines)

_lock = threading.Lock()
_building = False
_build_error: str | None = None


def ollama_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")


def chat_model_name() -> str:
    return os.getenv("OLLAMA_MODEL", "granite3.2:8b").strip()


def embed_model_name() -> str:
    return os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text").strip()


def child_splitter() -> RecursiveCharacterTextSplitter:
    """按段落和句读切开。分隔符里没有空字符串，超长句保持整句，不按字符硬切。"""
    return RecursiveCharacterTextSplitter(
        chunk_size=CHILD_CHUNK_SIZE,
        chunk_overlap=CHILD_CHUNK_OVERLAP,
        add_start_index=True,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，"],
    )


def make_retriever(vectorstore, docstore):
    """LangChain ParentDocumentRetriever：子段检索，父文档是传入的整篇原文。"""
    from langchain_classic.retrievers import ParentDocumentRetriever

    return ParentDocumentRetriever(
        vectorstore=vectorstore,
        docstore=docstore,
        child_splitter=child_splitter(),
        parent_splitter=None,
        search_kwargs={"k": CHILD_K},
    )


def _clean_body(body: str) -> str:
    text = (body or "").replace("\xa0", " ").replace("\u200b", "")
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line in {"目录", "概览", "历史", "参考", "外部链接", "策略", "趣闻"}:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def load_wiki_documents() -> list[Document]:
    conn = connect()
    rows = conn.execute(
        """
        SELECT pageid, title, COALESCE(NULLIF(display_title, ''), title) AS display_title,
               url, body
        FROM wiki_pages
        """
    ).fetchall()
    conn.close()
    documents: list[Document] = []
    for row in rows:
        body = _clean_body(row["body"] or "")
        if len(body) < 20:
            continue
        documents.append(
            Document(
                page_content=body,
                metadata={
                    "pageid": int(row["pageid"]),
                    "title": row["display_title"] or row["title"],
                    "url": row["url"] or "",
                },
            )
        )
    return documents


def _stamp() -> str:
    return "|".join(
        [
            str(wiki_count()),
            last_sync() or "",
            embed_model_name(),
            f"child={CHILD_CHUNK_SIZE}",
            f"overlap={CHILD_CHUNK_OVERLAP}",
            "parent=full",
            f"k={CHILD_K}",
        ]
    )


def index_is_current() -> bool:
    if not STAMP_PATH.exists() or not CHROMA_DIR.exists() or not PARENT_DIR.exists():
        return False
    try:
        if not any(PARENT_DIR.iterdir()):
            return False
    except OSError:
        return False
    return STAMP_PATH.read_text(encoding="utf-8").strip() == _stamp()


def ollama_model_names() -> set[str] | None:
    try:
        response = httpx.get(f"{ollama_base_url()}/api/tags", timeout=2.0)
        response.raise_for_status()
    except Exception:
        return None
    names: set[str] = set()
    for item in response.json().get("models") or []:
        name = str(item.get("name") or "")
        if not name:
            continue
        names.add(name)
        names.add(name.split(":", 1)[0])
    return names


def _model_ready(names: set[str], wanted: str) -> bool:
    if wanted in names:
        return True
    return any(name == wanted or name.startswith(wanted + ":") for name in names)


_vector_cache = None
_doc_cache = None


def _embeddings():
    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(
        model=embed_model_name(),
        base_url=ollama_base_url(),
        client_kwargs={"timeout": 180.0},
    )


def _vectorstore():
    global _vector_cache
    if _vector_cache is not None:
        return _vector_cache
    from langchain_chroma import Chroma

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    _vector_cache = Chroma(
        collection_name=COLLECTION,
        embedding_function=_embeddings(),
        persist_directory=str(CHROMA_DIR),
    )
    return _vector_cache


def _docstore():
    global _doc_cache
    if _doc_cache is not None:
        return _doc_cache
    from langchain_classic.storage import LocalFileStore, create_kv_docstore

    PARENT_DIR.mkdir(parents=True, exist_ok=True)
    _doc_cache = create_kv_docstore(LocalFileStore(PARENT_DIR))
    return _doc_cache


def _reset_stores() -> None:
    global _vector_cache, _doc_cache
    try:
        _vectorstore().delete_collection()
    except Exception:
        pass
    _vector_cache = None
    _doc_cache = None
    if PARENT_DIR.exists():
        shutil.rmtree(PARENT_DIR, ignore_errors=True)


def ensure_index() -> int:
    """把整篇 Wiki 放进父文档库，子段写入 Chroma。已是当前 Wiki 时直接返回篇数。"""
    with _lock:
        documents = load_wiki_documents()
        if index_is_current():
            return len(documents)
        if STAMP_PATH.exists():
            STAMP_PATH.unlink()
        _reset_stores()
        retriever = make_retriever(_vectorstore(), _docstore())
        for start in range(0, len(documents), 2):
            group = documents[start : start + 2]
            ids = [str(int(item.metadata["pageid"])) for item in group]
            retriever.add_documents(group, ids=ids)
        STAMP_PATH.write_text(_stamp(), encoding="utf-8")
        return len(documents)


def warm_index() -> None:
    global _building, _build_error
    names = ollama_model_names()
    if names is None or not _model_ready(names, embed_model_name()):
        return
    with _lock:
        if _building or index_is_current():
            return
        _building = True
        _build_error = None
    try:
        ensure_index()
    except Exception as exc:
        with _lock:
            _build_error = str(exc)
    finally:
        with _lock:
            _building = False


def _kick_build() -> None:
    threading.Thread(target=warm_index, daemon=True).start()


def _sources(docs: list[Document]) -> list[dict]:
    found: list[dict] = []
    seen: set[int] = set()
    for doc in docs:
        pageid = int(doc.metadata.get("pageid") or 0)
        if not pageid or pageid in seen:
            continue
        seen.add(pageid)
        found.append(
            {
                "title": doc.metadata.get("title") or "Wiki",
                "url": doc.metadata.get("url") or "",
                "pageid": pageid,
            }
        )
        if len(found) >= 6:
            break
    return found


def _specific_terms(question: str) -> list[str]:
    terms = [term for term in _question_terms(question) if term not in _GENERIC_TERMS]
    return terms or _question_terms(question)


def _select_parents(docs: list[Document], question: str) -> list[Document]:
    """只留正文里真有该事物的篇目。问怎么获得时，丢掉只是顺带提到的条目。"""
    terms = _specific_terms(question)
    wants_source = any(word in question for word in _OBTAIN_WORDS)
    ranked: list[tuple[int, int, Document]] = []
    seen: set[int] = set()
    for index, doc in enumerate(docs):
        text = doc.page_content or ""
        if not text.strip():
            continue
        pageid = int(doc.metadata.get("pageid") or 0)
        if pageid and pageid in seen:
            continue
        if pageid:
            seen.add(pageid)
        if not any(term in text for term in terms):
            continue
        if wants_source and not _SOURCE_MARK.search(text):
            continue
        title = str(doc.metadata.get("title") or "")
        score = sum(text.count(term) for term in terms)
        if any(term in title for term in terms):
            score += 50
        ranked.append((score, index, doc))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [doc for _score, _index, doc in ranked[:1]]


_EXCERPT_SPLIT = (
    "什么", "怎么", "如何", "哪些", "从哪里", "从哪儿", "从哪", "在哪里", "在哪儿", "在哪",
    "哪里", "哪儿", "多少钱", "多少金", "卖多少", "卖钱",
    "售价", "价格", "多贵", "多少", "为什么", "吗", "呢", "一下", "几种", "有几",
    "获得", "获取", "得到", "拿到",
)
_GENERIC_TERMS = {
    "获得", "得到", "获取", "拿到", "购买", "怎么", "如何", "哪里", "可以",
    "什么", "哪些", "这个", "那个", "有没有", "是不是", "能否", "多少", "几种",
    "售价", "价格", "多贵", "卖钱", "从哪", "在哪",
}

_OBTAIN_WORDS = ("获得", "得到", "获取", "拿到", "购买", "哪里买", "怎么买")
_SOURCE_MARK = re.compile(r"种子|杂货店|Joja|超市|掉落|购买|旅行货车|种子生产器")
# 问句地点 → 正文里常见写法。避免「海里」检索时只剩下「钓鱼小游戏」。
_PLACE_TERM_MAP = (
    (("海里", "海洋", "海边", "海滩", "码头"), ("海洋", "海里", "海边", "深海", "海底")),
    (("河里", "河流", "河边"), ("河流", "河里", "淡水")),
    (("湖里", "湖泊", "湖边"), ("湖泊", "湖里", "淡水")),
    (("淡水",), ("淡水", "河流", "湖泊")),
    (("矿井", "矿山", "矿洞"), ("矿井",)),
    (("沙漠", "绿洲"), ("沙漠",)),
    (("下水道",), ("下水道",)),
    (("秘密森林",), ("秘密森林",)),
    (("姜岛",), ("姜岛",)),
    (("夜市",), ("夜市",)),
)


def _question_terms(question: str) -> list[str]:
    """从问句里拆出能在正文里对上的词。优先用 Wiki 侧的细拆结果。"""
    from .wiki import content_terms

    terms: list[str] = list(content_terms(question))
    pieces = re.split("|".join(re.escape(word) for word in _EXCERPT_SPLIT), question)
    for piece in pieces:
        chunk = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", piece)
        if len(chunk) >= 2:
            piece_term = chunk if len(chunk) <= 8 else chunk[:4]
            if piece_term not in terms:
                terms.append(piece_term)
    for word in ("喜欢", "讨厌", "礼物", "售价", "价格", "鱼类", "作物", "村民"):
        if word in question and word not in terms:
            terms.append(word)
    for triggers, marks in _PLACE_TERM_MAP:
        if any(trigger in question for trigger in triggers):
            for mark in marks:
                if mark not in terms:
                    terms.append(mark)
    unique: list[str] = []
    for term in terms:
        if term not in unique and term not in _GENERIC_TERMS:
            unique.append(term)
    if unique:
        return unique[:12]
    for term in terms:
        if term not in unique:
            unique.append(term)
    return unique[:12]


def _window(text: str, terms: list[str], limit: int) -> str:
    """长段落从最长关键词附近截取。旁边有数字时优先，避免停在目录。"""
    if len(text) <= limit:
        return text
    center = 0
    found = False
    for term in sorted((term for term in terms if term), key=len, reverse=True):
        start_at = 0
        while True:
            pos = text.find(term, start_at)
            if pos < 0:
                break
            nearby = text[max(0, pos - 30) : pos + len(term) + 40]
            if re.search(r"\d", nearby):
                center = pos
                found = True
                break
            if not found:
                center = pos
                found = True
            start_at = pos + max(len(term), 1)
        if found and re.search(r"\d", text[max(0, center - 30) : center + 80]):
            break
    start = max(0, center - limit // 5)
    return text[start : start + limit]


def _paragraph_score(part: str, terms: list[str], question: str = "") -> int:
    useful = [term for term in terms if len(term) >= 3] or terms
    score = 0
    for term in useful:
        count = part.count(term)
        if not count:
            continue
        score += count * len(term)
        if re.search(r"\d{2}", part):
            score += 20
    # 玩法/小游戏段落即使带「鱼」也不该压过带地点的条目。
    if any(word in part for word in ("小游戏", "绿条", "进度条", "运动模式", "请查看钓鱼")):
        score -= 40
    if any(mark in part for mark in ("海洋", "河流", "湖泊", "矿井", "沙漠", "下水道", "秘密森林", "姜岛")):
        score += 15
    # 多地点索引表（一串 •）容易让模型把邻近水域当成同一种鱼的地点。
    if part.count("•") >= 3:
        score -= 55
    # 问种类时，长段导语让位给词条。词条名在地点、季节这些短行的上面。
    if any(key in question for key in ("多少种", "几种", "有哪些", "哪些", "有什么")) and len(part) > 60 and "。" in part:
        score -= 40
    # 「怎么钓」优先采用写明能在哪钓到的句子，避免送礼/裁制段里的地点名抢戏。
    if any(word in question for word in ("钓", "抓", "怎么", "如何", "哪里")):
        if any(mark in part for mark in ("可以在", "中钓到", "钓到", "位置")):
            score += 50
        if any(mark in part for mark in ("送礼", "裁制", "染料", "收集包", "任务", "配方")):
            score -= 35
    return score


def _source_followers(lines: list[str], index: int, terms: list[str]) -> list[int]:
    """短标题后面只接价格、天数这类短行，碰到另一件事就停下。"""
    found: list[int] = []
    for cursor in range(index + 1, min(index + 8, len(lines))):
        line = lines[cursor]
        if any(term in line for term in terms):
            found.append(cursor)
            continue
        if len(line) <= 24 and (_SOURCE_MARK.search(line) or re.search(r"\d+\s*天", line)):
            found.append(cursor)
            continue
        break
    return found


def _entry_blurb_at(lines: list[str], index: int) -> int:
    """说明可以紧挨着名字，也可以隔着等级、价格这种短行。"""
    for cursor in range(index + 1, min(len(lines), index + 5)):
        line = lines[cursor]
        if "。" in line and 6 <= len(line) <= 40:
            return cursor
        # 碰到另一个短中文名就停下，避免把下一条武器的说明算到表头上。
        if re.fullmatch(r"[\u4e00-\u9fff]{2,8}", line) or len(line) > 24:
            return -1
    return -1


def _is_entry_name(lines: list[str], index: int) -> bool:
    """一条词条名：短中文，后面几行里有一句短说明。"""
    line = lines[index]
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,8}", line):
        return False
    return _entry_blurb_at(lines, index) >= 0


def _entry_span(lines: list[str], index: int) -> list[int] | None:
    """短行若落在某条词条的字段里，连同词条名和它下面的说明一起取出。"""
    head = None
    for cursor in range(index, max(-1, index - 30), -1):
        if _is_entry_name(lines, cursor):
            head = cursor
            break
    if head is None:
        return None
    span = [head]
    for cursor in range(head + 1, min(len(lines), head + 28)):
        if _is_entry_name(lines, cursor):
            break
        line = lines[cursor]
        if line.startswith("data-sort-value"):
            continue
        if re.search(r"\d[\d,]*金$", line) and any(re.search(r"\d[\d,]*金$", lines[i]) for i in span):
            continue
        span.append(cursor)
    return span


def _excerpt(question: str, text: str) -> str:
    """短文整篇送入。长文只留和问题有关的段落，控制进模型的字数。"""
    text = (text or "").strip()
    if len(text) <= EXCERPT_CHARS:
        return text
    terms = _specific_terms(question)
    paragraphs = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    if not paragraphs:
        return text[:EXCERPT_CHARS]
    ranked = sorted(
        ((_paragraph_score(part, terms, question), index, part) for index, part in enumerate(paragraphs)),
        key=lambda item: (-item[0], item[1]),
    )
    picked: list[int] = []
    used = 0
    for score, index, part in ranked:
        if score <= 0 or used >= EXCERPT_CHARS:
            break
        if re.match(r"^\d+(\.\d+)+\s", part):
            continue
        if part.count("•") >= 2 and not any(term in part for term in terms):
            continue
        if "混合种子" in part and "混合" not in question:
            continue
        group = _entry_span(paragraphs, index) or [index]
        if group == [index] and len(part) <= 16:
            group.extend(_source_followers(paragraphs, index, terms))
        for cursor in group:
            if cursor in picked:
                continue
            room = EXCERPT_CHARS - used
            if room < 20:
                break
            piece = paragraphs[cursor]
            if len(piece) > room:
                piece = _window(piece, terms, room)
            if not piece:
                continue
            paragraphs[cursor] = piece
            picked.append(cursor)
            used += len(piece)
    if not picked:
        return text[:EXCERPT_CHARS]
    return "\n".join(paragraphs[index] for index in sorted(picked))


def _kind_question(question: str) -> bool:
    return any(key in question for key in ("多少种", "几种", "有哪些", "哪些", "有什么", "可以钓", "能钓"))


_WEAPON_HEADS = ("剑", "匕首", "棍棒", "弹弓", "无法获得的武器")


def _section_blurb(lines: list[str], name: str) -> str:
    """取分类标题下面的第一句，不取表格里某一把武器的说明。"""
    for index, line in enumerate(lines[:-1]):
        if line != name or "•" in lines[index + 1]:
            continue
        sentence = lines[index + 1].split("。")[0].strip()
        if "。" in lines[index + 1] and 6 <= len(sentence) <= 40 and not sentence.startswith("影响"):
            return sentence + "。"
    return ""


def _weapon_entries(lines: list[str]) -> list[dict]:
    """种类是目录里的剑、匕首、棍棒、弹弓、无法获得的武器。武器属性不是一种武器。"""
    present = {
        name
        for name in _WEAPON_HEADS
        if any(re.fullmatch(rf"\d+\s+{re.escape(name)}", line) for line in lines)
    }
    if not {"剑", "匕首", "棍棒", "弹弓"} <= present:
        return []
    entries: list[dict] = []
    for name in _WEAPON_HEADS:
        if name not in present:
            continue
        entries.append({
            "name": name,
            "body": _section_blurb(lines, name) or "这一类武器。",
            "place": "",
            "season": "",
            "kind": "类别",
        })
    return entries


def _entry_tree(text: str) -> list[dict]:
    """把扁平正文收成词条树：名字是父节点，说明、地点、季节是子节点。"""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    weapons = _weapon_entries(lines)
    if weapons:
        return weapons
    entries: list[dict] = []
    seen: set[str] = set()
    index = 0
    while index < len(lines) - 1:
        name = lines[index]
        if name in {"食谱", "生鱼片利润", "历史", "参考", "外部链接", "生鱼寿司", "高级肥料", "生鱼片"}:
            break
        if not _is_entry_name(lines, index) or name in seen or name in {"钓鱼小游戏", "运动模式"}:
            index += 1
            continue
        blurb_at = _entry_blurb_at(lines, index)
        blurb = lines[blurb_at]
        if blurb.startswith("影响"):
            index = blurb_at + 1
            continue
        fields: list[str] = []
        cursor = blurb_at + 1
        while cursor < len(lines) and cursor < index + 28 and not _is_entry_name(lines, cursor):
            line = lines[cursor]
            if not line.startswith("data-sort-value") and not re.fullmatch(r"[\d,]+金", line):
                fields.append(line)
            cursor += 1
        place = next((line for line in fields if any(mark in line for mark in ("海洋", "河流", "湖泊", "矿井", "沙漠", "下水道", "森林", "姜岛", "池塘"))), "")
        season = next((line for line in fields if "季" in line and len(line) <= 12), "")
        entries.append({"name": name, "body": blurb, "place": place, "season": season})
        seen.add(name)
        index = cursor
    return entries


def _tree_context(title: str, text: str, question: str) -> str:
    """标题用来定位，送给模型的是父节点名字和它下面的正文。"""
    if not _kind_question(question):
        return ""
    entries = _entry_tree(text)
    if len(entries) < 5:
        return ""
    if entries[0].get("kind") == "类别":
        names = "、".join(entry["name"] for entry in entries)
        blocks = [
            f"【目录】{title}有 {len(entries)} 种：{names}。"
            "武器属性（速度、防御、重量、暴击率、暴击力量）不是武器，每一把具体的剑或匕首也不是单独的一种。"
            "数种类时只数下面这几个【词条】。"
        ]
    else:
        blocks = [
            f"【目录】{title} 下面有 {len(entries)} 个词条。数种类时只数【词条】，不要数地点、季节或用途。"
        ]
    for entry in entries:
        line = f"【词条】{entry['name']} 正文：{entry['body']}"
        if entry["place"]:
            line += f" 子字段地点：{entry['place']}"
        if entry["season"]:
            line += f" 子字段季节：{entry['season']}"
        blocks.append(line)
    return "\n".join(blocks)


def _format_parents(docs: list[Document], question: str) -> str:
    blocks = []
    total = 0
    for index, doc in enumerate(docs, start=1):
        if total >= CONTEXT_BUDGET:
            break
        title = doc.metadata.get("title") or "Wiki"
        tree = _tree_context(str(title), doc.page_content or "", question)
        body = tree or _excerpt(question, doc.page_content or "")
        room = CONTEXT_BUDGET - total
        if len(body) > room:
            body = body[:room]
        if not body.strip():
            continue
        blocks.append(f"【资料 {index}】标题：{title}\n{body}")
        total += len(body)
    return "\n\n".join(blocks)


def _is_no_data(text: str) -> bool:
    compact = re.sub(r"[\s。．.！!：:「」]", "", text or "")
    return compact == NO_DATA or (NO_DATA in compact and len(compact) <= len(NO_DATA) + 8)


def sticks_to_source(answer: str, source: str) -> bool:
    """总结必须沿用资料里的词。出现资料外的英文，或大段对不上，就当作没总结成。"""
    answer = (answer or "").strip()
    source = source or ""
    if len(answer) < 8:
        return False
    for word in re.findall(r"[A-Za-z]{3,}", answer):
        if word.lower() not in source.lower():
            return False
    chars = "".join(re.findall(r"[\u4e00-\u9fff]", answer))
    if len(chars) < 12:
        return True
    grams = [chars[i : i + 2] for i in range(len(chars) - 1)]
    hit = sum(1 for gram in grams if gram in source)
    return hit / len(grams) >= 0.5


def chat_supports_thinking() -> bool:
    """仅当 Ollama 声明该模型带 thinking 时才开推理；granite3.2 等会直接 400。"""
    try:
        response = httpx.post(
            f"{ollama_base_url()}/api/show",
            json={"name": chat_model_name()},
            timeout=5.0,
        )
        response.raise_for_status()
        caps = response.json().get("capabilities") or []
        return "thinking" in caps
    except Exception:
        return False


def _chat():
    from langchain_ollama import ChatOllama

    thinking = chat_supports_thinking()
    return ChatOllama(
        model=chat_model_name(),
        base_url=ollama_base_url(),
        temperature=0,
        num_ctx=NUM_CTX,
        num_predict=180,
        reasoning=thinking,
        keep_alive="30m",
        client_kwargs={"timeout": 120.0 if thinking else 90.0},
    )


_COUNT_INTENT = re.compile(r"多少种|有多少|有几[种把个条只]|几[种把个条只]|how many|number of", re.I)
_PRICE_INTENT = re.compile(r"多少钱|多少金|售价|价格|多贵|卖多少")
# 长词放前面，避免「传说鱼类」被「鱼」截走。
_COUNT_TARGETS = (
    ("无法获得的武器", "Weapon", "无法获得的武器", "无法获得的武器"),
    ("传说鱼类二代", "Fish", "传说鱼类二代", "传说鱼类二代"),
    ("传说鱼", "Fish", "传说鱼类", "传说鱼类"),
    ("蟹笼", "Fish", "蟹笼鱼类", "蟹笼鱼类"),
    ("夜市鱼", "Fish", "夜市鱼", "夜市鱼"),
    ("常规鱼", "Fish", "常规鱼类", "常规鱼类"),
    ("匕首", "Weapon", "匕首", "匕首"),
    ("棍棒", "Weapon", "棍棒", "棍棒"),
    ("弹弓", "Weapon", "弹弓", "弹弓"),
    ("剑", "Weapon", "剑", "剑"),
    ("春季作物", "Crop", "春季作物", "春季作物"),
    ("夏季作物", "Crop", "夏季作物", "夏季作物"),
    ("秋季作物", "Crop", "秋季作物", "秋季作物"),
    ("冬季作物", "Crop", "冬季作物", "冬季作物"),
    ("特殊作物", "Crop", "特殊作物", "特殊作物"),
    ("农作物", "Crop", None, "农作物"),
    ("作物", "Crop", None, "农作物"),
    ("武器", "Weapon", None, "武器"),
    ("weapon", "Weapon", None, "武器"),
    ("村民", "Villager", None, "村民"),
    ("villager", "Villager", None, "村民"),
    ("鱼", "Fish", None, "鱼"),
    ("fish", "Fish", None, "鱼"),
)


def entity_count(question: str) -> dict | None:
    """数量题对应 wiki_entities 的精确计数。售价题不算数量题。"""
    text = (question or "").strip()
    if not text or not _COUNT_INTENT.search(text) or _PRICE_INTENT.search(text):
        return None
    folded = text.lower()
    for needle, category, subcategory, label in _COUNT_TARGETS:
        if needle.lower() not in folded:
            continue
        sql = "SELECT COUNT(*) FROM wiki_entities WHERE category = ?"
        args: list[str] = [category]
        if subcategory:
            sql += " AND subcategory = ?"
            args.append(subcategory)
        conn = connect()
        total = conn.execute(sql, args).fetchone()[0]
        conn.close()
        return {
            "label": label,
            "total": int(total),
            "category": category,
            "subcategory": subcategory,
        }
    return None


def _count_note(fact: dict) -> str:
    return (
        f"已核实的数量：{fact['label']}共有 {fact['total']} 个。"
        "回答数量时第一句必须使用这个数字，不要重新数，也不要改成别的数。"
    )


_FTS_STOPS = frozenset({
    "有", "多少", "几", "种", "把", "个", "条", "只", "吗", "呢", "的", "了",
    "是", "什么", "哪些", "怎么", "如何", "请问", "可以", "一下", "这个", "那个",
    "还有", "是否", "吧", "在", "和", "与", "或", "哪", "哪里", "哪儿", "那里",
    "那儿", "呀", "啊", "嘛", "着", "过", "就", "也", "都", "不", "没", "很",
    "太", "最", "被", "给", "对", "从", "到", "会", "能", "要", "想", "问",
    "一共", "共", "how", "many", "there", "are", "the", "of", "a", "an", "is",
})


def _fts_keywords(question: str) -> list[str]:
    """用 jieba 把中文问句切开，丢掉「有多少」这类问法词。"""
    import logging

    import jieba

    jieba.setLogLevel(logging.ERROR)
    words: list[str] = []
    for word in jieba.lcut(question or ""):
        word = re.sub(r"[^\w\u4e00-\u9fff]", "", word).strip()
        if not word or word.lower() in _FTS_STOPS or word in words:
            continue
        words.append(word)
        if len(words) >= 4:
            break
    return words


def _fts_match(question: str) -> str:
    """「有多少武器？」变成 title:武器 OR body:武器，避免整句去对 FTS。"""
    parts = [f"title:{word} OR body:{word}" for word in _fts_keywords(question)]
    return " OR ".join(parts)


def _fts_context(question: str) -> tuple[str, list[dict]]:
    """FTS5 只取命中附近的短摘录，不把整页正文送进模型。"""
    match = _fts_match(question)
    if not match:
        return "", []
    conn = connect()
    try:
        rows = conn.execute(
            """
            SELECT wiki_pages.pageid AS pageid,
                   COALESCE(NULLIF(wiki_pages.display_title, ''), wiki_pages.title) AS title,
                   wiki_pages.url AS url,
                   snippet(wiki_fts, 1, '', '', '…', 32) AS snip
            FROM wiki_fts
            JOIN wiki_pages ON wiki_pages.pageid = wiki_fts.rowid
            WHERE wiki_fts MATCH ?
            ORDER BY rank
            LIMIT 3
            """,
            (match,),
        ).fetchall()
    except Exception:
        conn.close()
        return "", []
    conn.close()
    blocks: list[str] = []
    sources: list[dict] = []
    seen: set[int] = set()
    for index, row in enumerate(rows, start=1):
        snippet = re.sub(r"\s+", " ", row["snip"] or "").strip()
        if len(snippet) > 180:
            snippet = snippet[:180].rstrip() + "…"
        if not snippet:
            continue
        blocks.append(f"【资料 {index}】标题：{row['title']}\n{snippet}")
        pageid = int(row["pageid"])
        if pageid not in seen:
            seen.add(pageid)
            sources.append({"title": row["title"], "url": row["url"] or "", "pageid": pageid})
    return "\n\n".join(blocks), sources


def _prepare_ollama(question: str) -> tuple[str, str, list[dict]] | None:
    """数量题把 SQL 计数写进系统提示，并附上 FTS 命中的短摘录。"""
    context, sources = _fts_context(question)
    fact = entity_count(question)
    if fact:
        count_line = f"{fact['label']}共有 {fact['total']} 个。"
        merged = count_line if not context else count_line + "\n\n" + context
        return SYSTEM_PROMPT + "\n" + _count_note(fact), merged, sources
    if not context.strip():
        return None
    return SYSTEM_PROMPT, context, sources


def _complete(model, human: str, variables: dict, num_predict: int, system: str = SYSTEM_PROMPT) -> str:
    model.num_predict = num_predict
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system),
            ("human", human),
        ]
    )
    return (prompt | model | StrOutputParser()).invoke(variables).strip()


def _named_wiki_documents(question: str) -> list[Document]:
    """按条目名直接读站内 Wiki。向量检索没命中时，仍然能把原文交给模型。"""
    from .wiki import content_terms, get_pages_named, pages_mentioned_in, search_wiki

    pages = pages_mentioned_in(question)
    if not pages:
        # 「苹果售价多少」要先按拆出的「苹果」精确取页，避免向量漂到家具价签。
        pages = get_pages_named(content_terms(question))
    if not pages:
        pages = search_wiki(question, limit=2)
    documents: list[Document] = []
    for page in pages[:2]:
        body = _clean_body(page.get("body") or "")
        if len(body) < 20:
            continue
        documents.append(
            Document(
                page_content=body,
                metadata={
                    "pageid": int(page["pageid"]),
                    "title": page.get("display_title") or page.get("title") or "Wiki",
                    "url": page.get("url") or "",
                },
            )
        )
    return documents

def _merge_parents(question: str, retrieved: list[Document]) -> list[Document]:
    chosen: list[Document] = []
    seen: set[int] = set()
    named = _named_wiki_documents(question)
    # 问句已经精确点到条目时，只用该页，避免向量检索漂到无关家具价签。
    pool = named if named else _select_parents(retrieved, question)
    for doc in pool:
        pageid = int(doc.metadata.get("pageid") or 0)
        if pageid and pageid in seen:
            continue
        if pageid:
            seen.add(pageid)
        chosen.append(doc)
        if len(chosen) >= 2:
            break
    return chosen

def answer_entity_count(question: str, history: list | None = None) -> dict | None:
    """数量题先查表。模型可用时把精确数字写进系统提示，否则直接返回这个数字。"""
    fact = entity_count(question)
    if not fact:
        return None
    plain = f"{fact['label']}共有 {fact['total']} 个。"
    sources: list[dict] = []
    names = ollama_model_names()
    if names and _model_ready(names, chat_model_name()):
        try:
            prepared = _prepare_ollama(question)
            if prepared:
                system, context, sources = prepared
                answer = _complete(
                    _chat(),
                    _ANSWER_USER,
                    {"question": question, "context": context, "history": history_block(history)},
                    num_predict=180,
                    system=system,
                )
                if answer and not _is_no_data(answer) and sticks_to_source(answer, context):
                    return {"answer": answer, "sources": sources, "mode": "langchain"}
        except Exception:
            pass
    if not sources:
        _context, sources = _fts_context(question)
    return {"answer": plain, "sources": sources, "mode": "sql"}


def answer_with_langchain(question: str, history: list | None = None) -> dict | None:
    """数量题注入精确计数；其余问题只用 FTS 短摘录，再交给模型写答案。"""
    names = ollama_model_names()
    if names is None or not _model_ready(names, chat_model_name()):
        return None
    prepared = _prepare_ollama(question)
    if not prepared:
        return None
    system, context, sources = prepared
    answer = _complete(
        _chat(),
        _ANSWER_USER,
        {"question": question, "context": context, "history": history_block(history)},
        num_predict=260,
        system=system,
    )
    if not answer or _is_no_data(answer) or not sticks_to_source(answer, context):
        return None
    return {"answer": answer, "sources": sources, "mode": "langchain"}


def stream_answer_with_langchain(question: str, history: list | None = None):
    """流式版 answer_with_langchain。

    返回 None（模型不可用或没有摘录），或 (sources, gen, holder)。
    gen 逐块产出答案文本，遍历结束后 holder["ok"] / holder["text"]
    与同步版的校验结果一致。"""
    names = ollama_model_names()
    if names is None or not _model_ready(names, chat_model_name()):
        return None

    prepared = _prepare_ollama(question)
    if not prepared:
        return None
    system, context, sources = prepared

    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    model = _chat()
    model.num_predict = 260
    chain = (
        ChatPromptTemplate.from_messages([("system", system), ("human", _ANSWER_USER)])
        | model
        | StrOutputParser()
    )
    variables = {"question": question, "context": context, "history": history_block(history)}
    holder = {"ok": False, "text": ""}
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
        holder["ok"] = bool(text) and not _is_no_data(text) and sticks_to_source(text, context)

    return sources, gen(), holder


def main() -> None:
    documents = load_wiki_documents()
    pieces = child_splitter().split_documents(documents)
    longest = max((len(item.page_content) for item in pieces), default=0)
    print(
        f"父文档 {len(documents)} 篇（整篇保存）。"
        f"子段 {len(pieces)} 段，按句读切开，重叠 {CHILD_CHUNK_OVERLAP} 字，最长一段 {longest} 字。"
    )
    names = ollama_model_names()
    if names is None:
        print("Ollama 没开，检索库还没写。")
        return
    if not _model_ready(names, embed_model_name()):
        print(f"还没有嵌入模型。请先运行：ollama pull {embed_model_name()}")
        return
    count = ensure_index()
    print(f"已写入：{count} 篇完整父文档，子段在 Chroma。")


if __name__ == "__main__":
    main()
