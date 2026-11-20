"""The window's longer flows: fixing a failed download, getting the optional
helpers (ffmpeg, Deno), updating yt-dlp and updating MDI itself. Mixed into
App (gui.py)."""

from __future__ import annotations

import os
from tkinter import filedialog, messagebox, simpledialog

from . import addons, media, selfupdate, updater
from .dialogs import run_in_background
from .engine import ERROR, Download
from .manager import partial_files
from .safety import check_url, credentials_for, get_logger
from .texts import (JS_ENGINE_STEP, addon_link_text, addon_question,
                    js_setup_steps, status_notes, update_question)

log = get_logger()


class Flows:
    def apply_fix(self, d: Download, action: str | None) -> None:
        """Carry out the one fix suggested for a failed download."""
        def drop_partial():
            for p in partial_files(d):
                try:
                    os.remove(p)
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
                new = check_url(url)
            except ValueError as e:
                messagebox.showerror("MDI", str(e).capitalize() + ".")
                return
            # Cookies were captured for the old site: never send them to
            # a different one.
            d.headers = credentials_for(d.url, new, d.headers)
            d.url, d.final_url = new, None
        elif action == "change_folder":
            folder = filedialog.askdirectory(initialdir=d.save_dir,
                                             parent=self.root)
            if not folder:
                return
            drop_partial()
            d.save_dir, d.path = folder, None
        elif action == "restart":
            drop_partial()
        elif action == "update_ytdlp":
            self._update_ytdlp_now()
            return
        elif action == "video_setup":
            cmd = "pip install yt-dlp"
            self.root.clipboard_clear()
            self.root.clipboard_append(cmd)
            messagebox.showinfo(
                "MDI", f"Copied:  {cmd}\n\nPaste it into a terminal and "
                       "press Enter, then restart MDI and try again.")
            return
        elif action == "js_setup":
            steps = js_setup_steps()
            if not steps:            # fixed meanwhile (e.g. Node installed)
                self.manager.resume(d)
                return
            if steps == [JS_ENGINE_STEP]:
                self.get_addons(["deno"], then_retry=d)
                return
            cmd = next((s for s in steps if s.startswith(("pip", "winget"))),
                       "")
            if cmd:
                self.root.clipboard_clear()
                self.root.clipboard_append(cmd)
            messagebox.showinfo(
                "MDI", "To download this video MDI needs:\n\n" +
                "\n".join(f"•  {s}" for s in steps) +
                (f"\n\nCopied:  {cmd}\nPaste it into a terminal and press "
                 "Enter." if cmd else "") +
                "\n\nThen restart MDI and try again.")
            return
        elif action != "retry":
            return
        self.manager.resume(d)
        self.manager.save()

    def show_update_link(self):
        v = selfupdate.available() if selfupdate.enabled() else None
        self._update_shown = v
        if self._update_busy:
            return
        if v:
            self.update_link.config(text=f"MDI {v} is available · Update")
            self.update_link.pack(side="right")
        else:
            self.update_link.pack_forget()

    def update_app(self):
        """Download and verify the new installer, then save everything,
        start the installer and close. It reopens MDI when done."""
        version = selfupdate.available()
        if self._update_busy or not version:
            return
        if not messagebox.askokcancel(
                "Update MDI", update_question(version), parent=self.root):
            return
        self._update_busy = True
        pct = [0]

        def tick():
            self.update_link.config(
                text=f"Downloading MDI {version} · {pct[0]}%")

        def done(path, err):
            self._update_busy = False
            if err:
                log.warning("MDI update download failed: %s", err)
                self.show_update_link()
                messagebox.showerror(
                    "MDI", f"Couldn't download the update: {err}\n\nYou can "
                    "keep using this version and try again later.",
                    parent=self.root)
                return
            self.update_link.config(text=f"Installing MDI {version}…")
            self.root.update_idletasks()
            self._shutdown()
            try:
                selfupdate.launch(path)
            except OSError as e:
                log.warning("couldn't start the MDI installer: %s", e)
            self.root.destroy()

        run_in_background(
            self.root,
            lambda: selfupdate.download(version, lambda p: pct.__setitem__(0, p)),
            done, tick)

    def show_addon_link(self):
        need = addons.missing() if media.available() else []
        if need:
            log.info("helpers missing: %s", ", ".join(need))
        if need and not self._addon_busy:
            self.addon_link.config(text=addon_link_text(need))
            self.addon_link.pack(side="right")
        elif not self._addon_busy:
            self.addon_link.pack_forget()

    def get_addons(self, keys=None, then_retry: Download | None = None):
        """Ask, then download ffmpeg / Deno in the background."""
        keys = keys or addons.missing()
        if self._addon_busy or not keys:
            return
        if not messagebox.askokcancel(
                "Get HD video support", addon_question(keys),
                parent=self.root):
            return
        self._addon_busy = True
        status = ["Starting…"]

        def tick():
            self.addon_link.config(
                text=f"Getting HD video support · {status[0]}")
            self.addon_link.pack(side="right")

        def done(_, err):
            self._addon_busy = False
            self.bridge_note = status_notes()
            self.show_addon_link()
            if err:
                log.warning("couldn't install helpers: %s", err)
                messagebox.showerror(
                    "MDI", f"Couldn't download the helpers: {err}\n\n"
                    "Check your internet connection and try again.",
                    parent=self.root)
                return
            messagebox.showinfo(
                "MDI", "Done. HD video, MP3 audio and protected YouTube "
                       "videos now work.", parent=self.root)
            if then_retry is not None and then_retry.status == ERROR:
                self.manager.resume(then_retry)

        tick()
        run_in_background(
            self.root,
            lambda: addons.install(keys, lambda t: status.__setitem__(0, t)),
            done, tick)

    def _update_ytdlp_now(self):
        """The "Update now" fix: fetch the latest yt-dlp in the background."""
        self.status.config(text="Updating yt-dlp…")

        def done(version, err):
            if err:
                messagebox.showerror(
                    "MDI", f"Couldn't update yt-dlp: {err}\n\nCheck your "
                    "internet connection and try again.", parent=self.root)
            elif version:
                messagebox.showinfo(
                    "MDI", f"yt-dlp updated to {version}.\n\nClose and "
                           "reopen MDI to use it, then try the download "
                           "again.", parent=self.root)
            else:
                messagebox.showinfo(
                    "MDI", "yt-dlp is already the latest version. The site "
                           "may have changed in a way yt-dlp doesn't support "
                           "yet; try again in a few days.", parent=self.root)

        run_in_background(
            self.root,
            lambda: updater.update(media.version_text(), force=True), done,
            every=200)
