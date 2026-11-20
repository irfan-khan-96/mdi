"""The shared background-task helper used by the dialogs and flows."""

import threading
import time
import tkinter as tk
import unittest

from mdi.dialogs import run_in_background


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def pump(self, until, limit=5):
        t = time.time()
        while not until() and time.time() - t < limit:
            self.root.update()
            time.sleep(0.01)

    def test_result_and_ticks_arrive_on_the_window_thread(self):
        got, ticks = [], []
        main = threading.get_ident()

        def work():
            time.sleep(0.2)
            return 42

        run_in_background(self.root, work,
                          lambda r, e: got.append((r, e, threading.get_ident())),
                          lambda: ticks.append(1), every=20)
        self.pump(lambda: got)
        self.assertEqual(got, [(42, None, main)])
        self.assertGreater(len(ticks), 1)

    def test_errors_are_handed_over_not_raised(self):
        got = []

        def work():
            raise ValueError("offline")

        run_in_background(self.root, work, lambda r, e: got.append((r, e)),
                          every=10)
        self.pump(lambda: got)
        self.assertIsNone(got[0][0])
        self.assertIsInstance(got[0][1], ValueError)

    def test_nothing_happens_after_the_window_closes(self):
        got = []
        top = tk.Toplevel(self.root)
        run_in_background(top, lambda: time.sleep(0.2),
                          lambda r, e: got.append(1), every=10)
        top.destroy()
        t = time.time()
        while time.time() - t < 0.5:
            self.root.update()
            time.sleep(0.01)
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
