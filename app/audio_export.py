# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Small, local AAC voice downloads using the PyAV dependency already installed."""
from datetime import datetime
from pathlib import Path
import ctypes
import os
import re
import uuid

import numpy as np


def music_directory():
    """Resolve Windows' real Music folder, including a redirected location."""
    if os.name == "nt":
        folder_id = (ctypes.c_ubyte * 16).from_buffer_copy(
            uuid.UUID("4BD8D571-6D19-48D3-BE97-422220080E43").bytes_le)
        result = ctypes.c_wchar_p()
        shell = ctypes.OleDLL("shell32")
        shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                              ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        status = shell.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(result))
        if status != 0:
            raise OSError("Windows could not locate your Music folder.")
        try:
            return Path(result.value)
        finally:
            ole = ctypes.OleDLL("ole32")
            ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            ole.CoTaskMemFree(ctypes.cast(result, ctypes.c_void_p))
    return Path.home() / "Music"


def default_voice_name(folder):
    # Scan once even if the user has saved thousands of recordings.
    used = {int(match[1]) for path in Path(folder).glob("voice*_*.m4a")
            if (match := re.match(r"voice(\d+)_", path.name))}
    index = 1
    while index in used:
        index += 1
    return f"voice{index:03d}_{datetime.now():%Y-%m-%d_%H-%M-%S}"


def validate_name(name):
    # Keep Unicode intact: Dari/Persian names need no transliteration or encoding.
    name = name.strip()
    if name.lower().endswith(".m4a"):
        name = name[:-4]
    if (not name or len(name) > 150 or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.endswith((".", " "))
            or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name)):
        raise ValueError("Choose a filename without slashes or Windows-reserved characters.")
    return name


def save_voice(audio, folder, name):
    """Export mono 16 kHz AAC at 32 kbit/s (about 240 KB per minute).

    Never overwrite another recording. Export is called on a background thread;
    the original float samples remain available for recognition and repeat saves.
    """
    import av
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
        raise ValueError("The recording is empty or contains invalid audio samples.")
    folder = Path(folder)
    name = validate_name(name)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + ".m4a")
    number = 2
    while True:
        try:
            output = path.open("xb")
            break
        except FileExistsError:
            path = folder / f"{name} ({number}).m4a"
            number += 1
    try:
        with output, av.open(output, mode="w", format="mp4") as container:
            stream = container.add_stream("aac", rate=16000)
            stream.layout = "mono"
            stream.bit_rate = 32000
            for start in range(0, len(audio), 16000):
                samples = np.clip(audio[start:start+16000], -1, 1).astype(np.float32)
                frame = av.AudioFrame.from_ndarray(samples[None, :], format="fltp", layout="mono")
                frame.sample_rate = 16000
                frame.pts = start
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
    except Exception:
        output.close()
        path.unlink(missing_ok=True)
        raise
    return path
