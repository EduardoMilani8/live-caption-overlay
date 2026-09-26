"""Drive a Firefox tab over the Remote Debugging Protocol, for unattended runs.

Same connection as load_extension.py (so navigator.webdriver stays off). The
console actor evaluates JS in the page's own world, which is where Netflix's
internal player API lives; markers are POSTed to server.py so they land in the
same log as the probe's events.

    drive.py open https://www.netflix.com/watch/81234567
    drive.py eval netflix 'document.title'
    drive.py mark 'mid-roll'
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

from load_extension import Rdp

RECEIVER = "http://127.0.0.1:8765/event"


class Console:
    """A console actor: evaluates JS in a page's own world, or in the browser
    chrome for the parent process."""

    def __init__(self, rdp: Rdp, actor: str) -> None:
        self.rdp = rdp
        self.actor = actor

    @classmethod
    def tab(cls, rdp: Rdp, url_part: str) -> Console:
        tabs = rdp.request("root", "listTabs")["tabs"]
        matches = [t for t in tabs if url_part in t.get("url", "")]
        if not matches:
            raise LookupError(f"no tab with {url_part!r}; open: {[t.get('url') for t in tabs]}")
        return cls(rdp, rdp.request(matches[-1]["actor"], "getTarget")["frame"]["consoleActor"])

    @classmethod
    def browser(cls, rdp: Rdp) -> Console:
        process = rdp.request("root", "getProcess", id=0)["processDescriptor"]["actor"]
        return cls(rdp, rdp.request(process, "getTarget")["process"]["consoleActor"])

    def eval(self, js: str, timeout: float = 30) -> object:
        """Evaluate an expression; the page side JSON-encodes the value so it
        comes back whole instead of as an object grip."""
        text = f"(() => {{ const __v = ({js}); return JSON.stringify(__v === undefined ? null : __v); }})()"
        ack = self.rdp.request(self.actor, "evaluateJSAsync", text=text)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            packet = self.rdp.recv()
            if packet.get("type") != "evaluationResult" or packet.get("resultID") != ack["resultID"]:
                continue
            if packet.get("exceptionMessage"):
                raise RuntimeError(packet["exceptionMessage"])
            return json.loads(self._string(packet["result"]))
        raise TimeoutError(js)

    def _string(self, grip: object) -> str:
        if isinstance(grip, str):
            return grip
        if isinstance(grip, dict) and grip.get("type") == "longString":
            return self.rdp.request(grip["actor"], "substring", start=0, end=grip["length"])["substring"]
        raise TypeError(f"unexpected result {grip!r}")


def open_tab(rdp: Rdp, url: str) -> None:
    """New foreground tab in the debugged instance (`firefox --new-tab` would
    go to whichever instance owns the default profile)."""
    Console.browser(rdp).eval(
        "gBrowser.selectedTab = gBrowser.addTab(" + json.dumps(url) + ","
        " {triggeringPrincipal: Services.scriptSecurityManager.getSystemPrincipal()}), true")


def mark(note: str) -> None:
    now = int(time.time() * 1000)
    body = json.dumps({"type": "mark", "note": note, "t": now}).encode()
    req = urllib.request.Request(RECEIVER, body, {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=6000)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_open = sub.add_parser("open", help="open a URL in a new tab")
    p_open.add_argument("url")
    p_eval = sub.add_parser("eval", help="evaluate JS in the tab whose URL contains TAB")
    p_eval.add_argument("tab")
    p_eval.add_argument("js")
    p_mark = sub.add_parser("mark", help="log a scenario marker via server.py")
    p_mark.add_argument("note")
    args = parser.parse_args()

    if args.cmd == "mark":
        mark(args.note)
    elif args.cmd == "open":
        open_tab(Rdp(args.port, 10), args.url)
    else:
        result = Console.tab(Rdp(args.port, 10), args.tab).eval(args.js)
        print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
