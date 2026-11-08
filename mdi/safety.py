"""Security helpers shared by the app, the CLI and the native-messaging host.

- Input validation for anything that arrives from outside (URLs, headers).
- DPAPI encryption for cookies kept on disk (Windows, per-user key).
- Mark-of-the-Web, so Windows SmartScreen treats downloads like browser ones.
- Single-instance lock, app folder, and a log file with URLs redacted.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import urllib.parse
from logging.handlers import RotatingFileHandler

MAX_URL = 8192
MAX_HEADER = 16 * 1024
ALLOWED_HEADERS = {"referer": "Referer", "cookie": "Cookie"}
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def app_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    path = os.path.join(base, "MDI")
    os.makedirs(path, exist_ok=True)
    return path


# ------------------------------------------------------------- validation

def check_url(url) -> str:
    """Return a clean http(s) URL or raise ValueError."""
    if not isinstance(url, str):
        raise ValueError("URL must be text")
    url = url.strip()
    if not url or len(url) > MAX_URL:
        raise ValueError("URL is empty or too long")
    if _CTRL.search(url) or " " in url:
        raise ValueError("URL contains spaces or control characters")
    p = urllib.parse.urlsplit(url)
    if p.scheme.lower() not in ("http", "https"):
        raise ValueError("only http:// and https:// links are supported")
    if not p.hostname:
        raise ValueError("URL has no host name")
    return url


def clean_headers(headers) -> dict:
    """Keep only Referer and Cookie, as single-line, size-capped strings."""
    out = {}
    for k, v in (headers or {}).items():
        name = ALLOWED_HEADERS.get(str(k).lower())
        if not name or v is None:
            continue
        v = _CTRL.sub("", str(v)).strip()[:MAX_HEADER]
        if not v:
            continue
        if name == "Referer":
            try:
                v = check_url(v)
            except ValueError:
                continue
        out[name] = v
    return out


def redact(url: str) -> str:
    """URL without credentials, query or fragment: safe to log."""
    try:
        p = urllib.parse.urlsplit(url)
        host = p.hostname or ""
        if p.port:
            host += f":{p.port}"
        tail = "?…" if p.query else ""
        return urllib.parse.urlunsplit((p.scheme, host, p.path, "", "")) + tail
    except ValueError:
        return "<invalid url>"


def same_host(a: str, b: str) -> bool:
    try:
        return (urllib.parse.urlsplit(a).hostname or "").lower() == \
               (urllib.parse.urlsplit(b).hostname or "").lower()
    except ValueError:
        return False


def is_downgrade(a: str, b: str) -> bool:
    return (urllib.parse.urlsplit(a).scheme.lower() == "https" and
            urllib.parse.urlsplit(b).scheme.lower() == "http")


def credentials_for(original_url: str, target_url: str, headers: dict) -> dict:
    """Headers safe to send to ``target_url``. Cookies were captured for the
    original host, so they never go to another host or over plain HTTP."""
    if same_host(original_url, target_url) and \
            not is_downgrade(original_url, target_url):
        return dict(headers)
    return {k: v for k, v in headers.items()
            if k.lower() not in ("cookie", "authorization")}


# ------------------------------------------------------ secrets at rest

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _UI_FORBIDDEN = 0x1

    def _dpapi(data: bytes, protect: bool) -> bytes:
        buf = ctypes.create_string_buffer(data, len(data))
        blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        fn = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        if not fn(ctypes.byref(blob_in), None, None, None, None,
                  _UI_FORBIDDEN, ctypes.byref(blob_out)):
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def protect(obj) -> dict | None:
    """Encrypt a JSON-able value for this Windows user (DPAPI). Elsewhere
    it's stored as-is, relying on the user-only app folder."""
    if not obj:
        return None
    raw = json.dumps(obj).encode()
    if sys.platform == "win32":
        return {"dpapi": base64.b64encode(_dpapi(raw, True)).decode()}
    return {"plain": obj}


def unprotect(blob, default=None):
    if not isinstance(blob, dict):
        return default
    try:
        if "dpapi" in blob and sys.platform == "win32":
            return json.loads(_dpapi(base64.b64decode(blob["dpapi"]), False))
        if "plain" in blob:
            return blob["plain"]
    except (OSError, ValueError):
        pass
    return default


# --------------------------------------------------------- downloaded files

_BIDI = re.compile("[​-‏‪-‮⁦-⁩﻿]")


def strip_spoofing(name: str) -> str:
    """Remove invisible / right-to-left override characters that can make
    ``evil‮fdp.exe`` display as ``evilexe.pdf``."""
    return _BIDI.sub("", name)


def mark_of_the_web(path: str, url: str, referrer: str | None = None) -> None:
    """Tag a file as downloaded from the Internet (Zone 3), exactly like
    browsers do, so SmartScreen / Office Protected View still apply."""
    if sys.platform != "win32" or not path:
        return
    lines = ["[ZoneTransfer]", "ZoneId=3"]
    if referrer:
        lines.append("ReferrerUrl=" + redact(referrer).removesuffix("?…"))
    lines.append("HostUrl=" + redact(url).removesuffix("?…"))
    try:
        with open(path + ":Zone.Identifier", "w", encoding="utf-8") as f:
            f.write("\r\n".join(lines) + "\r\n")
    except OSError:
        pass  # e.g. FAT32 / network shares without alternate streams


# ------------------------------------------------------- single instance

def acquire_instance_lock(path: str | None = None):
    """Exclusive lock held for the app's lifetime. Returns a handle to keep
    open, or None if another instance holds it."""
    path = path or os.path.join(app_dir(), "mdi.lock")
    fh = open(path, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fh
    except OSError:
        fh.close()
        return None


# ---------------------------------------------------------------- logging

def get_logger() -> logging.Logger:
    log = logging.getLogger("mdi")
    if not log.handlers:
        log.setLevel(logging.INFO)
        try:
            h = RotatingFileHandler(os.path.join(app_dir(), "mdi.log"),
                                    maxBytes=1 << 20, backupCount=3,
                                    encoding="utf-8")
            h.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(h)
        except OSError:
            log.addHandler(logging.NullHandler())
        log.propagate = False
    return log
