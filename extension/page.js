// Runs in the page's own JS world (MAIN): the only place that sees both the
// subtitle file the player downloads and Netflix's internal player API.
// Everything goes to content.js via window.postMessage:
//
//   track   each subtitle file once: {kind, url, lang, movieId, body}
//   anchor  the content clock at wall time t: {vt, rate, paused, ad, clock}
//   cue     subtitle text on screen, with the same clock (verification only)
//
// vt is in *content* time. The Netflix ad tier splices ads into the same
// <video>, whose currentTime counts them; the player API's getCurrentTime()
// does too, while getSegmentTime() is content time (spike test 2). paused
// means "content clock stopped" (paused or ad on screen), so the app can
// always extrapolate vt + (now - t) * rate unless paused. clock says where vt
// came from: "content" (API) or "video" (API missing, vt counts ads).
(() => {
  const TAG = "__liveCaption";
  const TIMEUPDATE_MIN_MS = 1000;
  const SCREEN_LINE = ".player-timedtext-text-container";

  const post = (msg) => window.postMessage({ [TAG]: true, t: Date.now(), path: location.pathname, ...msg },
    location.origin);
  const watching = () => location.pathname.startsWith("/watch");

  // --- Netflix player API (internal, may change: every use is guarded) ---

  function player() {
    try {
      const api = window.netflix?.appContext?.state?.playerApp?.getAPI?.().videoPlayer;
      const ids = api?.getAllPlayerSessionIds?.() || [];
      const id = ids.find((s) => s.startsWith("watch")) ?? ids[0];
      return id ? api.getVideoPlayerBySessionId(id) : null;
    } catch (e) {
      return null;
    }
  }

  const call = (obj, name) => {
    try { return obj?.[name]?.(); } catch (e) { return undefined; }
  };

  function movieId(p) {
    const id = call(p, "getMovieId");
    return typeof id === "number" ? id : Number(location.pathname.split("/")[2]) || null;
  }

  function adOnScreen(p) {
    const presenting = call(p, "getAdManager")?.adPresenting?.value;
    if (typeof presenting === "boolean") return presenting;
    return !!document.querySelector('[data-uia="ads-info-container"]');  // fallback: the UI's ad badge
  }

  function contentClock(video) {
    const p = player();
    const ad = adOnScreen(p);
    const cur = call(p, "getCurrentTime");
    const seg = call(p, "getSegmentTime");
    const base = { rate: video.playbackRate, paused: video.paused || ad, ad, movieId: movieId(p) };
    if (typeof cur === "number" && typeof seg === "number") {
      // During an ad segmentTime sits on the break's position; afterwards the
      // difference is the length of the ads played so far.
      const vt = ad ? seg / 1000 : video.currentTime - (cur - seg) / 1000;
      return { ...base, vt, clock: "content" };
    }
    return { ...base, vt: video.currentTime, clock: "video" };
  }

  // --- anchors: media events only (no timers, so no background throttling) ---

  let lastAnchorAt = 0;
  let lastAd = null;
  let video = null;

  function anchor(reason) {
    if (!video || !watching()) return;
    const clock = contentClock(video);
    lastAnchorAt = Date.now();
    lastAd = clock.ad;
    post({ type: "anchor", reason, ...clock });
  }

  const adManagers = new WeakSet();
  function watchAds() {
    // The ad state is an observable; subscribing makes ad edges immediate.
    // A new episode or reload brings a new player, hence a new ad manager.
    const presenting = call(player(), "getAdManager")?.adPresenting;
    if (!presenting || adManagers.has(presenting) || typeof presenting.addListener !== "function") return;
    adManagers.add(presenting);
    try { presenting.addListener(() => anchor("ad")); } catch (e) { /* timeupdate still catches it */ }
  }

  const MEDIA_EVENTS = ["play", "pause", "seeked", "ratechange", "loadedmetadata", "emptied", "timeupdate"];
  for (const name of MEDIA_EVENTS) {
    // Media events don't bubble, but capture-phase listeners still see them.
    document.addEventListener(name, (e) => {
      if (!(e.target instanceof HTMLVideoElement)) return;
      video = e.target;
      watchAds();
      if (name !== "timeupdate") return anchor(name);
      // Ad edges carry no media event; the ad check on timeupdate is the backstop.
      if (Date.now() - lastAnchorAt >= TIMEUPDATE_MIN_MS || adOnScreen(player()) !== lastAd) anchor(name);
    }, true);
  }

  // --- on-screen subtitle text, for the app's drift check ---

  function nodeText(node) {
    let out = "";
    for (const child of node.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) out += child.data;
      else if (child.nodeName === "BR") out += "\n";
      else out += nodeText(child);
    }
    return out;
  }

  let lastText = null;
  function checkScreen() {
    if (!video || !watching()) return;
    const lines = [];
    for (const el of document.querySelectorAll(SCREEN_LINE)) {
      for (const line of nodeText(el).split("\n")) {
        const clean = line.replace(/\s+/g, " ").trim();
        if (clean) lines.push(clean);
      }
    }
    const text = lines.join("\n");
    if (text === lastText) return;
    lastText = text;
    post({ type: "cue", text, ...contentClock(video) });
  }

  function observeScreen() {
    new MutationObserver(checkScreen).observe(document.documentElement, {
      subtree: true, childList: true, characterData: true,
    });
  }
  if (document.documentElement) observeScreen();
  else document.addEventListener("DOMContentLoaded", observeScreen, { once: true });

  // --- subtitle file interception ---

  const MAX_BYTES = 8 * 1024 * 1024;
  const sent = new Set();
  const xhrUrls = new WeakMap();

  function isTtml(head) {
    const s = head.replace(/^﻿/, "").trimStart();
    return (s.startsWith("<?xml") || s.startsWith("<tt")) && s.includes("<tt");
  }

  function sendTrack(url, body) {
    // Preview autoplay on /browse downloads subtitles too; only the player's count.
    if (!watching() || sent.has(url) || !isTtml(body.slice(0, 4096))) return;
    sent.add(url);
    const lang = (body.match(/<tt\b[^>]*\bxml:lang="([^"]+)"/) || [])[1] || null;
    post({ type: "track", kind: "ttml", url, lang, movieId: movieId(player()), size: body.length, body });
  }

  function inspectBuffer(url, buf) {
    if (!buf || buf.byteLength > MAX_BYTES) return;
    const decoder = new TextDecoder();
    const head = decoder.decode(new Uint8Array(buf, 0, Math.min(4096, buf.byteLength)));
    if (isTtml(head)) sendTrack(url, decoder.decode(buf));
  }

  function inspectXhr(xhr) {
    const url = xhrUrls.get(xhr) || xhr.responseURL;
    const type = xhr.responseType;
    if (type === "" || type === "text") sendTrack(url, xhr.responseText);
    else if (type === "arraybuffer") inspectBuffer(url, xhr.response);
    else if (type === "blob" && xhr.response && xhr.response.size <= MAX_BYTES) {
      xhr.response.arrayBuffer().then((buf) => inspectBuffer(url, buf), () => {});
    }
  }

  const open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...rest) {
    xhrUrls.set(this, String(url));
    return open.call(this, method, url, ...rest);
  };
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    this.addEventListener("load", () => {
      try { inspectXhr(this); } catch (e) { /* never break the player */ }
    });
    return send.apply(this, args);
  };

  const origFetch = window.fetch;
  window.fetch = function (...args) {
    return origFetch.apply(this, args).then((res) => {
      try {
        const type = res.headers.get("content-type") || "";
        const len = Number(res.headers.get("content-length"));
        if (!/^(video|audio|image)\//.test(type) && len > 0 && len <= MAX_BYTES) {
          res.clone().arrayBuffer().then((buf) => inspectBuffer(res.url, buf), () => {});
        }
      } catch (e) { /* never break the player */ }
      return res;
    });
  };
})();
