"""The application: one window, opened again if it is already running."""

from __future__ import annotations

import os

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import APP_ID, APP_NAME

# A translucent window: the night tile of the logo, a little see-through,
# with a champagne glow up right and a cool one down left. The page above
# it is transparent (styles.css, html.platform-linux); a video covers it.
_CSS = b"""
window.jellyashy-window { background: transparent; }
.jellyashy-backdrop {
  background-color: rgba(7, 8, 12, 0.86);
  background-image:
    radial-gradient(ellipse at 85% 0%, rgba(217, 169, 79, 0.13), transparent 55%),
    radial-gradient(ellipse at 0% 100%, rgba(90, 110, 255, 0.10), transparent 50%);
}
headerbar.jellyashy-titlebar {
  background: transparent; box-shadow: none; min-height: 38px;
}
"""


class JellyAshyApplication(Adw.Application):
    def __init__(self):
        flags = Gio.ApplicationFlags.DEFAULT_FLAGS
        if os.environ.get("JELLYASHY_TEST_PORT"):
            # A test run (devtools.py) is its own instance, beside one in use.
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID, flags=flags)
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
