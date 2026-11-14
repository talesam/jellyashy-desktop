"""Entry point."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("WebKit", "6.0")


def main() -> int:
    from .application import JellyAshyApplication

    return JellyAshyApplication().run(sys.argv)
