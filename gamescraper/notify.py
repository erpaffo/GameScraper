"""Avvisi di Windows (fumetto/toast) per prezzi, giochi gratis, port aggiornati e lavori finiti.

Solo Windows. Usa Shell_NotifyIcon con ctypes: nessuna dipendenza in piu' e nessun processo esterno
(il fumetto compare come notifica e resta nel centro notifiche). Se qualcosa non riesce, l'avviso si
perde in silenzio: un lavoro non deve mai fallire per colpa di una notifica.
"""

from __future__ import annotations

import queue
import sys
import threading

_WM_APP = 0x8000
_NIF_MESSAGE, _NIF_ICON, _NIF_TIP, _NIF_INFO = 0x01, 0x02, 0x04, 0x10
_NIM_ADD, _NIM_MODIFY, _NIM_DELETE = 0, 1, 2
_NIIF_INFO = 0x1
_IDI_APPLICATION = 32512
# shortcut: il clic sulla notifica non apre niente (nessuna azione); si aggiunge quando serve

_queue: queue.Queue = queue.Queue()
_thread: threading.Thread | None = None
_lock = threading.Lock()
_ready = False  # l'icona nascosta esiste: si possono mostrare i fumetti
_error = ""  # ultimo errore interno (per la diagnosi) 


def enabled(cfg) -> bool:
    """Impostazione dell'app (Impostazioni â€º Integrazioni); di base attive solo su Windows."""
    return sys.platform == "win32" and cfg.app.get("notify") is not False


def send(cfg_or_title, title: str = "", body: str = "") -> bool:
    """Mostra un avviso. `cfg` si puo' omettere: send("Titolo", "Testo")."""
    if isinstance(cfg_or_title, str):  # forma corta, senza impostazioni: sempre
        title, body = cfg_or_title, title
    elif not enabled(cfg_or_title):
        return False
    if not title:
        return False
    try:
        _ensure_thread().put((title[:64], body[:256]))
        return True
    except Exception:  # mai un errore per una notifica
        return False


def _ensure_thread() -> queue.Queue:
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, daemon=True, name="notify")
            _thread.start()
    return _queue


def _loop():
    """Finestra nascosta + coda: Shell_NotifyIcon deve essere chiamato sempre dallo stesso thread."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32  # Shell_NotifyIconW sta qui, non in user32

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                        ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                        ("szInfo", wintypes.WCHAR * 256), ("uTimeout", wintypes.UINT),
                        ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON)]

        user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        user32.CreateWindowExW.argtypes = (wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.LoadIconW.argtypes = (wintypes.HINSTANCE, ctypes.c_void_p)
        user32.LoadIconW.restype = wintypes.HICON
        shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL
        proc = WNDPROC(lambda hwnd, msg, wparam, lparam: user32.DefWindowProcW(hwnd, msg, wparam, lparam))
        cls = WNDCLASSW()
        cls.lpfnWndProc = proc
        cls.hInstance = kernel32.GetModuleHandleW(None)
        cls.lpszClassName = "GameScraperNotify"
        user32.RegisterClassW(ctypes.byref(cls))
        hwnd = user32.CreateWindowExW(0, cls.lpszClassName, "GameScraper", 0, 0, 0, 0, 0, None, None,
                                      cls.hInstance, None)
        if not hwnd:
            return
        icon = user32.LoadIconW(None, ctypes.c_void_p(_IDI_APPLICATION))  # MAKEINTRESOURCE: un numero, non una stringa
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd, data.uID, data.hIcon = hwnd, 1, icon
        data.uFlags = _NIF_ICON | _NIF_TIP | _NIF_MESSAGE
        data.uCallbackMessage = _WM_APP + 1
        data.szTip = "GameScraper"
        if not shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(data)):
            return
        global _ready
        _ready = True
        try:
            while True:
                try:
                    title, body = _queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                data.uFlags = _NIF_INFO
                data.szInfoTitle, data.szInfo, data.dwInfoFlags, data.uTimeout = title, body, _NIIF_INFO, 8000
                if not shell32.Shell_NotifyIconW(_NIM_MODIFY, ctypes.byref(data)):
                    # alcuni temi disegnano solo i fumetti "classici": si riprova come icona con tooltip
                    data.uFlags = _NIF_INFO
                    shell32.Shell_NotifyIconW(_NIM_ADD, ctypes.byref(data))
        finally:
            shell32.Shell_NotifyIconW(_NIM_DELETE, ctypes.byref(data))
    except Exception as e:  # mai un errore per una notifica
        global _error
        _error = f"{type(e).__name__}: {e}"
        return
