"""Load the probe extension as a temporary add-on into a running Firefox.

Same thing about:debugging's "Load Temporary Add-on" does, over Firefox's
Remote Debugging Protocol (what web-ext uses), so it works without node.
Firefox must have been started with the debugger server on, which needs
`devtools.debugger.remote-enabled` set in the profile beforehand:

    firefox --start-debugger-server 6000
    .venv/bin/python spikes/subtitle_probe/load_extension.py --port 6000

The RDP is used instead of WebDriver BiDi on purpose: BiDi sets
navigator.webdriver, which streaming sites may treat as a bot.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

EXTENSION_DIR = Path(__file__).resolve().parent / "extension"


class Rdp:
    """Minimal client: packets are `<byte length>:<JSON>` both ways."""

    def __init__(self, port: int, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._sock = socket.create_connection(("127.0.0.1", port), timeout=10)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        self._buf = b""
        self.recv()  # greeting from the root actor

    def recv(self) -> dict:
        while b":" not in self._buf:
            self._fill()
        size, _, rest = self._buf.partition(b":")
        self._buf = rest
        while len(self._buf) < int(size):
            self._fill()
        packet, self._buf = self._buf[: int(size)], self._buf[int(size):]
        return json.loads(packet)

    def request(self, to: str, type_: str, **params) -> dict:
        data = json.dumps({"to": to, "type": type_, **params}).encode()
        self._sock.sendall(str(len(data)).encode() + b":" + data)
        while True:  # skip unsolicited notifications from other actors
            reply = self.recv()
            if reply.get("from") == to:
                if "error" in reply:
                    raise RuntimeError(f"{reply['error']}: {reply.get('message')}")
                return reply

    def _fill(self) -> None:
        chunk = self._sock.recv(65536)
        if not chunk:
            raise ConnectionError("Firefox closed the debugger connection")
        self._buf += chunk


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=6000)
    parser.add_argument("--timeout", type=float, default=60, help="seconds to wait for Firefox")
    args = parser.parse_args()

    rdp = Rdp(args.port, args.timeout)
    addons = rdp.request("root", "getRoot")["addonsActor"]
    reply = rdp.request(addons, "installTemporaryAddon", addonPath=str(EXTENSION_DIR), openDevTools=False)
    print(f"loaded {reply['addon']['id']} from {EXTENSION_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
