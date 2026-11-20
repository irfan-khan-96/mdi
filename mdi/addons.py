"""Optional helpers MDI downloads when you ask for them ("Get HD video
support" in the status bar):

- ffmpeg: merges YouTube's separate HD video and audio streams into one
  .mp4, and converts "Audio only" to MP3. From yt-dlp's own FFmpeg builds.
- Deno: the JavaScript engine yt-dlp uses by default to pass YouTube's
  JavaScript check (needed for some videos, such as ones made for kids).
  QuickJS is smaller but was about 10 s slower per video, so Deno it is.

Both come from their projects' official GitHub releases. Each file must
match the SHA-256 digest GitHub lists for it (Deno is also pinned to one
known version and hash in this file). They are saved in
%LOCALAPPDATA%\\MDI\\tools, which MDI puts first on its own PATH, so
yt-dlp finds them without any system-wide change.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile

from .safety import app_dir, get_logger

log = get_logger()

API = "https://api.github.com/repos/{repo}/releases/{which}"


class Addon:
    def __init__(self, key, title, repo, which, asset, size_mb, max_mb,
                 files, extract=None, sha256=None, save_as=None):
        self.key, self.title, self.repo, self.which = key, title, repo, which
        self.asset, self.size_mb, self.max_bytes = asset, size_mb, max_mb << 20
        self.files = files                  # exe names that must end up there
        self.extract = re.compile(extract) if extract else None
        self.sha256, self.save_as = sha256, save_as


ADDONS = {
    "ffmpeg": Addon(
        "ffmpeg", "ffmpeg (HD video and MP3)", "yt-dlp/FFmpeg-Builds",
        "latest", "ffmpeg-master-latest-win64-gpl-shared.zip", 86, 250,
        files=("ffmpeg.exe", "ffprobe.exe"),
        # Only the two programs and their DLLs; never ffplay or docs.
        extract=r"^[^/]+/bin/(ffmpeg\.exe|ffprobe\.exe|[^/]+\.dll)$"),
    "deno": Addon(
        "deno", "Deno (YouTube's JavaScript check)", "denoland/deno",
        "tags/v2.9.8", "deno-x86_64-pc-windows-msvc.zip", 41, 150,
        files=("deno.exe",), extract=r"^deno\.exe$",
        sha256="cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf"),
}

JS_RUNTIMES = {"deno": "deno", "node": "node", "bun": "bun", "quickjs": "qjs"}


def tools_dir() -> str:
    base = os.environ.get("LOCALAPPDATA")
    return os.path.join(base, "MDI", "tools") if base else \
        os.path.join(app_dir(), "tools")


def add_to_path() -> None:
    """Make downloaded helpers visible to shutil.which and yt-dlp, for this
    process only. Safe to call more than once."""
    d = tools_dir()
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if d not in parts:
        os.environ["PATH"] = os.pathsep.join([d] + [p for p in parts if p])


def has_js_runtime() -> bool:
    return any(shutil.which(exe) for exe in JS_RUNTIMES.values())


def missing() -> list[str]:
    """Helpers MDI could download that aren't available yet."""
    need = []
    if not shutil.which("ffmpeg"):
        need.append("ffmpeg")
    if not has_js_runtime():
        need.append("deno")
    return need


def download_size_mb(keys) -> int:
    return sum(ADDONS[k].size_mb for k in keys)


# ---------------------------------------------------------------- install

def _get(url: str, limit: int, progress=None, accept=None) -> bytes:
    import urllib.request
    from .engine import OPENER, TIMEOUT, USER_AGENT
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    chunks, got = [], 0
    with OPENER.open(urllib.request.Request(url, headers=headers),
                     timeout=TIMEOUT) as r:
        total = int(r.headers.get("Content-Length") or 0)
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            got += len(block)
            if got > limit:
                raise ValueError("download is larger than expected")
            chunks.append(block)
            if progress and total:
                progress(got, total)
    return b"".join(chunks)


def release_asset(addon: Addon) -> tuple[str, str]:
    """(download URL, expected sha256) for the add-on's file."""
    rel = json.loads(_get(API.format(repo=addon.repo, which=addon.which),
                          1 << 20, accept="application/vnd.github+json"))
    for a in rel.get("assets", []):
        if a.get("name") == addon.asset:
            url = a.get("browser_download_url", "")
            digest = str(a.get("digest") or "")
            if not url.startswith("https://github.com/" + addon.repo + "/"):
                raise ValueError(f"unexpected download address for {addon.key}")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError(f"GitHub gave no checksum for {addon.asset}")
            want = digest.split(":", 1)[1]
            if addon.sha256 and want != addon.sha256:
                raise ValueError(f"{addon.asset} isn't the version MDI expects")
            return url, want
    raise ValueError(f"{addon.asset} not found in {addon.repo}'s release")


def _unpack(addon: Addon, data: bytes, dest: str) -> None:
    """Write the add-on's files into ``dest`` (a fresh staging folder)."""
    if not addon.extract:
        with open(os.path.join(dest, addon.save_as), "wb") as f:
            f.write(data)
        return
    with tempfile.TemporaryFile() as tmp:
        tmp.write(data)
        tmp.seek(0)
        with zipfile.ZipFile(tmp) as z:
            for info in z.infolist():
                if info.is_dir() or not addon.extract.match(info.filename):
                    continue
                name = os.path.basename(info.filename)   # no paths from zip
                with z.open(info) as src, \
                        open(os.path.join(dest, name), "wb") as out:
                    shutil.copyfileobj(src, out)


def install(keys, progress=None) -> None:
    """Download, verify and install the given add-ons. ``progress(text)``
    gets short status lines. Raises on any failure; nothing half-installed
    is left behind."""
    dest = tools_dir()
    os.makedirs(dest, exist_ok=True)
    for n, key in enumerate(keys, 1):
        addon = ADDONS[key]
        say = (lambda t, a=addon, n=n: progress and progress(
            f"{a.title.split(' (')[0]} ({n}/{len(keys)}): {t}"))
        say("checking…")
        url, want = release_asset(addon)
        data = _get(url, addon.max_bytes,
                    lambda got, total: say(f"{got * 100 // total}%"))
        if hashlib.sha256(data).hexdigest() != want:
            raise ValueError(f"{addon.asset} failed its checksum; not installed")
        say("installing…")
        stage = tempfile.mkdtemp(dir=dest, prefix=".stage-")
        try:
            _unpack(addon, data, stage)
            names = os.listdir(stage)
            for exe in addon.files:
                if exe not in names:
                    raise ValueError(f"{exe} is missing from {addon.asset}")
            for name in names:
                os.replace(os.path.join(stage, name), os.path.join(dest, name))
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        log.info("installed %s from %s", key, url)
    add_to_path()
