"""The release-time check that pinned helpers haven't gone stale."""

import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import check_pins  # noqa: E402

TODAY = dt.date(2027, 1, 1)


class PinTests(unittest.TestCase):
    def test_age_and_limit(self):
        self.assertEqual(check_pins.age_days("2026-12-01T10:00:00Z", TODAY), 31)
        self.assertFalse(check_pins.stale("2026-07-02T00:00:00Z", TODAY))
        self.assertTrue(check_pins.stale("2026-06-30T00:00:00Z", TODAY))

    def test_stale_pin_fails_with_the_latest_version(self):
        def github(path):
            if path.endswith("/latest"):
                return {"tag_name": "v9.9.9", "published_at": "2026-12-30T00:00:00Z"}
            return {"tag_name": "v2.9.8", "published_at": "2026-01-01T00:00:00Z"}
        with mock.patch.object(check_pins, "github", github), \
                mock.patch("builtins.print") as out:
            self.assertEqual(check_pins.main(), 1)
        self.assertIn("v9.9.9", out.call_args_list[-1][0][0])

    def test_fresh_pin_passes(self):
        fresh = (dt.date.today() - dt.timedelta(days=10)).isoformat() + "T00:00:00Z"
        with mock.patch.object(check_pins, "github",
                               lambda p: {"tag_name": "v2.9.8",
                                          "published_at": fresh}), \
                mock.patch("builtins.print"):
            self.assertEqual(check_pins.main(), 0)


if __name__ == "__main__":
    unittest.main()
