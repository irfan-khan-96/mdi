"""Windows shell integration with no extra dependencies (ctypes only):

- Taskbar: overall progress drawn on MDI's taskbar button (ITaskbarList3).
- Notifier: a Windows notification when a download finishes or fails.
  Clicking it is reported back through ``Notifier.clicks``.

On other platforms both are silent no-ops.
"""

from __future__ import annotations

import os
import queue
import sys
import threading

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
APP_ID = "MDI.Downloader"
IS_WIN = sys.platform == "win32"

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    kernel32 = ctypes.windll.kernel32
    ole32 = ctypes.windll.ole32

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                      wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                  wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE


# ---------------------------------------------------------------- taskbar

class Taskbar:
    """Progress on the taskbar button. ``set(fraction)`` with 0..1, or
    ``set(None)`` for "busy, size unknown"; ``clear()`` hides it."""

    NOPROGRESS, INDETERMINATE, NORMAL = 0, 1, 2

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self._com = None
        self._last = None
        if not IS_WIN or not hwnd:
            return
        try:
            ole32.CoInitialize(None)
            clsid = _guid("{56FDF344-FD6D-11d0-958A-006097C9A090}")
            iid = _guid("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")
            ptr = ctypes.c_void_p()
            if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1,
                                      ctypes.byref(iid),
                                      ctypes.byref(ptr)) == 0:
                self._com = ptr
                self._call(3)                      # HrInit
        except OSError:
            self._com = None

    def _call(self, index: int, *args) -> None:
        vtbl = ctypes.cast(self._com, ctypes.POINTER(
            ctypes.POINTER(ctypes.c_void_p))).contents
        argtypes = [ctypes.c_void_p] + [a[0] for a in args]
        fn = ctypes.WINFUNCTYPE(ctypes.c_long, *argtypes)(vtbl[index])
        fn(self._com, *[a[1] for a in args])

    def set(self, fraction: float | None) -> None:
        if not self._com:
            return
        key = None if fraction is None else round(fraction, 3)
        if key == self._last and self._last != "clear":
            return
        self._last = key
        h = (wintypes.HWND, self.hwnd)
        if fraction is None:
            self._call(10, h, (ctypes.c_int, self.INDETERMINATE))
        else:
            self._call(10, h, (ctypes.c_int, self.NORMAL))
            self._call(9, h, (ctypes.c_ulonglong, int(fraction * 1000)),
                       (ctypes.c_ulonglong, 1000))

    def clear(self) -> None:
        if self._com and self._last != "clear":
            self._last = "clear"
            self._call(10, (wintypes.HWND, self.hwnd),
                       (ctypes.c_int, self.NOPROGRESS))


def _guid(text: str):
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]
    g = GUID()
    ole32.CLSIDFromString(text, ctypes.byref(g))
    return g


# ----------------------------------------------------------- notifications

if IS_WIN:
    class _NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD), ("guidItem", ctypes.c_byte * 16),
            ("hBalloonIcon", wintypes.HICON)]

    _WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                  wintypes.UINT, wintypes.WPARAM,
                                  wintypes.LPARAM)

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR)]

WM_APP_TRAY = 0x8000 + 1
NIN_SELECT, NIN_BALLOONUSERCLICK = 0x400, 0x405
WM_LBUTTONUP, WM_CLOSE, WM_DESTROY = 0x202, 0x10, 0x2


class Notifier:
    """Windows notifications via a notification-area icon, added only when
    the first notification is shown. Clicks arrive in ``clicks`` as
    ("notification", payload) or ("icon", None)."""

    def __init__(self):
        self.clicks: queue.Queue = queue.Queue()
        self._hwnd = None
        self._payload = None
        self._icon_added = False
        self._ready = threading.Event()
        if IS_WIN:
            threading.Thread(target=self._loop, daemon=True,
                             name="notifier").start()
            self._ready.wait(3)

    # -- public ------------------------------------------------------
    def notify(self, title: str, text: str, payload=None) -> None:
        if not self._hwnd:
            return
        self._payload = payload
        nid = self._nid()
        nid.uFlags = 0x01 | 0x02 | 0x04 | 0x10      # MESSAGE|ICON|TIP|INFO
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = 0x04 | 0x20               # NIIF_USER|LARGE_ICON
        nid.hBalloonIcon = self._big_icon
        if not self._icon_added:
            _register_app_id()
            self._icon_added = bool(shell32.Shell_NotifyIconW(0, ctypes.byref(nid)))
            nid.uVersion = 4                         # NOTIFYICON_VERSION_4
            shell32.Shell_NotifyIconW(4, ctypes.byref(nid))
        else:
            shell32.Shell_NotifyIconW(1, ctypes.byref(nid))

    def close(self) -> None:
        if self._hwnd:
            if self._icon_added:
                shell32.Shell_NotifyIconW(2, ctypes.byref(self._nid()))
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
            self._hwnd = None

    # -- internals -------------------------------------------------------
    def _nid(self):
        nid = _NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(_NOTIFYICONDATAW)
        nid.hWnd, nid.uID = self._hwnd, 1
        nid.uCallbackMessage = WM_APP_TRAY
        nid.hIcon = self._small_icon
        nid.szTip = "MDI Downloader"
        return nid

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_APP_TRAY:
            event = lparam & 0xFFFF
            if event == NIN_BALLOONUSERCLICK:
                self.clicks.put(("notification", self._payload))
            elif event in (NIN_SELECT, WM_LBUTTONUP):
                self.clicks.put(("icon", None))
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _loop(self) -> None:
        try:
            hinst = kernel32.GetModuleHandleW(None)
            self._proc = _WNDPROC(self._wndproc)     # keep a reference
            wc = _WNDCLASSW(lpfnWndProc=self._proc, hInstance=hinst,
                            lpszClassName="MDINotifier")
            user32.RegisterClassW(ctypes.byref(wc))
            ico = os.path.join(ASSETS, "icon.ico")
            self._small_icon = user32.LoadImageW(None, ico, 1, 16, 16, 0x10)
            self._big_icon = user32.LoadImageW(None, ico, 1, 64, 64, 0x10)
            self._hwnd = user32.CreateWindowExW(
                0, "MDINotifier", "MDI notifier", 0, 0, 0, 0, 0,
                wintypes.HWND(-3), None, hinst, None)  # message-only window
        finally:
            self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


def _register_app_id() -> None:
    """Give the app's taskbar identity a name and icon, so notifications
    say "MDI Downloader" rather than "Python" (per-user, harmless)."""
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"Software\Classes\AppUserModelId\{APP_ID}") as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ,
                              "MDI Downloader")
            winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ,
                              os.path.join(ASSETS, "icon.png"))
    except OSError:
        pass


def unregister_app_id() -> None:
    if IS_WIN:
        try:
            import winreg
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                             rf"Software\Classes\AppUserModelId\{APP_ID}")
        except OSError:
            pass


def is_foreground(hwnd: int) -> bool:
    return IS_WIN and bool(hwnd) and user32.GetForegroundWindow() == hwnd
