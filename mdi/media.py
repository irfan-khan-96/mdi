"""Optional video-site support (YouTube and ~1800 others) through yt-dlp.

MDI's own engine downloads files. Video sites don't serve files: their pages
load separate, signed audio/video streams that change format often. yt-dlp
(https://github.com/yt-dlp/yt-dlp) tracks those changes, so MDI hands such
links to it instead of re-implementing it.

Install:  pip install yt-dlp     (optional: ffmpeg on PATH for HD + merging)
Without ffmpeg only "single-file" formats can be used (often 360p on YouTube).
"""

from __future__ import annotations

import os
import shutil

try:
    import yt_dlp
except ImportError:  # optional dependency
    yt_dlp = None


def available() -> bool:
    return yt_dlp is not None


# yt-dlp older than this sent a Cookie header to every host it was
# redirected to (CVE-2023-35934); newer versions scope it to the domain.
COOKIE_SAFE_VERSION = (2023, 7, 6)


def version() -> tuple[int, ...]:
    try:
        from yt_dlp.version import __version__
        return tuple(int(x) for x in __version__.split(".")[:3])
    except Exception:  # noqa: BLE001
        return (0,)


def age_days() -> int | None:
    """Days since the installed yt-dlp was released (versions are dates).
    Video sites change constantly, so an old yt-dlp breaks or misbehaves."""
    import datetime
    v = version()
    try:
        return (datetime.date.today() - datetime.date(*v[:3])).days
    except (TypeError, ValueError):
        return None


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


# YouTube serves some videos (e.g. "made for kids") only to clients that
# solve a JavaScript challenge. yt-dlp does that with the yt-dlp-ejs
# scripts run in a JS runtime - but only enables Deno by default. Enable
# whichever of them is installed.
_JS_RUNTIMES = ("deno", "node", "bun")


def js_runtimes() -> dict:
    found = {name: {} for name in _JS_RUNTIMES if shutil.which(name)}
    return found or {"deno": {}}


def challenge_ready() -> bool:
    """True if YouTube's JavaScript challenge can be solved."""
    if not any(shutil.which(n) for n in _JS_RUNTIMES):
        return False
    try:
        import yt_dlp_ejs  # noqa: F401
    except ImportError:
        return False
    return True


NEEDS_JS = ("YouTube asked for a JavaScript check that MDI couldn't solve "
            "(needs yt-dlp-ejs and Node.js or Deno)")


def _explain_failure(msg: str) -> str:
    """Spot failures caused by the missing JS-challenge helper: YouTube
    then just says "not available" or offers no formats."""
    low = msg.lower()
    if not challenge_ready() and ("not available" in low or
                                  "page needs to be reloaded" in low):
        return NEEDS_JS
    return msg


MAX_PLAYLIST = 1000   # YouTube "mixes" are endless; stop somewhere sane
_UNAVAILABLE = ("[private video]", "[deleted video]", "[unavailable video]")


def looks_like_playlist(url: str) -> bool:
    """Cheap check (no network) used to decide whether to offer the
    "whole playlist" option."""
    from urllib.parse import parse_qs, urlsplit
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    return bool(parse_qs(u.query).get("list")) or "/playlist" in u.path


def expand_playlist(url: str) -> tuple[str, list[str]]:
    """(playlist title, video page URLs). Reads only the list, downloads
    nothing. Private/deleted entries are skipped."""
    from urllib.parse import parse_qs, urlsplit
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is not installed")
    # A watch?v=X&list=Y link means "video X"; ask for list Y itself.
    q = parse_qs(urlsplit(url).query)
    if q.get("list") and "youtu" in urlsplit(url).netloc:
        url = f"https://www.youtube.com/playlist?list={q['list'][0]}"
    opts = {"extract_flat": "in_playlist", "quiet": True,
            "no_warnings": True, "logger": _SilentLogger(),
            "playlistend": MAX_PLAYLIST, "skip_download": True,
            "js_runtimes": js_runtimes()}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(_explain_failure(_clean(e))) from e
    return playlist_entries(info)


def playlist_entries(info: dict) -> tuple[str, list[str]]:
    urls = []
    for e in info.get("entries") or []:
        if not e or str(e.get("title", "")).lower() in _UNAVAILABLE:
            continue
        u = e.get("webpage_url") or e.get("url")
        if isinstance(u, str) and u.startswith(("http://", "https://")):
            urls.append(u)
    return str(info.get("title") or "Playlist"), urls[:MAX_PLAYLIST]


_FORMAT_PART = None


def leftovers(stem: str | None) -> list[str]:
    """yt-dlp's temporary files for one video: ``<stem>.f399.mp4.part``,
    ``<stem>.f251.webm`` (waiting to be merged), ``.ytdl``, fragments.
    ``stem`` is the output path without its extension. The finished file
    (``<stem>.mp4``) is never included."""
    import re
    global _FORMAT_PART
    if _FORMAT_PART is None:
        _FORMAT_PART = re.compile(r"^\.f[\w-]+\.\w+$")
    if not stem:
        return []
    folder, prefix = os.path.split(stem)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    found = []
    for n in names:
        if not n.startswith(prefix + "."):
            continue
        rest = n[len(prefix):]
        if rest.endswith((".part", ".ytdl")) or ".part-Frag" in rest or \
                _FORMAT_PART.match(rest):
            found.append(os.path.join(folder, n))
    return found


class _Paused(Exception):
    pass


class _SilentLogger:
    """Errors reach the UI through the exception, not the console."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _clean(msg: str) -> str:
    msg = str(msg).replace("\x1b[0;31m", "").replace("\x1b[0m", "")
    return msg.removeprefix("ERROR: ").strip()


def download(dl) -> None:
    """Download ``dl.url`` with yt-dlp, reporting progress on ``dl``.
    Returns normally when finished or paused; raises on failure."""
    from .engine import FatalError

    finished = {"bytes": 0}

    def hook(d: dict) -> None:
        if dl._halt.is_set():
            raise _Paused()
        got = d.get("downloaded_bytes") or 0
        if d["status"] == "downloading":
            dl._media_got = finished["bytes"] + got
            dl.speed = d.get("speed") or 0.0
        elif d["status"] == "finished":
            finished["bytes"] += d.get("total_bytes") or got
            dl._media_got = finished["bytes"]

    ffmpeg = has_ffmpeg()
    opts = {
        "outtmpl": os.path.join(dl.save_dir, "%(title).150B [%(id)s].%(ext)s"),
        # Best video+audio merged into one file needs ffmpeg; otherwise the
        # best format that already contains both.
        "format": "bv*+ba/b" if ffmpeg else "b[vcodec!=none][acodec!=none]/b",
        "noplaylist": True,          # a watch?v=..&list=.. link = that video
        "quiet": True, "no_warnings": True, "noprogress": True,
        "progress_hooks": [hook],
        "logger": _SilentLogger(),
        "windowsfilenames": True,
        "continuedl": True,          # resume .part files after a pause
        "concurrent_fragment_downloads": dl.connections,
        "js_runtimes": js_runtimes(),
    }
    if ffmpeg:
        opts["merge_output_format"] = "mp4"
    headers = dict(dl.headers)
    if "Cookie" in headers and version() < COOKIE_SAFE_VERSION:
        headers.pop("Cookie")   # old yt-dlp would leak it to other hosts
    if headers:
        opts["http_headers"] = headers

    os.makedirs(dl.save_dir, exist_ok=True)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(dl.url, download=False)
            if info.get("_type") == "playlist":   # bare playlist link
                info = next(iter(info.get("entries") or []), None)
                if info is None:
                    raise FatalError("playlist is empty")
                info = ydl.extract_info(info.get("webpage_url") or
                                        info["url"], download=False)
            formats = info.get("requested_formats") or [info]
            sizes = [f.get("filesize") or f.get("filesize_approx")
                     for f in formats]
            dl.size = int(sum(sizes)) if all(sizes) else None
            planned = ydl.prepare_filename(info)
            dl.media_stem = os.path.splitext(planned)[0]
            dl.filename = os.path.basename(planned)
            if ffmpeg and len(formats) > 1:
                dl.filename = os.path.splitext(dl.filename)[0] + ".mp4"
            dl.resumable = True

            info = ydl.process_ie_result(info, download=True)
            reqs = info.get("requested_downloads") or [{}]
            path = reqs[0].get("filepath") or ydl.prepare_filename(info)
    except FatalError:
        raise
    except Exception as e:  # noqa: BLE001 - yt-dlp wraps everything
        if dl._halt.is_set():
            return                    # paused by the user
        raise FatalError(_explain_failure(_clean(e))) from e

    # Video titles are chosen by whoever uploaded them: strip characters
    # that could disguise the file type, then tag it as from the Internet.
    from .engine import sanitize_filename, unique_path
    from .safety import mark_of_the_web
    clean = sanitize_filename(os.path.basename(path))
    if os.path.exists(path) and clean != os.path.basename(path):
        new = unique_path(os.path.dirname(path), clean)
        os.replace(path, new)
        path = new
    if not os.path.isfile(path):
        raise FatalError("the finished video file is missing")
    mark_of_the_web(path, dl.url, dl.headers.get("Referer"))

    dl.path, dl.filename = path, os.path.basename(path)
    dl.size = os.path.getsize(path)
    dl._media_got = dl.size
