# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Behavior tests: silence, bounded memory, cancellation, persistence, and inference."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from app_storage import History, load_settings, save_settings
from audio_capture import AudioBuffer, Microphone, SAMPLE_RATE
from speech_engine import FasterWhisperAdapter, TranscriptionCancelled, discover_models
from transcription_worker import TranscriptionWorker
from windows_integration import InputTarget, insert_text


def samples(seconds, amplitude=0):
    return np.full(int(seconds * SAMPLE_RATE), amplitude, dtype=np.float32)


class AudioTests(unittest.TestCase):
    def test_silence_does_not_create_a_transcript(self):
        audio = AudioBuffer()
        audio.feed(samples(10))
        self.assertEqual(audio.snapshot().stop_reason, "No speech detected")
        self.assertIsNone(audio.take_phrase(final=True))

    def test_stops_only_after_the_selected_silence_duration(self):
        audio = AudioBuffer(silence_seconds=2)
        audio.feed(samples(1, .05))
        audio.feed(samples(1.9))
        self.assertFalse(audio.snapshot().stop_reason)
        audio.feed(samples(.1))
        self.assertEqual(audio.snapshot().stop_reason, "Silence detected")

    def test_level_tracks_real_amplitude(self):
        audio = AudioBuffer()
        audio.feed(samples(.1, .01))
        low = audio.snapshot().level
        audio.feed(samples(.1, .06))
        self.assertGreater(audio.snapshot().level, low)
        audio.feed(samples(.1))
        self.assertEqual(audio.snapshot().level, 0)

    def test_short_noise_does_not_count_as_speech(self):
        audio = AudioBuffer()
        audio.feed(samples(.05, .5))
        audio.feed(samples(2))
        self.assertFalse(audio.snapshot().heard_speech)
        self.assertIsNone(audio.take_phrase(final=True))

    def test_live_chunks_are_consumed_once(self):
        audio = AudioBuffer(live=True)
        audio.feed(samples(1, .05))
        audio.feed(samples(.6))
        first = audio.take_phrase()
        self.assertEqual(len(first), 25600)
        self.assertIsNone(audio.take_phrase())
        audio.feed(samples(1, .07))
        second = audio.take_phrase(final=True)
        self.assertEqual(len(second), SAMPLE_RATE)
        self.assertAlmostEqual(float(second.mean()), .07, places=4)

    def test_nonlive_audio_waits_for_finish(self):
        audio = AudioBuffer(live=False)
        audio.feed(samples(13, .05))
        self.assertIsNone(audio.take_phrase())
        self.assertEqual(len(audio.take_phrase(final=True)), 13*SAMPLE_RATE)

    def test_continuous_live_speech_flushes_at_twelve_seconds(self):
        audio = AudioBuffer(live=True)
        audio.feed(samples(12, .05))
        self.assertIsNotNone(audio.take_phrase())
        self.assertEqual(audio.chunk_samples, 0)

    def test_recording_memory_is_bounded(self):
        audio = AudioBuffer()
        for _ in range(150):
            audio.feed(samples(1, .05))
        self.assertEqual(audio.samples, 120*SAMPLE_RATE)
        self.assertTrue(audio.snapshot().stop_reason)

    def test_stream_is_closed_when_start_fails(self):
        stream = Mock()
        stream.start.side_effect = RuntimeError("device busy")
        with patch("sounddevice.InputStream", return_value=stream):
            with self.assertRaises(RuntimeError):
                Microphone(AudioBuffer()).start()
        stream.close.assert_called_once()

    def test_close_runs_even_if_stop_fails(self):
        mic = Microphone(AudioBuffer())
        stream = mic.stream = Mock()
        stream.stop.side_effect = RuntimeError("disconnected")
        with self.assertRaises(RuntimeError):
            mic.close()
        stream.close.assert_called_once()
        self.assertIsNone(mic.stream)

    def test_muted_microphone_keeps_stream_open_but_drops_audio(self):
        stream = Mock()
        with patch("sounddevice.InputStream", return_value=stream) as create:
            mic = Microphone(AudioBuffer())
            mic.start()
            self.addCleanup(mic.close)
        callback = create.call_args.kwargs["callback"]
        block = np.full((800, 1), .05, dtype=np.float32)
        mic.set_muted(True)
        callback(block, 800, None, "")
        self.assertEqual(mic.buffer.samples, 0)
        mic.set_muted(False)
        callback(block, 800, None, "")
        mic.audio_queue.join()
        self.assertEqual(mic.buffer.samples, 800)
        stream.stop.assert_not_called()
        mic.close()


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"

    def test_invalid_json_uses_defaults(self):
        self.path.write_text("broken", encoding="utf-8")
        self.assertEqual(load_settings(self.path)["silence_seconds"], 2)

    def test_untrusted_preferences_are_validated(self):
        self.path.write_text(json.dumps({"height": 10000, "live": "false", "threshold": -1,
                                         "hotkey": "Bad", "theme": "Bad"}), encoding="utf-8")
        settings = load_settings(self.path)
        self.assertEqual(settings["height"], 1000)
        self.assertFalse(settings["live"])
        self.assertEqual(settings["threshold"], .002)
        self.assertEqual(settings["hotkey"], "Ctrl+Alt+Space")

    def test_atomic_save_roundtrip(self):
        settings = load_settings(self.path)
        settings["language"] = "fa"
        save_settings(self.path, settings)
        self.assertEqual(load_settings(self.path), settings)

    def test_nonfinite_preferences_are_rejected(self):
        self.path.write_text(json.dumps({"threshold": float("nan"), "silence_seconds": float("inf")}))
        settings = load_settings(self.path)
        self.assertEqual(settings["threshold"], .008)
        self.assertEqual(settings["silence_seconds"], 2)

    def test_flat_layout_model_selection_survives_upgrade(self):
        from project_paths import ROOT, DEFAULT_MODELS_DIR
        self.path.write_text(json.dumps({"models_dir": str(ROOT / "models"),
                                        "selected_model_key": str(ROOT / "models" / "small")}))
        settings = load_settings(self.path)
        self.assertEqual(Path(settings["models_dir"]), DEFAULT_MODELS_DIR)
        self.assertEqual(Path(settings["selected_model_key"]), DEFAULT_MODELS_DIR / "small")

    def test_overlay_position_accepts_secondary_monitors_and_rejects_bad_data(self):
        self.path.write_text(json.dumps({"overlay_position": [-600, 140]}))
        self.assertEqual(load_settings(self.path)["overlay_position"], [-600, 140])
        for position in ([1, True], ["12", 2], [1], [2000000, 0]):
            self.path.write_text(json.dumps({"overlay_position": position}))
            self.assertIsNone(load_settings(self.path)["overlay_position"])

    def test_history_unicode_search_and_delete(self):
        history = History(self.path.with_suffix(".db"))
        history.add("Microphone", "Small", "سلام دنیا")
        history.add("File", "Small", "Hello world")
        row = history.search("سلام")[0]
        self.assertEqual(row[4], "سلام دنیا")
        history.delete(row[0])
        self.assertEqual(len(history.search()), 1)

    def test_empty_history_is_not_saved(self):
        history = History(self.path.with_suffix(".db"))
        history.add("Mic", "Small", "  ")
        self.assertEqual(history.search(), [])

    def test_history_can_delete_multiple_selected_rows_atomically(self):
        history = History(self.path.with_suffix(".db"))
        for text in ("one", "two", "three"):
            history.add("Mic", "Small", text)
        rows = history.search()
        history.delete_many([rows[0][0], rows[1][0], rows[0][0], -1, "bad"])
        self.assertEqual([row[4] for row in history.search()], ["one"])


class EngineTests(unittest.TestCase):
    def test_cancel_is_not_wrapped_as_a_model_error(self):
        adapter = FasterWhisperAdapter(Path("."))
        adapter.model = Mock()
        cancel = threading.Event()
        def segments():
            cancel.set()
            yield SimpleNamespace(text="must not appear")
        adapter.model.transcribe.return_value = (segments(), None)
        with self.assertRaises(TranscriptionCancelled):
            adapter.transcribe_audio(samples(1), "en", cancel)

    def test_audio_goes_directly_to_model_with_vad(self):
        adapter = FasterWhisperAdapter(Path("."))
        adapter.model = Mock()
        adapter.model.transcribe.return_value = ([SimpleNamespace(text=" Hello ")], None)
        audio = samples(1, .02)
        self.assertEqual(adapter.transcribe_audio(audio, "en"), "Hello")
        args, kwargs = adapter.model.transcribe.call_args
        self.assertIs(args[0], audio)
        self.assertTrue(kwargs["vad_filter"])
        self.assertEqual(kwargs["beam_size"], 3)

    def test_missing_model_directory_returns_no_models(self):
        self.assertEqual(discover_models(Path("nonexistent-model-test")), ([], []))

    def test_quality_presets_change_search_width(self):
        adapter = FasterWhisperAdapter(Path("."))
        adapter.model = Mock()
        adapter.model.transcribe.return_value = ([], None)
        for quality, width in (("Fast", 1), ("Balanced", 3), ("Accurate", 5)):
            adapter.quality = quality
            adapter.transcribe_audio(samples(1), "fa")
            self.assertEqual(adapter.model.transcribe.call_args.kwargs["beam_size"], width)
            self.assertEqual(adapter.model.transcribe.call_args.kwargs["language"], "fa")

    def test_short_audio_skips_timestamps_but_long_audio_keeps_them(self):
        adapter = FasterWhisperAdapter(Path("."))
        adapter.model = Mock()
        adapter.model.transcribe.return_value = ([], None)
        for duration, expected in ((4, True), (26, False)):
            adapter.transcribe_audio(samples(duration), "en")
            self.assertEqual(adapter.model.transcribe.call_args.kwargs["without_timestamps"], expected)

    def test_segment_progress_never_claims_completion(self):
        adapter = FasterWhisperAdapter(Path("."))
        adapter.model = Mock()
        adapter.progress_callback = Mock()
        adapter.model.transcribe.return_value = (
            [SimpleNamespace(text="One", end=3), SimpleNamespace(text="Two", end=10)],
            SimpleNamespace(duration=10))
        self.assertEqual(adapter.transcribe_audio(samples(10), "en"), "One Two")
        self.assertEqual([call.args[0] for call in adapter.progress_callback.call_args_list], [.3, .95])

    def test_worker_skips_cancelled_jobs(self):
        output = []
        worker = TranscriptionWorker(lambda *event: output.append(event))
        self.addCleanup(worker.close)
        cancel = threading.Event()
        cancel.set()
        worker.transcribe(42, samples(.1), "en", cancel, "Fast")
        worker.tasks.join()
        self.assertEqual(output, [])

    def test_worker_reports_error_with_session_id(self):
        output = []
        worker = TranscriptionWorker(lambda *event: output.append(event))
        self.addCleanup(worker.close)
        worker.transcribe(42, samples(.1), "en", threading.Event(), "Fast")
        worker.tasks.join()
        self.assertEqual(output[0][0], "transcribe_error")
        self.assertEqual(output[0][1][0], 42)

    def test_changed_destination_never_receives_input(self):
        with patch("windows_integration.target_is_current", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Destination changed"):
                insert_text(InputTarget(1, 2, 3), "private text")


if __name__ == "__main__":
    unittest.main()
