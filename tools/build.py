"""Build MDI.exe and (if Inno Setup is installed) the installer.

    py -m venv build/venv
    build/venv/Scripts/python -m pip install pyinstaller yt-dlp yt-dlp-ejs certifi
    build/venv/Scripts/python tools/build.py

Output:
    dist/MDI/MDI.exe                    the app (folder build: starts fast,
                                        fewer antivirus false alarms)
    dist/MDI-Setup-<version>.exe        per-user installer, no admin needed
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from mdi import __version__  # noqa: E402

VERSION_INFO = """\
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({t}), prodvers=({t}), mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'MDI Downloader'),
      StringStruct('FileDescription', 'MDI Downloader'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('ProductVersion', '{v}'),
      StringStruct('InternalName', 'MDI'),
      StringStruct('OriginalFilename', 'MDI.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""


def find_iscc() -> str | None:
    for p in (shutil.which("iscc"),
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
              r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
              r"C:\Program Files\Inno Setup 6\ISCC.exe"):
        if p and os.path.isfile(p):
            return p
    return None


def main() -> int:
    import PyInstaller.__main__

    work = os.path.join(ROOT, "build")
    os.makedirs(work, exist_ok=True)
    nums = [int(x) for x in __version__.split(".")] + [0]
    ver_file = os.path.join(work, "version_info.txt")
    with open(ver_file, "w", encoding="utf-8") as f:
        f.write(VERSION_INFO.format(t=", ".join(map(str, nums[:4])),
                                    v=__version__))

    PyInstaller.__main__.run([
        os.path.join(ROOT, "tools", "mdi_app.py"),
        "--name", "MDI",
        "--windowed",                       # no console window
        "--onedir",
        "--noconfirm", "--clean",
        "--icon", os.path.join(ROOT, "mdi", "assets", "icon.ico"),
        "--add-data", os.path.join(ROOT, "mdi", "assets") + os.pathsep +
        "mdi/assets",
        "--version-file", ver_file,
        "--paths", ROOT,
        "--collect-submodules", "mdi",
        "--hidden-import", "yt_dlp",
        # YouTube's JavaScript-challenge scripts (data files, not code
        # PyInstaller would find on its own). A JS runtime (Node/Deno) is
        # still needed on the PC.
        "--hidden-import", "yt_dlp_ejs",
        "--collect-data", "yt_dlp_ejs",
        "--exclude-module", "PIL",          # only tools/make_icon.py uses it
        "--distpath", os.path.join(ROOT, "dist"),
        "--workpath", os.path.join(work, "pyinstaller"),
        "--specpath", work,
    ])

    iscc = find_iscc()
    if not iscc:
        print("\nInno Setup not found: skipped the installer. "
              "dist/MDI/ is ready to zip or run as-is.")
        return 0
    subprocess.run([iscc, f"/DAppVersion={__version__}", "/Q",
                    os.path.join(ROOT, "tools", "installer.iss")], check=True)
    print(f"\nInstaller: dist/MDI-Setup-{__version__}.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
