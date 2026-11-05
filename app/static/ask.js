(function () {
  const DRAFT_KEY = "stardew-ask-draft";
  const HISTORY_KEY = "stardew-ask-history";
  let busy = false;
  let active = null;
  let lastTurn = null; // { question, answer, mode }，供反馈接口使用

  const MODE_LABELS = {
    langchain: "由本地模型整理",
    ollama: "由本地模型整理",
    llm: "由在线模型整理",
    wiki: "来自 Wiki 原文整理",
    off: "规则兜底",
  };

  function escapeHtml(text) {
    return String(text || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function questionBox() {
    return document.getElementById("question");
  }

  function resultBox() {
    return document.getElementById("result");
  }

  function cancelBtn() {
    return document.getElementById("ask-cancel");
  }

  function statusBox() {
    return document.getElementById("ask-status");
  }

  function submitBtn(form) {
    return form ? form.querySelector('button[type="submit"]') : null;
  }

  /* ---------- 小谷就绪状态灯 ---------- */

  function primeStatusBox() {
    const box = statusBox();
    if (box && !box.textContent) {
      box.className = "ask-status";
      box.textContent = "检测中…";
      box.title = "正在检查本地模型和检索库";
    }
  }

  function renderStatus(status) {
    const box = statusBox();
    if (!box) return;
    if (!status) return;
    const ready = status.ollama && status.chat_ready && status.embed_ready && status.index_ready;
    const warming = status.ollama && (!status.index_ready || !status.chat_ready || !status.embed_ready);
    if (ready) {
      box.className = "ask-status ready";
      box.textContent = "小谷就绪";
      box.title = "本地模型和 Wiki 检索库均已就绪";
    } else if (warming) {
      box.className = "ask-status warming";
      box.textContent = "准备中";
      box.title = status.index_ready ? "模型或索引未完全就绪" : "检索库正在写入，请稍后再试";
    } else {
      box.className = "ask-status offline";
      box.textContent = "本地模型未连接";
      box.title = "Ollama 未启动或缺少模型，提问将走 Wiki 规则模式";
    }
  }

  async function fetchStatus() {
    primeStatusBox();
    try {
      const response = await fetch("/api/ask/status", { credentials: "same-origin" });
      if (!response.ok) return;
      const status = await response.json();
      renderStatus(status);
    } catch (_) {}
  }

  /* ---------- 草稿与多轮历史 ---------- */

  function restoreDraft() {
    const box = questionBox();
    if (!box || box.value.trim()) return;
    try {
      const draft = sessionStorage.getItem(DRAFT_KEY);
      if (draft) box.value = draft;
    } catch (_) {}
  }

  function loadHistory() {
    try {
      const raw = JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]");
      return Array.isArray(raw) ? raw.slice(-3) : [];
    } catch (_) {
      return [];
    }
  }

  function saveTurn(question, answer, mode) {
    if (!question || !answer) return;
    try {
      const turns = loadHistory();
      turns.push({ q: String(question).slice(0, 200), a: String(answer).slice(0, 400) });
      sessionStorage.setItem(HISTORY_KEY, JSON.stringify(turns.slice(-3)));
    } catch (_) {}
    lastTurn = { question: question, answer: String(answer || ""), mode: mode || "" };
  }

  /* ---------- 忙碌状态与加载动画 ---------- */

  function setBusy(form, on) {
    busy = on;
    const btn = submitBtn(form);
    if (btn) {
      btn.disabled = on;
      btn.textContent = on ? "小谷思考中…" : "开始整理回答";
    }
    const cancel = cancelBtn();
    if (cancel) {
      cancel.disabled = !on;
      cancel.classList.toggle("ghost", !on);
    }
    const box = questionBox();
    if (box) box.readOnly = on;
  }

  function renderLoading(text) {
    const result = resultBox();
    if (!result) return;
    result.innerHTML =
      '<div class="loading-box">' +
      '<span class="loading-dots" aria-hidden="true"></span>' +
      '<p class="loading-text">' + escapeHtml(text || "小谷正在根据你的问题思考…") + "</p>" +
      "</div>";
  }

  function setLoadingText(text) {
    const result = resultBox();
    if (!result) return;
    const el = result.querySelector(".loading-text");
    if (el) el.textContent = text || "";
  }

  function showError(text) {
    const result = resultBox();
    if (!result) return;
    result.innerHTML = '<p class="error">' + escapeHtml(text) + "</p>";
  }

  /* ---------- 渲染答案 ---------- */

  function modeLineHtml(mode, sources) {
    const label = MODE_LABELS[mode] || "来自 Wiki 资料";
    const count = (sources || []).length;
    let suffix = "";
    if (mode !== "off") {
      suffix = count > 0 ? " · 依据 " + count + " 条 Wiki 资料" : " · 未命中具体资料";
    }
    return '<p class="mode-line">' + escapeHtml(label + suffix) + "</p>";
  }

  function answerHtml(text) {
    return String(text || "")
      .split("\n")
      .filter(function (line) {
        return line.trim();
      })
      .map(function (line) {
        return "<p>" + escapeHtml(line) + "</p>";
      })
      .join("");
  }

  function sourcesHtml(sources) {
    const list = sources || [];
    if (!list.length) return "";
    const links = list
      .map(function (s) {
        return '<li><a href="/wiki/' + s.pageid + '">' + escapeHtml(s.title) + "</a></li>";
      })
      .join("");
    return '<div class="sources"><strong>参考资料</strong><ul>' + links + "</ul></div>";
  }

  function extrasHtml() {
    return (
      '<p class="ask-followup">可以接着追问（比如「那夏天呢」），小谷会记得最近几轮对话。</p>' +
      '<div class="ask-feedback"><span>这个回答有帮助吗？</span>' +
      '<button type="button" class="ask-fb-btn" data-helpful="1">有帮助</button>' +
      '<button type="button" class="ask-fb-btn" data-helpful="0">没帮助</button>' +
      "</div>"
    );
  }

  function renderSkeleton(mode, sources) {
    const result = resultBox();
    if (!result) return;
    result.innerHTML = modeLineHtml(mode, sources) + '<div class="answer"></div>' + sourcesHtml(sources);
  }

  function setAnswerText(text, streaming) {
    const result = resultBox();
    if (!result) return;
    const el = result.querySelector(".answer");
    if (!el) return;
    el.innerHTML = answerHtml(text) + (streaming ? '<span class="stream-caret"></span>' : "");
  }

  function appendExtras() {
    const result = resultBox();
    if (!result) return;
    result.insertAdjacentHTML("beforeend", extrasHtml());
  }

  function finalize(question, raw, mode, sources) {
    if (!raw || !raw.trim()) raw = "小谷这次没能生成回答，换个问法再试一次。";
    renderSkeleton(mode, sources);
    setAnswerText(raw, false);
    appendExtras();
    saveTurn(question, raw, mode);
  }

  function renderResultJson(question, data) {
    if (!data || !data.answer) {
      showError("提问失败，请再试一次。");
      return;
    }
    const mode = data.mode || "wiki";
    const sources = data.sources || [];
    const result = resultBox();
    if (!result) return;
    result.innerHTML =
      modeLineHtml(mode, sources) +
      '<div class="answer">' +
      answerHtml(data.answer) +
      "</div>" +
      sourcesHtml(mode === "off" ? [] : sources) +
      extrasHtml();
    saveTurn(question, data.answer, mode);
  }

  /* ---------- 流式提问 ---------- */

  async function askJson(question, signal) {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question }),
      credentials: "same-origin",
      signal: signal,
    });
    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      data = {};
    }
    if (!response.ok) {
      return { error: data.detail || "提问失败，请再试一次。" };
    }
    return { data: data };
  }

  async function askStream(question, signal) {
    const response = await fetch("/api/ask/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question, history: loadHistory() }),
      credentials: "same-origin",
      signal: signal,
    });
    const ctype = response.headers.get("content-type") || "";
    if (!response.ok || ctype.indexOf("text/event-stream") < 0) {
      // 服务器不支持流式（或出错）：回退到 JSON 接口。
      if (response.ok) {
        const json = await askJson(question, signal);
        if (json.error) showError(json.error);
        else renderResultJson(question, json.data);
        return;
      }
      let detail = "";
      try {
        const data = await response.json();
        detail = data.detail || "";
      } catch (_) {}
      showError(detail || "提问失败，请再试一次。");
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let skeleton = false;
    let raw = "";
    let mode = "wiki";
    let sources = [];
    let finished = false;

    function ensureSkeleton() {
      if (!skeleton) {
        renderSkeleton(mode, sources);
        skeleton = true;
      }
    }

    function handleEvent(event) {
      if (!event || !event.kind) return;
      if (event.kind === "status") {
        setLoadingText(event.text || "");
        return;
      }
      if (event.kind === "meta") {
        mode = event.mode || mode;
        sources = event.sources || sources;
        renderSkeleton(mode, sources);
        skeleton = true;
        return;
      }
      if (event.kind === "delta") {
        ensureSkeleton();
        raw += event.text || "";
        setAnswerText(raw, true);
        return;
      }
      if (event.kind === "replace") {
        raw = event.text || "";
        if (event.mode) mode = event.mode;
        if (event.sources) sources = event.sources;
        renderSkeleton(mode, sources);
        skeleton = true;
        setAnswerText(raw, true);
        return;
      }
      if (event.kind === "done") {
        if (event.sources) sources = event.sources;
        finalize(question, raw, mode, sources);
        finished = true;
        return;
      }
      if (event.kind === "error") {
        showError(event.message || "回答生成中断，请再问一次。");
        finished = true;
      }
    }

    function pump(frame) {
      frame.split("\n").forEach(function (line) {
        if (line.indexOf("data: ") !== 0) return;
        try {
          handleEvent(JSON.parse(line.slice(6)));
        } catch (_) {}
      });
    }

    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      buffer += decoder.decode(chunk.value, { stream: true });
      let cut;
      while ((cut = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        pump(frame);
      }
    }
    if (!finished) {
      // 流意外结束：有文本就按最终答案收尾，否则报错。
      if (raw.trim()) finalize(question, raw, mode, sources);
      else showError("回答生成中断，请再问一次。");
    }
  }

  /* ---------- 反馈 ---------- */

  function sendFeedback(helpful, btn) {
    if (!lastTurn) return;
    const row = btn.closest(".ask-feedback");
    if (row) row.innerHTML = '<span class="muted">感谢反馈！</span>';
    fetch("/api/ask/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question: lastTurn.question,
        answer: (lastTurn.answer || "").slice(0, 6000),
        mode: lastTurn.mode || "",
        helpful: helpful,
      }),
      credentials: "same-origin",
    }).catch(function () {});
  }

  /* ---------- 事件绑定（全部用委托，软导航换页后依然有效） ---------- */

  document.addEventListener("click", function (event) {
    const cancel = event.target.closest("#ask-cancel");
    if (cancel) {
      if (active) {
        try {
          active.abort();
        } catch (_) {}
      }
      return;
    }
    const fb = event.target.closest(".ask-fb-btn");
    if (fb && !fb.disabled) {
      sendFeedback(fb.getAttribute("data-helpful") === "1", fb);
      return;
    }
    const chip = event.target.closest(".ask-chip");
    if (chip) {
      const q = chip.getAttribute("data-question");
      if (!q) return;
      const box = questionBox();
      const form = document.getElementById("ask-form");
      if (!box || !form) return;
      box.value = q;
      try {
        sessionStorage.setItem(DRAFT_KEY, q);
      } catch (_) {}
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    }
  });

  document.addEventListener("input", function (event) {
    if (event.target && event.target.id === "question") {
      try {
        sessionStorage.setItem(DRAFT_KEY, event.target.value);
      } catch (_) {}
    }
  });

  document.addEventListener(
    "submit",
    async function (event) {
      const form = event.target;
      if (!(form instanceof HTMLFormElement) || form.id !== "ask-form") return;
      event.preventDefault();
      event.stopPropagation();

      const box = questionBox();
      const result = resultBox();
      if (!box || !result) return;

      const question = box.value.trim();
      if (!question) return;
      if (busy) return;

      try {
        sessionStorage.setItem(DRAFT_KEY, question);
      } catch (_) {}
      box.value = question;

      if (active) {
        try {
          active.abort();
        } catch (_) {}
      }
      active = typeof AbortController !== "undefined" ? new AbortController() : null;
      const timer =
        active &&
        setTimeout(function () {
          try {
            active.abort();
          } catch (_) {}
        }, 180000);

      setBusy(form, true);
      result.classList.remove("hidden");
      renderLoading("小谷正在 Wiki 里检索相关资料…");

      try {
        await askStream(question, active ? active.signal : undefined);
      } catch (err) {
        if (err && err.name === "AbortError") {
          showError("这次提问已取消或超时，请再问一次。");
        } else {
          showError("提问失败，请再试一次。");
        }
      } finally {
        if (timer) clearTimeout(timer);
        active = null;
        setBusy(form, false);
        if (box && !box.value.trim()) box.value = question;
        restoreDraft();
      }
    },
    true
  );

  /* ---------- 初始化与软导航 ---------- */

  restoreDraft();
  primeStatusBox();
  fetchStatus();

  const main = document.querySelector("main.wrap");
  if (main && typeof MutationObserver !== "undefined") {
    const observer = new MutationObserver(function () {
      restoreDraft();
      fetchStatus();
      const form = document.getElementById("ask-form");
      const btn = submitBtn(form);
      if (btn && btn.disabled && !busy) {
        btn.disabled = false;
        btn.textContent = "开始整理回答";
      }
      const cancel = cancelBtn();
      if (cancel) {
        cancel.disabled = !busy;
        cancel.classList.toggle("ghost", !busy);
      }
      const box = questionBox();
      if (box) box.readOnly = busy;
    });
    observer.observe(main, { childList: true });
  }
})();
