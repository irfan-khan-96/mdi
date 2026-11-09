"""Download list, queue and persistence.

Keeps at most ``max_parallel`` downloads running; the rest wait as
``queued``. The whole list (including every segment's position) is saved to
a JSON file every couple of seconds, which is what makes resume survive an
app restart or crash. Cookies are stored DPAPI-encrypted and forgotten as
soon as a download completes.
"""

from __future__ import annotations

import json
import os
import threading
import time

from .engine import COMPLETED, DOWNLOADING, PAUSED, QUEUED, Download
from .safety import (app_dir, check_url, clean_headers, get_logger,  # noqa: F401
                     protect, redact, unprotect)

log = get_logger()

DEFAULT_SETTINGS = {
    "download_dir": os.path.join(os.path.expanduser("~"), "Downloads"),
    "connections": 8,
    "max_parallel": 3,
}
MAX_INBOX_FILE = 1 << 20
STOP_WAIT = 40    # seconds; longer than the engine's socket timeout


def partial_files(d: Download) -> list[str]:
    """Half-finished files a download leaves on disk."""
    if d.kind == "media":
        from . import media
        return media.leftovers(d.media_stem)
    return [d.part_path] if d.part_path else []


def _delete(paths) -> None:
    for p in paths:
        if p and os.path.isfile(p):
            try:
                os.remove(p)
            except OSError as e:
                log.warning("couldn't delete %s: %s", p, e)


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


class Manager:
    def __init__(self, store_path: str | None = None,
                 inbox: str | None = None):
        self.store_path = store_path or os.path.join(app_dir(), "mdi.json")
        self.inbox = inbox or os.path.join(os.path.dirname(self.store_path),
                                           "inbox")
        self.settings = dict(DEFAULT_SETTINGS)
        self.downloads: list[Download] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._load()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="scheduler")
        self._thread.start()

    # ---- list operations ------------------------------------------------
    def add(self, url: str, headers: dict | None = None,
            filename: str | None = None, save_dir: str | None = None,
            connections: int | None = None, start: bool = True,
            source: str = "user") -> Download:
        """Raises ValueError for anything that isn't a plain http(s) URL."""
        url = check_url(url)
        d = Download(url, save_dir or self.settings["download_dir"],
                     connections or self.settings["connections"],
                     clean_headers(headers), filename)
        d.status = QUEUED if start else PAUSED
        with self._lock:
            self.downloads.append(d)
        log.info("added (%s) %s%s", source, redact(url),
                 " with cookies" if "Cookie" in d.headers else "")
        self.save()
        return d

    def resume(self, d: Download) -> None:
        d.resume()

    def pause(self, d: Download) -> None:
        d.pause()

    def remove(self, d: Download, delete_files: bool = False) -> None:
        d.pause()
        d.join(timeout=5)
        with self._lock:
            if d in self.downloads:
                self.downloads.remove(d)
        if delete_files:
            _delete(partial_files(d))
            if d.status == COMPLETED:
                _delete([d.path])
        self.save()

    # ---- undoable removal ----------------------------------------------
    # take() hides downloads from the list straight away; restore() puts
    # them back exactly where they were; discard() makes it final by
    # deleting the half-finished .part files (useless without the list
    # entry). Finished files are never touched.
    def take(self, items: list[Download]) -> list[tuple[int, Download, bool]]:
        taken = []
        with self._lock:
            for d in items:
                if d in self.downloads:
                    taken.append((self.downloads.index(d), d,
                                  d.status in (DOWNLOADING, QUEUED)))
            for _, d, _ in taken:
                self.downloads.remove(d)
                d.pause()
        if taken:
            log.info("removed %d download(s) (undoable)", len(taken))
            self.save()
        return taken

    def restore(self, taken: list[tuple[int, Download, bool]]) -> None:
        with self._lock:
            for index, d, was_active in sorted(taken, key=lambda t: t[0]):
                if d not in self.downloads:
                    self.downloads.insert(min(index, len(self.downloads)), d)
        for _, d, was_active in taken:
            if was_active:
                self.resume(d)   # re-queues itself once it has stopped
        if taken:
            log.info("restored %d download(s)", len(taken))
            self.save()

    def discard(self, taken: list[tuple[int, Download, bool]],
                delete_finished: bool = False,
                wait: float = STOP_WAIT) -> None:
        """Make a removal final. May wait for connections to stop, so call
        it off the UI thread. ``delete_finished`` also deletes completed
        files (Shift+Delete)."""
        deadline = time.time() + wait
        for _, d, _ in taken:
            if d in self.downloads:          # was restored meanwhile
                continue
            d.join(timeout=max(0.0, deadline - time.time()))
            if d.status == COMPLETED:
                if delete_finished:
                    _delete([d.path])
            else:
                _delete(partial_files(d))

    def find(self, url: str) -> Download | None:
        with self._lock:
            for d in self.downloads:
                if d.url == url and d.status != COMPLETED:
                    return d
        return None

    # ---- requests from the browser extension ----------------------------
    def process_inbox(self) -> int:
        """Import requests dropped by the native-messaging host."""
        try:
            names = sorted(n for n in os.listdir(self.inbox)
                           if n.endswith(".json"))
        except OSError:
            return 0
        added = 0
        for name in names[:50]:
            path = os.path.join(self.inbox, name)
            try:
                if os.path.getsize(path) > MAX_INBOX_FILE:
                    raise ValueError("request file too large")
                with open(path, encoding="utf-8") as f:
                    rec = json.load(f)
                self.add(rec.get("url"), unprotect(rec.get("headers"), {}),
                         rec.get("filename"), source="browser")
                added += 1
            except Exception as e:  # noqa: BLE001 - untrusted input
                log.warning("rejected browser request %s: %s", name, e)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
        return added

    # ---- scheduler ------------------------------------------------------
    def _loop(self) -> None:
        state = {"last_save": 0.0, "seen": {}}
        while not self._stop.wait(0.5):
            try:
                self._tick(state)
            except Exception:  # noqa: BLE001 - never let the queue die
                log.exception("scheduler error")

    def _tick(self, state: dict) -> None:
        seen = state["seen"]
        with self._lock:
            running = sum(1 for d in self.downloads if d.is_running)
            for d in self.downloads:
                if running >= self.settings["max_parallel"]:
                    break
                if d.status == QUEUED and not d.is_running:
                    d.start()
                    running += 1
            busy = any(d.status == DOWNLOADING for d in self.downloads)
            for d in self.downloads:          # log status changes
                if seen.get(d.id) != d.status:
                    if d.status == COMPLETED and d.id in seen:
                        log.info("completed %s -> %s", redact(d.url), d.path)
                    elif d.status == "error":
                        log.warning("failed %s: %s", redact(d.url), d.error)
                    seen[d.id] = d.status
        since = time.time() - state["last_save"]
        if since > 10 or (busy and since > 2):
            self.save()                # a failed save is retried next time
            state["last_save"] = time.time()

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            running = [d for d in self.downloads if d.is_running]
        for d in running:
            d.pause()
        deadline = time.time() + 5
        for d in running:
            d.join(timeout=max(0.0, deadline - time.time()))
            if d.status in (PAUSED, DOWNLOADING):
                d.status = QUEUED   # pick up again on next launch
        self.save()

    # ---- persistence ----------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self.store_path, encoding="utf-8") as f:
                data = json.load(f)
        except OSError:
            return                              # first run
        except ValueError:
            data = None
        if not isinstance(data, dict):
            self._set_aside()
            return
        s = data.get("settings")
        s = s if isinstance(s, dict) else {}
        records = data.get("downloads")
        records = records if isinstance(records, list) else []
        if isinstance(s.get("download_dir"), str) and s["download_dir"]:
            self.settings["download_dir"] = s["download_dir"]
        self.settings["connections"] = _clamp(s.get("connections"), 1, 32, 8)
        self.settings["max_parallel"] = _clamp(s.get("max_parallel"), 1, 10, 3)
        for rec in records:
            try:
                rec = dict(rec)
                check_url(rec["url"])
                rec["headers"] = clean_headers(
                    unprotect(rec.get("headers"), {}))
                self.downloads.append(Download.from_dict(rec))
            except (KeyError, TypeError, ValueError) as e:
                log.warning("skipped corrupt saved download: %s", e)

    def _set_aside(self) -> None:
        """Keep an unreadable state file for recovery instead of letting
        the next save overwrite it."""
        bad = self.store_path + ".damaged"
        try:
            os.replace(self.store_path, bad)
            log.warning("state file was unreadable; kept as %s", bad)
        except OSError:
            pass

    def save(self) -> bool:
        try:
            self._save()
            return True
        except OSError as e:
            log.warning("couldn't save the download list: %s", e)
            return False

    def _save(self) -> None:
        with self._lock:  # also serialises concurrent writers
            records = []
            for d in self.downloads:
                if d.status == COMPLETED and d.headers:
                    d.headers = {}           # cookies no longer needed
                rec = d.to_dict()
                rec["headers"] = protect(d.headers)
                records.append(rec)
            data = {"settings": self.settings, "downloads": records}
            tmp = self.store_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self.store_path)
