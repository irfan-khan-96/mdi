"""Icon files, toolbar glyphs, the About box and status colours."""

import os
import struct
import tempfile
import tkinter as tk
import unittest
from unittest import mock

from mdi import __version__, dialogs, gui
from mdi.engine import COMPLETED, DOWNLOADING, ERROR, PAUSED, Download
from mdi.manager import Manager

ASSETS = os.path.join(os.path.dirname(gui.__file__), "assets")


def ico_sizes(path):
    """Image sizes in a .ico, read from its directory (no Pillow needed)."""
    with open(path, "rb") as f:
        data = f.read()
    count = struct.unpack_from("<H", data, 4)[0]
    return {data[6 + 16 * i] or 256 for i in range(count)}


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


class IconFileTests(unittest.TestCase):
    def test_ico_has_hand_tuned_sizes(self):
        sizes = ico_sizes(os.path.join(ASSETS, "icon.ico"))
        self.assertTrue({16, 20, 24, 32, 48, 256} <= sizes, sizes)

    def test_every_glyph_exists_in_both_sizes(self):
        for name in ("add", "resume", "pause", "remove", "folder",
                     "settings", "about"):
            for px in (20, 32):
                self.assertEqual(
                    png_size(os.path.join(ASSETS, f"tb_{name}_{px}.png")),
                    (px, px))


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(Download, "start", lambda s: None),
                        mock.patch.object(Manager, "_loop", lambda s: None)]
        for p in self.patches:
            p.start()
        tmp = tempfile.mkdtemp()
        self.m = Manager(os.path.join(tmp, "s.json"),
                         os.path.join(tmp, "inbox"))
        for i, st in enumerate((COMPLETED, DOWNLOADING, PAUSED, ERROR)):
            d = Download(f"https://a.test/{i}", tmp)
            d.filename, d.status = f"f{i}", st
            self.m.downloads.append(d)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = gui.App(self.root, self.m)

    def tearDown(self):
        self.root.destroy()
        for p in self.patches:
            p.stop()

    def test_toolbar_buttons_have_icons(self):
        self.assertEqual(len(self.app._glyphs), 7)

    def test_missing_glyph_files_fall_back_to_text(self):
        with mock.patch.object(gui, "ASSETS", tempfile.mkdtemp()):
            self.assertEqual(gui.load_glyphs(self.root), {})

    def test_rows_are_tagged_with_their_status(self):
        for d in self.m.downloads:
            self.assertEqual(self.app.tree.item(d.id, "tags"), (d.status,))
        self.assertEqual(
            str(self.app.tree.tag_configure(ERROR, "foreground")), "#c01c28")
        d = self.m.downloads[2]                 # paused -> downloading
        d.status = DOWNLOADING
        self.app._refresh_rows()
        self.assertEqual(self.app.tree.item(d.id, "tags"), (DOWNLOADING,))

    def test_about_box(self):
        dlg = dialogs.AboutDialog(self.app)
        texts = []
        stack = list(dlg.winfo_children())
        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())
            try:
                texts.append(str(w.cget("text")))
            except tk.TclError:
                pass
        self.assertIn(f"Version {__version__}", texts)
        self.assertIn("github.com/irfan-khan-96/mdi", texts)
        with mock.patch("webbrowser.open") as web:
            dialogs.open_link(dialogs.PROJECT_URL)
        web.assert_called_once_with("https://github.com/irfan-khan-96/mdi")
        dlg.destroy()


if __name__ == "__main__":
    unittest.main()
