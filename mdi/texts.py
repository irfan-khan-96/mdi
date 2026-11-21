"""What the window says, and small decisions behind it. No tkinter here,
so it's easy to test and the command-line tool can share the formatting."""

from __future__ import annotations

import sys

from . import addons, media, updater
from .advice import explain
from .engine import COMPLETED, DOWNLOADING, ERROR, QUEUED, Download


def fmt_size(n: float | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def fmt_eta(s: float | None) -> str:
    if s is None or s < 0:
        return ""
    s = int(s)
    h, m = divmod(s, 3600)
    m, s = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


EMPTY_HINT = "Copy a link and press Ctrl+V to download it.  F1: shortcuts"


IDLE_HINT = "F1: keyboard shortcuts"


SHORTCUTS_HELP = """\
Ctrl+V\tAdd the link on the clipboard
Space\tPause / resume the selected downloads
Enter\tOpen the file (or fix / start the download)
Delete\tRemove from the list (Ctrl+Z brings it back)
Shift+Delete\tRemove and delete the file from disk
Ctrl+O\tShow in folder
Ctrl+A\tSelect all
Esc\tClear the selection"""


UNDO_SECONDS = 10


def close_text(active: int) -> str:
    what = "1 download is" if active == 1 else f"{active} downloads are"
    return (f"{what} still running.\n\nClosing pauses them. They continue "
            "from where they stopped the next time you open MDI.")


def undo_text(n: int) -> str:
    what = "download" if n == 1 else f"{n} downloads"
    return f"Removed {what} · Undo (Ctrl+Z)"


def row_key(d: Download):
    """What an idle download's row depends on, or None while it's active
    (its numbers move, so the row is recomputed on every refresh)."""
    if d.status == DOWNLOADING or d.is_running:
        return None
    return (d.status, d.filename, d.url, d.size, d.resumable, d.error,
            d.kind, d._media_got if d.kind == "media" else len(d.segments))


def toggle_plan(items: list[Download]) -> str | None:
    """Space: pause if anything selected is running or waiting, else resume
    whatever can be resumed."""
    if any(d.status in (DOWNLOADING, QUEUED) for d in items):
        return "pause"
    if any(d.status != COMPLETED for d in items):
        return "resume"
    return None


def overall_progress(active: list[Download]) -> float | None:
    """Combined fraction for the taskbar; None if no size is known yet."""
    sized = [d for d in active if d.size]
    if not sized:
        return None
    total = sum(d.size for d in sized)
    return min(1.0, sum(min(d.downloaded, d.size) for d in sized) / total)


def finished_since(last: dict, downloads: list[Download]):
    """Downloads that went from queued/downloading to completed / failed
    since the previous call (fast ones can skip "downloading" entirely
    between two refreshes). Updates ``last`` in place."""
    done, failed = [], []
    for d in downloads:
        before = last.get(d.id)
        if before in (QUEUED, DOWNLOADING) and d.status == COMPLETED:
            done.append(d)
        elif before in (QUEUED, DOWNLOADING) and d.status == ERROR:
            failed.append(d)
        last[d.id] = d.status
    return done, failed


def notification_for(done: list[Download], failed: list[Download]):
    """(title, text, payload) for one notification. Failures come first:
    they need you; finished files can wait."""
    if failed:
        d = failed[0]
        title = "Download failed" if len(failed) == 1 else \
            f"{len(failed)} downloads failed"
        advice = explain(d.error, d.kind)[0].split(". ")[0].rstrip(".")
        extra = f" {len(done)} other download(s) finished." if done else ""
        return (title, f"{d.filename or d.url}\n{advice}. Click to fix it."
                       f"{extra}", ("fix", d.id))
    if len(done) == 1:
        d = done[0]
        return ("Download complete",
                f"{d.filename}\nClick to show it in its folder.",
                ("reveal", d.id))
    names = ", ".join(d.filename or "file" for d in done[:3])
    more = f" and {len(done) - 3} more" if len(done) > 3 else ""
    return (f"{len(done)} downloads complete", names + more,
            ("reveal", done[0].id))


JS_ENGINE_STEP = "a small JavaScript engine (MDI can download it)"


def update_question(version: str) -> str:
    return (f"MDI will download version {version} (about 20 MB) from its "
            "GitHub release and check it against the release's SHA-256 "
            "checksums.\n\nThen MDI saves your download list, closes, "
            "installs the update and opens again by itself. Running "
            "downloads pause and continue afterwards. Continue?")


def addon_link_text(need: list[str]) -> str:
    return ("Get HD video support" if "ffmpeg" in need
            else "Fix some YouTube videos") + " (one click)"


def addon_question(keys: list[str]) -> str:
    lines = "\n".join(f"•  {addons.ADDONS[k].title}, about "
                      f"{addons.ADDONS[k].size_mb} MB" for k in keys)
    return (f"MDI will download:\n\n{lines}\n\nfrom the projects' official "
            "GitHub releases, check each file's SHA-256 checksum, and keep "
            f"them in:\n{addons.tools_dir()}\n\nNothing is installed "
            "system-wide. Continue?")


def js_setup_steps() -> list[str]:
    """What's missing for YouTube's JavaScript check, as steps."""
    steps = []
    try:
        import yt_dlp_ejs  # noqa: F401
    except ImportError:
        steps.append("install a newer MDI" if getattr(sys, "frozen", False)
                     else 'pip install -U "yt-dlp[default]"')
    if not addons.has_js_runtime():
        steps.append(JS_ENGINE_STEP)
    return steps


def status_notes() -> str:

    notes = []
    if not media.available():
        notes.append("video sites OFF (pip install yt-dlp)")
    else:
        age = media.age_days()
        if age is not None and age > 60 and updater.load_state().get("error"):
            notes.append(f"yt-dlp is {age} days old and couldn't update "
                         "itself (see mdi.log)")
        # Missing ffmpeg / JS engine: shown as the one-click link instead.
    return "".join(" · " + n for n in notes)
