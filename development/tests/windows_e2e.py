# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Real Win32 hotkeys/input, real Whisper, and real microphone smoke test.

Text insertion targets only input_host.py, a disposable process owned by this test.
The microphone device check discards audio; recognition uses a local SAPI fixture.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))
import ctypes as C
import json
import subprocess
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from audio_capture import AudioBuffer, Microphone
from speech_engine import DEFAULT_MODELS_DIR, discover_models, load_audio_mono
from voice_app import VoiceTypingApp
from windows_integration import (user32, INPUT, KEYBDINPUT, MOUSEINPUT,
                                 GlobalHotkey, capture_target, insert_text, target_is_current)
from capture_window import capture
from clipboard_insertion import ClipboardPaste

ARTIFACTS = Path(__file__).parent / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)
results = {}
app = None
original_clipboard = None
progress_samples = []


def pump(seconds=.15):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        app.update()
        if app.finishing:
            progress_samples.append(app.display_progress)
        time.sleep(.01)


def until(predicate, timeout=30):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        pump(.05)
        if predicate():
            return
    raise AssertionError("Timed out: " + app.status.get() + " host=" + repr(state()))


def keys(sequence):
    inputs = (INPUT * len(sequence))()
    for index, (key, up) in enumerate(sequence):
        inputs[index] = INPUT(type=1, ki=KEYBDINPUT(wVk=key, dwFlags=2 if up else 0))
    assert user32.SendInput(len(inputs), inputs, C.sizeof(INPUT)) == len(inputs)


def shortcut(name):
    """Send whichever supported shortcut is free on the test machine."""
    sequences = {
        "Ctrl+Alt+Space": [(0x11, False), (0x12, False), (0x20, False),
                           (0x20, True), (0x12, True), (0x11, True)],
        "Ctrl+Shift+Space": [(0x11, False), (0x10, False), (0x20, False),
                             (0x20, True), (0x10, True), (0x11, True)],
        "Ctrl+Alt+D": [(0x11, False), (0x12, False), (0x44, False),
                       (0x44, True), (0x12, True), (0x11, True)],
    }
    keys(sequences[name])


class FixtureMicrophone:
    def __init__(self, buffer):
        self.buffer = buffer
        self.stream = SimpleNamespace(active=True)
    def start(self, device=None):
        pass
    def set_muted(self, muted):
        if not muted:
            self.buffer.resume()
    def close(self):
        self.stream.active = False


with tempfile.TemporaryDirectory() as temp:
    directory = Path(temp)
    host_log = (ARTIFACTS / "input-host.log").open("w", encoding="utf-8")
    host = subprocess.Popen([sys.executable, str(Path(__file__).with_name("input_host.py")), temp],
                            creationflags=subprocess.CREATE_NO_WINDOW, stdout=host_log, stderr=host_log)
    def state():
        try:
            return json.loads((directory / "host-state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
    def command(action):
        identity = str(time.monotonic_ns())
        temporary = directory / "command.tmp"
        temporary.write_text(json.dumps({"id": identity, "action": action}))
        temporary.replace(directory / "command.json")
        until(lambda: state().get("command") == identity)
    def focus(field="first"):
        command(field)
        # Windows may reject programmatic foreground changes when the runner did
        # not receive the last user input. Click only our own, temporarily topmost
        # host window instead of depending on SetForegroundWindow's permission.
        hwnd = state()["hwnd"]
        user32.SetWindowPos.argtypes = [C.wintypes.HWND, C.wintypes.HWND, C.c_int, C.c_int, C.c_int, C.c_int, C.c_uint]
        old_point = C.wintypes.POINT()
        user32.GetCursorPos(C.byref(old_point))
        try:
            assert user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
            x, y = state()[field + "_point"]
            user32.SetCursorPos(x, y)
            click = (INPUT * 2)(INPUT(type=0, mi=MOUSEINPUT(dwFlags=2)),
                                INPUT(type=0, mi=MOUSEINPUT(dwFlags=4)))
            assert user32.SendInput(2, click, C.sizeof(INPUT)) == 2
            pump(.15)
        finally:
            user32.SetCursorPos(old_point.x, old_point.y)
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0013)
        # A venv python.exe can be a launcher whose child owns the actual window.
        until(lambda: capture_target() and capture_target().process == state().get("pid"))
        return capture_target()
    try:
        app = VoiceTypingApp(data_dir=directory / "app", start_services=False)
        def trace_emit(kind, payload=None):
            if kind in ("result", "transcribe_error"):
                print(kind, repr(payload), flush=True)
            app.emit(kind, payload)
        app.worker.emit = trace_emit
        original_event = app._event
        def trace_event(kind, payload):
            if kind == "hotkey":
                print("hotkey target", repr(payload), flush=True)
            original_event(kind, payload)
        app._event = trace_event
        app.geometry("900x660+700+60")
        until(lambda: state().get("hwnd"))
        mic = Microphone(AudioBuffer())
        mic.start()
        pump(.6)
        mic.close()
        assert mic.buffer.samples > 0
        results["physical_microphone"] = {"samples_received": mic.buffer.samples, "stream_closed": mic.stream is None}
        models, _ = discover_models(DEFAULT_MODELS_DIR)
        app.select_model(next(m for m in models if m.status == "ready"))
        until(lambda: app.model_ready, 60)
        results["model_load_seconds"] = app.worker.adapter.load_seconds
        audio = load_audio_mono(Path(__file__).parent / "fixtures" / "english-fixture.wav")
        if isinstance(audio, tuple):
            audio = audio[0]
        results["fixture_seconds"] = len(audio)/16000
        # The user's installed app may already own the default shortcut. Probe
        # all supported choices so this isolated test never needs to kill it.
        selected_hotkey = None
        for candidate in ("Ctrl+Alt+Space", "Ctrl+Shift+Space", "Ctrl+Alt+D"):
            probe = GlobalHotkey(candidate, lambda *event: None)
            probe.ready.wait(2)
            if probe.registered:
                selected_hotkey = candidate
                probe.close()
                break
            probe.close()
        assert selected_hotkey, "All supported shortcuts are occupied"
        app.settings["hotkey"] = selected_hotkey
        app.set_hotkey()
        until(lambda: app.hotkey.ready.is_set())
        assert app.hotkey.registered, "Shortcut is occupied by another app"
        conflict_events = []
        conflict = GlobalHotkey(app.settings["hotkey"], lambda *event: conflict_events.append(event))
        conflict.ready.wait(2)
        assert not conflict.registered
        conflict.close()
        results["hotkey_conflict_reported"] = True
        app.show_overlay(True)
        pump()
        app.cancel()
        original_clipboard = ClipboardPaste(user32.GetAncestor(app.winfo_id(), 2))
        original_clipboard.prepare("clipboard must survive dictation")
        with patch("voice_app.Microphone", FixtureMicrophone):
            target = focus()
            shortcut(selected_hotkey)
            until(lambda: app.active)
            assert app.target == target
            # Feed the test speech in callback-sized blocks, then 2.1 seconds of silence.
            for i in range(0, len(audio), 800):
                app.buffer.feed(audio[i:i+800])
            app.buffer.feed(np.zeros(33600, dtype=np.float32))
            print("speech buffer", app.buffer.snapshot(), "session", app.session, flush=True)
            until(lambda: not app.busy, 60)
            until(lambda: "text box" in state().get("first", "").lower())
            text = state()["first"]
            assert "hello" in text.lower() and "text box" in text.lower()
            until(lambda: app.clipboard_get() == "clipboard must survive dictation")
            results["hotkey_silence_recognition_insertion"] = text
            results["dictation_processing_seconds"] = app.processing_seconds
            assert progress_samples and max(progress_samples) < 1
            assert all(a <= b for a, b in zip(progress_samples, progress_samples[1:]))
            results["progress_before_real_completion"] = max(progress_samples)
            results["progress_calibration_saved"] = (app.data_dir / "processing_timings.json").is_file()
            results["clipboard_preserved"] = True
            capture(state()["hwnd"], ARTIFACTS / "external-input-result.png")
            command("clear")
            target = focus()
            mixed_text = "سلام دنیا — Unicode 123 — café — " + "این یک آزمایش طولانی برای حفظ تمام حروف است. " * 24
            insert_text(target, mixed_text, owner=user32.GetAncestor(app.winfo_id(), 2))
            until(lambda: state().get("first", "") == mixed_text.strip())
            results["unicode_insertion"] = {"exact_match": True, "characters": len(state()["first"])}
            target = capture_target()
            command("second")
            pump()
            assert not target_is_current(target)
            try:
                insert_text(target, "MUST NOT TYPE")
                raise AssertionError("Changed target accepted")
            except RuntimeError:
                pass
            assert state()["second"] == ""
            results["changed_input_blocks_insertion"] = True
            focus()
            app.overlay.geometry("+680+420")
            pump()
            wave_target = capture_target()
            # Native mouse input verifies WM_MOUSEACTIVATE and real double-clicks.
            old_point = __import__('ctypes').wintypes.POINT()
            user32.GetCursorPos(C.byref(old_point))
            # Only the colored wave/dot catches clicks; keyed background passes through.
            x = app.overlay.dot.winfo_rootx()+13
            y = app.overlay.dot.winfo_rooty()+20
            user32.SetCursorPos(x, y)
            def click():
                events = (INPUT*2)(INPUT(type=0, mi=MOUSEINPUT(dwFlags=2)),
                                   INPUT(type=0, mi=MOUSEINPUT(dwFlags=4)))
                assert user32.SendInput(2, events, C.sizeof(INPUT)) == 2
                pump(.08)
            click()
            until(lambda: app.active)
            assert capture_target() == wave_target, "Wave stole input focus"
            results["wave_click_preserves_caret"] = True
            # A single click is the documented start/stop interaction.
            pump(.6)
            click()
            until(lambda: not app.busy)
            user32.SetCursorPos(old_point.x, old_point.y)
            results["wave_single_click_stops"] = True
            app.live.set(True)
            app.show_main()
            app.start_recording()
            assert app.active
            for i in range(0, len(audio), 800):
                app.buffer.feed(audio[i:i+800])
            app.buffer.feed(np.zeros(9600, dtype=np.float32))
            until(lambda: bool(app.draft), 60)
            assert app.active, "Live result must arrive before recording stops"
            results["live_preview_before_stop"] = app.draft
            app.stop_recording()
            until(lambda: not app.busy)
            assert len(app.parts) == 1, "Final stop repeated a live phrase"
            results["live_final_has_no_duplicate"] = True
            app.start_recording()
            for i in range(0, len(audio), 800):
                app.buffer.feed(audio[i:i+800])
            app.toggle_mute()
            assert app.active and app.muted and app.pending
            until(lambda: not app.pending, 60)
            assert "offline" in app._session_text().lower()
            assert app.active and app.muted
            app.toggle_mute()
            for i in range(0, len(audio), 800):
                app.buffer.feed(audio[i:i+800])
            app.stop_recording()
            until(lambda: not app.busy, 60)
            assert len(app.parts) == 2
            assert len(app.export_audio) == len(audio) * 2
            results["mute_transcribes_unmute_continues_without_duplicates"] = True
            app.begin_session("File test")
            app.finishing = True
            app.submit_audio(Path(__file__).parent / "fixtures" / "english-fixture.wav")
            until(lambda: not app.busy, 60)
            assert "offline" in " ".join(app.parts).lower()
            results["file_transcription"] = True
        app.hotkey.close()
        app.hotkey = None
        replacement = GlobalHotkey(selected_hotkey, lambda *args: None)
        replacement.ready.wait(2)
        assert replacement.registered
        replacement.close()
        results["hotkey_released_on_close"] = True
    finally:
        if app:
            if original_clipboard:
                if app.clipboard_paste:
                    app.clipboard_paste.restore()
                try:
                    if app.clipboard_get() == "clipboard must survive dictation":
                        original_clipboard.restore()
                    else:
                        original_clipboard.discard()
                except Exception:
                    original_clipboard.discard()
            app.quit_app()
        # Ask the actual Tk child to exit; terminating a venv launcher can orphan it.
        (directory / "command.json").write_text(json.dumps({"id": "cleanup", "action": "quit"}))
        try:
            host.wait(timeout=5)
        except subprocess.TimeoutExpired:
            host.terminate()
            host.wait(timeout=5)
        host_log.close()

(ARTIFACTS / "windows-e2e-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(results, ensure_ascii=True, indent=2))
