"""The download list only redraws rows that changed, and still shows every
change."""

import os
import tempfile
import tkinter as tk
import unittest
from unittest import mock

from mdi import gui
from mdi.engine import COMPLETED, DOWNLOADING, ERROR, PAUSED, Download
from mdi.manager import Manager
from mdi.texts import row_key


class RowTests(unittest.TestCase):
    def setUp(self):
        self.patches = [mock.patch.object(Download, "start", lambda s: None),
                        mock.patch.object(Manager, "_loop", lambda s: None)]
        for p in self.patches:
            p.start()
        tmp = tempfile.mkdtemp()
        self.m = Manager(os.path.join(tmp, "s.json"),
                         os.path.join(tmp, "inbox"))
        self.ds = []
        for i, status in enumerate((COMPLETED, PAUSED, ERROR, DOWNLOADING)):
            d = Download(f"https://a.test/f{i}", tmp)
            d.filename, d.status, d.size = f"f{i}.bin", status, 1000
            d.kind = "media"      # lets the test set progress directly
            if status == ERROR:
                d.error = "HTTP 404 Not Found"
            self.ds.append(d)
        self.m.downloads.extend(self.ds)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = gui.App(self.root, self.m)

    def tearDown(self):
        self.root.destroy()
        for p in self.patches:
            p.stop()

    def cell(self, d, col):
        return self.app.tree.set(d.id, col)

    def updates(self):
        """Ids whose row was rewritten during one refresh."""
        with mock.patch.object(self.app.tree, "item",
                               wraps=self.app.tree.item) as item:
            self.app._refresh_rows()
        return {c.args[0] for c in item.call_args_list if "values" in c.kwargs}

    def test_idle_rows_are_left_alone(self):
        done, paused, failed, active = self.ds
        active._media_got = 100
        self.assertEqual(self.updates(), {active.id})
        self.assertEqual(self.cell(active, "progress"), "10.0%")

    def test_unchanged_active_row_isnt_rewritten(self):
        active = self.ds[3]
        active.speed = 0
        self.updates()
        self.assertEqual(self.updates(), set())

    def test_status_changes_still_show(self):
        done, paused, failed, active = self.ds
        paused.status = COMPLETED
        active.status, active._media_got = ERROR, 1000
        active.error = "HTTP 503 Service Unavailable"
        self.assertEqual(self.updates(), {paused.id, active.id})
        self.assertEqual(self.cell(paused, "status"), "Completed")
        self.assertIn("busy", self.cell(active, "status"))

    def test_renamed_or_new_error_shows(self):
        done, paused, failed, active = self.ds
        done.filename = "renamed.bin"
        failed.error = "[WinError 123] The filename is incorrect"
        self.assertEqual(self.updates(), {done.id, failed.id})
        self.assertEqual(self.cell(done, "name"), "renamed.bin")
        self.assertIn("can't save", self.cell(failed, "status"))

    def test_removed_and_restored_rows(self):
        done = self.ds[0]
        self.app.tree.selection_set(done.id)
        self.app.remove()
        self.assertNotIn(done.id, self.app._rows)
        self.app.undo()              # straight away: no refresh in between
        self.assertEqual(self.app.tree.get_children()[0], done.id)
        self.assertEqual(self.cell(done, "status"), "Completed")

    def test_key_is_none_while_active(self):
        done, paused, failed, active = self.ds
        self.assertIsNone(row_key(active))
        self.assertIsNotNone(row_key(done))
        with mock.patch.object(Download, "is_running",
                               new_callable=mock.PropertyMock,
                               return_value=True):
            self.assertIsNone(row_key(paused))   # still stopping


if __name__ == "__main__":
    unittest.main()
