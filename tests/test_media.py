"""Video-site hand-off, tested with a stand-in for yt-dlp."""

import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

from mdi import media
from mdi.engine import COMPLETED, ERROR, Download
from tests import test_engine


class FakeYDL:
    """Mimics the slice of yt_dlp.YoutubeDL that media.py uses."""
    fail = None
    seen_opts = None

    def __init__(self, opts):
        self.opts = opts
        FakeYDL.seen_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        if FakeYDL.fail:
            raise Exception(f"ERROR: {FakeYDL.fail}")
        return {"id": "abc", "title": "My Video", "ext": "mp4",
                "filesize": 3000, "webpage_url": url}

    def prepare_filename(self, info):
        return self.opts["outtmpl"].replace("%(title).150B", info["title"]) \
            .replace("%(id)s", info["id"]).replace("%(ext)s", info["ext"])

    def process_ie_result(self, info, download=True):
        path = self.prepare_filename(info)
        hook = self.opts["progress_hooks"][0]
        for got in (1000, 2000, 3000):
            hook({"status": "downloading", "downloaded_bytes": got,
                  "total_bytes": 3000, "speed": 1e6})
        with open(path, "wb") as f:
            f.write(b"x" * 3000)
        hook({"status": "finished", "total_bytes": 3000})
        return {**info, "requested_downloads": [{"filepath": path}]}


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_engine.EngineTests.setUpClass()
        cls.base = test_engine.EngineTests.base

    @classmethod
    def tearDownClass(cls):
        test_engine.EngineTests.tearDownClass()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        FakeYDL.fail = None
        fake = types.SimpleNamespace(YoutubeDL=FakeYDL)
        p = mock.patch.object(media, "yt_dlp", fake)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_dl(self, url):
        d = Download(url, self.dir, 4)
        d.start()
        d.join(30)
        return d

    def test_web_page_goes_to_yt_dlp(self):
        d = self.run_dl(self.base + "/page?v=abc&list=PL1")
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertEqual(d.kind, "media")
        self.assertEqual(d.filename, "My Video [abc].mp4")
        self.assertEqual(d.downloaded, 3000)
        self.assertTrue(os.path.exists(d.path))
        self.assertTrue(FakeYDL.seen_opts["noplaylist"])
        restored = Download.from_dict(d.to_dict())
        self.assertEqual(restored.kind, "media")

    def test_yt_dlp_error_is_shown_cleanly(self):
        FakeYDL.fail = "Video unavailable"
        d = self.run_dl(self.base + "/page?v=gone")
        self.assertEqual(d.status, ERROR)
        self.assertEqual(d.error, "Video unavailable")

    def test_plain_files_still_use_own_engine(self):
        d = self.run_dl(self.base + "/file.bin")
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertEqual(d.kind, "file")

    def test_without_yt_dlp_explains_how_to_enable(self):
        with mock.patch.object(media, "yt_dlp", None):
            d = self.run_dl(self.base + "/page?v=abc")
        self.assertEqual(d.status, ERROR)
        self.assertIn("pip install yt-dlp", d.error)


if __name__ == "__main__":
    unittest.main()
