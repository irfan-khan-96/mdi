"""Tkinter front-end: one list, one toolbar, one per-connection progress bar."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from .advice import ACTION_LABELS, explain
from . import media
from .engine import (COMPLETED, DOWNLOADING, ERROR, QUEUED, Download,
                     sanitize_filename)
from .manager import Manager
from .safety import acquire_instance_lock, check_url
from .winshell import Notifier, Taskbar, is_foreground


def fmt_size(n: float | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def fmt_eta(s: float | None) -> str:
    if s is None:
        return ""
    s = int(s)
    h, m = divmod(s, 3600)
    m, s = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


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
        self.whole_row = len(rows)
        self.note = ttk.Label(f, foreground="gray")
        self.note.grid(row=len(rows) + 1, column=0, columnspan=3, sticky="w")
        self.url.trace_add("write", lambda *a: self._show_playlist_option())
        self._show_playlist_option()

        b = ttk.Frame(f)
        b.grid(row=len(rows) + 2, column=0, columnspan=3, sticky="e",
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
        if self.whole.get():
            return self._add_playlist(conns)
        try:
            self.app.manager.add(self.url.get(),
                                 filename=self.name.get().strip() or None,
                                 save_dir=self.dir.get().strip() or None,
                                 connections=conns)
        except ValueError as e:
            messagebox.showerror("MDI", str(e).capitalize() + ".",
                                 parent=self)
            return
        self.destroy()

    def _add_playlist(self, conns: int):
        """Read the list in the background (a second or two), then add one
        row per video inside a folder named after the playlist."""
        url, base = self.url.get().strip(), self.dir.get().strip() or None
        self.start_btn.state(["disabled"])
        self.note.config(text="Reading the playlist…")
        result: dict = {}

        def work():
            try:
                result["ok"] = media.expand_playlist(url)
            except Exception as e:  # noqa: BLE001 - shown to the user
                result["err"] = str(e)

        def wait():
            if not self.winfo_exists():
                return
            if not result:
                return self.after(100, wait)
            if "err" in result:
                self.start_btn.state(["!disabled"])
                self.note.config(text="")
                messagebox.showerror(
                    "MDI", "Couldn't read the playlist: " + result["err"],
                    parent=self)
                return
            title, urls = result["ok"]
            if not urls:
                self.start_btn.state(["!disabled"])
                self.note.config(text="That playlist has no videos MDI can "
                                      "download.")
                return
            self.app.add_many(urls, base, title, conns)
            self.destroy()

        threading.Thread(target=work, daemon=True).start()
        wait()


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
        self.app.manager.save()
        self.destroy()


class App:
    COLUMNS = (("name", "File", 300), ("size", "Size", 80),
               ("progress", "Progress", 70), ("speed", "Speed", 90),
               ("eta", "Time left", 70), ("conns", "Conn.", 45),
               ("status", "Status", 300))

    def __init__(self, root: tk.Tk, manager: Manager):
        self.root, self.manager = root, manager
        root.title("MDI Downloader")
        root.geometry("900x480")
        root.minsize(640, 320)

        bar = ttk.Frame(root, padding=(6, 6, 6, 0))
        bar.pack(fill="x")
        for text, cmd in (("Add URL", self.add), ("Resume", self.resume),
                          ("Pause", self.pause), ("Remove", self.remove),
                          ("Open folder", self.open_folder),
                          ("Settings", lambda: SettingsDialog(self))):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)

        body = ttk.Frame(root, padding=6)
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, columns=[c[0] for c in self.COLUMNS],
                                 show="headings", selectmode="extended")
        for key, title, width in self.COLUMNS:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width,
                             anchor="w" if key in ("name", "status") else "e",
                             stretch=key == "name")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._draw_segments())
        # Keyboard: every toolbar action has a key (F1 lists them).
        for key, fn in (("<Delete>", self.remove),
                        ("<Shift-Delete>", self.delete_files),
                        ("<space>", self.toggle),
                        ("<Return>", self.activate),
                        ("<Control-a>", self.select_all),
                        ("<Escape>", lambda: self.tree.selection_set(())),
                        ("<Control-o>", self.open_folder)):
            self.tree.bind(key, lambda e, fn=fn: (fn(), "break")[1])
        for key in ("<Down>", "<Up>"):
            self.tree.bind(key, self._arrow_from_nothing)
        self.tree.focus_set()

        # IDM's signature view: where each connection is in the file.
        detail = ttk.Frame(root, padding=(6, 0, 6, 0))
        detail.pack(fill="x")
        self.detail_label = ttk.Label(detail, text="", foreground="gray")
        self.detail_label.pack(anchor="w")
        self.canvas = tk.Canvas(detail, height=18, bg="#e6e6e6",
                                highlightthickness=0)
        self.canvas.pack(fill="x", pady=(2, 4))

        foot = ttk.Frame(root, relief="sunken")
        foot.pack(fill="x", side="bottom")
        self.status = ttk.Label(foot, padding=(8, 2))
        self.status.pack(side="left", fill="x", expand=True)
        # "Removed 2 downloads · Undo" - only there for a few seconds.
        self.undo_link = ttk.Label(foot, padding=(8, 2), foreground="#1a5fb4",
                                   cursor="hand2")
        self.undo_link.bind("<Button-1>", lambda e: self.undo())
        self._undo: list = []
        self._undo_timer = None
        self.bridge_note = ""

        root.bind("<Control-v>", lambda e: self.add())
        root.bind("<Control-n>", lambda e: self.add())
        root.bind("<Control-z>", lambda e: self.undo())
        root.bind("<F1>", lambda e: messagebox.showinfo(
            "Keyboard shortcuts", SHORTCUTS_HELP, parent=root))
        root.protocol("WM_DELETE_WINDOW", self.quit)

        # Taskbar progress + notifications when the window isn't in front.
        root.update_idletasks()
        try:
            self.hwnd = int(root.wm_frame(), 16)
        except (tk.TclError, ValueError):
            self.hwnd = 0
        self.taskbar = Taskbar(self.hwnd)
        self.notifier = Notifier()
        self._last_status = {d.id: d.status for d in manager.downloads}
        self._refresh()

    # ---- helpers ----------------------------------------------------------
    def selected(self) -> list[Download]:
        ids = set(self.tree.selection())
        return [d for d in self.manager.downloads if d.id in ids]

    def _clipboard_url(self) -> str:
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            return ""
        return text if text.startswith(("http://", "https://")) and \
            " " not in text else ""

    # ---- actions ----------------------------------------------------------
    def add(self, url: str | None = None):
        AddDialog(self, url or self._clipboard_url())

    def add_many(self, urls: list[str], base: str | None, folder: str,
                 conns: int) -> int:
        """Queue several links into ``base/<folder>``; skips ones already
        in the list. Returns how many were added."""
        save_dir = os.path.join(base or self.manager.settings["download_dir"],
                                sanitize_filename(folder) or "Playlist")
        added = 0
        for u in urls:
            if self.manager.find(u):
                continue
            try:
                self.manager.add(u, save_dir=save_dir, connections=conns,
                                 source="playlist")
                added += 1
            except ValueError:
                pass
        self._refresh_rows()
        return added

    def resume(self):
        for d in self.selected():
            self.manager.resume(d)

    def pause(self):
        for d in self.selected():
            self.manager.pause(d)

    def remove(self):
        """Remove from the list at once - no question, but undoable."""
        items = self.selected()
        if not items:
            return
        self._finalize_removal()          # one undo level keeps it simple
        self._undo = self.manager.take(items)
        for d in items:
            if self.tree.exists(d.id):
                self.tree.delete(d.id)
        self.undo_link.config(text=undo_text(len(self._undo)))
        self.undo_link.pack(side="right")
        self._undo_timer = self.root.after(UNDO_SECONDS * 1000,
                                           self._finalize_removal)

    def undo(self):
        if not self._undo:
            return
        taken, self._undo = self._undo, []
        self._hide_undo()
        self.manager.restore(taken)
        self._refresh_rows()
        self.tree.selection_set([d.id for _, d, _ in taken
                                 if self.tree.exists(d.id)])
        self.tree.focus_set()

    def _finalize_removal(self):
        taken, self._undo = self._undo, []
        self._hide_undo()
        if taken:   # file cleanup may wait on stopping threads: off the UI
            threading.Thread(target=self.manager.discard, args=(taken,),
                             daemon=True).start()

    def _hide_undo(self):
        if self._undo_timer is not None:
            self.root.after_cancel(self._undo_timer)
            self._undo_timer = None
        self.undo_link.pack_forget()

    def delete_files(self):
        """Shift+Delete: remove AND delete from disk. Can't be undone, so
        this is the one place that still asks first."""
        items = self.selected()
        if not items or not messagebox.askyesno(
                "Delete files",
                f"Delete {len(items)} download(s) and their file(s) from "
                "disk?\n\nThis can't be undone.", icon="warning",
                default="no", parent=self.root):
            return
        for d in items:
            self.manager.remove(d, delete_files=True)
            if self.tree.exists(d.id):
                self.tree.delete(d.id)

    def open_folder(self):
        items = self.selected()
        if items and items[0].path:
            d = items[0]
            reveal(d.path if d.status == COMPLETED else d.save_dir)
        else:
            open_path(self.manager.settings["download_dir"])

    def toggle(self):
        items = self.selected()
        plan = toggle_plan(items)
        for d in items:
            if plan == "pause":
                self.manager.pause(d)
            elif plan == "resume":
                self.manager.resume(d)

    def _arrow_from_nothing(self, _event):
        """First arrow press with nothing selected selects the first row."""
        rows = self.tree.get_children()
        if rows and not self.tree.selection():
            self.tree.selection_set(rows[0])
            self.tree.focus(rows[0])
            self.tree.see(rows[0])
            return "break"
        return None

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def _on_double(self, _event):
        self.activate()

    def activate(self):
        """Enter / double-click: open it, fix it, or start it."""
        for d in self.selected()[:1]:
            if d.status == COMPLETED and d.path and os.path.exists(d.path):
                open_path(d.path)
            elif d.status == ERROR:
                FixDialog(self, d)
            elif d.status != DOWNLOADING:
                self.manager.resume(d)

    def apply_fix(self, d: Download, action: str | None) -> None:
        """Carry out the one fix suggested for a failed download."""
        def drop_partial():
            if d.part_path and os.path.isfile(d.part_path):
                try:
                    os.remove(d.part_path)
                except OSError:
                    pass
            d.segments = []

        if action == "new_link":
            url = simpledialog.askstring(
                "Paste new link", "Paste a fresh link to the same file.\n"
                "MDI keeps what it already downloaded if the file is "
                "unchanged.", parent=self.root)
            if not url:
                return
            try:
                d.url, d.final_url = check_url(url), None
            except ValueError as e:
                messagebox.showerror("MDI", str(e).capitalize() + ".")
                return
        elif action == "change_folder":
            folder = filedialog.askdirectory(initialdir=d.save_dir,
                                             parent=self.root)
            if not folder:
                return
            drop_partial()
            d.save_dir, d.path = folder, None
        elif action == "restart":
            drop_partial()
        elif action in ("video_setup", "update_ytdlp"):
            cmd = "pip install yt-dlp" if action == "video_setup" \
                else "pip install -U yt-dlp"
            self.root.clipboard_clear()
            self.root.clipboard_append(cmd)
            messagebox.showinfo(
                "MDI", f"Copied:  {cmd}\n\nPaste it into a terminal and "
                       "press Enter, then restart MDI and try again.")
            return
        elif action != "retry":
            return
        self.manager.resume(d)
        self.manager.save()

    def quit(self):
        self.status.config(text="Saving state…")
        self.root.update_idletasks()
        taken, self._undo = self._undo, []
        self.manager.discard(taken)       # removal becomes final on exit
        self.manager.shutdown()
        self.taskbar.clear()
        self.notifier.close()
        self.root.destroy()

    # ---- periodic refresh -------------------------------------------------
    def _row(self, d: Download) -> tuple:
        got = d.downloaded
        pct = f"{got * 100 / d.size:.1f}%" if d.size else \
            ("100%" if d.status == COMPLETED else "")
        running = d.status == DOWNLOADING
        status = d.status.capitalize()
        if d.kind == "media" and d.status == DOWNLOADING:
            status = "Downloading (video)"
        if d.status == ERROR:
            # First sentence only; the full advice is shown below the list.
            status = "⚠ " + explain(d.error, d.kind)[0].split(". ")[0].rstrip(".") + "."
        elif d.status != COMPLETED and d.size and not d.resumable and got:
            status += " (no resume)"
        return (d.filename or d.url.rsplit("/", 1)[-1] or d.url,
                fmt_size(d.size) if d.size is not None else
                (fmt_size(got) if got else "?"),
                pct,
                f"{fmt_size(d.speed)}/s" if running and d.speed else "",
                fmt_eta(d.eta) if running else "",
                str(d.active_connections) if running else "",
                status)

    def _refresh(self):
        if self.manager.process_inbox():      # new downloads from browser
            self.root.deiconify()
            self.root.lift()
        total = self._refresh_rows()
        active = sum(1 for d in self.manager.downloads
                     if d.status == DOWNLOADING)
        self.status.config(text=f"{active} active · {fmt_size(total)}/s"
                                f"{self.bridge_note}")
        self._draw_segments()
        self._update_taskbar()
        self._notify_finished()
        self._handle_clicks()
        self.root.after(500, self._refresh)

    def _refresh_rows(self) -> float:
        """Sync the list with the manager; returns total speed."""
        total = 0.0
        for i, d in enumerate(list(self.manager.downloads)):
            values = self._row(d)
            if self.tree.exists(d.id):
                self.tree.item(d.id, values=values)
            else:   # at its own position, so an undo puts it back in place
                self.tree.insert("", i, iid=d.id, values=values)
            total += d.speed if d.status == DOWNLOADING else 0
        return total

    # ---- taskbar & notifications ---------------------------------------
    def _update_taskbar(self):
        active = [d for d in self.manager.downloads if d.status == DOWNLOADING]
        if not active:
            self.taskbar.clear()
        else:
            self.taskbar.set(overall_progress(active))

    def _notify_finished(self):
        done, failed = finished_since(self._last_status, self.manager.downloads)
        if (not done and not failed) or is_foreground(self.hwnd):
            return
        note = notification_for(done, failed)
        if note:
            self.notifier.notify(*note)

    def _handle_clicks(self):
        while not self.notifier.clicks.empty():
            kind, payload = self.notifier.clicks.get_nowait()
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            action, did = payload if payload else (None, None)
            d = next((x for x in self.manager.downloads if x.id == did), None)
            if d is None:
                continue
            self.tree.selection_set(d.id)
            self.tree.see(d.id)
            if action == "fix" and d.status == ERROR:
                FixDialog(self, d)
            elif action == "reveal" and d.path and os.path.exists(d.path):
                reveal(d.path)


    def _draw_segments(self):
        c = self.canvas
        c.delete("all")
        items = self.selected()
        if not items:
            self.detail_label.config(
                text=IDLE_HINT if self.manager.downloads else EMPTY_HINT)
            return
        d = items[0]
        if d.status == ERROR:
            self.detail_label.config(
                text="⚠ " + explain(d.error, d.kind)[0] +
                     "  Double-click the download to fix it.")
        else:
            self.detail_label.config(
                text=f"{d.url}  →  {d.path or d.save_dir}")
        w, h = c.winfo_width(), int(c["height"])
        done_color = "#2e9e4f" if d.status == COMPLETED else "#3a7bd5"
        if d.status == COMPLETED:
            c.create_rectangle(0, 0, w, h, fill=done_color, width=0)
            return
        if not d.size:
            return
        if d.kind == "media":  # yt-dlp: one overall bar
            c.create_rectangle(0, 0, min(w, d.downloaded * w / d.size), h,
                               fill=done_color, width=0)
            return
        for s in list(d.segments):
            x0 = s.start * w / d.size
            x1 = s.pos * w / d.size
            if x1 > x0:
                c.create_rectangle(x0, 0, x1, h, fill=done_color, width=0)
            if s.active:  # connection head
                c.create_line(x1, 0, x1, h, fill="#d33", width=2)



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


def undo_text(n: int) -> str:
    what = "download" if n == 1 else f"{n} downloads"
    return f"Removed {what} · Undo (Ctrl+Z)"


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


ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def set_icon(root: tk.Tk) -> None:
    try:
        if sys.platform == "win32":
            root.iconbitmap(default=os.path.join(ASSETS, "icon.ico"))
        else:
            root._icon = tk.PhotoImage(file=os.path.join(ASSETS, "icon.png"))
            root.iconphoto(True, root._icon)
    except tk.TclError:
        pass  # missing icon files shouldn't stop the app


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if sys.platform == "win32":
        try:  # crisp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            # Own taskbar identity, so Windows shows our icon, not Python's.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "MDI.Downloader")
        except Exception:  # noqa: BLE001
            pass
    lock = acquire_instance_lock()
    if lock is None:
        # Already running: hand any URLs to that instance and leave.
        from .nmhost import write_inbox
        for url in argv:
            try:
                write_inbox(url)
            except ValueError:
                pass
        if not argv:
            r = tk.Tk()
            r.withdraw()
            messagebox.showinfo("MDI", "MDI Downloader is already running.")
            r.destroy()
        return

    manager = Manager()
    root = tk.Tk()
    set_icon(root)
    app = App(root, manager)
    app.bridge_note = status_notes()

    for url in argv:  # `python -m mdi <url>` adds it straight away
        try:
            manager.add(url, source="command line")
        except ValueError:
            pass
    root.mainloop()
    lock.close()


def status_notes() -> str:
    from . import media
    from .install import is_installed
    notes = []
    if not is_installed():
        notes.append("browser not connected (python -m mdi.install)")
    if not media.available():
        notes.append("video sites OFF (pip install yt-dlp)")
    else:
        age = media.age_days()
        if age is not None and age > 90:
            notes.append(f"yt-dlp is {age} days old (pip install -U yt-dlp)")
        if not media.has_ffmpeg():
            notes.append("no ffmpeg: video limited to single-file formats")
    return "".join(" · " + n for n in notes)
