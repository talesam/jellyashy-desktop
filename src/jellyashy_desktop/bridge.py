"""The bridge between the page and the desktop side.

The page (JellyAshy's web app) calls `window.JellyAshyLinux`, the same
interface the Android app offers: the player is driven with it and its
events come back at `window.__jellyashy.event(...)`. WebKitGTK's messages
are one-way, so the calls that answer at once (status, tracks) read what
this side last pushed to the page.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

MESSAGE_HANDLER = "jellyashy"

# Installed before the page's own script runs.
SHIM = """
(function () {
  var post = function (m) { window.webkit.messageHandlers.%(handler)s.postMessage(JSON.stringify(m)); };
  var status = { pos: 0, dur: 0, buf: 0 };
  var tracks = [];
  var info = %(info)s;
  window.__jellyashyNative = {
    status: function (s) { status = s; },
    tracks: function (t) { tracks = t; }
  };
  window.JellyAshyLinux = {
    info: function () { return JSON.stringify(info); },
    setMode: function () {},
    exit: function () { post({ cmd: "exit" }); },
    playerLoad: function (json) { status = { pos: 0, dur: 0, buf: 0 }; tracks = []; post({ cmd: "load", arg: json }); },
    playerPlay: function () { post({ cmd: "play" }); },
    playerPause: function () { post({ cmd: "pause" }); },
    playerStop: function () { status = { pos: 0, dur: 0, buf: 0 }; tracks = []; post({ cmd: "stop" }); },
    playerSeek: function (s) { status.pos = s; post({ cmd: "seek", arg: s }); },
    playerRate: function (r) { post({ cmd: "rate", arg: r }); },
    playerAudio: function (id) { post({ cmd: "audio", arg: id }); },
    playerAspect: function (m) { post({ cmd: "aspect", arg: m }); },
    playerStatus: function () { return JSON.stringify(status); },
    playerTracks: function () { return JSON.stringify(tracks); }
  };
})();
"""


def shim(info: dict) -> str:
    """The bridge script, with what the page asks of the device."""
    return SHIM % {"handler": MESSAGE_HANDLER, "info": json.dumps(info)}


@dataclass
class Command:
    """A call from the page: `cmd` and its argument, if any."""

    cmd: str
    arg: object = None


@dataclass
class Load:
    """What playerLoad sends: the stream, where to start, the user agent."""

    url: str
    start: float = 0.0
    user_agent: str = ""
    extra: dict = field(default_factory=dict)


COMMANDS = {"exit", "load", "play", "pause", "stop", "seek", "rate", "audio", "aspect"}


def parse_message(text: str) -> Command | None:
    """A message from the page, or None when it is not one of ours."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("cmd") not in COMMANDS:
        return None
    return Command(data["cmd"], data.get("arg"))


def parse_load(arg: object) -> Load | None:
    """playerLoad's JSON; None without a URL to play."""
    try:
        data = json.loads(arg) if isinstance(arg, str) else None
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("url"):
        return None
    try:
        start = max(0.0, float(data.get("startAt") or 0))
    except (TypeError, ValueError):
        start = 0.0
    return Load(str(data["url"]), start, str(data.get("userAgent") or ""), data)


def event_script(event: dict) -> str:
    """JavaScript that hands an event to the page."""
    return f"window.__jellyashy&&window.__jellyashy.event({json.dumps(event)})"


def status_script(status: dict, tracks: list | None = None) -> str:
    """JavaScript that updates what playerStatus/playerTracks answer."""
    native = "window.__jellyashyNative&&window.__jellyashyNative"
    parts = [f"{native}.status({json.dumps(status)})"]
    if tracks is not None:
        parts.append(f"{native}.tracks({json.dumps(tracks)})")
    return ";".join(parts)


def to_number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
