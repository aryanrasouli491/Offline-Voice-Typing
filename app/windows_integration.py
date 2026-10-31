# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Native Windows hotkeys, a non-activating overlay, and Unicode text insertion.

No keyboard hooks, administrator rights, brightness
changes, or background key logging. Only the registered shortcut is observed.
"""
from __future__ import annotations
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import os
import threading
import hashlib
from app_info import APP_NAME

IS_WINDOWS = os.name == "nt"
HOTKEYS = {"Ctrl+Alt+Space": (0x0003, 0x20),
           "Ctrl+Shift+Space": (0x0006, 0x20), "Ctrl+Alt+D": (0x0003, 0x44)}


@dataclass(frozen=True)
class InputTarget:
    window: int
    focus: int
    process: int
    caret_window: int = 0
    caret_x: int = 0
    caret_y: int = 0


if IS_WINDOWS:
    user32 = C.WinDLL("user32", use_last_error=True)
    kernel32 = C.WinDLL("kernel32", use_last_error=True)
    LONG_PTR = C.c_ssize_t
    WNDPROC = C.WINFUNCTYPE(LONG_PTR, W.HWND, W.UINT, W.WPARAM, W.LPARAM)

    class GUIINFO(C.Structure):
        _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD),
                    ("hwndActive", W.HWND), ("hwndFocus", W.HWND),
                    ("hwndCapture", W.HWND), ("hwndMenuOwner", W.HWND),
                    ("hwndMoveSize", W.HWND), ("hwndCaret", W.HWND), ("rcCaret", W.RECT)]

    class KEYBDINPUT(C.Structure):
        _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD),
                    ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]

    class MOUSEINPUT(C.Structure):
        _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD),
                    ("dwFlags", W.DWORD), ("time", W.DWORD), ("dwExtraInfo", C.c_size_t)]

    class INPUTUNION(C.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(C.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", W.DWORD), ("u", INPUTUNION)]

    # Explicit signatures are essential on 64-bit Windows: HWND is not an int32.
    user32.GetForegroundWindow.restype = W.HWND
    user32.GetAncestor.argtypes = [W.HWND, W.UINT]
    user32.GetAncestor.restype = W.HWND
    user32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    user32.GetWindowThreadProcessId.restype = W.DWORD
    user32.GetGUIThreadInfo.argtypes = [W.DWORD, C.POINTER(GUIINFO)]
    user32.GetGUIThreadInfo.restype = W.BOOL
    user32.IsWindow.argtypes = [W.HWND]
    user32.IsWindow.restype = W.BOOL
    user32.SendInput.argtypes = [W.UINT, C.POINTER(INPUT), C.c_int]
    user32.SendInput.restype = W.UINT
    user32.GetAsyncKeyState.argtypes = [C.c_int]
    user32.GetAsyncKeyState.restype = W.SHORT
    user32.RegisterHotKey.argtypes = [W.HWND, C.c_int, W.UINT, W.UINT]
    user32.UnregisterHotKey.argtypes = [W.HWND, C.c_int]
    user32.GetMessageW.argtypes = [C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
    user32.PostThreadMessageW.argtypes = [W.DWORD, W.UINT, W.WPARAM, W.LPARAM]
    user32.CallWindowProcW.argtypes = [C.c_void_p, W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    user32.CallWindowProcW.restype = LONG_PTR
    get_long = user32.GetWindowLongPtrW if C.sizeof(C.c_void_p) == 8 else user32.GetWindowLongW
    set_long = user32.SetWindowLongPtrW if C.sizeof(C.c_void_p) == 8 else user32.SetWindowLongW
    get_long.argtypes = [W.HWND, C.c_int]
    get_long.restype = LONG_PTR
    set_long.argtypes = [W.HWND, C.c_int, LONG_PTR]
    set_long.restype = LONG_PTR


def capture_target(allow_own_process=False):
    if not IS_WINDOWS:
        return None
    window = user32.GetForegroundWindow()
    pid = W.DWORD()
    thread = user32.GetWindowThreadProcessId(window, C.byref(pid))
    if not window or (pid.value == os.getpid() and not allow_own_process):
        return None
    info = GUIINFO(cbSize=C.sizeof(GUIINFO))
    if not user32.GetGUIThreadInfo(thread, C.byref(info)) or not info.hwndFocus:
        return None
    return InputTarget(int(window), int(info.hwndFocus), pid.value,
                       int(info.hwndCaret or 0), info.rcCaret.left, info.rcCaret.top)


def target_is_current(target, check_caret=True):
    current = capture_target(allow_own_process=True)
    if not target or not current:
        return False
    if check_caret:
        return current == target
    return (current.window, current.focus, current.process) == (target.window, target.focus, target.process)


def modifiers_pressed():
    return IS_WINDOWS and any(user32.GetAsyncKeyState(key) & 0x8000
                              for key in (0x10, 0x11, 0x12, 0x5B, 0x5C))


def insert_text(target: InputTarget, text: str, method="Clipboard paste", schedule=None, owner=None):
    """Insert Unicode into the original, still-focused input, without Enter.

    Focus is checked again for each batch. If the user changes applications or
    fields, keep the transcript in the editor instead of typing into a new place.
    UIPI can block insertion into an elevated app; callers must retain the text.
    """
    if not IS_WINDOWS or not target_is_current(target):
        raise RuntimeError("Destination changed. Your text is ready in the editor; use Copy.")
    if modifiers_pressed():
        raise RuntimeError("Release shortcut keys before inserting text. Your text is in the editor.")
    # Do not send control characters that could submit a chat or form.
    text = " ".join(text.split())
    if not text:
        return None
    if method == "Clipboard paste":
        from clipboard_insertion import ClipboardPaste
        transaction = ClipboardPaste(owner or target.window)
        transaction.prepare(text)
        try:
            # Preparation can take time when another app renders clipboard data.
            if not target_is_current(target) or modifiers_pressed():
                raise RuntimeError("Destination changed. Your text is ready in the editor; use Copy.")
            # A single Unicode paste avoids VK_PACKET conversion to '?' in ANSI
            # message loops and avoids flooding browser inputs with key events.
            # Shift+Insert is layout-independent; Ctrl+V may become a Persian
            # letter in Tk and other apps when a non-Latin keyboard is active.
            events = (INPUT * 4)(
                INPUT(type=1, ki=KEYBDINPUT(wVk=0x10)),
                INPUT(type=1, ki=KEYBDINPUT(wVk=0x2D, dwFlags=1)),
                INPUT(type=1, ki=KEYBDINPUT(wVk=0x2D, dwFlags=3)),
                INPUT(type=1, ki=KEYBDINPUT(wVk=0x10, dwFlags=2)))
            if user32.SendInput(4, events, C.sizeof(INPUT)) != 4:
                # Ensure a partial SendInput cannot leave our modifier held.
                releases = (INPUT * 2)(events[2], events[3])
                user32.SendInput(2, releases, C.sizeof(INPUT))
                raise RuntimeError("Windows blocked paste. Your text is in the editor; use Copy.")
        except Exception:
            transaction.restore(schedule)
            raise
        # Leave time for asynchronous editors to read the clipboard. Tk schedules
        # this so the wave, Cancel, and main window remain responsive.
        if schedule:
            schedule(900, lambda: transaction.restore(schedule))
        else:
            import time
            time.sleep(.9)
            transaction.restore()
        return transaction
    encoded = text.encode("utf-16-le")
    units = [int.from_bytes(encoded[i:i+2], "little") for i in range(0, len(encoded), 2)]
    for offset in range(0, len(units), 160):
        # Our own inserted characters move the caret; only check window/focus here.
        if not target_is_current(target, check_caret=False):
            raise RuntimeError("Destination changed during insertion. Check the input before copying the rest.")
        batch = units[offset:offset+160]
        events = (INPUT * (2 * len(batch)))()
        for index, unit in enumerate(batch):
            events[index * 2] = INPUT(type=1, ki=KEYBDINPUT(wScan=unit, dwFlags=0x0004))
            events[index * 2 + 1] = INPUT(type=1, ki=KEYBDINPUT(wScan=unit, dwFlags=0x0006))
        if user32.SendInput(len(events), events, C.sizeof(INPUT)) != len(events):
            raise RuntimeError("Windows blocked text insertion. Open the destination normally, or use Copy.")


class GlobalHotkey:
    def __init__(self, name, emit):
        self.name = name
        self.emit = emit
        self.thread_id = None
        self.ready = threading.Event()
        self.registered = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="voice-hotkey")
        self.thread.start()

    def _run(self):
        if not IS_WINDOWS:
            self.ready.set()
            return
        self.thread_id = kernel32.GetCurrentThreadId()
        mods, key = HOTKEYS[self.name]
        self.registered = bool(user32.RegisterHotKey(None, 1, mods | 0x4000, key))
        self.ready.set()
        self.emit("hotkey_status", (self.name, self.registered))
        if not self.registered:
            return
        msg = W.MSG()
        try:
            while user32.GetMessageW(C.byref(msg), None, 0, 0) > 0:
                if msg.message == 0x0312:
                    self.emit("hotkey", capture_target())
        finally:
            user32.UnregisterHotKey(None, 1)

    def close(self):
        self.ready.wait(1)
        if IS_WINDOWS and self.thread_id and self.registered:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(timeout=1)


class NonActivatingWindow:
    """Keep the caret in the destination when the user clicks the wave."""
    def __init__(self, window):
        self.hwnd = None
        if not IS_WINDOWS:
            return
        window.update_idletasks()
        self.hwnd = user32.GetAncestor(window.winfo_id(), 2)
        style = get_long(self.hwnd, -20)
        set_long(self.hwnd, -20, (style | 0x08000000 | 0x80) & ~0x40000)
        self.original = get_long(self.hwnd, -4)
        def procedure(hwnd, msg, wparam, lparam):
            if msg == 0x0021:  # WM_MOUSEACTIVATE -> MA_NOACTIVATE
                return 3
            return user32.CallWindowProcW(self.original, hwnd, msg, wparam, lparam)
        self.callback = WNDPROC(procedure)  # Retain callback for the whole window lifetime.
        set_long(self.hwnd, -4, C.cast(self.callback, C.c_void_p).value)

    def position(self):
        if not self.hwnd:
            return None
        user32.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
        rect = W.RECT()
        return (rect.left, rect.top) if user32.GetWindowRect(self.hwnd, C.byref(rect)) else None

    def move_to(self, x, y, width, height):
        """Use physical desktop coordinates, including negative secondary monitors.

        Tk's negative geometry offsets mean 'from the right edge', so using
        geometry strings would restore negative monitor coordinates incorrectly.
        A disconnected monitor maps to the nearest remaining work area.
        """
        if not self.hwnd:
            return
        class MonitorInfo(C.Structure):
            _fields_ = [("size", W.DWORD), ("monitor", W.RECT), ("work", W.RECT), ("flags", W.DWORD)]
        user32.MonitorFromPoint.argtypes = [W.POINT, W.DWORD]
        user32.MonitorFromPoint.restype = W.HANDLE
        user32.GetMonitorInfoW.argtypes = [W.HANDLE, C.POINTER(MonitorInfo)]
        user32.SetWindowPos.argtypes = [W.HWND, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.UINT]
        monitor = user32.MonitorFromPoint(W.POINT(int(x+width/2), int(y+height/2)), 2)
        info = MonitorInfo(size=C.sizeof(MonitorInfo))
        if user32.GetMonitorInfoW(monitor, C.byref(info)):
            x = max(info.work.left, min(int(x), info.work.right-width))
            y = max(info.work.top, min(int(y), info.work.bottom-height))
        user32.SetWindowPos(self.hwnd, None, int(x), int(y), 0, 0, 0x0015)  # NOACTIVATE | NOZORDER | NOSIZE

    def close(self):
        if self.hwnd and user32.IsWindow(self.hwnd):
            set_long(self.hwnd, -4, self.original)
        self.hwnd = None


class SingleInstance:
    """A named mutex prevents duplicate microphone owners and shortcut conflicts."""
    def __init__(self, project_path):
        self.handle = None
        self.already_running = False
        if IS_WINDOWS:
            kernel32.CreateMutexW.argtypes = [C.c_void_p, W.BOOL, W.LPCWSTR]
            kernel32.CreateMutexW.restype = W.HANDLE
            kernel32.CloseHandle.argtypes = [W.HANDLE]
            digest = hashlib.sha256(str(project_path).lower().encode()).hexdigest()[:20]
            self.handle = kernel32.CreateMutexW(None, False, "Local\\NRVoiceStudio-" + digest)
            self.already_running = C.get_last_error() == 183
            if not self.handle:
                raise C.WinError(C.get_last_error())

    def show_existing(self):
        if IS_WINDOWS:
            user32.FindWindowW.argtypes = [W.LPCWSTR, W.LPCWSTR]
            user32.FindWindowW.restype = W.HWND
            user32.ShowWindow.argtypes = [W.HWND, C.c_int]
            user32.SetForegroundWindow.argtypes = [W.HWND]
            hwnd = user32.FindWindowW(None, f"{APP_NAME} · Offline")
            if hwnd:
                user32.ShowWindow(hwnd, 9)
                user32.SetForegroundWindow(hwnd)

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None
