# MDI Downloader

A minimal download manager built around the core ideas that make Internet Download Manager (IDM) fast. It's pure Python with no dependencies (Python 3.10+, tkinter included).

```bash
python -m mdi                      # GUI
python -m mdi https://host/file    # GUI, adds the URL straight away
python -m mdi.cli https://host/file -o D:\Downloads -n 16   # command line
python -m unittest discover -s tests -t .                    # tests
```

## Install (Windows)

Download `MDI-Setup-<version>.exe` from the [latest release](https://github.com/irfan-khan-96/mdi/releases/latest) and run it. Windows SmartScreen may say "Windows protected your PC", because the installer isn't code-signed; click **More info → Run anyway**. Compare the file with the SHA-256 checksum published next to it if you want to be sure it's the real one.

It installs for your user only, so there's no admin prompt. It puts MDI in `%LOCALAPPDATA%\Programs\MDI Downloader`, adds a Start Menu shortcut (a desktop shortcut is optional), and you uninstall it from **Settings → Apps**. Python and yt-dlp are bundled, and yt-dlp keeps itself up to date (see below). ffmpeg isn't, because it's large: click **Get HD video support** in the status bar and MDI downloads it for you (see below). Your download list and settings in `%APPDATA%\MDI` are kept when you uninstall.

**MDI updates itself.** Once a week the installed app asks GitHub for its latest release. If there's a newer one, the status bar shows **MDI x.y.z is available · Update**. Click it and confirm, and MDI downloads that release's installer and checks it twice: against the SHA-256 digest GitHub lists for the file, and against the release's `SHA256SUMS.txt`. It then saves your download list, closes, and runs the installer silently. The installer replaces the app and opens MDI again; running downloads continue where they stopped. Nothing happens without your click, and a source checkout (`python -m mdi`) never updates itself. See [mdi/selfupdate.py](mdi/selfupdate.py).

To build the installer yourself (needs [Inno Setup 6](https://jrsoftware.org/isinfo.php); without it you still get `dist/MDI/MDI.exe`):

```bash
py -m venv build/venv
build/venv/Scripts/python -m pip install pyinstaller yt-dlp yt-dlp-ejs certifi
build/venv/Scripts/python tools/build.py
```

The output is `dist/MDI/MDI.exe` (about 37 MB as a folder) and `dist/MDI-Setup-<version>.exe` (about 20 MB).

Releases are built by GitHub Actions: pushing a `v*` tag runs the tests, builds the installer, smoke-tests it ([tools/smoke_test.py](tools/smoke_test.py): installs it silently, has the installed MDI download a file and checks it arrived intact, then uninstalls) and only then publishes it with its SHA-256 checksum ([.github/workflows](.github/workflows)). Every push and pull request runs the tests.

## How IDM works (and what MDI copies)

IDM's speed doesn't come from anything secret. It comes from four ideas, and MDI implements all four in [mdi/engine.py](mdi/engine.py):

| # | IDM technique | What it does | MDI |
|---|---|---|---|
| 1 | **Probe with a Range request** | Sends `Range: bytes=0-`. A `206` reply with `Content-Range: bytes 0-N/TOTAL` means the server supports byte ranges and tells you the exact size. | `probe()` |
| 2 | **Multiple connections, one file** | Pre-allocates the file and gives each connection its own byte range. Each one writes straight to its own offset, so there's no merge step at the end. Many servers and CDNs cap speed per connection, so 8 connections can be close to 8× faster. | `_initial_segments()`, `_worker()` |
| 3 | **Dynamic segmentation** | When a connection finishes, it doesn't sit idle. It takes the biggest piece still downloading and splits it in half. A single slow piece can't hold up the whole download, and every connection stays busy until the last byte. This is the patented part people credit for IDM's speed. | `_next_segment()` |
| 4 | **Resume anywhere** | The position of every segment is saved. After a pause, crash, reboot or dropped network, it continues from exactly that byte. On resume it re-checks size, `ETag` and `Last-Modified`, and starts over if the file on the server changed, so you never get a file stitched together from two versions. | `to_dict()`, `_prepare()` |

It also does the supporting work that makes those four ideas reliable:
- automatic retries with exponential back-off for each connection; if one connection gives up, another picks up its piece
- immediate failure on 4xx errors
- following redirects
- getting the filename from `Content-Disposition` (including the RFC 5987 `filename*=`), cleaned up so it's a valid Windows name
- `name (1).ext` naming when a file already exists
- downloading into `*.part` and renaming only after the download completes
- falling back to a single connection when the server has no range support

**Queue** ([mdi/manager.py](mdi/manager.py)): runs N downloads at once (3 by default) and queues the rest. The state is saved to `%APPDATA%\MDI\mdi.json` every 2 s and when the app closes.

**Browser integration** ([mdi/nmhost.py](mdi/nmhost.py) + [extension/](extension/)): this is the other half of why IDM is popular. The browser extension catches downloads and passes them to the app, along with the page's **cookies and Referer**, so downloads that need a login still work. It uses the browser's **native messaging**, like IDM does, so nothing listens on the network. If MDI isn't running, it starts automatically.

**GUI** ([mdi/gui.py](mdi/gui.py), with the dialogs in [mdi/dialogs.py](mdi/dialogs.py), the fix / helper / update flows in [mdi/flows.py](mdi/flows.py) and the wording in [mdi/texts.py](mdi/texts.py)): download list (size, %, speed, ETA, connections, status), Add / Resume / Pause / Remove / Open folder / Settings, and IDM's per-connection bar that shows where each connection is in the file. Ctrl+V opens Add with the URL from the clipboard. Double-click opens a finished file.

- **When something fails:** the row says what went wrong in one plain sentence. Double-click it for the single fix (Try again, Paste new link, Choose folder or Start over). See [mdi/advice.py](mdi/advice.py).
- **While you're in another window:** the taskbar button shows the combined progress of all active downloads. A Windows notification tells you when a download finishes or fails. Clicking it shows the file in its folder or opens the fix. Nothing pops up while the MDI window is in front. See [mdi/winshell.py](mdi/winshell.py). It uses no extra libraries, and the notification-area icon only appears once the first notification is shown.
- **Keyboard:** Ctrl+V adds the copied link, Space pauses or resumes, Enter opens the file (or fixes the download), Delete removes it without asking (an "Undo" link shows in the status bar for 10 seconds, or press Ctrl+Z), Shift+Delete also deletes the file after a confirmation, Ctrl+O shows it in its folder. Press F1 in the app for the full list.

### YouTube and other video sites (optional)
A video-site link is a web page, not a file. If [yt-dlp](https://github.com/yt-dlp/yt-dlp) is installed, MDI hands such links to it automatically ([mdi/media.py](mdi/media.py)). The download appears in the same list, with progress, pause and resume. yt-dlp supports about 1,800 sites, and its maintainers keep up with YouTube's frequent changes.

```bash
pip install "yt-dlp[default]"    # includes yt-dlp-ejs, see below
```

- **For HD:** MDI needs [ffmpeg](https://ffmpeg.org/). YouTube sends HD video and audio as separate streams, and ffmpeg merges them into one `.mp4`. Without ffmpeg, MDI uses the best format that already has both, often 360p.
- **Quality:** for a video-site link, the Add dialog shows a **Quality** choice: Best, 1080p, 720p, 480p or Audio only. If the exact quality doesn't exist, MDI takes the best one below it. Audio only saves an MP3 (with ffmpeg) or the site's own M4A audio (without). MDI remembers your choice, and a playlist uses it for every video.
- **Playlists:** for a playlist link, the Add dialog shows a "Download the whole playlist" checkbox. Tick it and MDI reads the list (nothing is downloaded yet), then adds each video as its own row in a folder named after the playlist. Private or deleted videos and ones already in the list are skipped, and at most 1000 videos are added. Unticked, a `watch?v=…&list=…` link downloads only that video.
- **Your responsibility:** downloading from YouTube is against its Terms of Service, and copyright still applies. Only download what you have the right to.

**yt-dlp updates itself.** Video sites change often, and an old yt-dlp stops working. Once a week, in the background, MDI asks GitHub for yt-dlp's latest release. If it's newer than the copy in use, MDI downloads the official single-file build, checks it against the SHA-256 checksum yt-dlp publishes with the same release, and uses it from the next start. It's stored in `%APPDATA%\MDI\yt-dlp`. If the new copy fails to load, MDI goes back to the built-in one and never uses that version again. When a video download fails because the site changed, the fix dialog's **Update now** button checks straight away. See [mdi/updater.py](mdi/updater.py).

YouTube shows some videos, such as ones made for kids, only to players that pass a JavaScript check. To pass it, yt-dlp needs its `yt-dlp-ejs` package (bundled) and a JavaScript engine: Node.js, Deno, Bun or QuickJS. MDI turns on whichever is installed (yt-dlp on its own only looks for Deno). Without one, YouTube just says "This video is not available"; MDI recognises that, and the fix button gets the engine for you.

**One-click helpers.** While ffmpeg or a JavaScript engine is missing, the status bar shows **Get HD video support** (or **Fix some YouTube videos** if only the engine is missing). One click, after you confirm, downloads:
- **ffmpeg** (about 86 MB) from [yt-dlp's FFmpeg builds](https://github.com/yt-dlp/FFmpeg-Builds). Only `ffmpeg.exe`, `ffprobe.exe` and their DLLs are kept.
- **Deno** (about 41 MB download, 94 MB on disk) from [Deno's releases](https://github.com/denoland/deno), pinned to v2.9.8. It's yt-dlp's default JavaScript engine. The smaller QuickJS was tried and dropped: it added about 10 seconds to every YouTube video, while Deno is as fast as Node.js (about 4 s).

Each file must match the SHA-256 digest GitHub lists for it, and Deno must also match the hash written into [mdi/addons.py](mdi/addons.py). They're kept in `%LOCALAPPDATA%\MDI\tools` and only MDI's own PATH points there, so nothing changes system-wide. They work straight away, without a restart. If you already have ffmpeg, Node.js, Deno or Bun installed, MDI uses those and the link doesn't appear.

The status bar also says when yt-dlp is missing, or old and unable to update itself.

### Install the browser extension (Chrome / Edge / Brave)
1. Register MDI with your browsers. This is per-user, needs no admin rights, and `--remove` undoes it:
   ```bash
   python -m mdi.install
   ```
2. Open `chrome://extensions` (or `edge://extensions`) and turn on **Developer mode**.
3. Click **Load unpacked** and select the `extension` folder. The extension ID must be `agiignfakemhlpgffigkmjpjglbngmif`, which is pinned by the `key` in the manifest.
4. Downloads are now captured automatically. Click the toolbar button to turn capture ON or OFF, or right-click a link and choose **Download with MDI**. Downloads from incognito windows are never captured. If MDI isn't registered, the browser downloads the file normally.

### Icon
The icon is a cartoon orange globe with a smiling blue arrow diving in. It's an original design, a reversed take on the classic download-manager look. [mdi/assets/](mdi/assets/) holds the window/taskbar icon (`icon.ico`, `icon.png`) and [extension/icons/](extension/icons/) holds the browser toolbar icons. To redraw them, edit [tools/make_icon.py](tools/make_icon.py) and run it (needs Pillow).

## What was removed, and why

These IDM features aren't needed for fast, reliable downloading:

| Removed IDM feature | Why it's not needed |
|---|---|
| Built-in video "Download panel" grabber, stream sniffer | MDI hands video sites to yt-dlp instead of keeping its own site-specific parsers (see above). |
| Site Grabber / spider | It's a web crawler, not a downloader. |
| Scheduler (start/stop at times, shutdown or hang up when done) | Few people use it. The queue covers the common case. |
| Download categories, auto-sorting folders | You pick a folder per download, or set one default. |
| Speed limiter | This is a "be nice to others" setting. It isn't needed to download quickly or correctly. |
| Antivirus scan hook after download | Windows Defender already scans new files. |
| Dial-up / modem settings, proxy UI | Obsolete. Python follows the system `HTTP(S)_PROXY` variables. |
| FTP, MMS, RTSP protocols | Almost everything is served over HTTP(S) now. |
| Login manager, saved site passwords | The extension sends the browser's cookies instead, which covers modern logins without storing passwords. |
| Floating "drop target", taskbar tray tricks, skins/themes, toolbars | Cosmetic. |
| Registration, trial, serial checks, update nags, ads | Not features. |
| Per-download "File info" pop-up and completion dialogs | They add a click to every download. Status is shown in the list. |
| Checksum UI, export/import lists, language packs | Nice extras, not core. |

## Security

The design was audited against the OWASP Top 10 (2021). Each fix has a regression test in [tests/test_security.py](tests/test_security.py).

| OWASP | Risk in a download manager | How MDI handles it |
|---|---|---|
| **A01 Broken access control** | A website, a program squatting a local port, or another Windows user feeds downloads to the app or receives your cookies | No network listener at all. The extension talks to the app through native messaging, the browser only lets the pinned extension ID connect, and the host checks the caller's origin again. Requests are passed through a per-user folder. |
| **A02 Cryptographic failures** | Cookies stored or sent where they shouldn't be | Cookies are encrypted at rest with Windows DPAPI (tied to your user account) and deleted once a download completes. TLS certificates are always verified (system store + certifi). Cookies are never sent to another host after a redirect, and never over plain HTTP. |
| **A03 Injection** | Path traversal or spoofed names through `Content-Disposition` or video titles; header injection; `file://` URLs | One filename sanitiser removes `\ / : * ? " < > \|`, control characters, reserved names (`CON`, `NUL`…) and right-to-left override characters, so `invoice‮fdp.exe` can't pose as a PDF. Only `http(s)` URLs are accepted, and only `Referer`/`Cookie` headers, as single-line values. No shell is ever used. |
| **A04 Insecure design** | Disk filled by a fake size; a file stitched together from two versions | Free disk space is checked before pre-allocating. Every piece is checked against the file's total size and ETag, and the download stops if the file changed on the server. Writes never go past a piece's end, and partial writes are handled. |
| **A05 Security misconfiguration** | Unsafe defaults | No ports are open, isolated-mode Python (`-I`) runs the browser host, extension CSP is `script-src 'self'`, and settings loaded from disk are range-checked. |
| **A06 Vulnerable components** | An old yt-dlp | MDI only passes cookies to yt-dlp 2023.07.06 or later (older versions leaked them to other hosts, CVE-2023-35934). yt-dlp updates itself weekly (see above). MDI itself uses only the Python standard library. |
| **A07 Authentication failures** | Something pretending to be the app or the extension | The browser authenticates both sides of native messaging: the registered host path and the allowed extension ID. |
| **A08 Integrity failures** | Downloaded programs skipping Windows protections | Every finished file gets the **Mark-of-the-Web** (`Zone.Identifier`, Zone 3), like a browser download, so SmartScreen and Office Protected View still apply. yt-dlp updates come only from yt-dlp's official GitHub releases over HTTPS and must match the SHA-256 checksum published with that release, contain `yt_dlp`, and carry the expected version, or they are discarded. MDI's own updates must match both GitHub's SHA-256 digest and the release's `SHA256SUMS.txt`, and come only from this project's release. The one-click helpers (ffmpeg, Deno) are checked the same way against GitHub's SHA-256 digest, and Deno against a hash pinned in MDI. (The checksum file is fetched from the same place, so this proves the download is intact, not who made it; yt-dlp's GPG signature isn't checked, because Python has no built-in GPG.) |
| **A09 Logging and monitoring** | No record of what happened | `%APPDATA%\MDI\mdi.log` (rotated, 1 MB × 3) records adds, completions, failures and rejected requests. URLs are logged without query strings or credentials, and cookies are never logged. |
| **A10 SSRF** | A download redirected to `file://` or `ftp://`, or downgraded to HTTP | Redirects are only followed to `http(s)`, and never from HTTPS down to HTTP. The extension also withholds `SameSite=Strict` cookies on downloads started from another site, matching what the browser would send. |

**Limits:**
- MDI can't protect against malware already running as your Windows user, since that malware can read anything you can.
- The extension's public key is in the manifest, so another *unpacked* extension could copy it and get the same ID. That requires Developer mode and you loading it yourself. Store-published extensions can't do this.
- Downloading a program is still your decision: MDI only makes sure Windows warns you before it runs.

## Size and tests

**Result:** about 4,100 lines of Python (comments included) across 17 modules, plus a ~115-line extension and 150 tests. The tests cover:
- multi-connection downloads checked byte-for-byte
- dynamic splitting of a slow tail
- pause → restart → resume
- a file changing on the server while paused
- servers without range support
- retries on 503
- 404 handling
- redirects
- `Content-Disposition`
- name collisions
- the queue and saved state
- the security fixes listed above: cookie scoping, redirects, integrity, disk space, Mark-of-the-Web, input validation, encrypted storage and the native-messaging channel

## License

MDI Downloader is free software under the [GNU General Public License v3.0](LICENSE): you may use, study, share and change it, and anything you distribute that's built from it must also be under the GPL with its source code available. The source code is at https://github.com/irfan-khan-96/mdi.

The installer bundles Python (PSF License), yt-dlp (Unlicense) and certifi (MPL 2.0), all compatible with the GPL.

## Notes
- Cookies captured from the browser are saved DPAPI-encrypted in `mdi.json` only until the download finishes, so an unfinished download can resume after a restart.
- MDI identifies itself honestly as `MDI-Downloader/<version>`. Pretending to be Chrome gets downloads dropped by bot protection, because the network fingerprint doesn't match.
- If a server answers *429 Too Many Requests*, MDI closes the extra connections, hands their pieces to the ones that remain, and respects `Retry-After`. The first request is retried like the rest.
- Up to 32 connections per download are allowed. 8 is the default (IDM's default too). Some servers throttle or block clients that open too many connections.
