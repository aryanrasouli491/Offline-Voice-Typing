# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Transactional Unicode paste, with restoration of the previous clipboard.

Keep native handles out of Tk. All allocations are prepared before replacing
the clipboard; an unsupported format or a locked clipboard leaves it untouched.
The delayed restore never overwrites something the user copied in the meantime.
"""
import ctypes as C
from ctypes import wintypes as W
import time
import uuid

from windows_integration import IS_WINDOWS


if IS_WINDOWS:
    user = C.WinDLL("user32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    gdi = C.WinDLL("gdi32", use_last_error=True)
    for name, args, result in (
        ("OpenClipboard", [W.HWND], W.BOOL),
        ("CloseClipboard", [], W.BOOL),
        ("EmptyClipboard", [], W.BOOL),
        ("EnumClipboardFormats", [W.UINT], W.UINT),
        ("GetClipboardData", [W.UINT], W.HANDLE),
        ("SetClipboardData", [W.UINT, W.HANDLE], W.HANDLE),
        ("GetClipboardSequenceNumber", [], W.DWORD),
        ("RegisterClipboardFormatW", [W.LPCWSTR], W.UINT),
        ("CopyImage", [W.HANDLE, W.UINT, C.c_int, C.c_int, W.UINT], W.HANDLE),
    ):
        function = getattr(user, name)
        function.argtypes, function.restype = args, result
    for name, args, result in (
        ("GlobalAlloc", [W.UINT, C.c_size_t], W.HGLOBAL),
        ("GlobalSize", [W.HGLOBAL], C.c_size_t),
        ("GlobalLock", [W.HGLOBAL], C.c_void_p),
        ("GlobalUnlock", [W.HGLOBAL], W.BOOL),
        ("GlobalFree", [W.HGLOBAL], W.HGLOBAL),
    ):
        function = getattr(kernel, name)
        function.argtypes, function.restype = args, result
    gdi.CopyEnhMetaFileW.argtypes = [W.HANDLE, W.LPCWSTR]
    gdi.CopyEnhMetaFileW.restype = W.HANDLE
    gdi.DeleteEnhMetaFile.argtypes = [W.HANDLE]
    gdi.DeleteObject.argtypes = [W.HANDLE]


def _allocate(data):
    handle = kernel.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE: required by Windows.
    if not handle:
        raise RuntimeError("Not enough memory to prepare clipboard text.")
    pointer = kernel.GlobalLock(handle)
    if not pointer:
        kernel.GlobalFree(handle)
        raise RuntimeError("Could not prepare clipboard text.")
    try:
        C.memmove(pointer, data, len(data))
    finally:
        kernel.GlobalUnlock(handle)
    return handle


def _free(format_id, handle):
    if format_id in (2, 0x0082):  # Bitmap handles are not global memory.
        gdi.DeleteObject(handle)
    elif format_id == 14:
        gdi.DeleteEnhMetaFile(handle)
    else:
        kernel.GlobalFree(handle)


def _duplicate(format_id, handle):
    if format_id in (2, 0x0082):
        duplicate = user.CopyImage(handle, 0, 0, 0, 0x2000)  # LR_CREATEDIBSECTION
    elif format_id == 14:
        duplicate = gdi.CopyEnhMetaFileW(handle, None)
    elif format_id in (3, 9, 0x0080, 0x0083) or 0x0200 <= format_id <= 0x02ff:
        # Palette/metafile/private GDI handles have special ownership rules.
        raise RuntimeError("This clipboard format cannot be preserved. Choose Unicode typing in Settings, or use Copy text.")
    else:
        size = kernel.GlobalSize(handle)
        if not size or size > 64 * 1024 * 1024:
            raise RuntimeError("Clipboard content cannot be preserved safely. Use Copy text or Unicode typing.")
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            raise RuntimeError("Clipboard content is unavailable. Use Copy text.")
        try:
            duplicate = _allocate(C.string_at(pointer, size))
        finally:
            kernel.GlobalUnlock(handle)
    if not duplicate:
        raise RuntimeError("Could not preserve clipboard content. Use Copy text.")
    return duplicate


class ClipboardPaste:
    """Own backup handles until restoration or an unrelated clipboard change."""
    def __init__(self, owner):
        self.owner = owner
        self.saved = []
        self.sequence = None
        self.marker_format = None
        self.marker = uuid.uuid4().bytes
        self.closed = False

    def prepare(self, text):
        replacement = _allocate(text.encode("utf-16-le") + b"\0\0")
        excluded = None
        marker_handle = None
        opened = False
        changed = False
        try:
            opened = bool(user.OpenClipboard(self.owner))
            if not opened:
                raise RuntimeError("Clipboard is busy. Your transcript is ready here; try Copy text.")
            format_id = 0
            while True:
                C.set_last_error(0)
                format_id = user.EnumClipboardFormats(format_id)
                if not format_id:
                    if C.get_last_error():
                        raise RuntimeError("Could not read the clipboard. Use Copy text.")
                    break
                handle = user.GetClipboardData(format_id)
                if not handle:
                    raise RuntimeError("Clipboard content is not available yet. Use Copy text.")
                self.saved.append((format_id, _duplicate(format_id, handle)))
            if not user.EmptyClipboard():
                raise RuntimeError("Could not prepare the clipboard. Use Copy text.")
            changed = True
            if not user.SetClipboardData(13, replacement):  # CF_UNICODETEXT, never ANSI.
                raise RuntimeError("Could not prepare Unicode paste. Your text is in the editor.")
            replacement = None  # Windows owns this allocation after a successful set.
            # Transient dictation should not enter Windows cloud clipboard/history.
            excluded = _allocate(b"\0\0\0\0")
            privacy_format = user.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
            if not privacy_format or not user.SetClipboardData(privacy_format, excluded):
                raise RuntimeError("Could not prepare private clipboard paste. Use Unicode typing.")
            excluded = None
            self.marker_format = user.RegisterClipboardFormatW("NRVoiceStudio.PasteTransaction")
            marker_handle = _allocate(self.marker)
            if not self.marker_format or not user.SetClipboardData(self.marker_format, marker_handle):
                raise RuntimeError("Could not track clipboard ownership. Use Unicode typing.")
            marker_handle = None
            self.sequence = user.GetClipboardSequenceNumber()
        except Exception:
            if changed:
                self._restore_open()
            else:
                self.discard()
            raise
        finally:
            if opened:
                user.CloseClipboard()
            if replacement:
                kernel.GlobalFree(replacement)
            if excluded:
                kernel.GlobalFree(excluded)
            if marker_handle:
                kernel.GlobalFree(marker_handle)

    def _still_owns_clipboard(self):
        """Synthesizing ANSI/OEM text can change the sequence during a paste.

        A private per-paste marker survives those Windows conversions. A real
        user Copy clears it, so it is a better ownership guard than sequence alone.
        Call only while the clipboard is locked by this process.
        """
        if not self.marker_format:
            return False
        handle = user.GetClipboardData(self.marker_format)
        if not handle or kernel.GlobalSize(handle) != len(self.marker):
            return False
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            return False
        try:
            return C.string_at(pointer, len(self.marker)) == self.marker
        finally:
            kernel.GlobalUnlock(handle)

    def _restore_open(self):
        if not user.EmptyClipboard():
            raise RuntimeError("Could not restore the previous clipboard.")
        remaining = []
        for format_id, handle in self.saved:
            if not user.SetClipboardData(format_id, handle):
                remaining.append((format_id, handle))
        self.saved = remaining
        self.discard()

    def restore(self, schedule=None, attempt=0):
        if self.closed:
            return
        if not user.OpenClipboard(self.owner):
            if attempt < 8:
                if schedule:
                    schedule(120, lambda: self.restore(schedule, attempt+1))
                    return
                time.sleep(.12)
                return self.restore(attempt=attempt+1)
            self.discard()
            return
        try:
            # Compare while holding the lock; a concurrent Copy must win.
            if self._still_owns_clipboard():
                self._restore_open()
            else:
                self.discard()
        finally:
            user.CloseClipboard()

    def discard(self):
        for format_id, handle in self.saved:
            _free(format_id, handle)
        self.saved.clear()
        self.closed = True
