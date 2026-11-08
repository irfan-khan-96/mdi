"""Undoable removal: take, restore, discard."""

import os
import tempfile
import unittest

from mdi.engine import COMPLETED, PAUSED, QUEUED, Download
from mdi.gui import undo_text
from mdi.manager import Manager


class UndoTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.m = Manager(os.path.join(self.dir, "s.json"),
                         os.path.join(self.dir, "inbox"))
        self.ds = []
        for n in "abcd":
            d = Download(f"https://invalid.test/{n}", self.dir)
            d.filename, d.status = n, PAUSED
            d.path = os.path.join(self.dir, n)
            self.ds.append(d)
        self.m.downloads.extend(self.ds)

    def tearDown(self):
        self.m.shutdown()

    def names(self):
        return "".join(d.filename for d in self.m.downloads)

    def test_restore_puts_items_back_in_place(self):
        a, b, c, d = self.ds
        taken = self.m.take([d, b])
        self.assertEqual(self.names(), "ac")
        self.m.restore(taken)
        self.assertEqual(self.names(), "abcd")

    def test_restore_restarts_what_was_waiting(self):
        self.ds[0].status = QUEUED
        taken = self.m.take([self.ds[0], self.ds[1]])
        self.assertEqual(self.ds[0].status, PAUSED)
        self.m.restore(taken)
        self.assertEqual(self.ds[0].status, QUEUED)
        self.assertEqual(self.ds[1].status, PAUSED)

    def test_discard_deletes_partial_but_never_finished_files(self):
        part, done = self.ds[0], self.ds[1]
        done.status = COMPLETED
        for p in (part.part_path, done.path):
            open(p, "w").close()
        self.m.discard(self.m.take([part, done]))
        self.assertFalse(os.path.exists(part.part_path))
        self.assertTrue(os.path.exists(done.path))

    def test_discard_skips_restored_items(self):
        d = self.ds[0]
        open(d.part_path, "w").close()
        taken = self.m.take([d])
        self.m.restore(taken)
        self.m.discard(taken)
        self.assertTrue(os.path.exists(d.part_path))

    def test_removal_is_saved(self):
        self.m.take([self.ds[0]])
        m2 = Manager(self.m.store_path, self.m.inbox)
        try:
            self.assertEqual("".join(d.filename for d in m2.downloads), "bcd")
        finally:
            m2.shutdown()

    def test_text(self):
        self.assertEqual(undo_text(1), "Removed download · Undo (Ctrl+Z)")
        self.assertIn("3 downloads", undo_text(3))


if __name__ == "__main__":
    unittest.main()
