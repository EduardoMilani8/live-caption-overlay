// Content script (isolated world): only a relay. page.js does all the work in
// the page's own world, where the player API is; runtime messaging to the
// background script is only available here.
(() => {
  const TAG = "__liveCaption";
  window.addEventListener("message", (e) => {
    if (e.source !== window || !e.data || !e.data[TAG]) return;
    const { [TAG]: _, ...event } = e.data;
    browser.runtime.sendMessage(event).catch(() => {});
  });
})();
