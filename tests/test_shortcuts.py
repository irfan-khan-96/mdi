"""Keyboard shortcuts: Space decides between pause and resume."""

import unittest

from mdi.engine import COMPLETED, DOWNLOADING, ERROR, PAUSED, QUEUED, Download
from mdi.gui import SHORTCUTS_HELP, toggle_plan


def dl(status):
    d = Download("https://a.test/f", "C:/tmp")
    d.status = status
    return d


class ToggleTests(unittest.TestCase):
    def test_running_pauses(self):
        self.assertEqual(toggle_plan([dl(DOWNLOADING)]), "pause")

    def test_waiting_pauses(self):
        self.assertEqual(toggle_plan([dl(QUEUED)]), "pause")

    def test_paused_or_failed_resumes(self):
        self.assertEqual(toggle_plan([dl(PAUSED)]), "resume")
        self.assertEqual(toggle_plan([dl(ERROR)]), "resume")

    def test_mixed_selection_pauses_first(self):
        self.assertEqual(toggle_plan([dl(PAUSED), dl(DOWNLOADING)]), "pause")

    def test_finished_or_nothing_does_nothing(self):
        self.assertIsNone(toggle_plan([dl(COMPLETED)]))
        self.assertIsNone(toggle_plan([]))

    def test_help_lists_every_key(self):
        for key in ("Ctrl+V", "Space", "Enter", "Delete", "Ctrl+O", "Ctrl+A"):
            self.assertIn(key, SHORTCUTS_HELP)


if __name__ == "__main__":
    unittest.main()
