"""TVs the videos can go to: Chromecasts (Google Cast) and DLNA televisions.

Both play the server's URL themselves: the app sends it, keeps the
controls and follows the position. DLNA (most Samsung, LG, Sony…
televisions) needs nothing but the standard library; Chromecast needs
pychromecast and is simply not offered without it.

Network work runs on threads; everything the window sees is handed back
on the main loop (GLib.idle_add).
"""

from __future__ import annotations

import html
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.etree import ElementTree

from gi.repository import GLib

try:  # optional: Chromecast
    import pychromecast
    from pychromecast.discovery import CastBrowser, SimpleCastListener

    HAVE_CAST = True
except Exception:  # pragma: no cover - depends on the system
    HAVE_CAST = False

SSDP_ADDR = ("239.255.255.250", 1900)
RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"
AVT = "urn:schemas-upnp-org:service:AVTransport:1"
_UPNP_NS = {"d": "urn:schemas-upnp-org:device-1-0"}


# ---- DLNA ---------------------------------------------------------------------


@dataclass
class Renderer:
    """A DLNA television: its name and where its player takes orders."""

    udn: str
    name: str
    control: str
    seen: float = field(default_factory=time.time)


def parse_description(xml_text: str, location: str) -> Renderer | None:
    """The renderer described at [location], or None without an AVTransport."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    base = location
    url_base = root.find("d:URLBase", _UPNP_NS)
    if url_base is not None and url_base.text:
        base = url_base.text.strip()
    for device in root.iter("{urn:schemas-upnp-org:device-1-0}device"):
        name = device.findtext("d:friendlyName", "", _UPNP_NS).strip()
        udn = device.findtext("d:UDN", "", _UPNP_NS).strip()
        services = device.find("d:serviceList", _UPNP_NS)
        if services is None:
            continue
        for service in services.findall("d:service", _UPNP_NS):
            if service.findtext("d:serviceType", "", _UPNP_NS).startswith("urn:schemas-upnp-org:service:AVTransport:"):
                control = service.findtext("d:controlURL", "", _UPNP_NS).strip()
                if control and udn:
                    return Renderer(udn, name or "TV", urllib.parse.urljoin(base, control))
    return None


def to_hms(seconds: float) -> str:
    s = max(0, int(seconds))
    return "%d:%02d:%02d" % (s // 3600, s // 60 % 60, s % 60)


def from_hms(text: str) -> float:
    """"1:02:03" (or "NOT_IMPLEMENTED") in seconds."""
    m = re.match(r"^\s*(\d+):(\d{1,2}):(\d{1,2})(?:\.\d+)?\s*$", text or "")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) if m else 0.0


def mime_of(url: str) -> str:
    """The stream's type from its address (the server names the container)."""
    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith(".m3u8"):
        return "application/vnd.apple.mpegurl"
    for ext, mime in ((".mkv", "video/x-matroska"), (".webm", "video/webm"), (".ts", "video/mp2t"),
                      (".avi", "video/x-msvideo"), (".mov", "video/quicktime")):
        if path.endswith(ext):
            return mime
    return "video/mp4"


def didl(url: str, title: str, mime: str) -> str:
    """The item's description: Samsung TVs refuse a URL without one."""
    flags = DLNA_FEATURES
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="0" parentID="-1" restricted="1">'
        f"<dc:title>{html.escape(title or 'JellyAshy')}</dc:title>"
        "<upnp:class>object.item.videoItem</upnp:class>"
        f'<res protocolInfo="http-get:*:{mime}:{flags}">{html.escape(url)}</res>'
        "</item></DIDL-Lite>"
    )


def soap(control: str, action: str, args: dict, timeout: float = 6.0) -> dict:
    """Calls an AVTransport action; its answer's fields."""
    body = "".join(f"<{k}>{html.escape(str(v))}</{k}>" for k, v in args.items())
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{AVT}"><InstanceID>0</InstanceID>{body}</u:{action}>'
        "</s:Body></s:Envelope>"
    ).encode()
    req = urllib.request.Request(control, data=envelope, method="POST", headers={
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPACTION": f'"{AVT}#{action}"',
    })
    with urllib.request.urlopen(req, timeout=timeout) as res:
        text = res.read().decode("utf-8", "replace")
    out = {}
    for m in re.finditer(r"<(\w+)>([^<]*)</\1>", text):
        out[m.group(1)] = html.unescape(m.group(2))
    return out


def search(wait: float = 2.5) -> list[str]:
    """Description addresses of the renderers answering on this network."""
    msg = (
        "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\n"
        f"MX: 2\r\nST: {RENDERER}\r\n\r\n"
    ).encode()
    found: list[str] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        for _ in range(2):
            sock.sendto(msg, SSDP_ADDR)
        end = time.time() + wait
        while time.time() < end:
            try:
                data, _addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            loc = re.search(r"(?im)^location:\s*(\S+)", data.decode("utf-8", "ignore"))
            if loc and loc.group(1) not in found:
                found.append(loc.group(1))
    finally:
        sock.close()
    return found


class Relay:
    """Hands a stream to TVs on this network over plain HTTP.

    A DLNA television fetches the video itself, but older ones (a 2016
    Samsung) trust no current HTTPS certificate and the server only answers
    HTTPS: they answered "resource not found". This app fetches it for them
    (byte ranges passed through, so the TV still seeks) under a random path
    only the TV is told.
    """

    def __init__(self):
        self.streams: dict[str, str] = {}
        relay = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def _serve(self, head: bool):
                url = relay.streams.get(self.path.split("?")[0])
                if not url:
                    self.send_error(404)
                    return
                headers = {"User-Agent": "JellyAshy"}
                if self.headers.get("Range"):
                    headers["Range"] = self.headers["Range"]
                try:
                    up = urllib.request.urlopen(urllib.request.Request(url, headers=headers, method="HEAD" if head else "GET"), timeout=30)
                except urllib.error.HTTPError as e:
                    up = e
                except Exception:
                    self.send_error(502)
                    return
                self.send_response(getattr(up, "status", None) or up.code)
                for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Last-Modified"):
                    v = up.headers.get(k)
                    if v:
                        self.send_header(k, v)
                self.send_header("contentFeatures.dlna.org", DLNA_FEATURES)
                self.send_header("transferMode.dlna.org", "Streaming")
                self.end_headers()
                if head:
                    up.close()
                    return
                try:
                    while True:
                        chunk = up.read(256 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                except Exception:  # the TV hung up (a seek, a stop)
                    pass
                finally:
                    up.close()

            def do_HEAD(self):
                self._serve(True)

            def do_GET(self):
                self._serve(False)

        self.server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def publish(self, url: str, tv_host: str) -> str:
        """The address [tv_host] can fetch [url] from (this app's address on its network)."""
        path = "/" + secrets.token_urlsafe(16) + "/video" + _ext(url)
        self.streams = {path: url}  # one at a time: the last video sent
        return f"http://{local_ip(tv_host)}:{self.server.server_address[1]}{path}"

    def close(self):
        self.streams = {}
        _quiet(self.server.shutdown)


DLNA_FEATURES = "DLNA.ORG_OP=01;DLNA.ORG_CI=0;DLNA.ORG_FLAGS=01700000000000000000000000000000"


def _ext(url: str) -> str:
    m = re.search(r"(\.[a-z0-9]{2,4})$", urllib.parse.urlparse(url).path.lower())
    return m.group(1) if m else ".mp4"


def local_ip(toward: str) -> str:
    """This computer's address on the network that reaches [toward]."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((toward, 9))
        return s.getsockname()[0]
    finally:
        s.close()


class DlnaPlayer:
    """Plays one URL on a renderer and follows it (a poll a second)."""

    def __init__(self, renderer: Renderer, on_state, on_error, relay: Relay | None = None):
        self.renderer = renderer
        self.relay = relay
        self.on_state = on_state
        self.on_error = on_error
        self.status = {"pos": 0.0, "dur": 0.0, "buf": 0.0}
        self._stop = threading.Event()
        self._last = ""
        self._played = False

    def load(self, url: str, title: str, start: float) -> None:
        def run():
            c = self.renderer.control
            mime = mime_of(url)
            sent = self.relay.publish(url, urllib.parse.urlparse(c).hostname or "") if self.relay else url
            try:
                try:
                    soap(c, "Stop", {})
                except Exception:
                    pass
                soap(c, "SetAVTransportURI", {"CurrentURI": sent, "CurrentURIMetaData": didl(sent, title, mime)})
                soap(c, "Play", {"Speed": "1"})
            except Exception as e:  # the TV refused it
                self.on_error(str(e))
                return
            self.on_state("loading")
            seeked = start < 5
            while not self._stop.wait(1.0):
                try:
                    t = soap(c, "GetTransportInfo", {}, timeout=4)
                    p = soap(c, "GetPositionInfo", {}, timeout=4)
                except Exception:
                    continue
                pos, dur = from_hms(p.get("RelTime", "")), from_hms(p.get("TrackDuration", ""))
                self.status = {"pos": pos, "dur": dur, "buf": pos + 30}
                state = t.get("CurrentTransportState", "")
                page = {"PLAYING": "playing", "PAUSED_PLAYBACK": "paused", "TRANSITIONING": "buffering"}.get(state)
                if state == "PLAYING":
                    self._played = True
                    if not seeked:
                        seeked = True
                        try:
                            soap(c, "Seek", {"Unit": "REL_TIME", "Target": to_hms(start)})
                        except Exception:
                            pass
                if state in ("STOPPED", "NO_MEDIA_PRESENT") and self._played:
                    # The end (or stopped on the TV's own remote).
                    page = "ended"
                if page and page != self._last:
                    self._last = page
                    self.on_state(page)
                if page == "ended":
                    return

        threading.Thread(target=run, daemon=True).start()

    def command(self, action: str, args: dict | None = None) -> None:
        def run():
            try:
                soap(self.renderer.control, action, args or {})
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()

    def play(self):
        self.command("Play", {"Speed": "1"})

    def pause(self):
        self.command("Pause")

    def seek(self, seconds: float):
        self.status["pos"] = seconds
        self.command("Seek", {"Unit": "REL_TIME", "Target": to_hms(seconds)})

    def stop(self):
        self._stop.set()
        self.command("Stop")


# ---- Chromecast ---------------------------------------------------------------


class CastPlayer:
    """Plays one URL on a Chromecast through Google's Default Media Receiver."""

    def __init__(self, cast, on_state, on_error):
        self.cast = cast
        self.on_state = on_state
        self.on_error = on_error
        self.status = {"pos": 0.0, "dur": 0.0, "buf": 0.0}
        self._stop = threading.Event()
        self._last = ""
        self._played = False

    def load(self, url: str, title: str, start: float, image: str = "") -> None:
        def run():
            mc = self.cast.media_controller
            try:
                ctype = "application/x-mpegURL" if ".m3u8" in url.lower() else "video/mp4"
                mc.play_media(url, ctype, title=title or None, thumb=image or None, current_time=start or None)
                mc.block_until_active(timeout=20)
            except Exception as e:
                self.on_error(str(e))
                return
            self.on_state("loading")
            while not self._stop.wait(1.0):
                try:
                    mc.update_status()
                except Exception:
                    pass
                st = mc.status
                pos = float(st.adjusted_current_time or st.current_time or 0)
                self.status = {"pos": pos, "dur": float(st.duration or 0), "buf": pos + 30}
                page = {"PLAYING": "playing", "PAUSED": "paused", "BUFFERING": "buffering"}.get(st.player_state)
                if st.player_state == "PLAYING":
                    self._played = True
                if st.player_state == "IDLE" and self._played:
                    page = "error" if st.idle_reason == "ERROR" else "ended"
                if page and page != self._last:
                    self._last = page
                    if page == "error":
                        self.on_error("Chromecast")
                    else:
                        self.on_state(page)
                if page in ("ended", "error"):
                    return

        threading.Thread(target=run, daemon=True).start()

    def _mc(self, fn):
        def run():
            try:
                fn(self.cast.media_controller)
            except Exception:
                pass

        threading.Thread(target=run, daemon=True).start()

    def play(self):
        self._mc(lambda mc: mc.play())

    def pause(self):
        self._mc(lambda mc: mc.pause())

    def seek(self, seconds: float):
        self.status["pos"] = seconds
        self._mc(lambda mc: mc.seek(seconds))

    def stop(self):
        self._stop.set()
        self._mc(lambda mc: mc.stop())


# ---- the window's side --------------------------------------------------------


class Remotes:
    """The TVs found, the one connected, and what plays there.

    [on_event] gets the page's events ({"type": "cast"|"state"|"error", …}),
    always on the main loop.
    """

    def __init__(self, on_event):
        self.on_event = on_event
        self.renderers: dict[str, Renderer] = {}
        self.casts: dict[str, object] = {}  # uuid -> CastInfo
        self.kind = ""
        self.target_id = ""
        self.device = ""
        self.state = "none"
        self.player = None
        self._scan = threading.Event()
        self._cast_browser = None
        self._zconf = None
        self._cast = None
        self._relay: Relay | None = None

    # -- what the page asks --

    def info(self) -> dict:
        return {"available": True, "state": self.state, "device": self.device, "kind": self.kind or "cast"}

    def devices(self) -> list[dict]:
        out = [{"id": "dlna:" + r.udn, "name": r.name, "kind": "dlna"} for r in self.renderers.values()]
        for uuid, ci in list(self.casts.items()):
            out.append({"id": "cast:" + uuid, "name": getattr(ci, "friendly_name", "Chromecast"), "kind": "cast"})
        return sorted(out, key=lambda d: d["name"].lower())

    @property
    def connected(self) -> bool:
        return self.state == "connected"

    def scan(self, on: bool) -> None:
        if on and not self._scan.is_set():
            self._scan.set()
            threading.Thread(target=self._dlna_loop, daemon=True).start()
            self._start_cast_discovery()
        elif not on:
            self._scan.clear()

    def connect(self, ident: str) -> None:
        self.disconnect(quiet=True)
        kind, _, key = ident.partition(":")
        if kind == "dlna" and key in self.renderers:
            r = self.renderers[key]
            self._set("connected", "dlna", ident, r.name)
        elif kind == "cast" and key in self.casts and HAVE_CAST:
            ci = self.casts[key]
            name = getattr(ci, "friendly_name", "Chromecast")
            self._set("connecting", "cast", ident, name)

            def run():
                try:
                    cast = pychromecast.get_chromecast_from_cast_info(ci, self._zconf)
                    cast.wait(timeout=15)
                    self._cast = cast
                    GLib.idle_add(self._set, "connected", "cast", ident, name)
                except Exception as e:
                    GLib.idle_add(self._set, "none", "", "", "")
                    GLib.idle_add(self.on_event, {"type": "error", "message": "Chromecast", "detail": str(e)})

            threading.Thread(target=run, daemon=True).start()

    def disconnect(self, quiet: bool = False) -> None:
        self.stop()
        cast, self._cast = self._cast, None
        if cast is not None:
            threading.Thread(target=lambda: _quiet(lambda: (cast.quit_app(), cast.disconnect(timeout=3))), daemon=True).start()
        if not quiet and self.state != "none":
            self._set("none", "", "", "")
        elif quiet:
            self.state, self.kind, self.target_id, self.device = "none", "", "", ""

    # -- playback --

    def load(self, url: str, title: str, start: float, image: str = "") -> None:
        self.stop()
        on_state = lambda s: GLib.idle_add(self.on_event, {"type": "state", "state": s})  # noqa: E731
        on_error = lambda d: GLib.idle_add(self.on_event, {"type": "error", "message": self.device or "TV", "detail": d})  # noqa: E731
        if self.kind == "dlna":
            r = self.renderers.get(self.target_id.partition(":")[2])
            if r is None:
                return
            if self._relay is None:
                self._relay = Relay()
            self.player = DlnaPlayer(r, on_state, on_error, self._relay)
            self.player.load(url, title, start)
        elif self.kind == "cast" and self._cast is not None:
            self.player = CastPlayer(self._cast, on_state, on_error)
            self.player.load(url, title, start, image)

    def status(self) -> dict:
        return self.player.status if self.player else {"pos": 0, "dur": 0, "buf": 0}

    def play(self):
        if self.player:
            self.player.play()

    def pause(self):
        if self.player:
            self.player.pause()

    def seek(self, seconds: float):
        if self.player:
            self.player.seek(seconds)

    def stop(self):
        player, self.player = self.player, None
        if player:
            player.stop()

    def shutdown(self):
        self._scan.clear()
        self.disconnect(quiet=True)
        if self._relay is not None:
            self._relay.close()
        if self._cast_browser is not None:
            _quiet(self._cast_browser.stop_discovery)
        if self._zconf is not None:
            _quiet(self._zconf.close)

    # -- inside --

    def _set(self, state, kind, ident, device):
        self.state, self.kind, self.target_id, self.device = state, kind, ident, device
        self.on_event({"type": "cast", "state": state, "device": device})
        return False

    def _dlna_loop(self):
        while self._scan.is_set():
            for loc in search():
                try:
                    with urllib.request.urlopen(loc, timeout=4) as res:
                        r = parse_description(res.read().decode("utf-8", "replace"), loc)
                except Exception:
                    continue
                if r is not None:
                    GLib.idle_add(self._add_renderer, r)
            time.sleep(4)

    def _add_renderer(self, r: Renderer):
        self.renderers[r.udn] = r
        return False

    def _start_cast_discovery(self):
        if not HAVE_CAST or self._cast_browser is not None:
            return
        try:
            import zeroconf

            self._zconf = zeroconf.Zeroconf()
            listener = SimpleCastListener(
                add_callback=lambda uuid, _service: GLib.idle_add(self._add_cast, uuid),
                update_callback=lambda uuid, _service: GLib.idle_add(self._add_cast, uuid),
            )
            self._cast_browser = CastBrowser(listener, self._zconf)
            self._cast_browser.start_discovery()
        except Exception:
            self._cast_browser = None

    def _add_cast(self, uuid):
        ci = self._cast_browser.devices.get(uuid) if self._cast_browser else None
        # Not speakers or speaker groups (no screen). The type comes a moment
        # after the device (None at first): only a known audio type is left out.
        if ci is not None and getattr(ci, "cast_type", None) not in ("audio", "group"):
            self.casts[str(uuid)] = ci
        return False


def _quiet(fn):
    try:
        fn()
    except Exception:
        pass

