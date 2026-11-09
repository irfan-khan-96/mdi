"""Regression tests for the audit findings (numbers match the audit)."""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import ThreadingHTTPServer
from unittest import mock

from mdi import engine, gui, manager, media
from mdi.advice import explain
from mdi.engine import (COMPLETED, DOWNLOADING, ERROR, PAUSED, QUEUED,
                        Download, filename_from_response)
from mdi.manager import Manager
from mdi.safety import mark_of_the_web
from tests import test_engine


class ManagerCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = os.path.join(self.dir, "s.json")
        self.m = None

    def tearDown(self):
        if self.m:
            self.m.shutdown()

    def manager(self):
        self.m = Manager(self.store, os.path.join(self.dir, "inbox"))
        return self.m

    def paused(self, name, kind="file"):
        d = Download(f"https://invalid.test/{name}", self.dir)
        d.filename, d.status, d.kind = name, PAUSED, kind
        d.path = os.path.join(self.dir, name)
        return d


class SaveTests(ManagerCase):
    def test_1_failed_save_does_not_raise(self):
        m = self.manager()
        with mock.patch.object(manager.os, "replace",
                               side_effect=PermissionError(13, "in use")):
            self.assertFalse(m.save())
        self.assertTrue(m.save())

    def test_1_scheduler_survives_errors(self):
        m = self.manager()
        with mock.patch.object(m, "_tick", side_effect=RuntimeError("boom")):
            time.sleep(1.2)
        self.assertTrue(m._thread.is_alive())


class RefreshTests(ManagerCase):
    def test_2_bad_inbox_file_is_rejected_quietly(self):
        m = self.manager()
        os.makedirs(m.inbox, exist_ok=True)
        for i, rec in enumerate(({"url": "https://a.test/f",
                                  "filename": {"a": 1}}, [1, 2], "x")):
            with open(os.path.join(m.inbox, f"{i}.json"), "w") as f:
                json.dump(rec, f)
        self.assertEqual(m.process_inbox(), 0)
        self.assertEqual(os.listdir(m.inbox), [])

    def test_2_refresh_always_reschedules(self):
        app = gui.App.__new__(gui.App)
        app.root = mock.Mock()
        app._refresh_once = mock.Mock(side_effect=RuntimeError("boom"))
        app._refresh()
        app.root.after.assert_called_once_with(500, app._refresh)


class AdviceTests(unittest.TestCase):
    def test_3_busy_server_in_either_format(self):
        for err in ("HTTP Error 503: Service Unavailable",
                    "HTTP 503 Service Unavailable",
                    "HTTP Error 429: Too Many Requests"):
            advice, action = explain(err, "file")
            self.assertIn("busy", advice, err)
            self.assertEqual(action, "retry")

    def test_3_bad_folder_offers_another_folder(self):
        err = ("[WinError 123] The filename, directory name, or volume "
               "label syntax is incorrect: 'Q:\\\\x'")
        self.assertEqual(explain(err, "file")[1], "change_folder")

    def test_3_page_and_message_are_not_age_errors(self):
        self.assertNotIn("signed in", explain("bad message on page", "file")[0])
        self.assertIn("signed in", explain("Sign in to confirm your age",
                                           "media")[0])

    def test_3_engine_reports_http_errors_in_one_format(self):
        d = Download("https://invalid.test/f", tempfile.mkdtemp())
        err = urllib.error.HTTPError(d.url, 503, "Service Unavailable", {}, None)
        with mock.patch.object(Download, "_prepare", side_effect=err):
            d._run()
        self.assertEqual((d.status, d.error),
                         (ERROR, "HTTP 503 Service Unavailable"))


class LoadTests(ManagerCase):
    def test_4_wrong_shapes_do_not_crash(self):
        for content in ("[]", '{"settings": [], "downloads": {"a": 1}}',
                        '{"downloads": [1, "x", {"url": 5}]}'):
            with open(self.store, "w") as f:
                f.write(content)
            m = Manager(self.store, os.path.join(self.dir, "inbox"))
            m.shutdown()
            self.assertEqual(m.downloads, [], content)

    def test_4_unreadable_file_is_kept_not_overwritten(self):
        with open(self.store, "w") as f:
            f.write('{"downloads": [{"url": "https://a.test/f"')   # cut off
        m = self.manager()
        m.save()
        with open(self.store + ".damaged") as f:
            self.assertIn("a.test", f.read())


class CleanupTests(ManagerCase):
    def video(self):
        d = self.paused("Clip [abc].mp4", kind="media")
        d.media_stem = os.path.join(self.dir, "Clip [abc]")
        junk = ["Clip [abc].f399-sr.mp4.part", "Clip [abc].f251.webm",
                "Clip [abc].f251.webm.ytdl", "Clip [abc].mp4.part-Frag3"]
        keep = ["Clip [abc].mp4", "Clip [abc] extra.mp4", "Other.mp4.part"]
        for n in junk + keep:
            open(os.path.join(self.dir, n), "w").close()
        return d, junk, keep

    def test_5_leftovers_never_include_the_finished_file(self):
        d, junk, _ = self.video()
        self.assertEqual(sorted(os.path.basename(p)
                                for p in media.leftovers(d.media_stem)),
                         sorted(junk))
        self.assertEqual(media.leftovers(None), [])

    def test_5_removed_video_leaves_no_junk(self):
        d, junk, keep = self.video()
        m = self.manager()
        m.downloads.append(d)
        m.discard(m.take([d]))
        left = os.listdir(self.dir)
        for n in junk:
            self.assertNotIn(n, left)
        for n in keep:
            self.assertIn(n, left)

    def test_5_stem_survives_a_restart(self):
        d, _, _ = self.video()
        d2 = Download.from_dict(json.loads(json.dumps(d.to_dict())))
        self.assertEqual(d2.media_stem, d.media_stem)

    def test_6_discard_can_delete_finished_files_too(self):
        d = self.paused("done.zip")
        d.status = COMPLETED
        open(d.path, "w").close()
        m = self.manager()
        m.downloads.append(d)
        m.discard(m.take([d]))
        self.assertTrue(os.path.exists(d.path))      # plain Delete keeps it
        m.downloads.append(d)
        m.discard(m.take([d]), delete_finished=True)
        self.assertFalse(os.path.exists(d.path))     # Shift+Delete

    def test_6_restore_does_not_wait_for_connections(self):
        d = self.paused("a")
        d.status = DOWNLOADING
        d._thread = mock.Mock(is_alive=lambda: True)   # still stopping
        m = self.manager()
        m.downloads.append(d)
        with mock.patch.object(Download, "start"):
            taken = m.take([d])
            t = time.time()
            m.restore(taken)
        self.assertLess(time.time() - t, 1)
        d._thread.join.assert_not_called()
        self.assertTrue(d._resume_after_stop)


class ResumeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), test_engine.Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_7_resume_pressed_right_after_pause_is_kept(self):
        H = test_engine.Handler
        H.ranges, H.delay, H.slow_offset = True, 0.05, None
        H.etag, H.fail_first, H.lie_total = '"v1"', 0, False
        H.max_inflight, H.drop_probes = None, 0
        d = Download(self.base + "/file.bin", tempfile.mkdtemp(), 4)
        d.start()
        time.sleep(0.4)
        d.pause()
        d.resume()                       # connections are still stopping
        self.assertTrue(d.is_running)
        d.join(30)
        self.assertEqual(d.status, QUEUED)
        H.delay = 0.0
        d.start()
        d.join(60)
        self.assertEqual(d.status, COMPLETED, d.error)
        self.assertEqual(test_engine.sha(d.path), test_engine.SHA)

    def test_7_pause_cancels_a_pending_resume(self):
        d = Download("https://invalid.test/f", tempfile.mkdtemp())
        d._thread = mock.Mock(is_alive=lambda: True)
        d._halt.set()
        d.resume()
        d.pause()
        self.assertFalse(d._resume_after_stop)


class SmallFixTests(unittest.TestCase):
    def test_8_new_link_on_another_site_drops_cookies(self):
        app = gui.App.__new__(gui.App)
        app.root, app.manager = mock.Mock(), mock.Mock()
        for new, kept in (("https://old.test/other", True),
                          ("https://evil.test/file", False),
                          ("http://old.test/file", False)):
            d = Download("https://old.test/file", tempfile.mkdtemp(),
                         headers={"Cookie": "sid=1",
                                  "Referer": "https://old.test/"})
            with mock.patch.object(gui.simpledialog, "askstring",
                                   return_value=new):
                app.apply_fix(d, "new_link")
            self.assertEqual(d.url, new)
            self.assertEqual("Cookie" in d.headers, kept, new)

    def test_9_unknown_charset_in_filename_header(self):
        name = filename_from_response(
            {"Content-Disposition": "attachment; filename*=bogus''a%20b.zip"},
            "https://a.test/x")
        self.assertEqual(name, "a b.zip")

    def test_10_no_negative_time_or_over_100_percent(self):
        self.assertEqual(gui.fmt_eta(-5), "")
        d = Download("https://a.test/v", tempfile.mkdtemp())
        d.kind, d.size, d._media_got = "media", 100, 130
        d.status, d.speed = DOWNLOADING, 10.0
        self.assertEqual(d.eta, 0)
        row = gui.App._row(gui.App.__new__(gui.App), d)
        self.assertEqual(row[2], "100.0%")

    def test_11_close_warning_says_what_happens(self):
        self.assertIn("1 download is", gui.close_text(1))
        self.assertIn("3 downloads are", gui.close_text(3))
        self.assertIn("continue", gui.close_text(3))

    def test_11_shutdown_has_one_shared_deadline(self):
        m = Manager(os.path.join(tempfile.mkdtemp(), "s.json"))
        m._stop.set()
        for n in range(4):
            d = Download(f"https://invalid.test/{n}", tempfile.mkdtemp())
            d.status = DOWNLOADING
            d._thread = mock.Mock(is_alive=lambda: True)
            d._thread.join = lambda timeout=None: time.sleep(min(timeout, 3))
            m.downloads.append(d)
        t = time.time()
        m.shutdown()
        self.assertLess(time.time() - t, 7)          # was 10 s per download
        self.assertTrue(all(d.status == QUEUED for d in m.downloads))

    def test_minor_tagging_never_creates_a_file(self):
        ghost = os.path.join(tempfile.mkdtemp(), "ghost.mp4")
        mark_of_the_web(ghost, "https://a.test/x")
        self.assertFalse(os.path.exists(ghost))


if __name__ == "__main__":
    unittest.main()
