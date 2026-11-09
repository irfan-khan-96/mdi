"""Turn technical download errors into one plain sentence plus one fix.

Actions the GUI knows how to perform:
  retry          resume where it stopped
  new_link       paste a fresh link for the same file (keeps progress)
  change_folder  save somewhere else
  restart        throw away the partial file and start over
  video_setup    copy "pip install yt-dlp"
  update_ytdlp   copy "pip install -U yt-dlp"
  js_setup       explain / copy what's missing for YouTube's JS check
  None           nothing MDI can safely do; the sentence explains why
"""

from __future__ import annotations

import re

# (pattern in the lower-cased error, advice, action) — first match wins.
_RULES = [
    (r"web page, not a file",
     "This is a web page, not a file. Turn on video downloads to save "
     "videos from sites like YouTube.", "video_setup"),
    (r"javascript check",
     "YouTube wants a quick check before it shares this video (common for "
     "videos made for kids). MDI needs a small helper to pass it.",
     "js_setup"),
    (r"changed on the server",
     "The file was updated on the website after the download started.",
     "restart"),
    (r"byte range",
     "The site stopped allowing downloads to continue from where they "
     "left off.", "restart"),
    (r"http (?:error )?(401|403|410)\b",
     "The site refused the download. The link has probably expired or "
     "needs you to be signed in. Copy a fresh link from the site.",
     "new_link"),
    (r"http (?:error )?404\b",
     "The file isn't at this address any more. Copy a fresh link from "
     "the site.", "new_link"),
    (r"http (?:error )?(408|425|429|5\d\d)\b",
     "The site is busy or having problems. Try again in a few minutes.",
     "retry"),
    (r"certificate|ssl",
     "The site's security certificate couldn't be verified, so MDI "
     "stopped to keep you safe. Check your PC's date and time.", "retry"),
    (r"getaddrinfo|11001|name or service not known|nodename",
     "Couldn't reach the website. Check your internet connection.",
     "retry"),
    (r"permission denied|errno 13|access is denied",
     "MDI isn't allowed to save files in this folder.", "change_folder"),
    (r"winerror (3|123|267)\b|errno 30\b|read-only file system",
     "MDI can't save to this folder. It may not exist any more, or its "
     "name isn't valid.", "change_folder"),
    (r"refused (insecure )?redirect",
     "The site tried to send the download somewhere unsafe, so MDI "
     "blocked it.", None),
    (r"sign in|not a bot|private video|members[- ]only|confirm your age|"
     r"age[- ]restricted|inappropriate for some users",
     "This video needs you to be signed in on the site, so it can't be "
     "downloaded here.", None),
    (r"unsupported url",
     "There's no video on this page that MDI can download.", None),
    (r"unavailable|removed|copyright|not available",
     "This video isn't available any more.", None),
    (r"timed out|connection|10054|10060|10061|all connections failed",
     "The connection dropped. MDI will continue from where it stopped.",
     "retry"),
]

_DISK = re.compile(r"need (\d+) mb, (\d+) mb free")


def explain(error: str, kind: str = "file") -> tuple[str, str | None]:
    """Return (advice, action) for an error message."""
    low = (error or "").lower()
    m = _DISK.search(low)
    if m:
        need, free = (int(x) for x in m.groups())
        return (f"Not enough space: this needs {_mb(need)} but only "
                f"{_mb(free)} is free. Free up space or choose another "
                f"folder.", "change_folder")
    for pattern, advice, action in _RULES:
        if re.search(pattern, low):
            return advice, action
    if kind == "media":
        return ("The video site changed something. Updating yt-dlp usually "
                "fixes this.", "update_ytdlp")
    return "The download stopped unexpectedly.", "retry"


ACTION_LABELS = {
    "retry": "Try again",
    "new_link": "Paste new link…",
    "change_folder": "Choose folder…",
    "restart": "Start over",
    "video_setup": "Copy setup command",
    "update_ytdlp": "Copy update command",
    "js_setup": "Set up helper…",
}


def _mb(n: int) -> str:
    return f"{n / 1024:.1f} GB" if n >= 1024 else f"{n} MB"
