# Changelog

## 1.6.0

- **Speed limit.** Settings → Speed limit (MB/s) caps all downloads together, files and YouTube alike, so downloads don't make browsing or video calls laggy. It applies instantly, and the status bar shows it while it's on. 0 = no limit (the default).
- Releases now stop if the pinned Deno version is more than 6 months old.

## 1.5.0

- **Smoother with big lists.** The download list only redraws rows that changed; finished, paused and failed downloads cost nothing until they change. With 2,000 downloads a refresh takes about 45 ms instead of 120 ms, and 5,000 costs about the same.
- Every release is now smoke-tested before it's published: the installer is installed, the installed MDI downloads a file, and it's uninstalled again.

## 1.4.2

- **Fixed:** after updating itself (or when started from the installer's last page), MDI couldn't find an ffmpeg installed through WinGet and offered to download it again. The installer now starts MDI through Explorer, so it runs as a normal program. Updating to this version fixes it.

## 1.4.1

- No visible changes. The window code is split into smaller modules (dialogs, flows, wording), and four copies of the "work in the background, then update the window" code are now one shared helper.
- This release is also the first real test of self-update: MDI 1.4.0 offers it through the status-bar link.

## 1.4.0

### New
- **MDI updates itself.** The installed app checks GitHub weekly for a newer release and shows "MDI x.y.z is available · Update" in the status bar. One click (after confirming) downloads the installer, verifies it against GitHub's SHA-256 digest and the release's SHA256SUMS.txt, saves your list, installs silently and reopens MDI. Running downloads continue afterwards.

## 1.3.0

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
