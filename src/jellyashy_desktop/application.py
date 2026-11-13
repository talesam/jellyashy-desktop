"""The application: one window, opened again if it is already running."""

from __future__ import annotations

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import APP_ID, APP_NAME

# The window's own background shows only around a video that doesn't fill it.
_CSS = b".jellyashy-window, .jellyashy-window headerbar { background: #07080c; }"


class JellyAshyApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.window = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        Gtk.Window.set_default_icon_name(APP_ID)
        css = Gtk.CssProvider()
        css.load_from_bytes(GLib.Bytes.new(_CSS))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.window.close() if self.window else self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Primary>q"])

    def do_activate(self) -> None:
        if self.window is None:
            try:
                from .window import JellyAshyWindow
            except (ImportError, OSError) as error:  # libmpv or WebKitGTK missing
                self._missing(str(error))
                return
            self.window = JellyAshyWindow(self)
            self.window.connect("destroy", lambda *_: setattr(self, "window", None))
        self.window.present()

    def _missing(self, detail: str) -> None:
        dialog = Adw.AlertDialog(
            heading=APP_NAME,
            body="mpv (libmpv, python-mpv) and WebKitGTK 6 are needed to run.\n\n" + detail,
        )
        dialog.add_response("close", "OK")
        window = Adw.ApplicationWindow(application=self)
        dialog.connect("response", lambda *_: window.close())
        window.present()
        dialog.present(window)
