"""Segmented, resumable HTTP download engine.

This is the part of IDM that actually makes it fast:

1. Probe the URL with ``Range: bytes=0-`` to learn the size and whether the
   server honours byte ranges.
2. Pre-allocate the file and split it into N segments, one connection each.
   Every connection writes straight to its own offset in the same file, so
   there is no "merge" step at the end.
3. Dynamic segmentation: when a connection finishes its piece, it takes the
   largest piece still being downloaded and splits it in half. Every
   connection stays busy until the very end instead of idling while one slow
   segment drags on.
4. Segment positions are saved, so a pause, crash or network drop resumes
   exactly where it stopped. On resume the ETag / Last-Modified / size are
   re-checked so a changed file is never stitched together from two versions.
"""

from __future__ import annotations

import http.client
import mimetypes
import os
import re
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass

from . import __version__
from .safety import (credentials_for, get_logger, is_downgrade,
                     mark_of_the_web, redact, strip_spoofing)

log = get_logger()

CHUNK = 256 * 1024           # bytes per read(); fewer syscalls = faster
MIN_SPLIT = 1024 * 1024      # never create a segment smaller than this
TIMEOUT = 30                 # socket timeout (seconds)
DISK_MARGIN = 64 * 1024 * 1024  # keep this much disk free
MAX_RETRIES = 8              # per connection, with exponential back-off
# An honest name. Claiming to be Chrome backfires: bot protection notices
# the network fingerprint isn't Chrome's and hangs up.
USER_AGENT = f"MDI-Downloader/{__version__}"


def _ssl_context() -> ssl.SSLContext:
    """System certificates plus certifi's bundle when it's installed.
    On Windows Python only sees root certificates Windows has already
    cached, so some valid sites fail verification without certifi."""
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    return ctx


SSL_CONTEXT = _ssl_context()


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to http(s), never from HTTPS down to HTTP, and
    drop cookies / credentials as soon as the host changes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        scheme = urllib.parse.urlsplit(new.full_url).scheme.lower()
        if scheme not in ("http", "https"):
            raise FatalError(f"refused redirect to {scheme}:// link")
        if is_downgrade(req.full_url, new.full_url):
            raise FatalError("refused insecure redirect from HTTPS to HTTP")
        keep = credentials_for(req.full_url, new.full_url,
                               {"Cookie": "x", "Authorization": "x"})
        for k in ("Cookie", "Authorization"):
            if k not in keep:
                new.headers.pop(k, None)
                new.unredirected_hdrs.pop(k, None)
        return new


OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=SSL_CONTEXT), _SafeRedirect)

QUEUED, DOWNLOADING, PAUSED, COMPLETED, ERROR = (
    "queued", "downloading", "paused", "completed", "error")


class FatalError(Exception):
    """An error that retrying will not fix (404, 403, file changed, ...)."""


class WebPageError(FatalError):
    """The link returned an HTML page instead of a file."""


# ---------------------------------------------------------------- probing

@dataclass
class ProbeResult:
    final_url: str
    size: int | None
    resumable: bool
    filename: str
    etag: str | None
    last_modified: str | None
    content_type: str = ""


def _request(url: str, headers: dict, start: int | None = None,
             end: int | None = None):
    h = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity", **headers}
    if start is not None:
        h["Range"] = f"bytes={start}-{'' if end is None else end}"
    return OPENER.open(urllib.request.Request(url, headers=h),
                       timeout=TIMEOUT)


def _retry_after(e: urllib.error.HTTPError) -> float:
    """Seconds the server asked us to wait (Retry-After), capped at 2 min."""
    try:
        return min(120.0, max(0.0, float(e.headers.get("Retry-After", 0))))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _is_fatal_http(e: urllib.error.HTTPError) -> bool:
    return 400 <= e.code < 500 and e.code not in (408, 425, 429)


_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str) -> str:
    name = _BAD_CHARS.sub("_", strip_spoofing(name)).strip(" .")
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL",
                                      *(f"COM{i}" for i in range(1, 10)),
                                      *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    if len(name) > 200:                 # shorten, but keep the extension
        base, ext = os.path.splitext(name)
        ext = ext[:20]
        name = base[:200 - len(ext)].rstrip(" .") + ext
    return name or "download"


def filename_from_response(headers, url: str) -> str:
    cd = headers.get("Content-Disposition", "") or ""
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", cd, re.I)
    if m:
        name = urllib.parse.unquote(m.group(2).strip().strip('"'),
                                    encoding=m.group(1) or "utf-8",
                                    errors="replace")
        return sanitize_filename(name)
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, re.I) or \
        re.search(r"filename\s*=\s*([^;]+)", cd, re.I)
    if m:
        return sanitize_filename(m.group(1).strip())
    path = urllib.parse.urlparse(url).path
    return sanitize_filename(urllib.parse.unquote(path.rsplit("/", 1)[-1]))


def probe(url: str, headers: dict) -> ProbeResult:
    try:
        r = _request(url, headers, 0)
    except urllib.error.HTTPError as e:
        e.close()
        if e.code == 416:  # some servers reject bytes=0- on empty files
            r = _request(url, headers)
        elif _is_fatal_http(e):
            raise FatalError(f"HTTP {e.code} {e.reason}") from e
        else:
            raise
    with r:
        size, resumable = None, False
        m = re.match(r"bytes\s+\d+-\d+/(\d+)",
                     r.headers.get("Content-Range", "") or "")
        if r.status == 206 and m:
            size, resumable = int(m.group(1)), True
        else:
            cl = r.headers.get("Content-Length")
            size = int(cl) if cl and cl.isdigit() else None
        return ProbeResult(
            final_url=r.geturl(),
            size=size,
            resumable=resumable,
            filename=filename_from_response(r.headers, r.geturl()),
            etag=r.headers.get("ETag"),
            last_modified=r.headers.get("Last-Modified"),
            content_type=(r.headers.get("Content-Type") or "")
            .split(";")[0].strip().lower(),
        )


HTML_TYPES = {"text/html", "application/xhtml+xml"}


def with_extension(name: str, content_type: str) -> str:
    """Add an extension guessed from Content-Type if the name has none."""
    if os.path.splitext(name)[1] or not content_type or \
            content_type == "application/octet-stream":
        return name
    ext = mimetypes.guess_extension(content_type, strict=False)
    return name + ext if ext else name


def unique_path(directory: str, name: str) -> str:
    base, ext = os.path.splitext(name)
    candidate, i = name, 1
    while (os.path.exists(os.path.join(directory, candidate)) or
           os.path.exists(os.path.join(directory, candidate + ".part"))):
        candidate = f"{base} ({i}){ext}"
        i += 1
    return os.path.join(directory, candidate)


# --------------------------------------------------------------- segments

class Segment:
    """Byte range [start, end] (inclusive). ``pos`` is the next byte to
    write. ``end`` is None while the total size is unknown."""

    __slots__ = ("start", "end", "pos", "active")

    def __init__(self, start: int, end: int | None, pos: int | None = None):
        self.start, self.end = start, end
        self.pos = start if pos is None else pos
        self.active = False

    @property
    def remaining(self) -> int:
        return 0 if self.end is None else max(0, self.end - self.pos + 1)

    @property
    def done(self) -> bool:
        return self.end is not None and self.pos > self.end


# --------------------------------------------------------------- download

class Download:
    def __init__(self, url: str, save_dir: str, connections: int = 8,
                 headers: dict | None = None, filename: str | None = None,
                 id: str | None = None):
        self.id = id or uuid.uuid4().hex[:12]
        self.url = url
        self.save_dir = save_dir
        self.connections = max(1, min(32, connections))
        self.headers = headers or {}
        self.filename = sanitize_filename(filename) if filename else None
        self.path: str | None = None       # final file path
        self.final_url: str | None = None  # after redirects
        self.size: int | None = None
        self.resumable = False
        self.etag: str | None = None
        self.last_modified: str | None = None
        self.segments: list[Segment] = []
        self.status = QUEUED
        self.error = ""
        self.added = time.time()
        self.kind = "file"   # "media" = handed to yt-dlp (see media.py)
        self._media_got = 0

        self._lock = threading.Lock()
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None
        self._fatal: Exception | None = None
        self._samples: deque = deque(maxlen=12)
        self._req_headers: dict = {}
        self.speed = 0.0

    # ---- public API ---------------------------------------------------
    @property
    def downloaded(self) -> int:
        if self.kind == "media":
            return self._media_got
        with self._lock:
            return sum(s.pos - s.start for s in self.segments)

    @property
    def active_connections(self) -> int:
        with self._lock:
            return sum(1 for s in self.segments if s.active)

    @property
    def eta(self) -> float | None:
        if not self.size or self.speed <= 0:
            return None
        return (self.size - self.downloaded) / self.speed

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running or self.status == COMPLETED:
            return
        self._halt.clear()
        self._fatal = None
        self.error = ""
        self.status = DOWNLOADING
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"dl-{self.id}")
        self._thread.start()

    def pause(self) -> None:
        if self.is_running:
            self._halt.set()
        elif self.status in (QUEUED, ERROR):
            self.status = PAUSED

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    @property
    def part_path(self) -> str | None:
        return self.path + ".part" if self.path else None

    # ---- persistence --------------------------------------------------
    def to_dict(self) -> dict:
        with self._lock:
            segs = [[s.start, s.end, s.pos] for s in self.segments]
        return {
            "id": self.id, "url": self.url, "save_dir": self.save_dir,
            "connections": self.connections, "headers": self.headers,
            "filename": self.filename, "path": self.path,
            "size": self.size, "resumable": self.resumable,
            "etag": self.etag, "last_modified": self.last_modified,
            "segments": segs, "status": self.status, "error": self.error,
            "added": self.added, "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Download":
        dl = cls(d["url"], d["save_dir"], d.get("connections", 8),
                 d.get("headers"), d.get("filename"), d["id"])
        for k in ("path", "size", "resumable", "etag", "last_modified",
                  "status", "error", "added", "kind"):
            if k in d:
                setattr(dl, k, d[k])
        dl.segments = [Segment(*s) for s in d.get("segments", [])]
        if dl.status == DOWNLOADING:   # app was closed / crashed mid-way
            dl.status = QUEUED
        return dl

    # ---- main thread --------------------------------------------------
    def _run(self) -> None:
        try:
            if self.kind == "file":
                try:
                    self._prepare()
                except WebPageError:
                    from . import media
                    if not media.available():
                        raise
                    self.kind = "media"   # video site: let yt-dlp handle it
            if self.kind == "media":
                from . import media
                media.download(self)
                self.status = PAUSED if self._halt.is_set() else COMPLETED
                return
            self._download()
            if self._halt.is_set() and self._fatal is None:
                self.status = PAUSED
            elif self._fatal is not None:
                raise self._fatal
            elif all(s.done for s in self.segments):
                self._finish()
            else:
                raise FatalError("all connections failed; resume to retry")
        except Exception as e:  # noqa: BLE001 - surface everything in UI
            self.status = ERROR
            self.error = str(e) or e.__class__.__name__
            if not isinstance(e, FatalError):
                log.exception("unexpected error for %s", redact(self.url))
        finally:
            self.speed = 0.0
            with self._lock:
                for s in self.segments:
                    s.active = False

    def _probe_with_retry(self) -> ProbeResult:
        """The first request gets the same patience as the rest: dropped
        connections and busy servers (429/5xx) are retried."""
        for attempt in range(1, 5):
            try:
                return probe(self.url, self.headers)
            except FatalError:
                raise
            except (OSError, http.client.HTTPException) as e:
                wait = _retry_after(e) if isinstance(
                    e, urllib.error.HTTPError) else 0
                if attempt == 4 or self._halt.wait(max(2 ** attempt, wait)):
                    raise

    def _prepare(self) -> None:
        info = self._probe_with_retry()
        if info.content_type in HTML_TYPES:
            raise WebPageError(
                "this link is a web page, not a file. For YouTube and other "
                "video sites install yt-dlp:  pip install yt-dlp")
        changed = (
            self.size != info.size or
            (self.etag and info.etag and self.etag != info.etag) or
            (self.last_modified and info.last_modified and
             self.last_modified != info.last_modified)
        )
        can_resume = (self.segments and info.resumable and not changed and
                      self.part_path and os.path.exists(self.part_path))
        if not can_resume:
            self.segments = []

        self.final_url = info.final_url
        self._req_headers = credentials_for(self.url, info.final_url,
                                            self.headers)
        self.size, self.resumable = info.size, info.resumable
        self.etag, self.last_modified = info.etag, info.last_modified

        if not self.path:
            os.makedirs(self.save_dir, exist_ok=True)
            self.path = unique_path(self.save_dir, with_extension(
                self.filename or info.filename, info.content_type))
            self.filename = os.path.basename(self.path)

        if not self.segments:
            self._check_disk_space()
            self.segments = self._initial_segments()
            with open(self.part_path, "wb") as f:
                if self.resumable and self.size:
                    f.truncate(self.size)  # pre-allocate
        elif os.path.getsize(self.part_path) != self.size:
            with open(self.part_path, "r+b") as f:
                f.truncate(self.size)

    def _check_disk_space(self) -> None:
        if not self.size:
            return
        free = shutil.disk_usage(self.save_dir).free
        if self.size + DISK_MARGIN > free:
            raise FatalError(f"not enough disk space: need "
                             f"{self.size >> 20} MB, {free >> 20} MB free")

    def _initial_segments(self) -> list[Segment]:
        if not self.resumable or not self.size:
            end = self.size - 1 if self.size is not None else None
            return [Segment(0, end)]
        n = max(1, min(self.connections, self.size // MIN_SPLIT))
        step = self.size // n
        bounds = [i * step for i in range(n)] + [self.size]
        return [Segment(bounds[i], bounds[i + 1] - 1) for i in range(n)]

    def _download(self) -> None:
        if self.size == 0:
            return
        workers = []
        for _ in range(self.connections):
            seg = self._next_segment(allow_split=False)
            if seg is None:
                break
            t = threading.Thread(target=self._worker, args=(seg,),
                                 daemon=True)
            t.start()
            workers.append(t)
        while any(t.is_alive() for t in workers):
            time.sleep(0.25)
            self._sample_speed()

    def _finish(self) -> None:
        final = self.path
        if os.path.exists(final):          # appeared while we downloaded
            final = unique_path(self.save_dir, os.path.basename(final))
        os.replace(self.part_path, final)
        self.path, self.filename = final, os.path.basename(final)
        mark_of_the_web(final, self.final_url or self.url,
                        self.headers.get("Referer"))
        self.status = COMPLETED

    def _sample_speed(self) -> None:
        now, got = time.monotonic(), self.downloaded
        self._samples.append((now, got))
        t0, b0 = self._samples[0]
        if now - t0 > 0.5:
            self.speed = (got - b0) / (now - t0)

    # ---- segment scheduling (the IDM trick) ---------------------------
    def _next_segment(self, allow_split: bool = True) -> Segment | None:
        with self._lock:
            for s in self.segments:          # unfinished, unowned piece
                if not s.active and not s.done and (
                        s.end is not None or s.pos == s.start):
                    s.active = True
                    return s
            if not allow_split or not self.resumable:
                return None
            # Steal half of the biggest piece another connection is on.
            victim = max((s for s in self.segments if s.active),
                         key=lambda s: s.remaining, default=None)
            if victim is None or victim.remaining < 2 * MIN_SPLIT:
                return None
            mid = victim.pos + victim.remaining // 2
            new = Segment(mid, victim.end)
            victim.end = mid - 1
            new.active = True
            self.segments.append(new)
            return new

    def _worker(self, seg: Segment) -> None:
        retries = 0
        try:
            fh = open(self.part_path, "r+b", buffering=0)
        except OSError as e:
            self._fatal = e
            self._halt.set()
            with self._lock:
                seg.active = False
            return
        with fh:
            while seg is not None and not self._halt.is_set():
                if seg.done:
                    with self._lock:
                        seg.active = False
                    seg, retries = self._next_segment(), 0
                    continue
                try:
                    self._fetch(seg, fh)
                    retries = 0
                except FatalError as e:
                    self._fatal = e
                    self._halt.set()
                except urllib.error.HTTPError as e:
                    e.close()
                    if _is_fatal_http(e):
                        self._fatal = FatalError(f"HTTP {e.code} {e.reason}")
                        self._halt.set()
                    elif e.code in (429, 503) and self._shed_connection():
                        # "Too many requests": the server wants fewer
                        # connections. Hand this piece to the others.
                        log.info("server asked for fewer connections (%s) "
                                 "for %s", e.code, redact(self.url))
                        break
                    elif not self._backoff(retries := retries + 1,
                                           _retry_after(e)):
                        break
                except (OSError, http.client.HTTPException) as e:
                    if not self._backoff(retries := retries + 1):
                        break
            if seg is not None:
                with self._lock:
                    seg.active = False

    def _backoff(self, attempt: int, at_least: float = 0) -> bool:
        """Wait before retrying. False means give up on this connection
        (another connection will pick its segment up)."""
        if attempt > MAX_RETRIES:
            return False
        self._halt.wait(max(min(30, 2 ** attempt), at_least))
        return True

    def _shed_connection(self) -> bool:
        """True if this connection should close because others are still
        running (the server is rate-limiting us)."""
        with self._lock:
            return sum(1 for s in self.segments if s.active) > 1

    def _fetch(self, seg: Segment, fh) -> None:
        url = self.final_url or self.url
        headers = self._req_headers
        if self.resumable:
            r = _request(url, headers, seg.pos, seg.end)
            if r.status != 206:
                r.close()
                raise FatalError("server stopped accepting byte ranges")
            m = re.match(r"bytes\s+(\d+)-\d+/(\d+|\*)",
                         r.headers.get("Content-Range", "") or "")
            if not m or int(m.group(1)) != seg.pos:
                r.close()
                raise FatalError("server returned the wrong byte range")
            etag = r.headers.get("ETag")
            if (m.group(2) != "*" and int(m.group(2)) != self.size) or \
                    (etag and self.etag and etag != self.etag):
                r.close()
                raise FatalError("file changed on the server during the "
                                 "download; resume to start over")
        else:
            # No range support: every retry has to start from byte 0.
            with self._lock:
                seg.pos = seg.start = 0
            fh.truncate(0)
            r = _request(url, headers)

        with r:
            fh.seek(seg.pos)
            while not self._halt.is_set():
                with self._lock:
                    end = seg.end
                if end is not None and seg.pos > end:
                    return
                want = CHUNK if end is None else min(CHUNK, end - seg.pos + 1)
                data = r.read(want)
                if not data:
                    if end is None:            # unknown size: EOF = done
                        with self._lock:
                            seg.end = seg.pos - 1
                            self.size = seg.pos
                        return
                    raise ConnectionError("connection closed early")
                with self._lock:
                    end = seg.end              # may have shrunk (split)
                if end is not None:
                    data = data[:max(0, end - seg.pos + 1)]
                view = memoryview(data)
                while view:                    # raw writes may be partial
                    view = view[fh.write(view):]
                with self._lock:
                    seg.pos += len(data)
