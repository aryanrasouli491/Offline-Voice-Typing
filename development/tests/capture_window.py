# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Capture only a test-owned HWND to PNG, using the Windows GDI and stdlib."""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import struct
import zlib


def capture(hwnd, path):
    user = C.WinDLL("user32", use_last_error=True)
    gdi = C.WinDLL("gdi32", use_last_error=True)
    user.GetWindowDC.argtypes = [W.HWND]
    user.GetWindowDC.restype = W.HDC
    user.ReleaseDC.argtypes = [W.HWND, W.HDC]
    user.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
    user.PrintWindow.argtypes = [W.HWND, W.HDC, W.UINT]
    gdi.CreateCompatibleDC.argtypes = [W.HDC]
    gdi.CreateCompatibleDC.restype = W.HDC
    gdi.CreateCompatibleBitmap.argtypes = [W.HDC, C.c_int, C.c_int]
    gdi.CreateCompatibleBitmap.restype = W.HBITMAP
    gdi.SelectObject.argtypes = [W.HDC, W.HANDLE]
    gdi.SelectObject.restype = W.HANDLE
    gdi.GetDIBits.argtypes = [W.HDC, W.HBITMAP, W.UINT, W.UINT, C.c_void_p, C.c_void_p, W.UINT]
    gdi.DeleteObject.argtypes = [W.HANDLE]
    gdi.DeleteDC.argtypes = [W.HDC]
    rect = W.RECT()
    user.GetWindowRect(hwnd, C.byref(rect))
    width, height = rect.right-rect.left, rect.bottom-rect.top
    dc = user.GetWindowDC(hwnd)
    memory = gdi.CreateCompatibleDC(dc)
    bitmap = gdi.CreateCompatibleBitmap(dc, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if not user.PrintWindow(hwnd, memory, 2):
            raise RuntimeError("PrintWindow failed")
        gdi.SelectObject(memory, previous)
        header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, width*height*4, 0, 0, 0, 0)
        info = C.create_string_buffer(header + bytes(16))
        data = C.create_string_buffer(width*height*4)
        if not gdi.GetDIBits(memory, bitmap, 0, height, data, info, 0):
            raise RuntimeError("GetDIBits failed")
        import numpy as np
        bgra = np.frombuffer(data.raw, dtype=np.uint8).reshape(height, width, 4)
        rgb = bgra[:, :, [2, 1, 0]]
        scanlines = b"".join(b"\x00" + row.tobytes() for row in rgb)
        def chunk(kind, content):
            return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind+content)&0xffffffff)
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        Path(path).write_bytes(png + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b""))
    finally:
        gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, dc)
