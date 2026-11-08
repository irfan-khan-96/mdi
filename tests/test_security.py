"""Security regression tests (one per audit finding)."""

import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock

from mdi import engine, nmhost, safety
from mdi.engine import COMPLETED, ERROR, Download, FatalError, sanitize_filename
from mdi.manager import Manager
from tests import test_engine

Handler = test_engine.Handler


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_engine.EngineTests.setUpClass()
        cls.base = test_engine.EngineTests.base
        cls.port = test_engine.EngineTests.server.server_port

    @classmethod
    def tearDownClass(cls):
        test_engine.EngineTests.tearDownClass()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        Handler.ranges, Handler.delay, Handler.slow_offset = True, 0.0, None
        Handler.etag, Handler.fail_first = '"v1"', 0
        Handler.lie_total, Handler.seen = False, []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_dl(self, url, **kw):
        d = Download(url, self.dir, 4, **kw)
        d.start()
        d.join(60)
        self.assertFalse(d.is_running)
        return d


# A01 / A02: credentials must not follow redirects to other hosts --------

class CookieScopeTests(ServerCase):
    def test_cookie_not_sent_to_other_host_after_redirect(self):
        d = self.run_dl(self.base + "/xredir", headers={"Cookie": "sid=SECRET"})
        self.assertEqual(d.status, COMPLETED, d.error)
        first = [c for h, p, c in Handler.seen if p == "/xredir"]
        other = [c for h, p, c in Handler.seen if h == "localhost"]
        self.assertEqual(first, ["sid=SECRET"])
        self.assertTrue(other, "redirect target was never requested")
        self.assertEqual(set(other), {None}, "cookie leaked to other host")

    def test_cookie_kept_on_same_host_redirect(self):
        d = self.run_dl(self.base + "/redirect", headers={"Cookie": "sid=1"})
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertTrue(all(c == "sid=1" for h, p, c in Handler.seen))


# A10: redirects only to http(s), never HTTPS -> HTTP ---------------------

class RedirectTests(ServerCase):
    def test_redirect_to_ftp_refused(self):
        d = self.run_dl(self.base + "/ftpredir")
        self.assertEqual(d.status, ERROR)
        self.assertIn("ftp", d.error)

    def test_https_to_http_downgrade_refused(self):
        h = engine._SafeRedirect()
        req = urllib.request.Request("https://a.test/x",
                                     headers={"Cookie": "s=1"})
        with self.assertRaises(FatalError):
            h.redirect_request(req, None, 302, "Found", {}, "http://a.test/y")

    def test_cross_host_redirect_strips_cookie_header(self):
        h = engine._SafeRedirect()
        req = urllib.request.Request("https://a.test/x",
                                     headers={"Cookie": "s=1"})
        new = h.redirect_request(req, None, 302, "Found", {},
                                 "https://cdn.other.test/y")
        self.assertNotIn("Cookie", new.headers)


# A04 / A08: integrity of what lands on disk ------------------------------

class IntegrityTests(ServerCase):
    def test_size_change_mid_download_is_detected(self):
        Handler.lie_total = True
        d = self.run_dl(self.base + "/file.bin")
        self.assertEqual(d.status, ERROR)
        self.assertIn("changed on the server", d.error)

    def test_not_enough_disk_space(self):
        fake = shutil._ntuple_diskusage(10, 10, 1024)
        with mock.patch.object(engine.shutil, "disk_usage", return_value=fake):
            d = self.run_dl(self.base + "/file.bin")
        self.assertEqual(d.status, ERROR)
        self.assertIn("disk space", d.error)

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_mark_of_the_web_written(self):
        d = self.run_dl(self.base + "/file.bin?token=abc",
                        headers={"Referer": "https://site.test/page?q=1"})
        self.assertEqual(d.status, COMPLETED, d.error)
        with open(d.path + ":Zone.Identifier", encoding="utf-8") as f:
            zone = f.read()
        self.assertIn("ZoneId=3", zone)
        self.assertIn("HostUrl=http://127.0.0.1", zone)
        self.assertNotIn("token=abc", zone)


# A03: input validation / injection ---------------------------------------

class ValidationTests(unittest.TestCase):
    def test_only_http_urls(self):
        for bad in ("file:///C:/Windows/win.ini", "javascript:alert(1)",
                    "ftp://x.test/a", "http://", "https://a.test/a b",
                    "https://a.test/\r\nX: y", "x" * 9000, None, 42):
            with self.assertRaises(ValueError, msg=repr(bad)):
                safety.check_url(bad)
        self.assertEqual(safety.check_url(" https://a.test/f.zip "),
                         "https://a.test/f.zip")

    def test_headers_whitelisted_and_single_line(self):
        h = safety.clean_headers({"cookie": "a=1\r\nX-Evil: 1",
                                  "Referer": "javascript:alert(1)",
                                  "Host": "evil.test", "Authorization": "x"})
        self.assertEqual(h, {"Cookie": "a=1X-Evil: 1"})

    def test_filename_spoofing_and_traversal(self):
        self.assertEqual(sanitize_filename("invoice\u202efdp.exe"),
                         "invoicefdp.exe")
        self.assertEqual(sanitize_filename("..\\..\\Windows\\evil.dll"),
                         "_.._Windows_evil.dll")
        self.assertEqual(sanitize_filename(".."), "download")
        self.assertEqual(sanitize_filename("CON.txt"), "_CON.txt")
        long = sanitize_filename("a" * 300 + ".pdf")
        self.assertTrue(long.endswith(".pdf"))
        self.assertLessEqual(len(long), 200)

    def test_manager_rejects_bad_urls(self):
        d = tempfile.mkdtemp()
        m = Manager(os.path.join(d, "s.json"))
        try:
            with self.assertRaises(ValueError):
                m.add("file:///C:/secret.txt")
            self.assertEqual(m.downloads, [])
        finally:
            m.shutdown()
            shutil.rmtree(d, ignore_errors=True)

    def test_log_redacts_secrets(self):
        self.assertEqual(
            safety.redact("https://user:pw@a.test:8443/f.zip?sig=SECRET#x"),
            "https://a.test:8443/f.zip?…")


# A02: secrets at rest -----------------------------------------------------

class StorageTests(ServerCase):
    def test_cookies_encrypted_on_disk_and_dropped_when_done(self):
        store = os.path.join(self.dir, "s.json")
        m = Manager(store)
        m.settings["download_dir"] = self.dir
        d = m.add(self.base + "/file.bin", headers={"Cookie": "sid=TOPSECRET"},
                  start=False)
        m.save()
        raw = open(store, encoding="utf-8").read()
        if sys.platform == "win32":
            self.assertNotIn("TOPSECRET", raw)
        m.shutdown()

        m2 = Manager(store)                       # decrypts on load
        self.assertEqual(m2.downloads[0].headers, {"Cookie": "sid=TOPSECRET"})
        m2.downloads[0].status = COMPLETED
        m2.save()
        m2.shutdown()
        self.assertNotIn("headers\": {", open(store, encoding="utf-8").read())
        self.assertEqual(Manager(store).downloads[0].headers, {})
        self.assertEqual(d.headers, {"Cookie": "sid=TOPSECRET"})

    def test_tampered_store_is_sanitised(self):
        store = os.path.join(self.dir, "s.json")
        with open(store, "w", encoding="utf-8") as f:
            json.dump({"settings": {"connections": 10**9, "max_parallel": "x"},
                       "downloads": [{"id": "a", "url": "file:///etc/passwd",
                                      "save_dir": self.dir},
                                     {"bogus": True}]}, f)
        m = Manager(store)
        try:
            self.assertEqual(m.downloads, [])
            self.assertEqual(m.settings["connections"], 32)
            self.assertEqual(m.settings["max_parallel"], 3)
        finally:
            m.shutdown()


# A01 / A07: browser extension channel -------------------------------------

class NativeHostTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        p = mock.patch.object(nmhost, "inbox_dir", return_value=self.dir)
        p.start()
        self.addCleanup(p.stop)
        g = mock.patch.object(nmhost, "gui_running", return_value=True)
        g.start()
        self.addCleanup(g.stop)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def frame(self, obj):
        raw = json.dumps(obj).encode()
        return io.BytesIO(struct.pack("<I", len(raw)) + raw)

    def test_valid_request_reaches_inbox_encrypted(self):
        res = nmhost.handle({"type": "add", "url": "https://a.test/f.zip",
                             "cookies": "sid=SECRET",
                             "referer": "https://a.test/"})
        self.assertEqual(res, {"ok": True})
        files = os.listdir(self.dir)
        self.assertEqual(len(files), 1)
        raw = open(os.path.join(self.dir, files[0]), encoding="utf-8").read()
        if sys.platform == "win32":
            self.assertNotIn("SECRET", raw)

        m = Manager(os.path.join(self.dir, "s.json"), inbox=self.dir)
        try:
            self.assertEqual(m.process_inbox(), 1)
            self.assertEqual(m.downloads[0].headers["Cookie"], "sid=SECRET")
            self.assertEqual([n for n in os.listdir(self.dir)
                              if n.endswith(".json") and n != "s.json"], [])
        finally:
            m.shutdown()

    def test_bad_requests_rejected(self):
        for msg in ({"type": "add", "url": "file:///C:/x"},
                    {"type": "add"}, {"type": "exec", "cmd": "calc"}):
            self.assertFalse(nmhost.handle(msg)["ok"], msg)
        self.assertEqual(os.listdir(self.dir), [])

    def test_oversized_and_truncated_messages(self):
        with self.assertRaises(ValueError):
            nmhost.read_message(io.BytesIO(struct.pack("<I", 1 << 30)))
        with self.assertRaises(ValueError):
            nmhost.read_message(io.BytesIO(struct.pack("<I", 100) + b"{}"))
        self.assertEqual(nmhost.read_message(self.frame({"type": "ping"})),
                         {"type": "ping"})

    def test_unexpected_origin_refused(self):
        with mock.patch.object(sys, "argv", ["nmhost", "chrome-extension://evil/"]):
            self.assertEqual(nmhost.main(), 1)

    def test_planted_inbox_file_is_validated(self):
        with open(os.path.join(self.dir, "x.json"), "w") as f:
            json.dump({"url": "file:///C:/Windows/System32/cmd.exe"}, f)
        m = Manager(os.path.join(self.dir, "s.json"), inbox=self.dir)
        try:
            self.assertEqual(m.process_inbox(), 0)
            self.assertEqual(m.downloads, [])
            self.assertFalse(os.path.exists(os.path.join(self.dir, "x.json")))
        finally:
            m.shutdown()


if __name__ == "__main__":
    unittest.main()
