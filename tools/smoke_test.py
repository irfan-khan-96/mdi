"""Smoke-test a built installer before it's published.

    python tools/smoke_test.py dist/MDI-Setup-1.4.2.exe

1. Installs it silently into a temporary folder.
2. Checks the installed MDI.exe version, the license file and the
   "Installed apps" entry.
3. Starts the installed MDI with its own temporary data folders and has it
   download a small file from this repository; checks the file arrived
   intact and is tagged as downloaded from the Internet.
4. Uninstalls and checks nothing is left behind.

It refuses to run if MDI is already installed for this Windows user,
because both installs would share one "Installed apps" entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import winreg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mdi import __version__  # noqa: E402

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
SAMPLE = "mdi/assets/icon.png"      # binary, so line endings can't differ


def installed_entry() -> dict | None:
    """The "Installed apps" entry for MDI Downloader, if any."""
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL)
    except OSError:
        return None
    with root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                    if winreg.QueryValueEx(k, "DisplayName")[0] == \
                            "MDI Downloader":
                        return {n: winreg.QueryValueEx(k, n)[0]
                                for n in ("DisplayVersion", "InstallLocation")}
            except OSError:
                continue
    return None


def file_version(path: str) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-Item -LiteralPath '{path}').VersionInfo.ProductVersion"],
        capture_output=True, text=True, timeout=60)
    return out.stdout.strip()


def sha(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check(ok: bool, what: str) -> None:
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    if not ok:
        raise SystemExit(1)


def main(setup: str) -> int:
    setup = os.path.abspath(setup)
    check(os.path.isfile(setup), f"installer exists: {setup}")
    if installed_entry():
        print("MDI Downloader is already installed for this user; this test "
              "would take over its 'Installed apps' entry. Uninstall it or "
              "run the test on another account (CI does).")
        return 2

    tmp = tempfile.mkdtemp(prefix="mdi-smoke-")
    app, out = os.path.join(tmp, "app"), os.path.join(tmp, "downloads")
    roaming, local = os.path.join(tmp, "roaming"), os.path.join(tmp, "local")
    for d in (out, os.path.join(roaming, "MDI"), local):
        os.makedirs(d)
    proc = None
    try:
        # 1. install
        r = subprocess.run([setup, "/VERYSILENT", "/SUPPRESSMSGBOXES",
                            "/NORESTART", f"/DIR={app}"], timeout=300)
        check(r.returncode == 0, "silent install finished")
        exe = os.path.join(app, "MDI.exe")
        check(os.path.isfile(exe), "MDI.exe installed")

        # 2. what got installed
        check(file_version(exe) == __version__,
              f"MDI.exe reports version {__version__}")
        check(os.path.isfile(os.path.join(app, "LICENSE.txt")),
              "license installed")
        entry = installed_entry() or {}
        check(entry.get("DisplayVersion") == __version__,
              "'Installed apps' entry shows the version")

        # 3. the installed app downloads a file
        with open(os.path.join(roaming, "MDI", "mdi.json"), "w") as f:
            json.dump({"settings": {"download_dir": out}, "downloads": []}, f)
        # The same commit on both sides: GitHub's copy of the file at that
        # commit vs git's own copy (the working tree may hold newer edits).
        ref = os.environ.get("GITHUB_SHA") or subprocess.run(
            ["git", "rev-parse", "origin/main"], cwd=ROOT, capture_output=True,
            text=True).stdout.strip() or "main"
        expected = hashlib.sha256(subprocess.run(
            ["git", "show", f"{ref}:{SAMPLE}"], cwd=ROOT,
            capture_output=True).stdout).hexdigest()
        url = f"https://raw.githubusercontent.com/irfan-khan-96/mdi/{ref}/{SAMPLE}"
        env = dict(os.environ, APPDATA=roaming, LOCALAPPDATA=local)
        log = os.path.join(roaming, "MDI", "mdi.log")
        started = time.time()
        proc = subprocess.Popen([exe, url], env=env)
        result = ""
        while time.time() - started < 120 and not result:
            time.sleep(1)
            if proc.poll() is not None:
                break
            if os.path.exists(log):
                with open(log, encoding="utf-8") as f:
                    text = f.read()
                result = "completed" if " completed " in text else \
                    "failed" if " failed " in text else ""
        check(proc.poll() is None, "installed MDI starts and keeps running")
        check(result == "completed",
              f"it downloaded a file ({time.time() - started:.0f} s)")
        got = os.path.join(out, os.path.basename(SAMPLE))
        check(os.path.isfile(got) and sha(got) == expected,
              "the file arrived intact")
        check(os.path.exists(got + ":Zone.Identifier"),
              "it's tagged as downloaded from the Internet")
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait(15)
        # 4. uninstall
        uninstaller = os.path.join(app, "unins000.exe")
        if os.path.isfile(uninstaller):
            subprocess.run([uninstaller, "/VERYSILENT", "/SUPPRESSMSGBOXES"],
                           timeout=300)
            time.sleep(3)
    check(not os.path.exists(os.path.join(app, "MDI.exe")),
          "uninstall removed the app")
    check(installed_entry() is None, "uninstall removed the 'Installed apps' entry")
    shutil.rmtree(tmp, ignore_errors=True)
    print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
