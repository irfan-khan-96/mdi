"""Connect the browser extension to MDI (Chrome, Edge, Brave, Chromium).

    python -m mdi.install            register the native-messaging host
    python -m mdi.install --remove   unregister it

Writes a host manifest + launcher into %APPDATA%\\MDI\\nmhost (user-only
folder) and points the browsers at it under HKEY_CURRENT_USER. No admin
rights, nothing system-wide. Only the MDI extension's ID may connect.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys

from .safety import app_dir

HOST_NAME = "com.mdi.downloader"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BROWSER_KEYS = [
    r"Software\Google\Chrome\NativeMessagingHosts",
    r"Software\Microsoft\Edge\NativeMessagingHosts",
    r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
    r"Software\Chromium\NativeMessagingHosts",
]


def extension_id() -> str:
    """Chrome derives an extension's ID from the public key in its
    manifest: first 128 bits of SHA-256, hex digits mapped to a-p."""
    with open(os.path.join(ROOT, "extension", "manifest.json"),
              encoding="utf-8") as f:
        key = json.load(f)["key"]
    digest = hashlib.sha256(base64.b64decode(key)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def host_dir() -> str:
    path = os.path.join(app_dir(), "nmhost")
    os.makedirs(path, exist_ok=True)
    return path


def manifest_path() -> str:
    return os.path.join(host_dir(), HOST_NAME + ".json")


def install() -> None:
    if sys.platform != "win32":
        sys.exit("Automatic registration is Windows-only for now.")
    import winreg

    script = os.path.join(ROOT, "mdi", "nmhost.py")
    launcher = os.path.join(host_dir(), "mdi_host.bat")
    # -I: isolated mode (ignores PYTHON* env vars, user site, current dir).
    with open(launcher, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(f'@echo off\n"{sys.executable}" -I "{script}" %*\n')
    manifest = {
        "name": HOST_NAME,
        "description": "MDI Downloader",
        "path": launcher,
        "type": "stdio",
        "allowed_origins": [f"chrome-extension://{extension_id()}/"],
    }
    with open(manifest_path(), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    for base in BROWSER_KEYS:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              base + "\\" + HOST_NAME) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, manifest_path())
    print(f"Registered for Chrome, Edge, Brave and Chromium.\n"
          f"Extension ID allowed: {extension_id()}")


def uninstall() -> None:
    if sys.platform == "win32":
        import winreg
        for base in BROWSER_KEYS:
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                                 base + "\\" + HOST_NAME)
            except OSError:
                pass
    for name in (HOST_NAME + ".json", "mdi_host.bat"):
        try:
            os.remove(os.path.join(host_dir(), name))
        except OSError:
            pass
    from .winshell import unregister_app_id
    unregister_app_id()
    print("Unregistered.")


def is_installed() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            BROWSER_KEYS[0] + "\\" + HOST_NAME) as k:
            path = winreg.QueryValue(k, None)
        return os.path.exists(path)
    except OSError:
        return False


if __name__ == "__main__":
    uninstall() if "--remove" in sys.argv else install()
