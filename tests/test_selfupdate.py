"""MDI's own updates: version check, verified download, silent install.
GitHub is faked; nothing is installed or run."""

import hashlib
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from mdi import selfupdate, texts

REPO = selfupdate.REPO


def release(version, data, digest=None, sums=None, url=None):
    name = f"MDI-Setup-{version}.exe"
    prefix = f"https://github.com/{REPO}/releases/download/v{version}/"
    good = hashlib.sha256(data).hexdigest()
    rel = {"tag_name": f"v{version}", "assets": [
        {"name": name, "digest": digest or "sha256:" + good,
         "browser_download_url": url or prefix + name},
        {"name": "SHA256SUMS.txt", "digest": "sha256:" + "0" * 64,
         "browser_download_url": prefix + "SHA256SUMS.txt"}]}
    sums = f"{good}  {name}\n" if sums is None else sums

    def get(u, limit, progress=None, accept=None):
        if u.startswith("https://api.github.com/"):
            return json.dumps(rel).encode()
        if u.endswith("SHA256SUMS.txt"):
            return sums.encode()
        if progress:
            progress(len(data), len(data))
        return data
    return mock.patch("mdi.addons._get", side_effect=get)


class Case(unittest.TestCase):
    def setUp(self):
        self.app = tempfile.mkdtemp()
        self.local = tempfile.mkdtemp()
        self.patches = [
            mock.patch.object(selfupdate, "app_dir", lambda: self.app),
            mock.patch.dict(os.environ, {"LOCALAPPDATA": self.local}),
            mock.patch.object(selfupdate, "__version__", "1.3.0"),
            mock.patch.object(selfupdate, "latest_known", None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()


class CheckTests(Case):
    def test_newer_release_is_offered(self):
        with release("1.4.0", b"x"):
            self.assertEqual(selfupdate.check(), "1.4.0")
        self.assertEqual(selfupdate.available(), "1.4.0")
        self.assertEqual(selfupdate.load_state()["latest"], "1.4.0")

    def test_same_or_older_is_not(self):
        for v in ("1.3.0", "1.2.9"):
            with release(v, b"x"):
                self.assertIsNone(selfupdate.check(force=True))

    def test_known_update_survives_a_restart(self):
        with release("1.10.0", b"x"):
            selfupdate.check()
        with mock.patch.object(selfupdate, "latest_known", None):
            self.assertEqual(selfupdate.available(), "1.10.0")   # not "1.1"

    def test_checks_weekly_and_never_raises(self):
        with release("1.4.0", b"x") as get:
            selfupdate.check()
            selfupdate.check()                    # not due again
            self.assertEqual(get.call_count, 1)
        with mock.patch("mdi.addons._get", side_effect=OSError("offline")):
            # still offers the update it already knows about
            self.assertEqual(selfupdate.check(force=True), "1.4.0")
        self.assertIn("offline", selfupdate.load_state()["error"])

    def test_odd_release_names_are_ignored(self):
        with mock.patch("mdi.addons._get",
                        return_value=b'{"tag_name": "v9.9.9-evil"}'):
            self.assertIsNone(selfupdate.check(force=True))

    def test_only_the_installed_app_updates_itself(self):
        self.assertFalse(selfupdate.enabled())        # tests aren't frozen
        with mock.patch.object(selfupdate.sys, "frozen", True, create=True):
            self.assertEqual(selfupdate.enabled(),
                             selfupdate.sys.platform == "win32")


class DownloadTests(Case):
    def test_verified_installer_is_saved(self):
        data = b"MZ installer 1.4.0"
        seen = []
        with release("1.4.0", data):
            path = selfupdate.download("1.4.0", seen.append)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertEqual(os.path.basename(path), "MDI-Setup-1.4.0.exe")
        self.assertEqual(seen[-1], 100)

    def test_rejections_save_nothing(self):
        data = b"MZ installer"
        cases = [
            dict(digest="sha256:" + "1" * 64),                  # bad file
            dict(sums=f"{'2' * 64}  MDI-Setup-1.4.0.exe\n"),    # sums disagree
            dict(sums=""),                                      # no sum listed
            dict(url="https://evil.test/MDI-Setup-1.4.0.exe"),  # wrong place
            dict(digest="md5:abc")]                             # no digest
        for kw in cases:
            with release("1.4.0", data, **kw), \
                    self.assertRaises(ValueError, msg=kw):
                selfupdate.download("1.4.0")
        self.assertFalse(os.path.exists(os.path.join(
            selfupdate.updates_dir(), "MDI-Setup-1.4.0.exe")))

    def test_bad_version_text(self):
        with self.assertRaises(ValueError):
            selfupdate.download("1.4.0/../../x")

    def test_cleanup_removes_old_installers(self):
        os.makedirs(selfupdate.updates_dir())
        open(os.path.join(selfupdate.updates_dir(), "MDI-Setup-1.3.0.exe"),
             "w").close()
        selfupdate.cleanup()
        self.assertEqual(os.listdir(selfupdate.updates_dir()), [])

    def test_installer_runs_silently_and_reopens_mdi(self):
        with mock.patch.object(selfupdate.subprocess, "Popen") as popen:
            selfupdate.launch(r"C:\x\MDI-Setup-1.4.0.exe")
        args = popen.call_args[0][0]
        for flag in ("/SILENT", "/CLOSEAPPLICATIONS", "/RELAUNCH=1"):
            self.assertIn(flag, args)
        with open(os.path.join(os.path.dirname(__file__), "..", "tools",
                               "installer.iss"), encoding="utf-8") as f:
            iss = f.read()
        self.assertIn("Check: ShouldRelaunch", iss)
        self.assertIn("{param:RELAUNCH|0}", iss)

    def test_question_text(self):
        q = texts.update_question("1.4.0")
        self.assertIn("1.4.0", q)
        self.assertIn("SHA-256", q)
        self.assertIn("continue afterwards", q)


if __name__ == "__main__":
    unittest.main()
