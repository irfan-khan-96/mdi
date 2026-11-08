"""Command-line downloader:  python -m mdi.cli URL [-o DIR] [-n CONNECTIONS]

Ctrl+C pauses; running the same command again resumes.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from .engine import COMPLETED, DOWNLOADING, QUEUED
from .gui import fmt_eta, fmt_size
from .manager import Manager, app_dir


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mdi", description=__doc__.split("\n")[0])
    p.add_argument("url")
    p.add_argument("-o", "--output-dir", default=None)
    p.add_argument("-n", "--connections", type=int, default=None)
    args = p.parse_args(argv)

    m = Manager(os.path.join(app_dir(), "cli.json"))
    m.settings["max_parallel"] = 1
    d = m.find(args.url)
    if d:
        print("Resuming previous download")
        m.resume(d)
    else:
        try:
            d = m.add(args.url, save_dir=args.output_dir,
                      connections=args.connections, source="cli")
        except ValueError as e:
            m.shutdown()
            print(f"Error: {e}")
            return 2
    try:
        while d.status in (QUEUED, DOWNLOADING) or d.is_running:
            time.sleep(0.5)
            got = d.downloaded
            pct = f"{got * 100 / d.size:5.1f}%" if d.size else "  ?  "
            sys.stdout.write(
                f"\r{pct}  {fmt_size(got)}/{fmt_size(d.size)}  "
                f"{fmt_size(d.speed)}/s  {d.active_connections} conn  "
                f"ETA {fmt_eta(d.eta) or '-'}     ")
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\nPausing…")
        m.shutdown()
        print("Paused. Run the same command again to resume.")
        return 130
    print()
    if d.status == COMPLETED:
        m.remove(d)          # forget it; the file stays on disk
        m.shutdown()
        print(f"Saved to {d.path}")
        return 0
    m.shutdown()
    print(f"Failed: {d.error}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
