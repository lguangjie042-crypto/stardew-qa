(function () {
  const data = document.getElementById("bgm-tracks");
  const button = document.getElementById("bgm-btn");
  if (!data || !button) return;
  const tracks = JSON.parse(data.textContent || "[]");
  if (!tracks.length) return;

  const STORE = "stardew-bgm";
  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem(STORE) || "null");
  } catch (error) {
    saved = null;
  }

  const audio = new Audio();
  audio.id = "bgm-audio";
  audio.hidden = true;
  audio.preload = "auto";
  audio.loop = tracks.length === 1;
  document.body.appendChild(audio);

  let index = 0;
  if (saved && typeof saved.index === "number" && tracks.length) {
    index = ((saved.index % tracks.length) + tracks.length) % tracks.length;
  }
  let wantPlay = !(saved && saved.wantPlay === false);
  let lastSave = 0;

  function render() {
    const paused = !wantPlay;
    button.classList.toggle("is-paused", paused);
    button.setAttribute("aria-pressed", paused ? "false" : "true");
    button.setAttribute("aria-label", paused ? "播放音乐" : "停止音乐");
  }

  function remember() {
    sessionStorage.setItem(
      STORE,
      JSON.stringify({
        index: index,
        time: audio.currentTime || 0,
        wantPlay: wantPlay,
      })
    );
  }

  function playFrom(nextIndex) {
    index = ((nextIndex % tracks.length) + tracks.length) % tracks.length;
    audio.loop = tracks.length === 1;
    audio.src = tracks[index];
    return audio.play();
  }

  audio.addEventListener("timeupdate", function () {
    const now = Date.now();
    if (now - lastSave < 400) return;
    lastSave = now;
    remember();
  });
  audio.addEventListener("ended", function () {
    if (!wantPlay || tracks.length < 2) return;
    playFrom(index + 1).catch(function () {
      wantPlay = false;
      render();
      remember();
    });
  });
  window.addEventListener("pagehide", remember);

  button.addEventListener("click", function () {
    wantPlay = !wantPlay;
    render();
    remember();
    if (!wantPlay) {
      audio.pause();
      return;
    }
    const pending = audio.src ? audio.play() : playFrom(index);
    pending.catch(function () {
      wantPlay = false;
      render();
      remember();
    });
  });

  render();
  audio.src = tracks[index];
  audio.addEventListener(
    "loadedmetadata",
    function () {
      const jump = saved && typeof saved.time === "number" ? saved.time : 0;
      if (jump > 0.3 && jump < audio.duration - 0.3) audio.currentTime = jump;
      if (!wantPlay) return;
      audio.play().catch(function () {
        wantPlay = false;
        render();
        remember();
      });
    },
    { once: true }
  );
})();
