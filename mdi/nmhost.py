"""Native-messaging host: the only way the browser extension reaches MDI.

Chrome/Edge/Brave start this script themselves (registered by
``python -m mdi.install``) and talk to it over stdin/stdout. Nothing listens
on the network, so no website or other program can impersonate either side,
and the browser only lets the extension ID pinned in the host manifest
connect.

The host validates the message, drops it (cookies encrypted) into the
per-user inbox folder, starts MDI if it isn't running, and exits.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import uuid

if __package__ in (None, ""):  # started as a script by the browser
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mdi.safety import (acquire_instance_lock, app_dir, check_url,  # noqa: E402
                        clean_headers, get_logger, protect, redact)

HOST_NAME = "com.mdi.downloader"
MAX_MESSAGE = 1 << 20
log = get_logger()


def inbox_dir() -> str:
    path = os.path.join(app_dir(), "inbox")
    os.makedirs(path, exist_ok=True)
    return path


def write_inbox(url: str, headers: dict | None = None,
                filename: str | None = None, directory: str | None = None) -> str:
    """Atomically drop a validated request where the GUI will pick it up."""
    directory = directory or inbox_dir()
    os.makedirs(directory, exist_ok=True)
    record = {"url": check_url(url), "headers": protect(clean_headers(headers)),
              "filename": (str(filename)[:255] if filename else None)}
    name = uuid.uuid4().hex
    tmp = os.path.join(directory, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f)
    final = os.path.join(directory, name + ".json")
    os.replace(tmp, final)
    return final


def read_message(stream) -> dict | None:
    raw = stream.read(4)
    if len(raw) < 4:
        return None
    (n,) = struct.unpack("<I", raw)
    if n > MAX_MESSAGE:
        raise ValueError("message too large")
    data = stream.read(n)
    if len(data) != n:
        raise ValueError("truncated message")
    msg = json.loads(data)
    if not isinstance(msg, dict):
        raise ValueError("message must be an object")
    return msg


def send_message(stream, obj: dict) -> None:
    raw = json.dumps(obj).encode()
    stream.write(struct.pack("<I", len(raw)) + raw)
    stream.flush()


def gui_running() -> bool:
    lock = acquire_instance_lock()
    if lock is None:
        return True
    lock.close()
    return False


def launch_gui() -> None:
    exe = sys.executable
    if sys.platform == "win32":
        w = os.path.join(os.path.dirname(exe), "pythonw.exe")
        exe = w if os.path.exists(w) else exe
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    flags = 0
    if sys.platform == "win32":
        # Detach and leave the browser's job object, which would otherwise
        # kill MDI when this host process exits.
        flags = (subprocess.DETACHED_PROCESS |
                 subprocess.CREATE_NEW_PROCESS_GROUP | 0x01000000)
    kwargs = dict(cwd=root, stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  close_fds=True)
    try:
        subprocess.Popen([exe, "-m", "mdi"], creationflags=flags, **kwargs)
    except OSError:  # job forbids breakaway: start it attached instead
        subprocess.Popen([exe, "-m", "mdi"],
                         creationflags=flags & ~0x01000000, **kwargs)


def handle(msg: dict) -> dict:
    kind = msg.get("type")
    if kind == "ping":
        return {"ok": True, "app": "MDI"}
    if kind != "add":
        return {"ok": False, "error": "unknown message"}
    try:
        url = check_url(msg.get("url"))
        write_inbox(url, {"Referer": msg.get("referer"),
                          "Cookie": msg.get("cookies")}, msg.get("filename"))
    except (ValueError, OSError) as e:
        log.warning("extension request rejected: %s", e)
        return {"ok": False, "error": str(e)}
    log.info("queued from browser: %s", redact(url))
    if not gui_running():
        launch_gui()
    return {"ok": True}


def expected_origin() -> str:
    from mdi.install import extension_id
    return f"chrome-extension://{extension_id()}/"


def main() -> int:
    # Browsers pass the calling extension's origin as the first argument.
    origin = sys.argv[1] if len(sys.argv) > 1 else ""
    if origin != expected_origin():
        log.warning("native host called by unexpected origin %r", origin)
        return 1
    try:
        msg = read_message(sys.stdin.buffer)
        if msg is not None:
            send_message(sys.stdout.buffer, handle(msg))
    except (ValueError, OSError) as e:
        log.warning("bad native message: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
