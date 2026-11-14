#!/usr/bin/env python3
"""Drives a running JellyAshy started with JELLYASHY_TEST_PORT (see devtools.py).

    tools/drive.py 9440 eval "document.title"
    tools/drive.py 9440 shot /tmp/w.png
    tools/drive.py 9440 key Down Down Return
"""

import json
import socket
import sys
import time


def ask(port: int, request: dict) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=30) as s:
        s.sendall((json.dumps(request) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b"{}")


def main() -> int:
    port, what, *args = sys.argv[1:]
    port = int(port)
    if what == "key":
        for name in args:
            print(ask(port, {"key": name}))
            time.sleep(0.25)
        return 0
    answer = ask(port, {what: args[0]})
    print(json.dumps(answer.get("value"), ensure_ascii=False) if answer.get("ok") and what == "eval" else answer)
    return 0 if answer.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
