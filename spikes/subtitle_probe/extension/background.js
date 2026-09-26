// Forwards probe events to the local Python receiver (server.py). Content
// scripts can't reliably reach 127.0.0.1 under the site's CSP; the background
// script can, thanks to the host permission. Badge shows "ERR" while the
// receiver is unreachable.
const ENDPOINT = "http://127.0.0.1:8765/event";

browser.runtime.onMessage.addListener((event, sender) => {
  event.tab = sender.tab ? sender.tab.id : null;
  fetch(ENDPOINT, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(event),
  })
    .then((r) => {
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      browser.action.setBadgeText({ text: "" });
    })
    .catch(() => {
      browser.action.setBadgeText({ text: "ERR" });
      browser.action.setBadgeBackgroundColor({ color: "#c00" });
    });
});
