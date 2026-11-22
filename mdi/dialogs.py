"""The Add, Fix and Settings dialogs, plus small window helpers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import media
from .advice import ACTION_LABELS, explain
from .engine import Download
from .manager import apply_speed_limit

if TYPE_CHECKING:
    from .gui import App


def run_in_background(widget, work, done, tick=None, every=250) -> None:
    """Run ``work()`` on a thread and call ``done(result, error)`` back on
    the window's thread when it finishes; ``tick()`` runs meanwhile, every
    ``every`` ms. Nothing is called if ``widget`` is closed first."""
    box: dict = {}

    def run():
        try:
            box["ok"] = work()
        except Exception as e:  # noqa: BLE001 - handed to done()
            box["err"] = e

    def poll():
        try:
            if not widget.winfo_exists():
                return
        except tk.TclError:
            return
        if not box:
            if tick:
                tick()
            widget.after(every, poll)
            return
        done(box.get("ok"), box.get("err"))

    threading.Thread(target=run, daemon=True).start()
    poll()


def open_path(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open",
                          path])


def reveal(path: str) -> None:
    if sys.platform == "win32" and os.path.exists(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        open_path(os.path.dirname(path))


class AddDialog(tk.Toplevel):
    def __init__(self, app: "App", url: str = ""):
        super().__init__(app.root)
        self.app = app
        self.title("Add download")
        self.resizable(False, False)
        self.transient(app.root)
        s = app.manager.settings

        self.url = tk.StringVar(value=url)
        self.dir = tk.StringVar(value=s["download_dir"])
        self.name = tk.StringVar()
        self.conns = tk.IntVar(value=s["connections"])

        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        rows = [("URL", ttk.Entry(f, textvariable=self.url, width=60)),
                ("Save to", ttk.Entry(f, textvariable=self.dir, width=60)),
                ("File name", ttk.Entry(f, textvariable=self.name, width=60)),
                ("Connections", ttk.Spinbox(f, from_=1, to=32, width=5,
                                            textvariable=self.conns))]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=3)
            widget.grid(row=i, column=1, sticky="w", pady=3)
        ttk.Button(f, text="…", width=3, command=self._browse).grid(
            row=1, column=2, padx=(4, 0))
        ttk.Label(f, text="(leave empty to use the server's name)",
                  foreground="gray").grid(row=2, column=2, sticky="w", padx=4)

        # Only appears for playlist links (and only if yt-dlp is installed).
        self.whole = tk.BooleanVar(value=False)
        self.whole_box = ttk.Checkbutton(
            f, variable=self.whole,
            text="Download the whole playlist (each video in its own row)")
        self.whole_row = len(rows) + 1

        # Only appears for video-site links.
        self.labels = dict(media.QUALITIES)
        self.quality = tk.StringVar(
            value="Best")        # always starts at Best; change per download
        self.quality_label = ttk.Label(f, text="Quality")
        self.quality_box = ttk.Combobox(
            f, textvariable=self.quality, state="readonly", width=12,
            values=[label for _, label in media.QUALITIES])
        self.quality_row = len(rows)
        self._video_check = None

        self.note = ttk.Label(f, foreground="gray")
        self.note.grid(row=len(rows) + 2, column=0, columnspan=3, sticky="w")
        self.url.trace_add("write", lambda *a: self._show_options())
        self._show_options()

        b = ttk.Frame(f)
        b.grid(row=len(rows) + 3, column=0, columnspan=3, sticky="e",
               pady=(10, 0))
        self.start_btn = ttk.Button(b, text="Start", command=self._ok)
        self.start_btn.pack(side="left", padx=4)
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="left")
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        rows[0][1].focus_set()
        rows[0][1].select_range(0, "end")
        self.grab_set()

    def _browse(self):
        d = filedialog.askdirectory(initialdir=self.dir.get(), parent=self)
        if d:
            self.dir.set(d)

    def _show_options(self):
        self._show_playlist_option()
        self._show_quality_option()

    def _show_quality_option(self):
        """Show the quality choice for video links. The site check may
        still be loading; if so, look again shortly."""
        if self._video_check is not None:
            self.after_cancel(self._video_check)
            self._video_check = None
        video = media.is_video_site(self.url.get().strip())
        if video is None:
            self._video_check = self.after(200, self._show_quality_option)
            return
        if video:
            self.quality_label.grid(row=self.quality_row, column=0,
                                    sticky="w", pady=3)
            self.quality_box.grid(row=self.quality_row, column=1,
                                  sticky="w", pady=3)
        else:
            self.quality_label.grid_remove()
            self.quality_box.grid_remove()

    def chosen_quality(self) -> str | None:
        """The quality key if the option is showing. Not remembered: the
        next download starts at Best again."""
        if not self.quality_box.winfo_ismapped():
            return None
        key = next((k for k, v in self.labels.items()
                    if v == self.quality.get()), "best")
        return key

    def _show_playlist_option(self):
        if media.available() and media.looks_like_playlist(self.url.get()):
            self.whole_box.grid(row=self.whole_row, column=1, sticky="w",
                                pady=3)
        else:
            self.whole_box.grid_remove()
            self.whole.set(False)

    def _ok(self):
        if self.start_btn.instate(["disabled"]):   # playlist being read
            return
        try:
            conns = int(self.conns.get())
        except (tk.TclError, ValueError):
            conns = self.app.manager.settings["connections"]
        quality = self.chosen_quality()
        if self.whole.get():
            return self._add_playlist(conns, quality)
        try:
            self.app.manager.add(self.url.get(), quality=quality,
                                 filename=self.name.get().strip() or None,
                                 save_dir=self.dir.get().strip() or None,
                                 connections=conns)
        except ValueError as e:
            messagebox.showerror("MDI", str(e).capitalize() + ".",
                                 parent=self)
            return
        self.destroy()

    def _add_playlist(self, conns: int, quality: str | None = None):
        """Read the list in the background (a second or two), then add one
        row per video inside a folder named after the playlist."""
        url, base = self.url.get().strip(), self.dir.get().strip() or None
        self.start_btn.state(["disabled"])
        self.note.config(text="Reading the playlist…")
        def done(found, err):
            if err:
                self.start_btn.state(["!disabled"])
                self.note.config(text="")
                messagebox.showerror(
                    "MDI", f"Couldn't read the playlist: {err}", parent=self)
                return
            title, urls = found
            if not urls:
                self.start_btn.state(["!disabled"])
                self.note.config(text="That playlist has no videos MDI can "
                                      "download.")
                return
            self.app.add_many(urls, base, title, conns, quality)
            self.destroy()

        run_in_background(self, lambda: media.expand_playlist(url), done,
                          every=100)


class FixDialog(tk.Toplevel):
    """What went wrong in one sentence, and the one button that fixes it."""

    def __init__(self, app: "App", d: Download):
        super().__init__(app.root)
        self.title("Download problem")
        self.resizable(False, False)
        self.transient(app.root)
        advice, action = explain(d.error, d.kind)

        f = ttk.Frame(self, padding=16)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text=d.filename or d.url, font=("", 10, "bold"),
                  wraplength=420).pack(anchor="w")
        ttk.Label(f, text=advice, wraplength=420,
                  justify="left").pack(anchor="w", pady=(6, 4))
        ttk.Label(f, text=f"Details: {d.error}", foreground="#555555",
                  wraplength=420, justify="left").pack(anchor="w")

        b = ttk.Frame(f)
        b.pack(anchor="e", pady=(14, 0))
        if action:
            def run():
                self.destroy()
                app.apply_fix(d, action)
            primary = ttk.Button(b, text=ACTION_LABELS[action], command=run)
            primary.pack(side="left", padx=(0, 6))
            self.bind("<Return>", lambda e: run())
        close = ttk.Button(b, text="Close", command=self.destroy)
        close.pack(side="left")
        self.bind("<Escape>", lambda e: self.destroy())
        (primary if action else close).focus_set()
        self.grab_set()


class SettingsDialog(tk.Toplevel):
    def __init__(self, app: "App"):
        super().__init__(app.root)
        self.app = app
        self.title("Settings")
        self.resizable(False, False)
        self.transient(app.root)
        s = app.manager.settings
        self.vars = {
            "download_dir": tk.StringVar(value=s["download_dir"]),
            "connections": tk.IntVar(value=s["connections"]),
            "max_parallel": tk.IntVar(value=s["max_parallel"]),
            "speed_limit": tk.StringVar(value=limit_text(s["speed_limit"])),
        }
        f = ttk.Frame(self, padding=12)
        f.pack()
        ttk.Label(f, text="Default folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(f, textvariable=self.vars["download_dir"], width=50).grid(
            row=0, column=1, pady=3)
        ttk.Button(f, text="…", width=3, command=self._browse).grid(
            row=0, column=2, padx=(4, 0))
        ttk.Label(f, text="Connections per download").grid(
            row=1, column=0, sticky="w")
        ttk.Spinbox(f, from_=1, to=32, width=5,
                    textvariable=self.vars["connections"]).grid(
            row=1, column=1, sticky="w", pady=3)
        ttk.Label(f, text="Downloads at the same time").grid(
            row=2, column=0, sticky="w")
        ttk.Spinbox(f, from_=1, to=10, width=5,
                    textvariable=self.vars["max_parallel"]).grid(
            row=2, column=1, sticky="w", pady=3)
        ttk.Label(f, text="Speed limit (MB/s)").grid(
            row=3, column=0, sticky="w")
        limit = ttk.Frame(f)
        limit.grid(row=3, column=1, columnspan=2, sticky="w", pady=3)
        ttk.Spinbox(limit, from_=0, to=1000, increment=0.5, width=6,
                    textvariable=self.vars["speed_limit"]).pack(side="left")
        ttk.Label(limit, text="  0 = no limit. Applies to all downloads "
                              "together, at once.",
                  foreground="gray").pack(side="left")
        b = ttk.Frame(f)
        b.grid(row=4, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(b, text="Save", command=self._ok).pack(side="left", padx=4)
        ttk.Button(b, text="Cancel", command=self.destroy).pack(side="left")
        self.grab_set()

    def _browse(self):
        d = filedialog.askdirectory(
            initialdir=self.vars["download_dir"].get(), parent=self)
        if d:
            self.vars["download_dir"].set(d)

    def _ok(self):
        s = self.app.manager.settings
        try:
            s["connections"] = max(1, min(32, int(self.vars["connections"].get())))
            s["max_parallel"] = max(1, min(10, int(self.vars["max_parallel"].get())))
        except (tk.TclError, ValueError):
            pass
        s["download_dir"] = self.vars["download_dir"].get().strip() or s["download_dir"]
        s["speed_limit"] = parse_limit(self.vars["speed_limit"].get(),
                                       s["speed_limit"])
        apply_speed_limit(s["speed_limit"])
        self.app.manager.save()
        self.destroy()


def limit_text(mb: float) -> str:
    return "0" if not mb else f"{mb:g}"


def parse_limit(text: str, old: float) -> float:
    """MB/s from the settings box: "2", "2.5", "2,5"; blank = off."""
    text = (text or "").strip().replace(",", ".").lower().removesuffix("mb/s")
    if not text:
        return 0.0
    try:
        return round(max(0.0, min(10000.0, float(text))), 1)
    except ValueError:
        return old
