"""MDI updates itself (the installed app only, not a source checkout).

Once a week, in the background, MDI asks GitHub for its own latest
release. If it's newer, a link appears in the status bar. Clicking it
(after you confirm) downloads that release's installer and checks it twice:
against the SHA-256 digest GitHub lists for the file and against the
release's SHA256SUMS.txt. MDI then saves your download list, starts the
installer in silent mode and closes. The installer replaces the app files
and opens MDI again, which picks up where it left off.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

from . import __version__
from .safety import app_dir, get_logger

log = get_logger()

REPO = "irfan-khan-96/mdi"
API = f"https://api.github.com/repos/{REPO}/releases/"
CHECK_EVERY = 7 * 86400
RETRY_AFTER_ERROR = 86400
MAX_INSTALLER = 150 << 20
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

latest_known: str | None = None      # newer version found this session


def enabled() -> bool:
    """Only the installed MDI.exe updates itself."""
    return bool(getattr(sys, "frozen", False)) and sys.platform == "win32"


def vtuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def _state_path() -> str:
    return os.path.join(app_dir(), "self_update.json")


def load_state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, _state_path())


def available() -> str | None:
    """A newer MDI version known from this or an earlier check."""
    v = latest_known or load_state().get("latest")
    return v if isinstance(v, str) and vtuple(v) > vtuple(__version__) \
        else None


def _release(which: str) -> dict:
    from .addons import _get
    return json.loads(_get(API + which, 1 << 20,
                           accept="application/vnd.github+json"))


def check(force: bool = False) -> str | None:
    """Ask GitHub for the latest release (at most weekly). Returns the
    newer version, if any. Never raises; problems are logged."""
    global latest_known
    state = load_state()
    now = time.time()
    if not force and now - state.get("checked", 0) < (
            RETRY_AFTER_ERROR if state.get("error") else CHECK_EVERY):
        return available()
    try:
        m = _TAG.match(str(_release("latest").get("tag_name", "")))
        if not m:
            raise ValueError("unexpected release name")
        latest = ".".join(m.groups())
        state.update(checked=now, latest=latest, error=None)
        if vtuple(latest) > vtuple(__version__):
            latest_known = latest
            log.info("MDI %s is available", latest)
    except Exception as e:  # noqa: BLE001
        state.update(checked=now, error=str(e)[:300])
        log.warning("checking for an MDI update failed: %s", e)
    try:
        _save_state(state)
    except OSError:
        pass
    return available()


def check_in_background() -> None:
    import threading
    threading.Thread(target=check, daemon=True, name="mdi-update").start()


def updates_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or app_dir()
    return os.path.join(base, "MDI", "updates")


def cleanup() -> None:
    """Delete installers left from earlier updates."""
    d = updates_dir()
    try:
        for n in os.listdir(d):
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass
    except OSError:
        pass


def sums_hash(sums: str, name: str) -> str:
    for line in sums.splitlines():
        parts = line.strip().split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and \
                re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            return parts[0]
    raise ValueError(f"no checksum for {name} in SHA256SUMS.txt")


def download(version: str, progress=None) -> str:
    """Download and verify MDI-Setup-<version>.exe. Returns its path."""
    import hashlib
    from .addons import _get
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("bad version")
    name = f"MDI-Setup-{version}.exe"
    prefix = f"https://github.com/{REPO}/releases/download/v{version}/"
    assets = {a.get("name"): a for a in
              _release(f"tags/v{version}").get("assets", [])}
    setup, sums = assets.get(name), assets.get("SHA256SUMS.txt")
    if not setup or not sums:
        raise ValueError("the release is missing its installer or checksum")
    for a in (setup, sums):
        if a.get("browser_download_url") != prefix + a["name"]:
            raise ValueError("unexpected download address")
    digest = str(setup.get("digest") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("GitHub gave no checksum for the installer")
    want = digest.split(":", 1)[1]
    listed = sums_hash(_get(sums["browser_download_url"], 1 << 16)
                       .decode("utf-8", "replace"), name)
    if listed != want:
        raise ValueError("the release's checksums don't agree")
    data = _get(setup["browser_download_url"], MAX_INSTALLER,
                lambda got, total: progress and progress(got * 100 // total))
    if hashlib.sha256(data).hexdigest() != want:
        raise ValueError("the installer failed its checksum; not used")
    os.makedirs(updates_dir(), exist_ok=True)
    path = os.path.join(updates_dir(), name)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    log.info("downloaded and verified %s", name)
    return path


# Silent install; the installer closes anything still running and, because
# of /RELAUNCH=1, opens MDI again when it's done (see tools/installer.iss).
INSTALLER_ARGS = ["/SILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART",
                  "/CLOSEAPPLICATIONS", "/RELAUNCH=1"]


def launch(path: str) -> None:
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | \
            subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([path, *INSTALLER_ARGS], creationflags=flags,
                     close_fds=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log.info("started the installer for the update")
