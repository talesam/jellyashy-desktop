"""A local test hook, off unless JELLYASHY_TEST_PORT is set.

One JSON request per line on 127.0.0.1:<port>, one JSON answer:

    {"eval": "document.title"}            -> {"ok": true, "value": "JellyAshy"}
    {"shot": "/tmp/window.png"}           -> {"ok": true} (the window as drawn, video included)
    {"key": "Down"}                       -> {"ok": true} (a key pressed in the page)

tools/drive.py talks to it. It only listens on the loopback interface.
"""

from __future__ import annotations

import json

from gi.repository import Gdk, Gio, GLib, Graphene, Gtk


class TestHook:
    def __init__(self, window, port: int):
        self.window = window
        self.service = Gio.SocketService()
        self.service.add_address(
            Gio.InetSocketAddress.new_from_string("127.0.0.1", port),
            Gio.SocketType.STREAM, Gio.SocketProtocol.TCP, None,
        )
        self.service.connect("incoming", self._incoming)
        self.service.start()

    def _incoming(self, _service, connection, _source) -> bool:
        stream = Gio.DataInputStream.new(connection.get_input_stream())
        stream.read_line_async(GLib.PRIORITY_DEFAULT, None, self._on_line, connection)
        return True

    def _on_line(self, stream, result, connection) -> None:
        line, _ = stream.read_line_finish_utf8(result)
        try:
            request = json.loads(line or "{}")
        except ValueError:
            request = {}
        done = lambda answer: self._answer(connection, answer)  # noqa: E731
        try:
            if "eval" in request:
                self._eval(request["eval"], done)
            elif "shot" in request:
                self._shot(request["shot"])
                done({"ok": True})
            elif "key" in request:
                self._key(request["key"])
                done({"ok": True})
            else:
                done({"ok": False, "error": "unknown request"})
        except Exception as error:  # report it to the caller
            done({"ok": False, "error": str(error)})

    def _answer(self, connection, answer: dict) -> None:
        out = connection.get_output_stream()
        out.write_all((json.dumps(answer) + "\n").encode(), None)
        connection.close(None)

    def _eval(self, script: str, done) -> None:
        def finished(web, result):
            try:
                value = web.evaluate_javascript_finish(result)
                text = value.to_json(0) if value is not None and not value.is_undefined() else "null"
                done({"ok": True, "value": json.loads(text)})
            except GLib.Error as error:
                done({"ok": False, "error": error.message})
        self.window.web.evaluate_javascript(script, -1, None, None, None, finished)

    def _shot(self, path: str) -> None:
        widget = self.window
        width, height = widget.get_width(), widget.get_height()
        paintable = Gtk.WidgetPaintable.new(widget)
        snapshot = Gtk.Snapshot()
        paintable.snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node is None:
            raise RuntimeError("nothing drawn yet")
        renderer = widget.get_native().get_renderer()
        texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, width, height))
        texture.save_to_png(path)

    def _key(self, name: str) -> None:
        if not Gdk.keyval_from_name(name):
            raise ValueError(f"no key {name}")
        # The page hears keys as DOM events: send it one the same way.
        codes = {"Up": 38, "Down": 40, "Left": 37, "Right": 39, "Return": 13, "Escape": 27, "BackSpace": 8, "space": 32}
        code = codes.get(name)
        if code is None:
            raise ValueError(f"no DOM code for {name}")
        script = ("(document.activeElement||document.body).dispatchEvent(new KeyboardEvent('keydown',"
                  f"{{keyCode:{code},bubbles:true,cancelable:true}}))")
        self.window.web.evaluate_javascript(script, -1, None, None, None, None, None)


def install(window) -> TestHook | None:
    import os

    port = os.environ.get("JELLYASHY_TEST_PORT")
    if not port:
        return None
    return TestHook(window, int(port))
