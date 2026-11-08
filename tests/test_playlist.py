"""Playlist detection and expansion (no network: yt-dlp is faked)."""

import unittest
from unittest import mock

from mdi import media


class PlaylistTests(unittest.TestCase):
    def test_detects_playlist_links(self):
        for u in ("https://youtu.be/abc?list=PL1",
                  "https://www.youtube.com/watch?v=abc&list=PL1",
                  "https://www.youtube.com/playlist?list=PL1"):
            self.assertTrue(media.looks_like_playlist(u), u)
        for u in ("https://youtu.be/abc", "https://a.test/file.zip?list=",
                  "not a url"):
            self.assertFalse(media.looks_like_playlist(u), u)

    def test_entries_skip_unavailable_and_non_http(self):
        info = {"title": "Mix", "entries": [
            {"url": "https://www.youtube.com/watch?v=1", "title": "One"},
            {"url": "https://www.youtube.com/watch?v=2",
             "title": "[Private video]"},
            {"url": "file:///C:/evil", "title": "Bad"},
            None,
            {"webpage_url": "https://www.youtube.com/watch?v=3"}]}
        self.assertEqual(media.playlist_entries(info), (
            "Mix", ["https://www.youtube.com/watch?v=1",
                    "https://www.youtube.com/watch?v=3"]))

    def test_watch_link_asks_for_the_list_itself(self):
        fake = mock.MagicMock()
        ydl = fake.YoutubeDL.return_value.__enter__.return_value
        ydl.extract_info.return_value = {"title": "T", "entries": []}
        with mock.patch.object(media, "yt_dlp", fake):
            media.expand_playlist("https://youtu.be/abc?list=PL9&t=4")
        self.assertEqual(ydl.extract_info.call_args[0][0],
                         "https://www.youtube.com/playlist?list=PL9")
        opts = fake.YoutubeDL.call_args[0][0]
        self.assertEqual(opts["extract_flat"], "in_playlist")
        self.assertEqual(opts["playlistend"], media.MAX_PLAYLIST)

    def test_errors_are_readable(self):
        fake = mock.MagicMock()
        fake.YoutubeDL.return_value.__enter__.return_value.extract_info \
            .side_effect = Exception("\x1b[0;31mERROR:\x1b[0m nope")
        with mock.patch.object(media, "yt_dlp", fake), \
                self.assertRaises(RuntimeError) as cm:
            media.expand_playlist("https://www.youtube.com/playlist?list=X")
        self.assertNotIn("\x1b", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
