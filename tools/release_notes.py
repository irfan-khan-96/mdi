"""Print one version's section of CHANGELOG.md (used for GitHub Releases).

    python tools/release_notes.py 1.2.0
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def notes(version: str) -> str:
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    out, inside = [], False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].strip() == version
            continue
        if inside:
            out.append(line)
    if not out:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    body = "\n".join(out).strip()
    return (body + "\n\n**Install:** download `MDI-Setup-" + version +
            ".exe` below and run it. Windows SmartScreen may warn because the "
            "installer isn't code-signed: choose More info → Run anyway. "
            "`SHA256SUMS.txt` has its checksum.\n")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(notes(sys.argv[1]))
