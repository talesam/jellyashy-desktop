"""The video: libmpv drawn into a GtkGLArea (mpv's render API).

It sits *behind* the web page, like ExoPlayer in the Android app: the page
turns its background transparent while a video plays and draws the
controls and the subtitles on top.
"""

from __future__ import annotations

import ctypes
import locale

from typing import ClassVar

import gi

gi.require_version("Gtk", "4.0")

import mpv  # noqa: E402
from gi.repository import GLib, GObject, Gtk  # noqa: E402

# OpenGL entry points through EGL (GTK4 uses EGL on Wayland and, by
# default, on X11 too); GLX when libEGL is missing.
try:
    _egl = ctypes.CDLL("libEGL.so.1")
    _egl.eglGetProcAddress.restype = ctypes.c_void_p
    _egl.eglGetProcAddress.argtypes = [ctypes.c_char_p]
    _GET_PROC = _egl.eglGetProcAddress
except OSError:  # pragma: no cover - depends on the system
    _glx = ctypes.CDLL("libGL.so.1")
    _glx.glXGetProcAddressARB.restype = ctypes.c_void_p
    _glx.glXGetProcAddressARB.argtypes = [ctypes.c_char_p]
    _GET_PROC = _glx.glXGetProcAddressARB


def _proc(name: str) -> int:
    return _GET_PROC(name.encode("utf-8")) or 0


def _get_proc_address(_ctx, name) -> int:
    if isinstance(name, bytes):
        name = name.decode("utf-8")
    return _proc(name)


# python-mpv wants a ctypes callback, not a plain function.
_PROC_ADDRESS_CB = mpv.MpvGlGetProcAddressFn(_get_proc_address)

_GL_DRAW_FRAMEBUFFER_BINDING = 0x8CA6
_glGetIntegerv_t = ctypes.CFUNCTYPE(None, ctypes.c_uint, ctypes.POINTER(ctypes.c_int))
_glGetIntegerv = None


def _current_fbo() -> int:
    """The framebuffer GtkGLArea is drawing into."""
    global _glGetIntegerv
    if _glGetIntegerv is None:
        _glGetIntegerv = _glGetIntegerv_t(_proc("glGetIntegerv"))
    value = ctypes.c_int(0)
    _glGetIntegerv(_GL_DRAW_FRAMEBUFFER_BINDING, ctypes.byref(value))
    return value.value


def error_text(code: int) -> str:
    """mpv's words for an error code ("loading failed")."""
    try:
        return mpv._mpv_error_string(code).decode() if code else ""
    except Exception:
        return str(code)


def aspect_options(mode: str, width: int, height: int) -> dict[str, str]:
    """The page's picture modes as mpv properties (they apply while playing)."""
    if mode == "fill" and width > 0 and height > 0:  # stretched to the window
        return {"video-aspect-override": f"{width}:{height}", "panscan": "0"}
    if mode == "zoom":  # fills the window, the edges cut
        return {"video-aspect-override": "no", "panscan": "1"}
    return {"video-aspect-override": "no", "panscan": "0"}  # fit: the whole picture


def audio_tracks(track_list: list[dict] | None) -> list[dict]:
    """mpv's audio tracks in the shape the page reads (as from ExoPlayer)."""
    out = []
    for t in track_list or []:
        if t.get("type") != "audio":
            continue
        out.append({
            "id": str(t.get("id")),
            "language": t.get("lang") or "",
            "label": (t.get("title") or "").strip(),
            "selected": bool(t.get("selected")),
        })
    return out


class VideoView(Gtk.GLArea):
    """mpv's picture, and the player the page drives through the bridge."""

    __gtype_name__ = "JellyAshyVideoView"

    __gsignals__: ClassVar[dict] = {
        # loading | playing | paused | buffering | ended | error
        "state-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "tracks-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # message, detail
        "failed": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self, user_agent: str):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_has_depth_buffer(False)
        self.set_has_stencil_buffer(False)
        # libmpv parses numbers with the C locale.
        locale.setlocale(locale.LC_NUMERIC, "C")
        self._mpv = mpv.MPV(
            vo="libmpv",
            hwdec="auto-safe",
            ytdl=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            osc=False,
            # The page draws the subtitles (VTT from the server).
            sid="no",
            sub_auto="no",
            cache="yes",
            cache_secs="60",
            demuxer_max_bytes="150MiB",
            demuxer_max_back_bytes="50MiB",
            network_timeout="30",
            user_agent=user_agent,
            keep_open="no",
        )
        self._user_agent = user_agent
        self._ctx: mpv.MpvRenderContext | None = None
        self._state = "idle"
        self._started = False
        # Bumped on every load and stop: events queued for an older file
        # must not change the state of the new one.
        self._generation = 0
        self._render_pending = False
        self._terminated = False
        self._aspect = "fit"

        self._mpv.register_event_callback(self._on_event)
        for prop in ("pause", "paused-for-cache"):
            self._mpv.observe_property(prop, self._on_property)
        self._mpv.observe_property("track-list", self._on_tracks)

        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)
        self.connect("render", self._on_render)

    # ---- GL --------------------------------------------------------------

    def _on_realize(self, _area) -> None:
        self.make_current()
        if self.get_error() is not None or self._terminated:
            return
        self._ctx = mpv.MpvRenderContext(
            self._mpv, "opengl", opengl_init_params={"get_proc_address": _PROC_ADDRESS_CB},
        )
        self._ctx.update_cb = self._on_update

    def _on_unrealize(self, _area) -> None:
        if self._ctx is not None:
            self.make_current()
            self._ctx.free()
            self._ctx = None

    def _on_update(self) -> None:
        # From an mpv thread: one redraw on the main loop.
        if not self._render_pending:
            self._render_pending = True
            GLib.idle_add(self._queue)

    def _queue(self) -> bool:
        self._render_pending = False
        self.queue_render()
        return GLib.SOURCE_REMOVE

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        Gtk.GLArea.do_size_allocate(self, width, height, baseline)
        if self._aspect == "fill":
            self.set_aspect("fill")

    def _on_render(self, _area, _gl) -> bool:
        if self._ctx is None:
            return True
        scale = self.get_scale_factor()
        self._ctx.render(
            flip_y=True,
            opengl_fbo={"w": self.get_width() * scale, "h": self.get_height() * scale,
                        "fbo": _current_fbo()},
        )
        return True

    def terminate(self) -> None:
        """Shut mpv down (the window is closing)."""
        if self._terminated:
            return
        self._terminated = True
        if self._ctx is not None:
            if self.get_realized():
                self.make_current()
            self._ctx.free()
            self._ctx = None
        self._mpv.terminate()

    # ---- events (mpv thread -> main loop) --------------------------------

    def _on_event(self, event) -> None:
        eid = event.event_id.value
        gen = self._generation
        if eid == mpv.MpvEventID.END_FILE:
            data = event.data
            if data.reason == mpv.MpvEventEndFile.ERROR:
                detail = error_text(data.error)
                GLib.idle_add(self._fail, gen, detail)
            elif data.reason == mpv.MpvEventEndFile.EOF:
                GLib.idle_add(self._state_for, gen, "ended")
        elif eid == mpv.MpvEventID.PLAYBACK_RESTART:
            # The first frame after a load or a seek is on its way.
            GLib.idle_add(self._restarted, gen)

    def _restarted(self, gen: int) -> bool:
        if gen == self._generation:
            self._started = True
            self._set_state(self._playing_state())
        return GLib.SOURCE_REMOVE

    def _fail(self, gen: int, detail: str) -> bool:
        if gen == self._generation:
            self._set_state("error")
            self.emit("failed", "mpv", detail)
        return GLib.SOURCE_REMOVE

    def _state_for(self, gen: int, state: str) -> bool:
        if gen == self._generation:
            self._set_state(state)
        return GLib.SOURCE_REMOVE

    def _playing_state(self) -> str:
        try:
            if self._mpv.pause:
                return "paused"
            if self._mpv.paused_for_cache:
                return "buffering"
        except Exception:
            pass
        return "playing"

    def _on_property(self, _name, _value) -> None:
        GLib.idle_add(self._property_changed, self._generation)

    def _property_changed(self, gen: int) -> bool:
        if gen == self._generation and self._started and self._state not in ("ended", "error", "idle"):
            self._set_state(self._playing_state())
        return GLib.SOURCE_REMOVE

    def _on_tracks(self, _name, _value) -> None:
        GLib.idle_add(lambda: self.emit("tracks-changed") and False)

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.emit("state-changed", state)

    # ---- control ---------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def active(self) -> bool:
        return self._state != "idle"

    def load(self, url: str, start: float = 0, user_agent: str = "") -> None:
        self._generation += 1
        self._started = False
        options = {"user_agent": user_agent or self._user_agent, "speed": "1"}
        if start > 0:
            options["start"] = f"{start:.3f}"
        self._set_state("loading")
        self._mpv.loadfile(url, "replace", **options)
        self._mpv.pause = False

    def stop(self) -> None:
        self._generation += 1
        self._started = False
        try:
            self._mpv.command("stop")
        except Exception:
            pass
        self._set_state("idle")

    def play(self) -> None:
        if self._state != "ended":
            self._mpv.pause = False

    def pause(self) -> None:
        self._mpv.pause = True

    def seek(self, seconds: float) -> None:
        try:
            self._mpv.seek(max(0.0, seconds), "absolute")
        except Exception:
            pass  # not seekable yet

    def set_rate(self, rate: float) -> None:
        self._mpv.speed = min(4.0, max(0.25, rate))

    def set_audio(self, track_id: str) -> None:
        try:
            self._mpv["aid"] = int(track_id)
        except (TypeError, ValueError):
            pass

    def set_aspect(self, mode: str) -> None:
        self._aspect = mode
        for key, value in aspect_options(mode, self.get_width(), self.get_height()).items():
            self._mpv[key] = value

    def status(self) -> dict[str, float]:
        """{pos, dur, buf} in seconds, as the page asks for it."""
        def num(value) -> float:
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0
        pos = num(self._mpv.time_pos)
        ahead = num(self._mpv.demuxer_cache_duration)
        return {"pos": pos, "dur": num(self._mpv.duration), "buf": pos + ahead}

    def tracks(self) -> list[dict]:
        try:
            return audio_tracks(self._mpv.track_list)
        except Exception:
            return []
