"""Tkinter front-end: one list, one toolbar, one per-connection progress bar.

The dialogs live in dialogs.py, the longer flows (fixes, helpers, updates)
in flows.py, and the wording in texts.py.
"""

from __future__ import annotations

import os
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from . import media, selfupdate, updater
from .advice import explain
from .dialogs import AddDialog, FixDialog, SettingsDialog, open_path, reveal
from .engine import (COMPLETED, DOWNLOADING, ERROR, QUEUED, Download,
                     sanitize_filename)
from .flows import Flows
from .manager import Manager
from .safety import acquire_instance_lock, get_logger
from .texts import (EMPTY_HINT, IDLE_HINT, SHORTCUTS_HELP, UNDO_SECONDS,
                    close_text, finished_since, fmt_eta, fmt_size,
                    notification_for, overall_progress, row_key,
                    status_notes, toggle_plan, undo_text)
from .winshell import Notifier, Taskbar, is_foreground


log = get_logger()
















class App(Flows):
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
        # "Get HD video support" - only while ffmpeg / Deno are missing.
        self.addon_link = ttk.Label(foot, padding=(8, 2), foreground="#1a5fb4",
                                    cursor="hand2")
        self.addon_link.bind("<Button-1>", lambda e: self.get_addons())
        self._addon_busy = False
        # "MDI 1.4.0 is available · Update" - only when there is one.
        self.update_link = ttk.Label(foot, padding=(8, 2),
                                     foreground="#1a5fb4", cursor="hand2")
        self.update_link.bind("<Button-1>", lambda e: self.update_app())
        self._update_busy = False
        self._update_shown: str | None = None

        root.bind("<Control-v>", lambda e: self.add())
        root.bind("<Control-n>", lambda e: self.add())
        root.bind("<Control-z>", lambda e: self.undo())
        root.bind("<F1>", lambda e: messagebox.showinfo(
            "Keyboard shortcuts", SHORTCUTS_HELP, parent=root))
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.report_callback_exception = lambda *exc: log.error(
            "error in the window", exc_info=exc)

        # Taskbar progress + notifications when the window isn't in front.
        root.update_idletasks()
        try:
            self.hwnd = int(root.wm_frame(), 16)
        except (tk.TclError, ValueError):
            self.hwnd = 0
        self.taskbar = Taskbar(self.hwnd)
        self.notifier = Notifier()
        self._last_status = {d.id: d.status for d in manager.downloads}
        self._rows: dict = {}     # id -> (row_key, values) last shown
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
                 conns: int, quality: str | None = None) -> int:
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
                                 quality=quality, source="playlist")
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
            self._delete_row(d.id)
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
        taken = self.manager.take(items)
        for d in items:
            self._delete_row(d.id)
        # Stopping connections can take a moment: delete off the UI thread.
        threading.Thread(target=self.manager.discard, args=(taken,),
                         kwargs={"delete_finished": True},
                         daemon=True).start()

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







    def quit(self):
        active = sum(1 for d in self.manager.downloads
                     if d.status in (DOWNLOADING, QUEUED))
        if active and not messagebox.askokcancel(
                "Close MDI", close_text(active), parent=self.root):
            return
        self._shutdown()
        self.root.destroy()

    def _shutdown(self):
        """Save everything and stop downloads (they resume next time)."""
        self.status.config(text="Saving state…")
        self.root.update_idletasks()
        taken, self._undo = self._undo, []
        self.manager.shutdown()
        self.manager.discard(taken, wait=1)   # removal is final on exit
        self.taskbar.clear()
        self.notifier.close()

    # ---- periodic refresh -------------------------------------------------
    def _row(self, d: Download) -> tuple:
        got = d.downloaded
        pct = f"{min(got, d.size) * 100 / d.size:.1f}%" if d.size else \
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
        try:
            self._refresh_once()
        except Exception:  # noqa: BLE001 - one bad tick mustn't stop updates
            log.exception("error while refreshing the window")
        finally:
            self.root.after(500, self._refresh)

    def _refresh_once(self):
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
        if selfupdate.latest_known != self._update_shown and \
                selfupdate.latest_known:          # found by the weekly check
            self.show_update_link()

    def _delete_row(self, did: str) -> None:
        """Take a row off the list and forget what it showed, so it's
        drawn again if the download comes back (Undo)."""
        if self.tree.exists(did):
            self.tree.delete(did)
        self._rows.pop(did, None)

    def _refresh_rows(self) -> float:
        """Sync the list with the manager; returns total speed.

        Only rows that changed are sent to Tk, and finished, paused or
        failed downloads aren't even recomputed until something about them
        changes, so a list of thousands costs about as much as the few
        that are actually downloading."""
        total = 0.0
        cache = self._rows
        seen = set()
        for i, d in enumerate(list(self.manager.downloads)):
            seen.add(d.id)
            if d.status == DOWNLOADING:
                total += d.speed
            key = row_key(d)
            old = cache.get(d.id)
            if old is not None and key is not None and old[0] == key:
                continue                     # idle and unchanged
            values = self._row(d)
            if old is None or not self.tree.exists(d.id):
                if not self.tree.exists(d.id):
                    # at its own position, so an undo puts it back in place
                    self.tree.insert("", i, iid=d.id, values=values)
                else:
                    self.tree.item(d.id, values=values)
            elif old[1] != values:
                self.tree.item(d.id, values=values)
            cache[d.id] = (key, values)
        if len(cache) > len(seen):           # forget removed downloads
            for gone in set(cache) - seen:
                del cache[gone]
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
    app.show_addon_link()
    if selfupdate.enabled():        # weekly; see selfupdate.py
        selfupdate.cleanup()
        app.show_update_link()
        selfupdate.check_in_background()
    if media.available():           # weekly, silent; see updater.py
        updater.update_in_background(media.version_text())
        root.after(1500, media.warm_up)   # for the Add dialog's quality box

    for url in argv:  # `python -m mdi <url>` adds it straight away
        try:
            manager.add(url, source="command line")
        except ValueError:
            pass
    root.mainloop()
    lock.close()












