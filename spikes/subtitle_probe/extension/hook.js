// Runs in the page's own JS world (MAIN) so it can wrap the XHR/fetch the
// player uses. Any response that looks like a subtitle file (TTML, WebVTT,
// YouTube json3) is handed to probe.js via window.postMessage.
(() => {
  const TAG = "__liveCaptionProbe";
  const MAX_BYTES = 8 * 1024 * 1024;
  const reported = new Set();
  const xhrUrls = new WeakMap();

  function classify(head) {
    const s = head.replace(/^﻿/, "").trimStart();
    if (s.startsWith("WEBVTT")) return "vtt";
    if ((s.startsWith("<?xml") || s.startsWith("<tt")) && s.includes("<tt")) return "ttml";
    if (s.startsWith("{") && s.includes('"events"') && s.includes("tStartMs")) return "json3";
    return null;
  }

  function report(url, body) {
    const kind = classify(body.slice(0, 4096));
    if (!kind || reported.has(url)) return;
    reported.add(url);
    window.postMessage({ [TAG]: true, type: "track", kind, trackUrl: url, size: body.length, body }, location.origin);
  }

  function inspectBuffer(url, buf) {
    if (!buf || buf.byteLength > MAX_BYTES) return;
    const decoder = new TextDecoder();
    const head = decoder.decode(new Uint8Array(buf, 0, Math.min(4096, buf.byteLength)));
    if (classify(head)) report(url, decoder.decode(buf));
  }

  function inspectXhr(xhr) {
    const url = xhrUrls.get(xhr) || xhr.responseURL;
    const type = xhr.responseType;
    if (type === "" || type === "text") report(url, xhr.responseText);
    else if (type === "arraybuffer") inspectBuffer(url, xhr.response);
    else if (type === "json" && xhr.response) report(url, JSON.stringify(xhr.response));
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
        const small = len > 0 && len <= MAX_BYTES;
        if (!/^(video|audio|image)\//.test(type) && (small || res.url.includes("/api/timedtext"))) {
          res.clone().arrayBuffer().then((buf) => inspectBuffer(res.url, buf), () => {});
        }
      } catch (e) { /* never break the player */ }
      return res;
    });
  };

  // Ad-tier probe: Netflix's internal player API may know about ad breaks and
  // the content clock. Only zero-argument get*/is* methods whose names hint at
  // time or ads are called (read-only by convention); the full method list is
  // sent once so we can see what else exists.
  if (!location.hostname.includes("netflix")) return;
  const INTERESTING = /time|ad|break|segment|position|playing|paused|duration|movie|pod/i;

  function methodNames(obj) {
    const names = new Set();
    for (let o = obj; o && o !== Object.prototype; o = Object.getPrototypeOf(o)) {
      for (const name of Object.getOwnPropertyNames(o)) {
        try { if (typeof obj[name] === "function" && name !== "constructor") names.add(name); } catch (e) {}
      }
    }
    return [...names].sort();
  }

  function snapshot(player) {
    const values = {};
    for (const name of methodNames(player)) {
      if (!/^(get|is)/.test(name) || !INTERESTING.test(name) || player[name].length !== 0) continue;
      try {
        const v = player[name]();
        if (v === undefined || typeof v === "function") continue;
        values[name] = typeof v === "object" ? JSON.stringify(v).slice(0, 300) : v;
      } catch (e) { /* some getters throw outside playback */ }
    }
    return values;
  }

  // getAdManager() knows the break schedule in content time and whether an ad
  // is on screen; its getters return objects, so they're summarized here.
  function adState(player) {
    const m = player.getAdManager?.();
    if (!m) return {};
    const ms = (d) => (d && d.timescale ? Math.round((d.ticks * 1000) / d.timescale) : null);
    const brk = (b) => b && {
      i: b.viewableAdBreakIndex, at: b.locationMs, type: b.type, hydrated: b.isHydrated,
      played: b.hasPlayed, adsMs: ms(b.normalizedAdsDuration),
      ads: b.ads ? b.ads.map((a) => [a.startTimeMs, a.endTimeMs, a.type]) : null,
    };
    // The presenting break isn't shaped like a schedule entry; log its scalar
    // fields and key names until we know which ones matter.
    const scalars = (o) => {
      const out = {};
      for (let x = o; x && x !== Object.prototype; x = Object.getPrototypeOf(x)) {
        for (const name of Object.getOwnPropertyNames(x)) {
          let v;
          try { v = o[name]; } catch (e) { continue; }
          if (name in out || typeof v === "function") continue;
          out[name] = v === null || typeof v !== "object" ? v : Array.isArray(v) ? `[${v.length}]` : "{}";
        }
      }
      return out;
    };
    const state = {};
    const put = (name, f) => { try { state[name] = f(); } catch (e) { state[name] = `ERR ${e.message}`; } };
    put("ad.presenting", () => m.adPresenting?.value);
    put("ad.break", () => { const b = m.getPresentingAdBreak(); return JSON.stringify(b ? scalars(b) : null).slice(0, 600); });
    put("ad.control", () => JSON.stringify(m.getPlayerControlState()));
    put("ad.canSeek", () => m.canSeek());
    put("ad.schedule", () => JSON.stringify((m.getAds() || []).map(brk)));
    return state;
  }

  let describedSession = null;
  setInterval(() => {
    try {
      const api = window.netflix?.appContext?.state?.playerApp?.getAPI?.().videoPlayer;
      const ids = api?.getAllPlayerSessionIds?.() || [];
      const players = ids.map((id) => [id, api.getVideoPlayerBySessionId(id)]).filter(([, p]) => p);
      if (!players.length) return;
      const event = { [TAG]: true, type: "napi", players: players.map(([id, p]) => ({ id, ...snapshot(p), ...adState(p) })) };
      const key = ids.join(",");
      if (describedSession !== key) {
        describedSession = key;
        event.methods = methodNames(players[0][1]);
      }
      window.postMessage(event, location.origin);
    } catch (e) { /* API missing or changed: nothing to report */ }
  }, 1000);
})();
