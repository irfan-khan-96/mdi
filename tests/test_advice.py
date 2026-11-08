"""Plain-language error advice and the one-click fixes."""

import os
import shutil
import tempfile
import tkinter as tk
import unittest
from unittest import mock

from mdi.advice import ACTION_LABELS, explain
from mdi.engine import ERROR, Download, Segment


class ExplainTests(unittest.TestCase):
    # Real messages produced by engine.py, urllib and yt-dlp.
    CASES = [
        ("this link is a web page, not a file. For YouTube and other video "
         "sites install yt-dlp:  pip install yt-dlp", "file", "video_setup"),
        ("not enough disk space: need 2150 MB, 300 MB free", "file",
         "change_folder"),
        ("file changed on the server during the download; resume to start "
         "over", "file", "restart"),
        ("server stopped accepting byte ranges", "file", "restart"),
        ("HTTP 403 Forbidden", "file", "new_link"),
        ("HTTP 410 Gone", "file", "new_link"),
        ("HTTP 404 Not Found", "file", "new_link"),
        ("HTTP 503 Service Unavailable", "file", "retry"),
        ("<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify "
         "failed>", "file", "retry"),
        ("<urlopen error [Errno 11001] getaddrinfo failed>", "file", "retry"),
        ("[Errno 13] Permission denied: 'C:\\\\Windows\\\\a.zip.part'", "file",
         "change_folder"),
        ("refused insecure redirect from HTTPS to HTTP", "file", None),
        ("refused redirect to ftp:// link", "file", None),
        ("all connections failed; resume to retry", "file", "retry"),
        ("timed out", "file", "retry"),
        ("[youtube] abc: Sign in to confirm you're not a bot", "media", None),
        ("Unsupported URL: https://example.com/", "media", None),
        ("[youtube] abc: Video unavailable", "media", None),
        ("[youtube] abc: nsig extraction failed: Some formats may be missing",
         "media", "update_ytdlp"),
        ("something nobody expected", "file", "retry"),
    ]

    def test_every_known_error_gets_advice_and_a_valid_action(self):
        for error, kind, action in self.CASES:
            advice, got = explain(error, kind)
            self.assertEqual(got, action, error)
            self.assertTrue(advice.endswith("."), advice)
            self.assertNotIn("Errno", advice)
            if got:
                self.assertIn(got, ACTION_LABELS)

    def test_disk_space_numbers_are_friendly(self):
        advice, _ = explain("not enough disk space: need 2150 MB, 300 MB free")
        self.assertIn("2.1 GB", advice)
        self.assertIn("300 MB", advice)


class FixActionTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        from mdi.gui import App
        from mdi.manager import Manager
        self.m = Manager(os.path.join(self.dir, "s.json"))
        self.m.settings["max_parallel"] = 0   # keep test downloads queued
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, self.m)
        self.d = Download("https://a.test/f.zip", self.dir)
        self.d.status, self.d.error = ERROR, "HTTP 403 Forbidden"
        self.d.path = os.path.join(self.dir, "f.zip")
        open(self.d.part_path, "wb").close()
        self.m.downloads.append(self.d)

    def tearDown(self):
        self.m.shutdown()
        self.root.destroy()
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_new_link_keeps_progress_and_requeues(self):
        kept = Segment(0, 99, 50)
        self.d.segments = [kept]
        with mock.patch("mdi.gui.simpledialog.askstring",
                        return_value="https://a.test/f.zip?sig=new"):
            self.app.apply_fix(self.d, "new_link")
        self.assertEqual(self.d.url, "https://a.test/f.zip?sig=new")
        self.assertEqual(self.d.segments, [kept])
        self.assertEqual(self.d.status, "queued")

    def test_new_link_rejects_non_http(self):
        with mock.patch("mdi.gui.simpledialog.askstring",
                        return_value="file:///C:/x"), \
                mock.patch("mdi.gui.messagebox.showerror") as err:
            self.app.apply_fix(self.d, "new_link")
        err.assert_called_once()
        self.assertEqual(self.d.status, ERROR)

    def test_restart_discards_partial_file(self):
        self.app.apply_fix(self.d, "restart")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "f.zip.part")))
        self.assertEqual(self.d.segments, [])
        self.assertEqual(self.d.status, "queued")

    def test_change_folder(self):
        other = os.path.join(self.dir, "other")
        with mock.patch("mdi.gui.filedialog.askdirectory", return_value=other):
            self.app.apply_fix(self.d, "change_folder")
        self.assertEqual((self.d.save_dir, self.d.path), (other, None))
        self.assertEqual(self.d.status, "queued")

    def test_copy_command(self):
        with mock.patch("mdi.gui.messagebox.showinfo"):
            self.app.apply_fix(self.d, "update_ytdlp")
        self.assertEqual(self.root.clipboard_get(), "pip install -U yt-dlp")
        self.assertEqual(self.d.status, ERROR)

    def test_dialog_and_row_show_advice(self):
        from mdi.gui import FixDialog
        dlg = FixDialog(self.app, self.d)
        texts = [w.cget("text") for w in dlg.winfo_children()[0].winfo_children()
                 if isinstance(w, tk.ttk.Label)]
        self.assertTrue(any("refused the download" in t for t in texts))
        dlg.destroy()
        self.assertTrue(self.app._row(self.d)[-1].startswith("⚠ The site"))


if __name__ == "__main__":
    unittest.main()
