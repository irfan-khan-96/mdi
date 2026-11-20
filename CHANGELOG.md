# Changelog

## Unreleased

### New
- **One-click HD video support.** While ffmpeg or a JavaScript engine is missing, the status bar offers to download them: ffmpeg from yt-dlp's FFmpeg builds (about 86 MB) and the Deno JavaScript engine (about 41 MB). Both come from the projects' GitHub releases, are checked against GitHub's SHA-256 digests (Deno also against a pinned hash), and stay in MDI's own folder. They work without a restart.
- The "YouTube wants a quick check" fix now downloads the JavaScript engine for you instead of giving install commands, then retries the download.

## 1.2.0

### New
- **yt-dlp updates itself.** MDI checks once a week in the background and uses the latest official yt-dlp from the next start. Each download is checked against the SHA-256 checksum yt-dlp publishes. If a new copy fails to load, MDI falls back to the built-in one. The fix dialog's "Update now" button checks straight away.
- **Video quality.** The Add dialog shows a Quality choice for video links: Best, 1080p, 720p, 480p or Audio only (MP3 with ffmpeg). The choice is remembered and used for whole playlists too.
- **Licensed under GPL-3.0.**
- **Automatic tests and releases.** GitHub Actions runs the tests on every push and builds the installer for every version tag.

### Fixed
- YouTube videos that need a JavaScript check (such as videos made for kids) no longer fail with "This video is not available" when Node.js, Deno or Bun is installed.
- A failed save no longer stops the download queue, and an error no longer stops the window updating.
- Busy servers and invalid folders get the right advice and fix button.
- A damaged settings file no longer prevents MDI from starting; it's kept as `mdi.json.damaged`.
- Removing an unfinished video deletes its temporary files.
- Shift+Delete and Undo no longer freeze the window.
- Resume pressed straight after Pause is no longer ignored.
- "Paste new link" no longer sends cookies to a different site.
- An unusual filename header no longer fails the download.
- Video progress no longer shows over 100% or a negative time left.
- Closing MDI with downloads running now asks first, and closes within 5 seconds.

## 1.1.0
- Windows installer (per-user, no admin) with Start Menu shortcut and uninstaller.
- Whole-playlist downloads, taskbar progress, notifications, keyboard shortcuts and undo.
- Plain-language error advice with one-click fixes.
- Honest User-Agent, rate-limit handling and first-request retries.
