"""The window: JellyAshy's web app in a WebKitGTK view, mpv behind it."""

from __future__ import annotations

import os
import socket
from pathlib import Path

from gi.repository import Adw, Gdk, GLib, Gtk, WebKit

from . import APP_NAME, APP_VERSION, bridge
from .player import VideoView

WEB_DIR = Path(__file__).parent / "web"
# How often the page learns the position while a video is open.
_STATUS_MS = 250


def data_dir() -> Path:
    return Path(GLib.get_user_data_dir()) / "jellyashy"


def cache_dir() -> Path:
    return Path(GLib.get_user_cache_dir()) / "jellyashy"


def device_info() -> dict:
    """What the page asks of the device (androidInfo in the web app)."""
    return {
        "model": socket.gethostname()[:64] or "Linux",
        "tv": False,
        "mode": "tv",
        "uhd": True,
        "hevc": True,
        "ac3": True,
        "sdk": 0,
    }


def is_app_uri(uri: str) -> bool:
    """The app's own pages (everything else opens in the browser)."""
    return uri.startswith("file://") or uri.startswith("about:")


class JellyAshyWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1280, 720)
        self.add_css_class("jellyashy-window")
        self._status_source = 0
        self._inhibit = 0
        self._tracks_dirty = False

        user_agent = f"{APP_NAME}/{APP_VERSION} (Linux)"
        self.video = VideoView(user_agent)
        self.video.set_visible(False)
        self.video.connect("state-changed", self._on_state)
        self.video.connect("tracks-changed", self._on_tracks)
        self.video.connect("failed", self._on_failed)

        self.web = self._make_web(user_agent)

        overlay = Gtk.Overlay()
        overlay.set_child(self.video)
        overlay.add_overlay(self.web)

        # A slim title bar to move and close the window; gone in full screen.
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        full = Gtk.Button(icon_name="view-fullscreen-symbolic", tooltip_text="F11")
        full.connect("clicked", lambda *_: self._toggle_fullscreen())
        header.pack_end(full)
        self._toolbar = Adw.ToolbarView()
        self._toolbar.add_top_bar(header)
        self._toolbar.set_content(overlay)
        self.set_content(self._toolbar)
        self.connect("notify::fullscreened", lambda *_: self._toolbar.set_reveal_top_bars(not self.is_fullscreen()))

        keys = Gtk.ShortcutController()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("F11"),
            Gtk.CallbackAction.new(lambda *_: self._toggle_fullscreen()),
        ))
        self.add_controller(keys)
        self.connect("close-request", self._on_close)

        self.web.load_uri((WEB_DIR / "index.html").as_uri())
        from . import devtools

        self._test_hook = devtools.install(self)

    # ---- the web view -----------------------------------------------------

    def _make_web(self, user_agent: str) -> WebKit.WebView:
        data_dir().mkdir(parents=True, exist_ok=True)
        cache_dir().mkdir(parents=True, exist_ok=True)
        # Its own place on disk: the sign-in (localStorage) survives restarts.
        session = WebKit.NetworkSession.new(str(data_dir()), str(cache_dir()))

        content = WebKit.UserContentManager()
        content.add_script(WebKit.UserScript.new(
            bridge.shim(device_info()),
            WebKit.UserContentInjectedFrames.TOP_FRAME,
            WebKit.UserScriptInjectionTime.START,
            None, None,
        ))
        content.register_script_message_handler(bridge.MESSAGE_HANDLER, None)
        content.connect(f"script-message-received::{bridge.MESSAGE_HANDLER}", self._on_message)

        web = WebKit.WebView(network_session=session, user_content_manager=content)
        settings = web.get_settings()
        settings.set_user_agent(f"{settings.get_user_agent()} {user_agent}")
        # The app is a local file that talks to any Jellyfin server (http or https).
        settings.set_allow_file_access_from_file_urls(True)
        settings.set_allow_universal_access_from_file_urls(True)
        settings.set_enable_developer_extras(bool(os.environ.get("JELLYASHY_DEVTOOLS")))
        settings.set_hardware_acceleration_policy(WebKit.HardwareAccelerationPolicy.ALWAYS)
        settings.set_enable_back_forward_navigation_gestures(False)
        # Transparent: the video shows through while one plays.
        web.set_background_color(Gdk.RGBA(red=0, green=0, blue=0, alpha=0))
        web.connect("decide-policy", self._on_policy)
        web.connect("create", self._on_create)
        web.connect("context-menu", lambda *_: not os.environ.get("JELLYASHY_DEVTOOLS"))
        web.connect("web-process-terminated", self._on_crash)
        web.set_hexpand(True)
        web.set_vexpand(True)
        return web

    def _on_policy(self, _web, decision, kind) -> bool:
        if kind != WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        uri = decision.get_navigation_action().get_request().get_uri()
        if is_app_uri(uri):
            return False
        # The site, donations, help: in the browser.
        decision.ignore()
        self._open_external(uri)
        return True

    def _on_create(self, _web, action):
        uri = action.get_request().get_uri()
        if uri and not is_app_uri(uri):
            self._open_external(uri)
        return None

    def _open_external(self, uri: str) -> None:
        Gtk.UriLauncher.new(uri).launch(self, None, None, None)

    def _on_crash(self, _web, _reason) -> None:
        # The page's process died (out of memory): start it again.
        self.video.stop()
        self.web.load_uri((WEB_DIR / "index.html").as_uri())

    def _run(self, script: str) -> None:
        self.web.evaluate_javascript(script, -1, None, None, None, None, None)

    # ---- the page's calls ---------------------------------------------------

    def _on_message(self, _content, value) -> None:
        command = bridge.parse_message(value.to_string())
        if command is None:
            return
        cmd, arg = command.cmd, command.arg
        if cmd == "load":
            load = bridge.parse_load(arg)
            if load is None:
                return
            self.video.set_visible(True)
            self.video.load(load.url, load.start, load.user_agent)
            self._start_status()
        elif cmd == "stop":
            self.video.stop()
            self.video.set_visible(False)
            self._stop_status()
        elif cmd == "play":
            self.video.play()
        elif cmd == "pause":
            self.video.pause()
        elif cmd == "seek":
            self.video.seek(bridge.to_number(arg))
        elif cmd == "rate":
            self.video.set_rate(bridge.to_number(arg, 1.0))
        elif cmd == "audio":
            self.video.set_audio(str(arg))
        elif cmd == "aspect":
            self.video.set_aspect(str(arg))
        elif cmd == "exit":
            self.close()

    # ---- the player's news to the page --------------------------------------

    def _on_state(self, _video, state: str) -> None:
        if state != "idle":
            self._run(bridge.event_script({"type": "state", "state": state}))
        self._set_inhibit(state in ("loading", "playing", "buffering"))

    def _on_tracks(self, _video) -> None:
        self._tracks_dirty = True

    def _on_failed(self, _video, message: str, detail: str) -> None:
        self._run(bridge.event_script({"type": "error", "message": message, "detail": detail}))

    def _start_status(self) -> None:
        if not self._status_source:
            self._status_source = GLib.timeout_add(_STATUS_MS, self._push_status)

    def _stop_status(self) -> None:
        if self._status_source:
            GLib.source_remove(self._status_source)
            self._status_source = 0

    def _push_status(self) -> bool:
        if not self.video.active:
            self._status_source = 0
            return GLib.SOURCE_REMOVE
        tracks = None
        if self._tracks_dirty:
            self._tracks_dirty = False
            tracks = self.video.tracks()
        self._run(bridge.status_script(self.video.status(), tracks))
        if tracks is not None:
            self._run(bridge.event_script({"type": "tracks"}))
        return GLib.SOURCE_CONTINUE

    def _set_inhibit(self, on: bool) -> None:
        """No screen saver or sleep while a video plays."""
        app = self.get_application()
        if on and not self._inhibit and app is not None:
            flags = Gtk.ApplicationInhibitFlags.IDLE | Gtk.ApplicationInhibitFlags.SUSPEND
            self._inhibit = app.inhibit(self, flags, APP_NAME)
        elif not on and self._inhibit and app is not None:
            app.uninhibit(self._inhibit)
            self._inhibit = 0

    # ---- the window ----------------------------------------------------------

    def _toggle_fullscreen(self) -> bool:
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            self.fullscreen()
        return True

    def _on_close(self, _window) -> bool:
        self._stop_status()
        self._set_inhibit(False)
        self.video.terminate()
        return False
