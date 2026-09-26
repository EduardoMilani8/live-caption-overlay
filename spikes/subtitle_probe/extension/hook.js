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
})();
