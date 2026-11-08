"""End-to-end tests against a local HTTP server.  python -m unittest -v"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mdi import engine
from mdi.engine import COMPLETED, ERROR, PAUSED, Download
from mdi.manager import Manager

DATA = os.urandom(6 * 1024 * 1024 + 12345)
SHA = hashlib.sha256(DATA).hexdigest()


class Handler(BaseHTTPRequestHandler):
    ranges = True
    delay = 0.0          # seconds per 64 KiB, to make downloads take a while
    slow_offset = None   # bytes at/after this offset are 10x slower
    etag = '"v1"'
    fail_first = 0       # first N range requests get a 503
    lie_total = False    # non-probe range replies claim another file size
    seen = []            # (host, path, cookie) of every request
    max_inflight = None  # answer 429 when more range requests than this
    drop_probes = 0      # hang up on the first N probe requests
    inflight = 0
    got_429 = 0
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def do_GET(self):
        cls = type(self)
        cls.seen.append((self.headers.get("Host", "").split(":")[0],
                         self.path, self.headers.get("Cookie")))
        port = self.server.server_port
        target = {"/xredir": f"http://localhost:{port}/file.bin",
                  "/ftpredir": "ftp://127.0.0.1/file.bin"}.get(self.path)
        if target:
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        if self.path.startswith("/missing"):
            self.send_error(404)
            return
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("Location", "/file.bin")
            self.end_headers()
            return
        start, end = 0, len(DATA) - 1
        rng = self.headers.get("Range")
        if rng == "bytes=0-" and cls.drop_probes > 0:
            cls.drop_probes -= 1
            self.close_connection = True      # no response at all
            return
        if rng and cls.ranges:
            if cls.fail_first > 0 and rng != "bytes=0-":
                cls.fail_first -= 1
                self.send_error(503)
                return
            if cls.max_inflight is not None and rng != "bytes=0-":
                with cls.lock:
                    busy = cls.inflight >= cls.max_inflight
                    if busy:
                        cls.got_429 += 1
                    else:
                        cls.inflight += 1
                if busy:
                    self.send_response(429)
                    self.send_header("Retry-After", "0")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                try:
                    return self._range_reply(rng, cls)
                finally:
                    with cls.lock:
                        cls.inflight -= 1
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else end
            self.send_response(206)
            total = len(DATA)
            if cls.lie_total and rng != "bytes=0-":
                total += 1
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("ETag", cls.etag)
        if self.path.startswith("/page"):
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif self.path.startswith("/video"):
            self.send_header("Content-Type", "video/mp4")
        if self.path.startswith("/named"):
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf")
        self.end_headers()
        pos = start
        try:
            while pos <= end:
                n = min(64 * 1024, end - pos + 1)
                self.wfile.write(DATA[pos:pos + n])
                pos += n
                d = cls.delay
                if cls.slow_offset is not None and pos >= cls.slow_offset:
                    d *= 10
                if d:
                    time.sleep(d)
        except (ConnectionError, OSError):
            pass


def _range_reply(self, rng, cls):
    m = re.match(r"bytes=(\d+)-(\d*)", rng)
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else len(DATA) - 1
    self.send_response(206)
    self.send_header("Content-Range", f"bytes {start}-{end}/{len(DATA)}")
    self.send_header("Content-Length", str(end - start + 1))
    self.send_header("ETag", cls.etag)
    self.end_headers()
    try:
        for pos in range(start, end + 1, 64 * 1024):
            self.wfile.write(DATA[pos:min(end + 1, pos + 64 * 1024)])
            if cls.delay:
                time.sleep(cls.delay)
    except OSError:
        pass


Handler._range_reply = _range_reply


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        engine.MIN_SPLIT = 256 * 1024
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        Handler.ranges, Handler.delay, Handler.slow_offset = True, 0.0, None
        Handler.etag, Handler.fail_first = '"v1"', 0
        Handler.lie_total, Handler.seen = False, []
        Handler.max_inflight, Handler.inflight, Handler.got_429 = None, 0, 0
        Handler.drop_probes = 0

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_dl(self, d, timeout=60):
        d.start()
        d.join(timeout)
        self.assertFalse(d.is_running, "download hung")
        return d

    def test_multi_connection(self):
        d = self.run_dl(Download(self.base + "/file.bin", self.dir, 8))
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertTrue(d.resumable)
        self.assertEqual(sha(d.path), SHA)
        self.assertFalse(os.path.exists(d.path + ".part"))

    def test_dynamic_segmentation_steals_slow_tail(self):
        Handler.delay = 0.004
        Handler.slow_offset = len(DATA) * 3 // 4   # last segment crawls
        d = self.run_dl(Download(self.base + "/file.bin", self.dir, 4))
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertEqual(sha(d.path), SHA)
        self.assertGreater(len(d.segments), 4, "no segment was split")

    def test_pause_and_resume(self):
        Handler.delay = 0.06
        d = Download(self.base + "/file.bin", self.dir, 4)
        d.start()
        time.sleep(0.5)
        d.pause()
        d.join(30)
        self.assertEqual(d.status, PAUSED)
        got = d.downloaded
        self.assertGreater(got, 0)
        self.assertLess(got, len(DATA))

        # Simulate an app restart: round-trip through the saved state.
        d2 = Download.from_dict(json.loads(json.dumps(d.to_dict())))
        self.assertEqual(d2.downloaded, got)
        Handler.delay = 0.0
        self.run_dl(d2)
        self.assertEqual(d2.status, COMPLETED, d2.error)
        self.assertEqual(sha(d2.path), SHA)

    def test_resume_restarts_if_file_changed(self):
        Handler.delay = 0.06
        d = Download(self.base + "/file.bin", self.dir, 4)
        d.start()
        time.sleep(0.5)
        d.pause()
        d.join(30)
        Handler.etag, Handler.delay = '"v2"', 0.0
        self.run_dl(d)
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertEqual(d.etag, '"v2"')
        self.assertEqual(sha(d.path), SHA)

    def test_no_range_support_single_connection(self):
        Handler.ranges = False
        d = self.run_dl(Download(self.base + "/file.bin", self.dir, 8))
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertFalse(d.resumable)
        self.assertEqual(len(d.segments), 1)
        self.assertEqual(sha(d.path), SHA)

    def test_retries_transient_errors(self):
        Handler.fail_first = 3
        engine_retry = engine.MAX_RETRIES
        d = self.run_dl(Download(self.base + "/file.bin", self.dir, 4))
        self.assertEqual(engine_retry, engine.MAX_RETRIES)
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertEqual(sha(d.path), SHA)

    def test_rate_limited_server_gets_fewer_connections(self):
        Handler.max_inflight, Handler.delay = 2, 0.002
        t = time.time()
        d = self.run_dl(Download(self.base + "/file.bin", self.dir, 8))
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertEqual(sha(d.path), SHA)
        self.assertGreater(Handler.got_429, 0, "server never pushed back")
        self.assertLessEqual(Handler.got_429, 8, "kept hammering the server")
        self.assertLess(time.time() - t, 5, "waited instead of shedding")

    def test_first_request_dropped_is_retried(self):
        Handler.drop_probes = 1
        d = self.run_dl(Download(self.base + "/file.bin", self.dir, 4))
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertEqual(sha(d.path), SHA)

    def test_404_is_error(self):
        d = self.run_dl(Download(self.base + "/missing", self.dir))
        self.assertEqual(d.status, ERROR)
        self.assertIn("404", d.error)

    def test_redirect_and_content_disposition(self):
        d = self.run_dl(Download(self.base + "/redirect", self.dir, 2))
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertEqual(d.filename, "file.bin")
        d = self.run_dl(Download(self.base + "/named", self.dir, 2))
        self.assertEqual(d.filename, "résumé.pdf")

    def test_web_page_is_rejected(self):
        from unittest import mock
        from mdi import media
        with mock.patch.object(media, "yt_dlp", None):  # no video support
            d = self.run_dl(Download(self.base + "/page?v=abc", self.dir))
        self.assertEqual(d.status, ERROR)
        self.assertIn("web page", d.error)
        self.assertEqual(os.listdir(self.dir), [])

    def test_extension_from_content_type(self):
        d = self.run_dl(Download(self.base + "/video", self.dir, 2,
                                 filename="clip"))
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertEqual(d.filename, "clip.mp4")

    def test_name_collision(self):
        a = self.run_dl(Download(self.base + "/file.bin", self.dir, 2))
        b = self.run_dl(Download(self.base + "/file.bin", self.dir, 2))
        self.assertEqual(os.path.basename(b.path), "file (1).bin")
        self.assertNotEqual(a.path, b.path)

    def test_manager_queue_and_persistence(self):
        store = os.path.join(self.dir, "store.json")
        m = Manager(store)
        m.settings.update(download_dir=self.dir, max_parallel=1)
        d1 = m.add(self.base + "/file.bin")
        d2 = m.add(self.base + "/file.bin")
        deadline = time.time() + 60
        while time.time() < deadline and not (
                d1.status == d2.status == COMPLETED):
            time.sleep(0.2)
        m.shutdown()
        self.assertEqual((d1.status, d2.status), (COMPLETED, COMPLETED))
        m2 = Manager(store)
        self.assertEqual([d.id for d in m2.downloads], [d1.id, d2.id])
        m2.shutdown()

if __name__ == "__main__":
    unittest.main()
