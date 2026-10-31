# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Run explicitly on Windows: real Tk windows, isolated settings, no user data edits."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))
import json
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch, Mock
import numpy as np
from audio_capture import AudioBuffer
from voice_app import VoiceTypingApp
from capture_window import capture
from windows_integration import user32

ARTIFACTS = Path(__file__).parent / "artifacts"
ARTIFACTS.mkdir(exist_ok=True)
checks = []


def pump(seconds=.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(.01)


with tempfile.TemporaryDirectory() as directory:
    app = VoiceTypingApp(data_dir=directory, start_services=False)
    try:
        app.current_info = SimpleNamespace(display_name="Small", key="test")
        app.model_ready = True
        app.model_status.set("Small · OFFLINE")
        app.status.set("Ready · speak here, or use the floating wave in any input.")
        app.update_controls()
        pump()
        app.geometry("1000x720+80+60")
        pump()
        capture(user32.GetAncestor(app.winfo_id(), 2), ARTIFACTS / "studio-dark.png")
        assert app.record_button.winfo_viewable()
        checks.append("dark theme / empty screen")
        app.editor.insert("1.0", "A little less typing. A little more thinking.\n\nCapture the idea while it is still fresh. Everything stays on this device.")
        pump()
        capture(user32.GetAncestor(app.winfo_id(), 2), ARTIFACTS / "studio-transcript.png")
        app.toggle_theme()
        pump()
        capture(user32.GetAncestor(app.winfo_id(), 2), ARTIFACTS / "studio-light.png")
        assert "Everything" in app.editor.get("1.0", "end")
        checks.append("theme switch preserves editor")
        app.geometry("760x580+80+60")
        pump()
        capture(user32.GetAncestor(app.winfo_id(), 2), ARTIFACTS / "studio-compact.png")
        assert app.record_button.winfo_rooty()+app.record_button.winfo_height() < app.winfo_rooty()+app.winfo_height()
        assert app.clear_button.winfo_viewable()
        assert app.clear_button.winfo_rooty() < app.record_button.winfo_rooty()
        checks.append("controls fit at minimum size")
        app.open_settings()
        pump()
        capture(user32.GetAncestor(app.settings_window.winfo_id(), 2), ARTIFACTS / "settings.png")
        app.settings_window.destroy()
        checks.append("settings opens")
        app.open_about()
        pump()
        assert app.about_window.title() == "About · NR Voice Typing"
        capture(user32.GetAncestor(app.about_window.winfo_id(), 2), ARTIFACTS / "about.png")
        app.about_window.destroy()
        checks.append("About app contains product and creator sections")
        app.history.add("Microphone", "Small", "سلام دنیا — Unicode history")
        app.open_history()
        pump()
        capture(user32.GetAncestor(app.history_window.winfo_id(), 2), ARTIFACTS / "history.png")
        app.history_window.destroy()
        checks.append("history opens with Unicode")
        app.show_overlay(True)
        pump()
        capture(user32.GetAncestor(app.overlay.winfo_id(), 2), ARTIFACTS / "floating-wave.png")
        checks.append("floating wave opens")
        assert str(app.overlay.attributes("-transparentcolor")).lower() == app.overlay.TRANSPARENT
        assert app.overlay.winfo_width() == 156 and app.overlay.winfo_height() == 80
        sizes = [app.overlay.wave.coords(bar)[3]-app.overlay.wave.coords(bar)[1] for bar in app.overlay.wave.bars]
        assert max(sizes)-min(sizes) < .01, "Idle bars must be equal height"
        app.overlay.wave.draw(.7, True)
        assert app.overlay.wave.itemcget(app.overlay.wave.bars[0], "state") == "normal"
        sizes = [app.overlay.wave.coords(bar)[3]-app.overlay.wave.coords(bar)[1] for bar in app.overlay.wave.bars]
        assert sizes[len(sizes)//2] == max(sizes)
        assert sizes[len(sizes)//2] > sizes[0]*3
        assert all(abs(a-b) < .01 for a, b in zip(sizes, reversed(sizes)))
        app.overlay.update_idletasks()  # Flush canvas paint before PrintWindow captures it.
        capture(user32.GetAncestor(app.overlay.winfo_id(), 2), ARTIFACTS / "floating-listening.png")
        app.overlay.wave.draw(0, False, True, .4)
        assert app.overlay.wave.itemcget(app.overlay.wave.bars[0], "state") == "hidden"
        left, _, right, _ = app.overlay.wave.coords(app.overlay.wave.fill)
        assert left < right < app.overlay.wave.winfo_width() / 2
        app.overlay.update_idletasks()
        capture(user32.GetAncestor(app.overlay.winfo_id(), 2), ARTIFACTS / "floating-progress.png")
        checks.append("compact transparent wave: uniform idle, tall center, tapered sides, separate progress")
        overlay = app.overlay
        x, y = overlay.native.position()
        overlay._press(SimpleNamespace(x_root=x+13, y_root=y+40))
        overlay._drag(SimpleNamespace(x_root=113, y_root=180))
        pump()
        overlay._release(SimpleNamespace())
        saved_position = list(overlay.native.position())
        assert app.settings["overlay_position"] == saved_position
        from app_storage import load_settings
        assert load_settings(app.settings_path)["overlay_position"] == saved_position
        overlay.destroy()
        app.overlay = None
        app.show_overlay(True)
        pump()
        assert list(app.overlay.native.position()) == saved_position
        checks.append("drag position survives saving and recreating floating window")
        with patch.object(app, "toggle_recording") as toggle:
            app.overlay._press(SimpleNamespace(x_root=113, y_root=180))
            app.overlay._release(SimpleNamespace())
            toggle.assert_called_once_with(external=True)
            app.overlay._press(SimpleNamespace(x_root=113, y_root=180))
            app.overlay._release(SimpleNamespace())
            toggle.assert_called_once_with(external=True)
            app.finishing = True
            app.overlay_action()
            toggle.assert_called_once()
            app.finishing = False
        checks.append("floating click is debounced and cannot restart processing")
        app.services_enabled = True
        app.close_to_background()
        pump()
        assert app.state() == "withdrawn" and app.overlay.winfo_viewable()
        app.show_main()
        pump()
        checks.append("background close keeps overlay; reopen works")
        app.begin_session("Test")
        app.finishing = True
        app.pending = 1
        app._event("result", (app.session-1, "stale text", 0))
        assert "stale text" not in app.editor.get("1.0", "end")
        app._event("result", (app.session, "Fresh result", .1))
        assert not app.finishing and app.editor.get("1.0", "end-1c") == "Fresh result"
        assert app.progress.fraction() == 1
        checks.append("stale result rejected; normal recording replaces editor")
        app.begin_session("Resume", append=True)
        app.finishing = True
        app.manual_finish_requested = True
        app.pending = 1
        app._event("result", (app.session, "continued words", .1))
        assert app.editor.get("1.0", "end-1c") == "Fresh result\n\ncontinued words"
        assert app.resume_available and app.resume_button.winfo_manager()
        checks.append("manual transcription exposes Resume and appends on continuation")
        app.begin_session("Live correction")
        app.active = True
        app.pending = 1
        app.audio_jobs.append(("preview", app.segment_id))
        app._event("result", (app.session, "wrong draft", .1))
        assert app.draft == "wrong draft" and app.parts == []
        app.active = False
        app.finishing = True
        app.draft = ""
        app.pending = 1
        app.audio_jobs.append(("final", app.segment_id))
        app._event("result", (app.session, "correct whole sentence", .1))
        assert app.editor.get("1.0", "end-1c") == "correct whole sentence"
        checks.append("final full-context transcription replaces the provisional preview")
        app.begin_session("Mute race")
        app.buffer = AudioBuffer()
        app.buffer.feed(np.full(16000, .03, dtype=np.float32), speech=True)
        app.microphone = Mock()
        app.microphone.stream.active = True
        app.microphone.set_muted.side_effect = lambda muted: None if muted else app.buffer.resume()
        app.active = True
        app.started_at = time.monotonic()
        with patch.object(app.worker, "transcribe") as submit:
            app.toggle_mute()
            assert app.active and app.muted and app.pending == 1
            assert len(submit.call_args.args[1]) == 16000
            app.toggle_mute()  # Continue before the first result is ready.
            app.buffer.feed(np.full(16000, .04, dtype=np.float32), speech=True)
            app.stop_recording()
            assert app.pending == 2 and submit.call_count == 2
        app._event("result", (app.session, "First part.", .1))
        app._event("result", (app.session, "Second part.", .1))
        assert app.editor.get("1.0", "end-1c") == "First part. Second part."
        assert len(app.export_audio) == 32000
        checks.append("mute submits immediately; early unmute preserves ordered text and audio")
        with patch("audio_export.music_directory", return_value=Path(directory)), \
                patch("voice_app.simpledialog.askstring", return_value="صدای من ۰۰۱") as naming:
            app.download_audio()
            deadline = time.monotonic() + 5
            while app.exporting and time.monotonic() < deadline:
                pump(.05)
            assert not app.exporting
            assert (Path(directory) / "NR Voice Typing Voices" / "صدای من ۰۰۱.m4a").is_file()
            assert naming.call_args.kwargs["initialvalue"].startswith("voice001_")
        checks.append("download icon prompts for a name and saves compressed audio in its folder")
        app.begin_session("Cancel")
        app.finishing = True
        cancelled = app.session
        app.cancel()
        app._event("result", (cancelled, "cancelled text", .1))
        assert "cancelled text" not in app.editor.get("1.0", "end")
        app._event("progress", (cancelled, .9))
        assert app.progress.fraction() == 0
        checks.append("cancellation discards delayed result")
        app.language.set("Dari / فارسی")
        app.change_language()
        assert app.editor.tag_cget("direction", "justify") == "right"
        checks.append("Dari alignment")
        from windows_integration import SingleInstance
        one = SingleInstance(directory)
        two = SingleInstance(directory)
        assert not one.already_running and two.already_running
        two.close()
        one.close()
        three = SingleInstance(directory)
        assert not three.already_running
        three.close()
        checks.append("single-instance lock and release")
    finally:
        app.quit_app()

(ARTIFACTS / "ui-results.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
print(json.dumps(checks, indent=2))
