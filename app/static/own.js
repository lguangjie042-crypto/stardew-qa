(function () {
  var menu = document.createElement("div");
  menu.className = "own-menu";
  menu.setAttribute("popover", "manual");
  menu.setAttribute("role", "menu");
  menu.innerHTML = '<button type="button" data-act="go" class="danger"></button><button type="button" data-act="keep">保留</button>';
  document.body.appendChild(menu);

  var host = null;
  var openedAt = 0;
  var touchMode = false;
  var touchTimer = 0;
  var press = null;
  var place = { x: 0, y: 0, touch: false };

  function menuOpen() {
    return menu.matches(":popover-open") || menu.classList.contains("open");
  }

  function showMenu() {
    if (typeof menu.showPopover === "function") {
      if (!menu.matches(":popover-open")) menu.showPopover();
      return;
    }
    menu.classList.add("open");
  }

  function hideMenu() {
    if (typeof menu.hidePopover === "function" && menu.matches(":popover-open")) {
      menu.hidePopover();
    }
    menu.classList.remove("open");
  }

  function closeMenu() {
    host = null;
    hideMenu();
  }

  var askBox = document.createElement("dialog");
  askBox.className = "leave-ask";
  askBox.innerHTML = '<p class="leave-ask-title"></p><div class="leave-actions"><button type="button" class="btn" data-yes>确认</button><button type="button" class="btn" data-no>取消</button></div>';
  document.body.appendChild(askBox);
  var askTitle = askBox.querySelector(".leave-ask-title");
  var askYes = askBox.querySelector("[data-yes]");
  var askNo = askBox.querySelector("[data-no]");
  var askChoice = "";

  function restoreMenu() {
    if (!host) return;
    fillMenu(host);
    placeMenu(place.x, place.y, place.touch);
  }

  function closeAsk() {
    if (typeof askBox.hidePopover === "function" && askBox.matches(":popover-open")) askBox.hidePopover();
    if (askBox.open) askBox.close();
  }

  askBox.addEventListener("toggle", function (event) {
    if (event.newState !== "closed" || askChoice) return;
    askChoice = "no";
    restoreMenu();
  });

  function ask(message, onYes) {
    hideMenu();
    askTitle.textContent = message;
    askChoice = "";
    askYes.onclick = function () {
      askChoice = "yes";
      closeAsk();
      onYes();
    };
    askNo.onclick = function () {
      askChoice = "no";
      closeAsk();
      restoreMenu();
    };
    askBox.oncancel = function (event) {
      event.preventDefault();
      askChoice = "no";
      closeAsk();
      restoreMenu();
    };
    var opened = false;
    if (typeof askBox.showModal === "function") {
      try {
        askBox.showModal();
        opened = true;
      } catch (err) {
        opened = false;
      }
    }
    if (!opened && typeof askBox.showPopover === "function") {
      askBox.setAttribute("popover", "manual");
      askBox.showPopover();
      opened = true;
    }
    if (!opened) {
      if (window.confirm(message)) onYes();
      else restoreMenu();
    }
  }

  function placeMenu(x, y, touch) {
    menu.style.right = "auto";
    menu.style.bottom = "auto";
    menu.style.left = "0px";
    menu.style.top = "0px";
    showMenu();
    var rect = menu.getBoundingClientRect();
    var left = x;
    var top = touch ? y - rect.height - 8 : y;
    if (left + rect.width > window.innerWidth - 8) left = window.innerWidth - rect.width - 8;
    if (top + rect.height > window.innerHeight - 8) top = window.innerHeight - rect.height - 8;
    if (left < 8) left = 8;
    if (top < 8) top = 8;
    menu.style.left = left + "px";
    menu.style.top = top + "px";
  }

  function button(act, label) {
    var el = document.createElement("button");
    el.type = "button";
    el.dataset.act = act;
    el.textContent = label;
    return el;
  }

  function readPeers() {
    var node = document.getElementById("share-peers");
    if (!node) return [];
    try {
      var data = JSON.parse(node.textContent || "[]");
      return Array.isArray(data) ? data : [];
    } catch (err) {
      return [];
    }
  }

  function fillMenu(target) {
    var kind = target.dataset.own;
    menu.replaceChildren();
    menu.classList.toggle("plain", kind === "save" || kind === "mine" || kind === "comment");
    if (kind === "save") {
      menu.appendChild(button("unsave", "取消收藏"));
      menu.appendChild(button("share", "分享"));
      return;
    }
    if (kind === "mine") {
      menu.appendChild(button("remove", "删除"));
      menu.appendChild(button("share", "分享"));
      return;
    }
    if (kind === "comment") {
      menu.appendChild(button("drop-comment", "删除"));
      return;
    }
    var go = button("go", "删除");
    go.classList.add("danger");
    menu.appendChild(go);
    menu.appendChild(button("keep", "保留"));
  }

  function showShare() {
    if (!host) return;
    menu.replaceChildren();
    menu.classList.add("plain");
    var title = document.createElement("p");
    title.className = "muted";
    title.textContent = "分享给私聊对象";
    menu.appendChild(title);
    var peers = readPeers();
    if (!peers.length) {
      var empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "还没有私聊对象。先点头像进主页，再点私聊。";
      menu.appendChild(empty);
    } else {
      peers.forEach(function (mate) {
        var el = document.createElement("button");
        el.type = "button";
        el.dataset.act = "send";
        el.dataset.to = String(mate.id);
        var avatar = document.createElement("span");
        avatar.className = "avatar";
        avatar.textContent = mate.avatar || "";
        el.appendChild(avatar);
        el.appendChild(document.createTextNode(" " + (mate.name || "")));
        menu.appendChild(el);
      });
    }
    placeMenu(place.x, place.y, place.touch);
  }

  function openMenu(target, x, y, touch) {
    if (!target) return;
    host = target;
    openedAt = Date.now();
    place.x = x;
    place.y = y;
    place.touch = touch;
    fillMenu(target);
    placeMenu(x, y, touch);
    var sel = window.getSelection && window.getSelection();
    if (sel && sel.removeAllRanges) sel.removeAllRanges();
    if (!touch && target.dataset.own !== "save" && target.dataset.own !== "mine" && target.dataset.own !== "comment") {
      var go = menu.querySelector("[data-act=go]");
      if (go) go.focus();
    }
  }

  function safePath(url) {
    return !!url && url.charAt(0) === "/" && url.charAt(1) !== "/";
  }

  function hidden(form, name, value) {
    var input = document.createElement("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    form.appendChild(input);
  }

  function submit(url, next) {
    var form = document.createElement("form");
    form.method = "post";
    form.action = url;
    if (next && safePath(next)) hidden(form, "next", next);
    document.body.appendChild(form);
    form.submit();
  }

  function postStay(url, fields) {
    var body = new URLSearchParams();
    Object.keys(fields || {}).forEach(function (key) {
      body.append(key, fields[key]);
    });
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Stay": "1"
      },
      body: body
    }).then(function (res) {
      return res.json().then(function (data) {
        return !!(data && data.ok);
      }, function () { return false; });
    }, function () { return false; });
  }

  var toastTimer = 0;

  function showToast(text) {
    var old = document.getElementById("done-toast") || document.getElementById("share-toast");
    if (old) old.remove();
    var el = document.createElement("p");
    el.className = "toast";
    el.id = "done-toast";
    el.textContent = text;
    document.body.appendChild(el);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      if (el.parentNode) el.remove();
    }, 1500);
  }

  function placeAgain(x, y) {
    window.scrollTo(x, y);
  }

  function emptyNote(list) {
    var heading = list.parentElement && list.parentElement.querySelector("h2");
    var name = heading ? heading.textContent : "";
    var li = document.createElement("li");
    li.textContent = name.indexOf("收藏") >= 0
      ? "还没有收藏。在论坛帖子右边点五角星。"
      : "还没有发过帖。";
    list.appendChild(li);
  }

  function dropRow(row) {
    var list = row.parentElement;
    row.remove();
    if (list && !list.querySelector("li")) emptyNote(list);
  }

  function dropSaved(postId) {
    document.querySelectorAll('[data-own="save"][data-post="' + postId + '"]').forEach(dropRow);
  }

  function dropPost(postId) {
    document.querySelectorAll('[data-post="' + postId + '"]').forEach(dropRow);
  }

  function dropComment(commentId) {
    var thread = document.getElementById("thread-" + commentId);
    if (thread) {
      if (thread.open) thread.close();
      thread.remove();
    }
    document.querySelectorAll('[data-own="comment"][data-comment="' + commentId + '"]').forEach(function (node) {
      var branch = node.closest(".cnode");
      var card = node.closest(".cmt");
      if (branch) branch.remove();
      else if (card) card.remove();
    });
    document.querySelectorAll(".cmt").forEach(function (card) {
      var main = card.querySelector("[data-comment]");
      var countBtn = card.querySelector(".count");
      if (!main || !countBtn) return;
      var box = document.getElementById("thread-" + main.dataset.comment);
      var total = box ? Math.max(0, box.querySelectorAll(".cnode").length - 1) : 0;
      countBtn.textContent = total + "条回复";
    });
    if (document.querySelector(".cmt")) return;
    var section = document.querySelector("section.card");
    var heading = section && section.querySelector("h2");
    if (!section || !heading || heading.textContent.indexOf("回复") < 0) return;
    if (section.querySelector(":scope > p.muted")) return;
    var note = document.createElement("p");
    note.className = "muted";
    note.textContent = "还没有回复。";
    var composer = section.querySelector(".composer");
    if (composer) section.insertBefore(note, composer);
    else heading.insertAdjacentElement("afterend", note);
  }

  function act(name, source) {
    var target = host;
    var url = target && target.dataset.url;
    var kind = target && target.dataset.own;
    var confirmText = target && target.dataset.confirm;
    var next = target && target.dataset.next;
    if (name === "share") {
      showShare();
      return;
    }
    if (name === "drop-comment") {
      var commentUrl = url;
      var commentId = target.dataset.comment || "";
      var commentX = window.scrollX;
      var commentY = window.scrollY;
      var scroller = target.closest("dialog.thread");
      var dialogTop = scroller ? scroller.scrollTop : 0;
      ask("确定要删除评论吗", function () {
        closeMenu();
        if (!safePath(commentUrl)) return;
        postStay(commentUrl).then(function (ok) {
          if (!ok) return;
          dropComment(commentId);
          showToast("删除成功");
          placeAgain(commentX, commentY);
          if (scroller && scroller.isConnected) scroller.scrollTop = dialogTop;
        });
      });
      return;
    }
    if (name === "unsave") {
      var saveUrl = url;
      var saveId = target.dataset.post || "";
      var saveX = window.scrollX;
      var saveY = window.scrollY;
      ask("确定要取消收藏吗", function () {
        closeMenu();
        if (!safePath(saveUrl)) return;
        postStay(saveUrl).then(function (ok) {
          if (!ok) return;
          dropSaved(saveId);
          showToast("去除成功");
          placeAgain(saveX, saveY);
        });
      });
      return;
    }
    if (name === "remove") {
      var delUrl = url;
      var delId = target.dataset.post || "";
      var delX = window.scrollX;
      var delY = window.scrollY;
      ask("确定要删除帖子吗", function () {
        closeMenu();
        if (!safePath(delUrl)) return;
        postStay(delUrl).then(function (ok) {
          if (!ok) return;
          dropPost(delId);
          showToast("删除成功");
          placeAgain(delX, delY);
        });
      });
      return;
    }
    closeMenu();
    if (!target) return;
    if (name === "keep") return;
    if (name === "send") {
      var postId = target.dataset.post || "";
      var to = source && source.dataset.to || "";
      var shareX = window.scrollX;
      var shareY = window.scrollY;
      if (!/^\d+$/.test(postId) || !/^\d+$/.test(to)) return;
      postStay("/forum/" + postId + "/share", { to: to, next: "/me" }).then(function (ok) {
        if (!ok) return;
        showToast("已分享到私聊");
        placeAgain(shareX, shareY);
      });
      return;
    }
    if (!safePath(url)) return;
    if (window.confirm(confirmText || "确定删除？")) submit(url, next || "");
  }

  menu.addEventListener("click", function (event) {
    var button = event.target.closest("[data-act]");
    if (!button) return;
    event.preventDefault();
    if (touchMode && Date.now() - openedAt < 400) return;
    act(button.dataset.act, button);
  });
  menu.addEventListener("contextmenu", function (event) {
    event.preventDefault();
    event.stopPropagation();
  });

  document.addEventListener("contextmenu", function (event) {
    var target = event.target.closest("[data-own]");
    if (!target || menu.contains(event.target)) return;
    event.preventDefault();
    if (press) {
      clearTimeout(press.timer);
      press = null;
    }
    openMenu(target, event.clientX, event.clientY, touchMode);
  });

  document.addEventListener("pointerdown", function (event) {
    if (event.pointerType !== "mouse") {
      touchMode = true;
      clearTimeout(touchTimer);
    }
    if (menuOpen() && !menu.contains(event.target)) closeMenu();
    if (event.pointerType === "mouse" || !event.isPrimary) return;
    var target = event.target.closest("[data-own]");
    if (!target) return;
    var startX = event.clientX;
    var startY = event.clientY;
    var timer = setTimeout(function () {
      press = null;
      openMenu(target, startX, startY, true);
    }, 500);
    press = { timer: timer, x: startX, y: startY, id: event.pointerId };
  }, true);

  document.addEventListener("pointermove", function (event) {
    if (!press || event.pointerId !== press.id) return;
    if (Math.hypot(event.clientX - press.x, event.clientY - press.y) > 12) {
      clearTimeout(press.timer);
      press = null;
    }
  }, true);

  function endPress(event) {
    if (event.pointerType !== "mouse") {
      clearTimeout(touchTimer);
      touchTimer = setTimeout(function () { touchMode = false; }, 800);
    }
    if (!press || event.pointerId !== press.id) return;
    clearTimeout(press.timer);
    press = null;
  }
  document.addEventListener("pointerup", endPress, true);
  document.addEventListener("pointercancel", endPress, true);

  document.addEventListener("click", function (event) {
    if (!menuOpen()) return;
    if (touchMode && Date.now() - openedAt < 500 && !menu.contains(event.target)) {
      event.preventDefault();
      event.stopPropagation();
    }
  }, true);

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && menuOpen()) closeMenu();
  });

  window.addEventListener("scroll", function () {
    if (!menuOpen() || Date.now() - openedAt < 400) return;
    closeMenu();
  }, true);
})();
