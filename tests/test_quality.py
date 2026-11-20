"""Video quality choice: yt-dlp format strings and remembering the choice."""

import json
import os
import tempfile
import unittest
from unittest import mock

from mdi import media
from mdi.engine import Download
from mdi.manager import Manager


class FormatTests(unittest.TestCase):
    def test_height_caps(self):
        for q in ("1080", "720", "480"):
            for ffmpeg in (True, False):
                fmt = media.format_options(q, ffmpeg)["format"]
                self.assertIn(f"[height<={q}]", fmt)
                self.assertTrue(fmt.endswith("/b"), fmt)   # always a fallback

    def test_best_has_no_cap(self):
        self.assertNotIn("height", media.format_options("best", True)["format"])
        self.assertEqual(media.format_options("best", True)
                         ["merge_output_format"], "mp4")

    def test_audio(self):
        mp3 = media.format_options("audio", True)
        self.assertEqual(mp3["postprocessors"][0]["preferredcodec"], "mp3")
        plain = media.format_options("audio", False)
        self.assertNotIn("postprocessors", plain)
        self.assertTrue(plain["format"].startswith("ba[ext=m4a]"))

    def test_video_site_check_waits_for_patterns(self):
        with mock.patch.object(media, "_extractors", None), \
                mock.patch.object(media, "warm_up") as warm:
            self.assertIsNone(media.is_video_site("https://youtu.be/x"))
            warm.assert_called_once()
        yt = mock.Mock(suitable=lambda u: "youtu" in u)
        with mock.patch.object(media, "_extractors", [yt]):
            self.assertTrue(media.is_video_site("https://youtu.be/x"))
            self.assertFalse(media.is_video_site("https://a.test/f.zip"))


class RememberTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = os.path.join(self.dir, "s.json")

    def test_default_explicit_and_saved(self):
        m = Manager(self.store, os.path.join(self.dir, "inbox"))
        try:
            m.settings["video_quality"] = "480"
            a = m.add("https://a.test/1", start=False)
            b = m.add("https://a.test/2", start=False, quality="audio")
            c = m.add("https://a.test/3", start=False, quality="8k")
            self.assertEqual((a.quality, b.quality, c.quality),
                             ("480", "audio", "best"))
        finally:
            m.shutdown()
        m2 = Manager(self.store, os.path.join(self.dir, "inbox"))
        m2.shutdown()
        self.assertEqual(m2.settings["video_quality"], "480")
        self.assertEqual([d.quality for d in m2.downloads],
                         ["480", "audio", "best"])

    def test_tampered_setting_is_ignored(self):
        with open(self.store, "w") as f:
            json.dump({"settings": {"video_quality": "../x"}}, f)
        m = Manager(self.store, os.path.join(self.dir, "inbox"))
        m.shutdown()
        self.assertEqual(m.settings["video_quality"], "best")

    def test_old_saved_downloads_default_to_best(self):
        d = Download.from_dict({"id": "x", "url": "https://a.test/f",
                                "save_dir": self.dir})
        self.assertEqual(d.quality, "best")


if __name__ == "__main__":
    unittest.main()
