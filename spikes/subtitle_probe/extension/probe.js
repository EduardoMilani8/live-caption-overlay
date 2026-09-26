// Content script (isolated world). Watches the player's on-screen subtitle
// element and reports every change together with the video clock and the
// tab's visibility, so we can see whether subtitles keep flowing while the
// tab is behind another window, minimized, or not the active tab.
(() => {
  const TAG = "__liveCaptionProbe";
  const SITES = {
    netflix: { container: ".player-timedtext", line: ".player-timedtext-text-container" },
    youtube: { container: ".ytp-caption-window-container", line: ".caption-visual-line" },
  };
  const site = location.hostname.includes("netflix") ? "netflix" : "youtube";
  const sel = SITES[site];
  let seq = 0;
  let lastText = null;

  function video() {
    // YouTube keeps preview/ad players around; the main one has this class.
    return document.querySelector("video.html5-main-video") || document.querySelector("video");
  }

  function emit(event) {
    const v = video();
    const payload = {
      ...event,
      site,
      seq: seq++,
      t: Date.now(),
      vt: v ? v.currentTime : null,
      paused: v ? v.paused : null,
      vis: document.visibilityState,
      focus: document.hasFocus(),
      path: location.pathname,
    };
    browser.runtime.sendMessage(payload).catch(() => {});
  }

  // Text with <br> as line breaks; one entry per positioned line block.
  function nodeText(node) {
    let out = "";
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) out += child.data;
      else if (child.nodeName === "BR") out += "\n";
      else out += nodeText(child);
    }
    return out;
  }

  function currentText() {
    const lines = [];
    for (const el of document.querySelectorAll(sel.line)) {
      for (const line of nodeText(el).split("\n")) {
        const clean = line.replace(/\s+/g, " ").trim();
        if (clean) lines.push(clean);
      }
    }
    return lines.join("\n");
  }

  function check() {
    const text = currentText();
    if (text === lastText) return;
    lastText = text;
    emit({ type: "cue", text });
  }

  let uiaSeen = new Map();
  function diffUia() {
    const now = new Map();
    for (const el of document.querySelectorAll("[data-uia]")) {
      const name = el.getAttribute("data-uia");
      if (!now.has(name)) now.set(name, (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80));
    }
    const added = [...now].filter(([name]) => !uiaSeen.has(name));
    const removed = [...uiaSeen.keys()].filter((name) => !now.has(name));
    uiaSeen = now;
    if (added.length || removed.length) emit({ type: "uia", added: Object.fromEntries(added), removed });
  }

  function start() {
    // MutationObserver callbacks are microtasks, not timers, so background-tab
    // timer throttling does not delay them; only the player's own rendering can.
    new MutationObserver(check).observe(document.documentElement, {
      subtree: true, childList: true, characterData: true,
    });

    // Timer heartbeat: gaps between samples reveal timer throttling. It also
    // records every <video> (an ad may play in a separate element) and diffs
    // the player UI's data-uia markers, which is how an ad break shows up.
    setInterval(() => {
      if (!video()) return;
      const videos = [...document.querySelectorAll("video")].map((v) => ({
        vt: v.currentTime, dur: v.duration, paused: v.paused, src: v.currentSrc.slice(0, 60),
      }));
      emit({ type: "sample", capEl: !!document.querySelector(sel.container), videos });
      diffUia();
    }, 1000);

    const MEDIA_EVENTS = ["play", "pause", "seeked", "ratechange", "durationchange", "loadedmetadata", "emptied"];
    for (const name of MEDIA_EVENTS) {
      // Media events don't bubble, but capture-phase listeners still see them.
      document.addEventListener(name, (e) => {
        if (!(e.target instanceof HTMLMediaElement)) return;
        const idx = [...document.querySelectorAll("video")].indexOf(e.target);
        emit({ type: "media", what: name, idx, evt: e.target.currentTime, dur: e.target.duration });
      }, true);
    }
    document.addEventListener("visibilitychange", () => emit({ type: "vis" }));
    window.addEventListener("focus", () => emit({ type: "focus" }));
    window.addEventListener("blur", () => emit({ type: "focus" }));

    window.addEventListener("message", (e) => {
      if (e.source === window && e.data && e.data[TAG]) {
        const { [TAG]: _, ...track } = e.data;
        emit(track);
      }
    });

    emit({ type: "hello", ua: navigator.userAgent });
  }

  if (document.documentElement) start();
  else document.addEventListener("DOMContentLoaded", start, { once: true });
})();
