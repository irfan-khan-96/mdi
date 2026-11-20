"""Keeps yt-dlp current without reinstalling MDI.

Video sites change often, and an old yt-dlp stops working. Once a week MDI
asks GitHub for yt-dlp's latest release. If it's newer than the copy in
use, MDI downloads the official single-file build (a zip of yt-dlp plus
its matching YouTube challenge scripts), checks it against the SHA-256
checksum published with the same release, and saves it in
%APPDATA%\\MDI\\yt-dlp. On the next start MDI imports yt-dlp from that zip.
If it fails to load, MDI falls back to the bundled copy and never tries
that version again.

Everything runs in the background and only writes to the log.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import sys
import tempfile
import time
import zipfile

from .safety import app_dir, get_logger

log = get_logger()

API_LATEST = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
DOWNLOAD = "https://github.com/yt-dlp/yt-dlp/releases/download/{tag}/{name}"
ASSET = "yt-dlp"                   # the platform-independent zipapp
CHECK_EVERY = 7 * 86400
RETRY_AFTER_ERROR = 86400
MAX_ZIP = 50 << 20
MAX_TEXT = 1 << 20
_TAG = re.compile(r"^\d{4}\.\d{2}\.\d{2}(\.\d+)?$")
_VERSION_LINE = re.compile(rb"__version__\s*=\s*['\"]([^'\"]+)['\"]")

active_version: str | None = None    # set by activate() when the zip is used


def folder() -> str:
    return os.path.join(app_dir(), "yt-dlp")


def zip_path() -> str:
    return os.path.join(folder(), "yt-dlp.zip")


def _state_path() -> str:
    return os.path.join(folder(), "state.json")


def load_state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    os.makedirs(folder(), exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, _state_path())


def vtuple(v) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return (0,)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def installed_version() -> str | None:
    """Version of the yt-dlp that ships with MDI (or pip installed),
    without importing it."""
    try:
        return importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        return None


# ------------------------------------------------------------- start-up

def activate() -> str | None:
    """Put the downloaded yt-dlp first on the import path if it is newer
    than the installed one, intact, and not known to be broken. Must run
    before ``import yt_dlp``. Returns the version activated, if any."""
    global active_version
    state = load_state()
    version, digest = state.get("version"), state.get("sha256")
    path = zip_path()
    if not version or not digest or not os.path.isfile(path):
        return None
    if version in state.get("bad", []):
        return None
    if vtuple(version) <= vtuple(installed_version() or "0"):
        return None
    try:
        if _sha256(path) != digest:
            log.warning("downloaded yt-dlp is damaged; using the built-in one")
            return None
    except OSError:
        return None
    sys.path.insert(0, path)
    active_version = version
    log.info("using updated yt-dlp %s", version)
    return version


def deactivate() -> None:
    """The downloaded copy failed to import: stop using it for good and let
    the caller import the built-in one."""
    global active_version
    path = zip_path()
    while path in sys.path:
        sys.path.remove(path)
    sys.path_importer_cache.pop(path, None)
    for name in [m for m in sys.modules
                 if m == "yt_dlp" or m.startswith(("yt_dlp.", "yt_dlp_ejs"))]:
        del sys.modules[name]
    state = load_state()
    if active_version:
        state["bad"] = sorted(set(state.get("bad", [])) | {active_version})
        log.warning("yt-dlp %s failed to load; not using it again",
                    active_version)
    try:
        _save_state(state)
    except OSError:
        pass
    active_version = None


# ------------------------------------------------------------- updating

def _get(url: str, limit: int, accept: str | None = None) -> bytes:
    import urllib.request
    from .engine import OPENER, TIMEOUT, USER_AGENT
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    with OPENER.open(urllib.request.Request(url, headers=headers),
                     timeout=TIMEOUT) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url} is larger than expected")
    return data


def latest_tag() -> str:
    data = json.loads(_get(API_LATEST, MAX_TEXT, "application/vnd.github+json"))
    tag = str(data.get("tag_name", ""))
    if not _TAG.match(tag):
        raise ValueError(f"unexpected release name {tag!r}")
    return tag


def expected_hash(sums: str, name: str = ASSET) -> str:
    """Hash for ``name`` from a SHA2-256SUMS file (``<hex> [*]<name>``)."""
    for line in sums.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and \
                re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            return parts[0]
    raise ValueError(f"no checksum for {name} in SHA2-256SUMS")


def check_zip(path: str, tag: str) -> None:
    """The file must be an intact yt-dlp zip of exactly this version."""
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:
            raise ValueError("downloaded yt-dlp is corrupt")
        names = set(z.namelist())
        if "yt_dlp/__init__.py" not in names:
            raise ValueError("download is not yt-dlp")
        m = _VERSION_LINE.search(z.read("yt_dlp/version.py"))
    if not m or m.group(1).decode() != tag:
        raise ValueError("downloaded yt-dlp has the wrong version")


def update(current: str | None, force: bool = False) -> str | None:
    """Download a newer yt-dlp if there is one. Returns the new version, or
    None if already up to date / not due. Raises on failure."""
    state = load_state()
    now = time.time()
    if not force and now - state.get("checked", 0) < (
            RETRY_AFTER_ERROR if state.get("error") else CHECK_EVERY):
        return None
    try:
        tag = latest_tag()
        have = max((v for v in (current, state.get("version"),
                                installed_version()) if v),
                   key=vtuple, default="0")
        if vtuple(tag) <= vtuple(have) or tag in state.get("bad", []):
            state.update(checked=now, error=None)
            _save_state(state)
            return None
        sums = _get(DOWNLOAD.format(tag=tag, name="SHA2-256SUMS"),
                    MAX_TEXT).decode("utf-8", "replace")
        want = expected_hash(sums)
        os.makedirs(folder(), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder(), suffix=".download")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(_get(DOWNLOAD.format(tag=tag, name=ASSET), MAX_ZIP))
            got = _sha256(tmp)
            if got != want:
                raise ValueError("checksum mismatch: download rejected")
            check_zip(tmp, tag)
            os.replace(tmp, zip_path())
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        state.update(version=tag, sha256=want, checked=now, error=None)
        _save_state(state)
        log.info("yt-dlp updated to %s (used from the next start)", tag)
        return tag
    except Exception as e:
        state.update(checked=now, error=str(e)[:300])
        try:
            _save_state(state)
        except OSError:
            pass
        log.warning("yt-dlp update failed: %s", e)
        raise


def update_in_background(current: str | None) -> None:
    import threading

    def run():
        try:
            update(current)
        except Exception:  # noqa: BLE001 - already logged
            pass
    threading.Thread(target=run, daemon=True, name="yt-dlp-update").start()
