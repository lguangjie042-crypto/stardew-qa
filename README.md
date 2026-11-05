# 星露谷问答社

中文社区站：资料来自[星露谷物语官方中文 Wiki](https://zh.stardewvalleywiki.com/)，用户可以发帖，也可以问站内 AI。AI 只根据已同步的 Wiki 条目生成答案。

## 运行

需要 Python 3.10+。

```powershell
cd $env:USERPROFILE\stardew-qa
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.ingest
python -m uvicorn app.main:app --reload --port 8000
```

浏览器打开 http://127.0.0.1:8000

首次 `python -m app.ingest` 会从 `https://zh.stardewvalleywiki.com/mediawiki/api.php` 拉取核心条目和分类页面。也可以在网站上点「同步官方 Wiki」。

## AI

问答固定经过四步：用小段在 Chroma 里检索，用 LangChain 的 `ParentDocumentRetriever` 取回该段所属的整篇 Wiki 原文，先让 Ollama 通读并归纳，再按问题写成完整答案。小段只负责命中。模型读到的是整篇原文。

子段按段落和句号切开，大约 400 字，相邻段重叠 80 字。父文档不做切分。

```powershell
ollama pull nomic-embed-text
ollama pull granite3.2:8b
python -m app.rag
```

`python -m app.rag` 会打印篇数和子段数。Ollama 开着、嵌入模型在时，子段写入 `data/chroma`，全文写入 `data/parent_store`。网站启动后也会在后台补写。模型名可在 `.env` 里改 `OLLAMA_EMBED_MODEL` 和 `OLLAMA_MODEL`。

默认先用规则整理「多少种 / 有哪些」这类带约束的题，再交给 Ollama。若要优先体验本地模型，在 `.env` 设 `ASK_PREFER_OLLAMA=1`；Ollama 失败或未就绪时仍回退到规则和 Wiki 原文。

Ollama 没开时，提问仍用本地 Wiki 整理原文。若要改用 OpenAI 兼容接口润色这些原文，复制 `.env.example` 为 `.env` 并填写密钥。
