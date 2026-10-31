# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Regressions for speech/noise decisions, full-context previews and voice export."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
from audio_capture import AudioBuffer, SpeechGate, SAMPLE_RATE
from audio_export import save_voice, validate_name, default_voice_name, music_directory
from speech_engine import load_audio_mono


class RecordingTests(unittest.TestCase):
    def test_loud_non_speech_does_not_reset_silence(self):
        buffer = AudioBuffer(silence_seconds=1)
        buffer.feed(np.ones(8000, dtype=np.float32) * .03, speech=True)
        buffer.feed(np.ones(16000, dtype=np.float32) * .08, speech=False)
        self.assertEqual(buffer.snapshot().stop_reason, "Silence detected")
        self.assertGreater(buffer.snapshot().level, .5)

    def test_preview_keeps_full_context_for_final_and_export(self):
        buffer = AudioBuffer(live=True)
        first = np.full(7 * SAMPLE_RATE, .04, dtype=np.float32)
        buffer.feed(first, speech=True)
        np.testing.assert_array_equal(buffer.take_preview(), first)
        self.assertIsNone(buffer.take_preview())
        buffer.feed(np.full(SAMPLE_RATE, .06, dtype=np.float32), speech=True)
        final = buffer.take_phrase(final=True)
        self.assertEqual(len(final), 8 * SAMPLE_RATE)
        self.assertIsNone(buffer.take_phrase(final=True))
        np.testing.assert_array_equal(buffer.recorded_audio(), final)

    def test_unmute_starts_new_silence_deadline(self):
        buffer = AudioBuffer(silence_seconds=1)
        buffer.feed(np.ones(8000, dtype=np.float32), speech=True)
        buffer.feed(np.zeros(16000, dtype=np.float32), speech=False)
        buffer.resume()
        self.assertFalse(buffer.snapshot().stop_reason)

    def test_real_vad_rejects_fan_noise_and_accepts_fixture(self):
        gate = SpeechGate(.008)
        rng = np.random.default_rng(12)
        noise = rng.normal(0, .015, 2 * SAMPLE_RATE).astype(np.float32)
        noise_decisions = [gate(noise[i:i+800]) for i in range(0, len(noise), 800)]
        self.assertLess(sum(noise_decisions), 3)
        fixture = Path(__file__).parent / "fixtures" / "english-fixture.wav"
        if not fixture.exists():
            self.skipTest("Optional local speech fixture is not available")
        speech, _ = load_audio_mono(fixture)
        gate.reset()
        decisions = [gate(speech[i:i+800]) for i in range(0, len(speech), 800)]
        self.assertGreater(sum(decisions), 20)


class ExportTests(unittest.TestCase):
    def test_music_path_and_default_name(self):
        self.assertTrue(music_directory().is_absolute())
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / "voice001_2026-01-01_12-00-00.m4a").touch()
            (folder / "voice003_2026-01-01_12-00-00.m4a").touch()
            self.assertTrue(default_voice_name(folder).startswith("voice002_"))

    def test_invalid_audio_never_leaves_an_empty_export(self):
        with tempfile.TemporaryDirectory() as folder:
            for samples in ([], [float("nan")], [[.1, .2]]):
                with self.assertRaises(ValueError):
                    save_voice(samples, folder, "صدای من")
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_compressed_audio_decodes_and_does_not_overwrite(self):
        samples = (.1 * np.sin(2 * np.pi * 220 * np.arange(16000 * 3) / 16000)).astype(np.float32)
        with tempfile.TemporaryDirectory() as folder:
            first = save_voice(samples, folder, "voice001_آزمایش")
            second = save_voice(samples, folder, "voice001_آزمایش")
            self.assertNotEqual(first, second)
            decoded, rate = load_audio_mono(first)
            self.assertEqual(rate, 16000)
            self.assertAlmostEqual(len(decoded)/rate, 3, delta=.15)
            self.assertLess(first.stat().st_size, 30000)

    def test_filenames_cannot_escape_download_folder(self):
        for name in ("../private", "C:\\recording", "NUL", "", "name."):
            with self.assertRaises(ValueError):
                validate_name(name)
