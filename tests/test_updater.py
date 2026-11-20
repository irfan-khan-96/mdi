"""yt-dlp self-update: checksum, version checks, activation and fallback."""

import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

from mdi import media, updater

TAG = "2099.01.02"


def make_zip(version=TAG, init="", corrupt=False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("__main__.py", "")
        z.writestr("yt_dlp/__init__.py", init)
        z.writestr("yt_dlp/version.py", f"__version__ = '{version}'\n")
    data = buf.getvalue()
    return data[:40] + b"garbage" + data[47:] if corrupt else data


def sums_for(data: bytes, name="yt-dlp") -> str:
    return (f"{'0' * 64}  yt-dlp.exe\n"
            f"{hashlib.sha256(data).hexdigest()}  {name}\n")


class UpdaterCase(unittest.TestCase):
    def setUp(self):
        self.app = tempfile.mkdtemp()
        self.p = mock.patch.object(updater, "app_dir", lambda: self.app)
        self.p.start()
        updater.active_version = None

    def tearDown(self):
        self.p.stop()
        updater.active_version = None
        while updater.zip_path() in sys.path:
            sys.path.remove(updater.zip_path())

    def serve(self, data, sums=None, tag=TAG):
        def get(url, limit, accept=None):
            if url.endswith("SHA2-256SUMS"):
                return (sums if sums is not None else sums_for(data)).encode()
            return data
        return mock.patch.multiple(updater, latest_tag=lambda: tag,
                                   _get=mock.Mock(side_effect=get))


class UpdateTests(UpdaterCase):
    def test_checksum_line_parsing(self):
        data = make_zip()
        self.assertEqual(updater.expected_hash(sums_for(data, "*yt-dlp")),
                         hashlib.sha256(data).hexdigest())
        with self.assertRaises(ValueError):
            updater.expected_hash("abc  yt-dlp\n")

    def test_downloads_verifies_and_records_a_newer_version(self):
        data = make_zip()
        with self.serve(data):
            self.assertEqual(updater.update("2026.01.01"), TAG)
        st = updater.load_state()
        self.assertEqual((st["version"], st["error"]), (TAG, None))
        with open(updater.zip_path(), "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertEqual([n for n in os.listdir(updater.folder())
                          if n.endswith(".download")], [])

    def test_checksum_mismatch_is_rejected(self):
        with self.serve(make_zip(), sums=sums_for(b"something else")), \
                self.assertRaises(ValueError):
            updater.update("2026.01.01")
        self.assertFalse(os.path.exists(updater.zip_path()))
        self.assertIn("checksum", updater.load_state()["error"])

    def test_wrong_version_or_corrupt_zip_is_rejected(self):
        for data in (make_zip(version="2000.01.01"), make_zip(corrupt=True)):
            with self.serve(data), self.assertRaises(Exception):
                updater.update("2026.01.01", force=True)
            self.assertFalse(os.path.exists(updater.zip_path()))

    def test_up_to_date_downloads_nothing(self):
        with self.serve(make_zip()) as m:
            self.assertIsNone(updater.update(TAG))
            updater._get.assert_not_called()
        self.assertTrue(updater.load_state()["checked"])

    def test_checks_at_most_weekly_and_retries_errors_next_day(self):
        updater._save_state({"checked": time.time() - 3 * 86400})
        with self.serve(make_zip()):
            self.assertIsNone(updater.update("2026.01.01"))   # not due
        updater._save_state({"checked": time.time() - 2 * 86400,
                             "error": "offline"})
        with self.serve(make_zip()):
            self.assertEqual(updater.update("2026.01.01"), TAG)

    def test_rejects_odd_release_names(self):
        with mock.patch.object(updater, "_get",
                               return_value=b'{"tag_name": "../evil"}'):
            with self.assertRaises(ValueError):
                updater.latest_tag()


class ActivateTests(UpdaterCase):
    def install(self, data=None, **state):
        data = data or make_zip()
        os.makedirs(updater.folder(), exist_ok=True)
        with open(updater.zip_path(), "wb") as f:
            f.write(data)
        updater._save_state({"version": TAG,
                             "sha256": hashlib.sha256(data).hexdigest(),
                             **state})

    def test_newer_intact_copy_goes_first(self):
        self.install()
        with mock.patch.object(updater, "installed_version",
                               return_value="2026.01.01"):
            self.assertEqual(updater.activate(), TAG)
        self.assertEqual(sys.path[0], updater.zip_path())

    def test_skips_older_damaged_or_bad_copies(self):
        with mock.patch.object(updater, "installed_version",
                               return_value="2026.01.01"):
            self.install()
            with mock.patch.object(updater, "installed_version",
                                   return_value="2100.01.01"):
                self.assertIsNone(updater.activate())          # older
            self.install(bad=[TAG])
            self.assertIsNone(updater.activate())              # known bad
            self.install()
            with open(updater.zip_path(), "ab") as f:
                f.write(b"tampered")
            self.assertIsNone(updater.activate())              # damaged
        self.assertNotIn(updater.zip_path(), sys.path)

    def test_broken_copy_falls_back_to_built_in(self):
        self.install(make_zip(init="raise RuntimeError('broken build')"))
        saved = {k: v for k, v in sys.modules.items()
                 if k == "yt_dlp" or k.startswith(("yt_dlp.", "yt_dlp_ejs"))}
        try:
            for k in saved:
                del sys.modules[k]
            with mock.patch.object(updater, "installed_version",
                                   return_value="2026.01.01"):
                mod = media._import_yt_dlp()
            self.assertIsNotNone(mod)                          # built-in one
            self.assertNotIn(updater.zip_path(), sys.path)
            self.assertIn(TAG, updater.load_state()["bad"])
        finally:
            for k in [k for k in sys.modules if k == "yt_dlp" or
                      k.startswith(("yt_dlp.", "yt_dlp_ejs"))]:
                del sys.modules[k]
            sys.modules.update(saved)


if __name__ == "__main__":
    unittest.main()
