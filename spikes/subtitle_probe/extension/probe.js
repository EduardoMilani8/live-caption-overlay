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

  function start() {
    // MutationObserver callbacks are microtasks, not timers, so background-tab
    // timer throttling does not delay them; only the player's own rendering can.
    new MutationObserver(check).observe(document.documentElement, {
      subtree: true, childList: true, characterData: true,
    });

    // Timer heartbeat: gaps between samples reveal timer throttling.
    setInterval(() => {
      if (video()) emit({ type: "sample", capEl: !!document.querySelector(sel.container) });
    }, 1000);

    for (const name of ["play", "pause", "seeked", "ratechange"]) {
      // Media events don't bubble, but capture-phase listeners still see them.
      document.addEventListener(name, (e) => {
        if (e.target instanceof HTMLMediaElement) emit({ type: "media", what: name });
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
