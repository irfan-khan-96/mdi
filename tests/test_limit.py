"""Speed limit: one shared cap for all downloads."""

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from mdi import engine
from mdi.dialogs import limit_text, parse_limit
from mdi.engine import COMPLETED, LIMITER, Download, RateLimiter
from mdi.manager import MB, Manager
from tests import test_engine

H = test_engine.Handler


class LimiterTests(unittest.TestCase):
    def test_rate_is_respected(self):
        lim = RateLimiter()
        lim.set(4 * MB)
        t = time.monotonic()
        for _ in range(32):                     # 8 MB in 256 KB pieces
            lim.take(256 * 1024)
        took = time.monotonic() - t
        self.assertGreater(took, 1.6)           # 2 s minus the 0.25 s burst
        self.assertLess(took, 2.6)

    def test_no_limit_never_waits(self):
        lim = RateLimiter()
        t = time.monotonic()
        for _ in range(1000):
            lim.take(10 * MB)
        self.assertLess(time.monotonic() - t, 0.2)

    def test_pause_interrupts_the_wait(self):
        lim = RateLimiter()
        lim.set(100 * 1024)                     # 100 KB/s
        halt = threading.Event()
        threading.Timer(0.2, halt.set).start()
        t = time.monotonic()
        lim.take(10 * MB, halt)                 # would be ~100 s
        self.assertLess(time.monotonic() - t, 1)

    def test_reads_get_smaller_under_a_limit(self):
        lim = RateLimiter()
        self.assertEqual(lim.read_size(256 * 1024), 256 * 1024)
        lim.set(1 * MB)
        self.assertEqual(lim.read_size(256 * 1024), MB // 10)
        lim.set(10 * 1024)
        self.assertEqual(lim.read_size(256 * 1024), 16 * 1024)


class EngineLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.MIN_SPLIT = 256 * 1024
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        H.ranges, H.delay, H.slow_offset = True, 0.0, None
        H.etag, H.fail_first, H.lie_total = '"v1"', 0, False
        H.max_inflight, H.drop_probes = None, 0

    def tearDown(self):
        LIMITER.set(0)

    def download(self, connections):
        d = Download(self.base + "/file.bin", tempfile.mkdtemp(), connections)
        t = time.monotonic()
        d.start()
        d.join(60)
        return d, time.monotonic() - t

    def test_all_connections_share_one_limit(self):
        size = len(test_engine.DATA) / MB               # about 6 MB
        LIMITER.set(3 * MB)
        d, took = self.download(8)
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertEqual(test_engine.sha(d.path), test_engine.SHA)
        self.assertGreater(took, size / 3 - 0.5)        # ~2 s, not 8x faster
        self.assertLess(took, size / 3 + 1.5)

    def test_unlimited_is_fast(self):
        d, took = self.download(8)
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertLess(took, 1.5)


class SettingTests(unittest.TestCase):
    def test_settings_box(self):
        self.assertEqual(parse_limit("2.5", 0), 2.5)
        self.assertEqual(parse_limit("2,5", 0), 2.5)
        self.assertEqual(parse_limit(" 3 MB/s ", 0), 3.0)
        self.assertEqual(parse_limit("", 4), 0.0)
        self.assertEqual(parse_limit("fast", 4), 4)
        self.assertEqual(parse_limit("-1", 4), 0.0)
        self.assertEqual(limit_text(0), "0")
        self.assertEqual(limit_text(2.5), "2.5")

    def test_saved_limit_is_applied_and_clamped(self):
        store = os.path.join(tempfile.mkdtemp(), "s.json")
        for saved, expected in ((2.5, 2.5), ("junk", 0.0), (99999, 10000.0)):
            with open(store, "w") as f:
                json.dump({"settings": {"speed_limit": saved}}, f)
            m = Manager(store, os.path.join(os.path.dirname(store), "inbox"))
            m.shutdown()
            self.assertEqual(m.settings["speed_limit"], expected)
            self.assertEqual(LIMITER.rate, expected * MB)
        LIMITER.set(0)


if __name__ == "__main__":
    unittest.main()
