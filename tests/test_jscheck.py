"""YouTube's JavaScript check: runtime detection and clear advice."""

import unittest
from unittest import mock

from mdi import media
from mdi.advice import explain


def which_only(*names):
    return lambda n: f"C:/bin/{n}.exe" if n in names else None


class JsCheckTests(unittest.TestCase):
    def test_enables_every_installed_runtime(self):
        with mock.patch.object(media.shutil, "which", which_only("node", "bun")):
            self.assertEqual(media.js_runtimes(), {"node": {}, "bun": {}})

    def test_falls_back_to_yt_dlp_default(self):
        with mock.patch.object(media.shutil, "which", which_only()):
            self.assertEqual(media.js_runtimes(), {"deno": {}})
            self.assertFalse(media.challenge_ready())

    def test_not_available_without_helper_becomes_actionable(self):
        with mock.patch.object(media, "challenge_ready", return_value=False):
            msg = media._explain_failure("[youtube] x: This video is not available")
        self.assertEqual(msg, media.NEEDS_JS)
        advice, action = explain(msg, "media")
        self.assertEqual(action, "js_setup")
        self.assertIn("made for kids", advice)

    def test_not_available_with_helper_stays_as_is(self):
        with mock.patch.object(media, "challenge_ready", return_value=True):
            msg = media._explain_failure("[youtube] x: This video is not available")
        self.assertEqual(explain(msg, "media")[1], None)


if __name__ == "__main__":
    unittest.main()
