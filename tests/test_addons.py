"""Optional helpers (ffmpeg, QuickJS): verified download and safe install.
No network and nothing is executed: GitHub is faked."""

import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from mdi import addons, texts


def ffmpeg_zip(extra=()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in ("ffmpeg-master/bin/ffmpeg.exe",
                     "ffmpeg-master/bin/ffprobe.exe",
                     "ffmpeg-master/bin/avcodec-62.dll",
                     "ffmpeg-master/bin/ffplay.exe",
                     "ffmpeg-master/doc/readme.txt", *extra):
            z.writestr(name, "x")
    return buf.getvalue()


class AddonCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.p = mock.patch.object(addons, "tools_dir", lambda: self.dir)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def github(self, key, data, digest=None, url=None):
        a = addons.ADDONS[key]
        rel = {"assets": [
            {"name": "other.zip", "digest": "sha256:" + "0" * 64,
             "browser_download_url": f"https://github.com/{a.repo}/x"},
            {"name": a.asset,
             "digest": digest or "sha256:" + hashlib.sha256(data).hexdigest(),
             "browser_download_url":
                 url or f"https://github.com/{a.repo}/releases/download/v/{a.asset}"}]}

        def get(u, limit, progress=None, accept=None):
            if u.startswith("https://api.github.com/"):
                return json.dumps(rel).encode()
            if progress:
                progress(len(data), len(data))
            return data
        return mock.patch.object(addons, "_get", side_effect=get)

    def pin_deno(self, data):
        return mock.patch.object(addons.ADDONS["deno"], "sha256",
                                 hashlib.sha256(data).hexdigest())


class InstallTests(AddonCase):
    def test_ffmpeg_installs_only_the_programs_and_dlls(self):
        lines = []
        with self.github("ffmpeg", ffmpeg_zip()):
            addons.install(["ffmpeg"], lines.append)
        self.assertEqual(sorted(os.listdir(self.dir)),
                         ["avcodec-62.dll", "ffmpeg.exe", "ffprobe.exe"])
        self.assertTrue(any("100%" in t for t in lines))

    def test_zip_paths_cannot_escape_the_folder(self):
        evil = ffmpeg_zip(extra=("ffmpeg-master/bin/../../evil.dll",
                                 "../bin/x.dll"))
        with self.github("ffmpeg", evil):
            addons.install(["ffmpeg"])
        parent = os.path.dirname(self.dir)
        self.assertFalse(os.path.exists(os.path.join(parent, "evil.dll")))
        self.assertNotIn("evil.dll", os.listdir(self.dir))

    def test_checksum_mismatch_installs_nothing(self):
        with self.github("ffmpeg", ffmpeg_zip(),
                         digest="sha256:" + "1" * 64), \
                self.assertRaisesRegex(ValueError, "checksum"):
            addons.install(["ffmpeg"])
        self.assertEqual(os.listdir(self.dir), [])

    def test_deno_must_be_the_pinned_version(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("deno.exe", "MZ fake deno")
            z.writestr("README.md", "x")
        data = buf.getvalue()
        with self.github("deno", data), \
                self.assertRaisesRegex(ValueError, "isn't the version"):
            addons.install(["deno"])
        with self.pin_deno(data), self.github("deno", data):
            addons.install(["deno"])
        self.assertEqual(os.listdir(self.dir), ["deno.exe"])

    def test_download_must_come_from_the_project(self):
        with self.github("ffmpeg", ffmpeg_zip(),
                         url="https://evil.test/ffmpeg.zip"), \
                self.assertRaisesRegex(ValueError, "unexpected download"):
            addons.install(["ffmpeg"])

    def test_missing_digest_is_refused(self):
        with self.github("ffmpeg", ffmpeg_zip(), digest="md5:abc"), \
                self.assertRaisesRegex(ValueError, "no checksum"):
            addons.install(["ffmpeg"])

    def test_archive_without_ffmpeg_installs_nothing(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("x/bin/ffprobe.exe", "x")
        with self.github("ffmpeg", buf.getvalue()), \
                self.assertRaisesRegex(ValueError, "ffmpeg.exe is missing"):
            addons.install(["ffmpeg"])
        self.assertEqual(os.listdir(self.dir), [])   # staging cleaned up

    def test_size_limit(self):
        class R(io.BytesIO):
            headers = {"Content-Length": "0"}
            def __enter__(self): return self
            def __exit__(self, *a): pass
        with mock.patch("mdi.engine.OPENER.open", return_value=R(b"x" * 3000)), \
                self.assertRaisesRegex(ValueError, "larger"):
            addons._get("https://github.com/a", 1000)


class DetectTests(AddonCase):
    def test_path_is_extended_once(self):
        with mock.patch.dict(os.environ, {"PATH": r"C:\Windows"}):
            addons.add_to_path()
            addons.add_to_path()
            self.assertEqual(os.environ["PATH"].split(os.pathsep),
                             [self.dir, r"C:\Windows"])

    def test_missing_lists_what_isnt_available(self):
        def which(*have):
            return lambda n: f"C:/x/{n}.exe" if n in have else None
        with mock.patch.object(addons.shutil, "which", which()):
            self.assertEqual(addons.missing(), ["ffmpeg", "deno"])
        with mock.patch.object(addons.shutil, "which", which("ffmpeg", "node")):
            self.assertEqual(addons.missing(), [])
        with mock.patch.object(addons.shutil, "which", which("qjs")):
            self.assertEqual(addons.missing(), ["ffmpeg"])

    def test_quickjs_counts_as_a_runtime_for_yt_dlp(self):
        from mdi import media
        with mock.patch.object(media.shutil, "which",
                               lambda n: "C:/t/qjs.exe" if n == "qjs" else None):
            self.assertEqual(media.js_runtimes(), {"quickjs": {}})


class TextTests(unittest.TestCase):
    def test_link_and_question(self):
        self.assertIn("HD video", texts.addon_link_text(["ffmpeg", "deno"]))
        self.assertIn("YouTube", texts.addon_link_text(["deno"]))
        q = texts.addon_question(["ffmpeg", "deno"])
        self.assertIn("about 86 MB", q)
        self.assertIn("about 41 MB", q)
        self.assertIn("SHA-256", q)


if __name__ == "__main__":
    unittest.main()
