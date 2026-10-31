# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Compare the previous Balanced settings with the current short-utterance path."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))
import json
import statistics
import time
from speech_engine import FasterWhisperAdapter, DEFAULT_MODELS_DIR, load_audio_mono

fixture = Path(__file__).parent / "fixtures" / "english-fixture.wav"
audio, sample_rate = load_audio_mono(fixture)
adapter = FasterWhisperAdapter(DEFAULT_MODELS_DIR / "small")
adapter.load()
adapter.transcribe_audio(audio, "en")  # Warm the VAD and inference caches.
old_times, new_times = [], []
for _ in range(3):
    started = time.perf_counter()
    old_text = " ".join(s.text.strip() for s in adapter.model.transcribe(
        audio, language="en", beam_size=3, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 450}, condition_on_previous_text=False)[0])
    old_times.append(time.perf_counter()-started)
    started = time.perf_counter()
    new_text = adapter.transcribe_audio(audio, "en")
    new_times.append(time.perf_counter()-started)
result = {"audio_seconds": len(audio)/sample_rate, "runs": 3,
          "old_decode_median_seconds": statistics.median(old_times),
          "new_decode_median_seconds": statistics.median(new_times),
          "old_text": old_text, "new_text": new_text,
          "speedup_percent": (1-statistics.median(new_times)/statistics.median(old_times))*100,
          "note": "Same loaded CPU INT8 model, beam3, array and VAD. New: short audio skips timestamp tokens. Synthetic English only; not an accent-accuracy test."}
(Path(__file__).parent / "artifacts").mkdir(exist_ok=True)
(Path(__file__).parent / "artifacts" / "benchmark.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
