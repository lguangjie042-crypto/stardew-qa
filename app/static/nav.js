(function () {
  const main = document.querySelector("main.wrap");
  if (!main) return;

  function sameDocument(url) {
    return url.origin === location.origin;
  }

  function runScripts(root) {
    root.querySelectorAll("script").forEach(function (old) {
      // 小谷脚本已挂在整页，软导航时不要重复注入，避免绑定丢失或抢答。
      const src = old.getAttribute("src") || "";
      if (src.indexOf("/static/ask.js") >= 0) {
        old.remove();
        return;
      }
      const script = document.createElement("script");
      script.async = false;
      if (old.src) script.src = old.src;
      else script.textContent = old.textContent;
      old.replaceWith(script);
    });
  }

  // 手机上打开私聊时锁住背后的列表滚动，退出后再还到原来的位置。
  let chatLockY = null;

  function hereScroll() {
    return chatLockY != null ? chatLockY : window.scrollY;
  }

  function place(y) {
    history.scrollRestoration = "manual";
    const narrow = window.matchMedia("(max-width: 720px)").matches;
    const open = document.body.classList.contains("chat-thread");
    if (open && narrow) {
      if (chatLockY == null) chatLockY = y;
      document.body.style.position = "fixed";
      document.body.style.top = "-" + chatLockY + "px";
      document.body.style.left = "0";
      document.body.style.right = "0";
      document.body.style.width = "100%";
      return;
    }
    chatLockY = null;
    document.body.style.position = "";
    document.body.style.top = "";
    document.body.style.left = "";
    document.body.style.right = "";
    document.body.style.width = "";
    window.scrollTo(0, y);
    requestAnimationFrame(function () {
      window.scrollTo(0, y);
    });
  }

  function apply(doc, scrollY) {
    const next = doc.querySelector("main.wrap");
    if (!next) return false;
    if (window.__composeUnbind) window.__composeUnbind();
    main.innerHTML = next.innerHTML;
    document.title = doc.title;
    document.body.className = doc.body.className;
    const fresh = new Map();
    doc.querySelectorAll("header nav a").forEach(function (link) {
      fresh.set(link.getAttribute("href"), link.className);
    });
    document.querySelectorAll("header nav a").forEach(function (link) {
      const name = fresh.get(link.getAttribute("href"));
      if (name != null) link.className = name;
    });
    if (window.__valleyUnbind) window.__valleyUnbind();
    const nextFoot = doc.querySelector("footer.foot");
    const foot = document.querySelector("footer.foot");
    if (nextFoot && foot) foot.innerHTML = nextFoot.innerHTML;
    runScripts(main);
    place(scrollY);
    return true;
  }

  function keepsPlace(anchor, url) {
    if (anchor.classList.contains("chip")) return true;
    if (url.pathname !== "/wiki") return false;
    if (!url.searchParams.has("cat") || url.searchParams.has("q") || url.searchParams.has("p")) return false;
    return !!anchor.closest(".catalog-card h3, h3.catalog-wide");
  }

  function go(url, push, scrollY) {
    const y = typeof scrollY === "number" ? scrollY : 0;
    return fetch(url, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("nav");
        return response.text();
      })
      .then(function (html) {
        const doc = new DOMParser().parseFromString(html, "text/html");
        if (!apply(doc, y)) throw new Error("nav");
        if (push) {
          history.pushState(
            { nav: 1, y: y, back: location.pathname + location.search + location.hash },
            "",
            url
          );
        }
      });
  }

  function isChatPath(path) {
    return path === "/chat" || /^\/chat\/\d+$/.test(path);
  }

  function chatCameFrom() {
    const back = history.state && history.state.back;
    if (!back) return "";
    return String(back).split("#")[0].split("?")[0];
  }

  function rememberHere(key) {
    const state = Object.assign({}, history.state, { nav: 1, y: hereScroll() });
    const log = document.querySelector("body.chat-thread .chat-log");
    if (log) state.chatLog = log.scrollTop;
    history.replaceState(state, "", key || location.pathname + location.search + location.hash);
  }

  function restoreChatLog(top) {
    if (typeof top !== "number") return;
    const applyTop = function () {
      const log = document.querySelector(".chat-log");
      if (log) log.scrollTop = top;
    };
    applyTop();
    requestAnimationFrame(applyTop);
  }

  document.addEventListener("click", function (event) {
    const back = event.target.closest("a.chat-back");
    if (
      back &&
      event.button === 0 &&
      !event.metaKey &&
      !event.ctrlKey &&
      !event.shiftKey &&
      !event.altKey
    ) {
      event.preventDefault();
      const y = hereScroll();
      rememberHere();
      go(new URL("/chat", location.href).href, true, y).catch(function () {
        location.href = "/chat";
      });
      return;
    }
    const anchor = event.target.closest("a[href]");
    if (!anchor || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (anchor.target && anchor.target !== "_self") return;
    if (anchor.hasAttribute("download")) return;
    const url = new URL(anchor.href, location.href);
    if (!sameDocument(url)) return;
    if (url.href === location.href) return;
    const nextKey = url.pathname + url.search + url.hash;
    const hereKey = location.pathname + location.search + location.hash;
    // 同一页的目录锚点留给浏览器自己跳，返回时才能回到刚才的位置。
    if (url.pathname === location.pathname && url.search === location.search) {
      rememberHere(hereKey);
      return;
    }
    const backTo = history.state && history.state.back;
    if (backTo && nextKey === backTo) {
      event.preventDefault();
      rememberHere(hereKey);
      history.back();
      return;
    }
    // 从聊天点开分享的帖子后，点「论坛」回到刚才那条聊天，而不是论坛列表。
    if (
      url.pathname === "/forum" &&
      /^\/forum\/\d+$/.test(location.pathname) &&
      isChatPath(chatCameFrom())
    ) {
      event.preventDefault();
      rememberHere(hereKey);
      history.back();
      return;
    }
    event.preventDefault();
    const leaving = hereScroll();
    rememberHere(hereKey);
    const stay =
      keepsPlace(anchor, url) ||
      (isChatPath(location.pathname) && isChatPath(url.pathname));
    const scrollY = stay ? leaving : 0;
    go(url.href, true, scrollY).catch(function () {
      location.href = url.href;
    });
  });

  function showShareToast(doc) {
    if (!doc.getElementById("share-toast")) return;
    let el = document.getElementById("share-toast");
    if (!el) {
      el = document.createElement("p");
      el.className = "toast";
      el.id = "share-toast";
      el.textContent = "已分享到私聊";
      document.body.appendChild(el);
    }
    setTimeout(function () {
      if (el.parentNode) el.remove();
    }, 1500);
  }

  function formPath(form) {
    return new URL(form.getAttribute("action") || "", location.href).pathname;
  }

  function staysPut(form) {
    return /\/forum\/\d+\/(like|save|share)$/.test(formPath(form));
  }

  function staysOnChat(form) {
    return /^\/chat\/\d+$/.test(formPath(form));
  }

  function settleChat() {
    const narrow = window.matchMedia("(max-width: 720px)").matches;
    const log = main.querySelector(".chat-log");
    if (narrow && log) log.scrollTop = log.scrollHeight;
    else {
      const composer = main.querySelector(".chat-pane form");
      if (composer) composer.scrollIntoView({ block: "end" });
    }
    const box = main.querySelector('.chat-pane textarea[name="body"]');
    if (box) box.focus({ preventScroll: true });
  }

  document.addEventListener("submit", function (event) {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented) return;
    const method = (form.getAttribute("method") || "get").toLowerCase();
    const onChat = method === "post" && staysOnChat(form);
    if (method === "post" && (staysPut(form) || onChat)) {
      event.preventDefault();
      const y = window.scrollY;
      let sent = false;
      fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        credentials: "same-origin",
        redirect: "follow",
      })
        .then(function (response) {
          if (!response.ok) throw new Error("nav");
          sent = true;
          return response.text().then(function (html) {
            return { html: html, url: response.url };
          });
        })
        .then(function (result) {
          const doc = new DOMParser().parseFromString(result.html, "text/html");
          const url = new URL(result.url, location.href);
          if (onChat && !/^\/chat\/\d+$/.test(url.pathname)) throw new Error("nav");
          if (!apply(doc, y)) throw new Error("nav");
          showShareToast(doc);
          url.searchParams.delete("shared");
          history.replaceState(
            Object.assign({}, history.state, { nav: 1, y: window.scrollY }),
            "",
            url.pathname + url.search + url.hash
          );
          if (onChat) requestAnimationFrame(settleChat);
        })
        .catch(function () {
          if (onChat && sent) location.href = form.action;
          else form.submit();
        });
      return;
    }
    if (method !== "get") return;
    if (form.id === "ask-form") return;
    const url = new URL(form.action || location.href, location.href);
    url.search = new URLSearchParams(new FormData(form)).toString();
    event.preventDefault();
    go(url.href, true).catch(function () {
      form.submit();
    });
  });

  window.addEventListener("popstate", function () {
    const state = history.state || {};
    const y = typeof state.y === "number" ? state.y : 0;
    go(location.href, false, y)
      .then(function () {
        restoreChatLog(state.chatLog);
      })
      .catch(function () {
        location.reload();
      });
  });

  window.matchMedia("(max-width: 720px)").addEventListener("change", function () {
    if (!document.body.classList.contains("chat-thread") && chatLockY == null) return;
    const y = hereScroll();
    if (window.matchMedia("(max-width: 720px)").matches && document.body.classList.contains("chat-thread")) {
      chatLockY = null;
    }
    place(y);
  });

  if (document.body.classList.contains("chat-thread")) place(window.scrollY);
})();
