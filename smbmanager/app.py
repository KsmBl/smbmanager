"""Application object and entry point."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gio, Gtk  # noqa: E402

from . import APP_ID  # noqa: E402
from .window import MainWindow, apply_css  # noqa: E402


class SmbManagerApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )
        self.window = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        apply_css()

    def do_activate(self):
        if self.window is None:
            self.window = MainWindow(self)
        self.window.show_all()
        self.window.present()


def main(argv=None) -> int:
    return SmbManagerApp().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
