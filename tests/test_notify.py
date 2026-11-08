"""Taskbar progress and notification decisions."""

import unittest

from mdi.engine import COMPLETED, DOWNLOADING, ERROR, Download
from mdi.gui import finished_since, notification_for, overall_progress


def dl(name, status=DOWNLOADING, size=None, got=0, error=""):
    d = Download(f"https://a.test/{name}", "C:/tmp")
    d.filename, d.status, d.size, d.error = name, status, size, error
    d.kind, d._media_got = "media", got      # lets us set "downloaded"
    return d


class ProgressTests(unittest.TestCase):
    def test_combined_fraction_weighted_by_size(self):
        self.assertAlmostEqual(
            overall_progress([dl("a", size=100, got=50),
                              dl("b", size=300, got=0)]), 50 / 400)

    def test_unknown_sizes_mean_indeterminate(self):
        self.assertIsNone(overall_progress([dl("a", size=None, got=10)]))

    def test_never_above_100_percent(self):
        self.assertEqual(overall_progress([dl("a", size=10, got=20)]), 1.0)


class NotificationTests(unittest.TestCase):
    def test_only_transitions_from_downloading_count(self):
        a, b, c = dl("a"), dl("b"), dl("c", status=COMPLETED)
        last = {a.id: DOWNLOADING, b.id: DOWNLOADING, c.id: COMPLETED}
        a.status, b.status = COMPLETED, ERROR
        done, failed = finished_since(last, [a, b, c])
        self.assertEqual((done, failed), ([a], [b]))
        self.assertEqual(finished_since(last, [a, b, c]), ([], []))

    def test_fast_failure_straight_from_queued_counts(self):
        a = dl("a", status="queued")
        last = {a.id: "queued"}
        a.status = ERROR
        self.assertEqual(finished_since(last, [a]), ([], [a]))

    def test_paused_downloads_do_not_notify(self):
        a = dl("a", status="paused")
        last = {a.id: "paused"}
        a.status = COMPLETED     # impossible in practice, but must not fire
        self.assertEqual(finished_since(last, [a]), ([], []))

    def test_loaded_or_new_downloads_do_not_notify(self):
        a = dl("a", status=COMPLETED)
        self.assertEqual(finished_since({}, [a]), ([], []))

    def test_single_completion(self):
        a = dl("movie.mp4", status=COMPLETED)
        title, text, payload = notification_for([a], [])
        self.assertEqual(title, "Download complete")
        self.assertIn("movie.mp4", text)
        self.assertEqual(payload, ("reveal", a.id))

    def test_failure_wins_and_says_what_to_do(self):
        ok = dl("ok.zip", status=COMPLETED)
        bad = dl("bad.zip", status=ERROR, error="HTTP 403 Forbidden")
        title, text, payload = notification_for([ok], [bad])
        self.assertEqual(title, "Download failed")
        self.assertIn("The site refused the download", text)
        self.assertIn("1 other download(s) finished", text)
        self.assertEqual(payload, ("fix", bad.id))

    def test_many_completions_are_one_notification(self):
        done = [dl(f"f{i}.bin", status=COMPLETED) for i in range(5)]
        title, text, _ = notification_for(done, [])
        self.assertEqual(title, "5 downloads complete")
        self.assertIn("and 2 more", text)


if __name__ == "__main__":
    unittest.main()
