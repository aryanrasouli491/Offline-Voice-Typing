# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""One serialized worker keeps inference and model loading outside the UI."""
from __future__ import annotations
import queue
import threading
import time
from pathlib import Path
from speech_engine import (TranscriptionCancelled, make_adapter,
                           write_float_audio_to_wav, get_wav_duration_seconds, LOGGER)


class TranscriptionWorker:
    def __init__(self, emit):
        self.emit = emit
        self.tasks = queue.Queue(maxsize=16)
        self.closed = threading.Event()
        self.adapter = None
        self.model_key = None
        self.thread = threading.Thread(target=self._run, daemon=True, name="voice-inference")
        self.thread.start()

    def load(self, info):
        self.tasks.put_nowait(("load", info))

    def transcribe(self, session, audio, language, cancel, quality):
        self.tasks.put_nowait(("transcribe", (session, audio, language, cancel, quality)))

    def _run(self):
        while not self.closed.is_set():
            try:
                kind, payload = self.tasks.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if kind == "load":
                    if self.model_key != payload.key:
                        self.adapter = None  # Release the previous model before loading another.
                        self.model_key = None
                        adapter = make_adapter(payload)
                        if adapter is None:
                            raise RuntimeError("This model format is not supported.")
                        adapter.load()
                        self.adapter, self.model_key = adapter, payload.key
                    self.emit("model_ready", payload)
                else:
                    session, audio, language, cancel, quality = payload
                    if cancel.is_set():
                        continue
                    started = time.monotonic()
                    if self.adapter is None:
                        raise RuntimeError("Load a speech model first.")
                    self.adapter.quality = quality
                    duration = get_wav_duration_seconds(audio) if isinstance(audio, Path) else len(audio) / 16000
                    key = (self.model_key, language, quality)
                    self.emit("progress_started", (session, key, duration, started))
                    # Callbacks only enqueue data; the UI thread owns every widget.
                    self.adapter.progress_callback = lambda fraction: self.emit("progress", (session, fraction))
                    self.adapter.duration_callback = lambda duration: self.emit("progress_duration", (session, duration))
                    temporary = None
                    try:
                        if isinstance(audio, Path):
                            text = self.adapter.transcribe(audio, language, cancel_event=cancel)
                        elif hasattr(self.adapter, "transcribe_audio"):
                            text = self.adapter.transcribe_audio(audio, language, cancel)
                        else:
                            temporary = write_float_audio_to_wav(audio, 16000, prefix="live")
                            text = self.adapter.transcribe(temporary, language, cancel_event=cancel)
                        if not cancel.is_set() and not self.closed.is_set():
                            seconds = time.monotonic() - started
                            work_seconds = getattr(self.adapter, "timing_audio_seconds", self.adapter.audio_seconds)
                            self.emit("progress_timing", (session, key, work_seconds, seconds))
                            self.emit("result", (session, text, seconds))
                    finally:
                        self.adapter.progress_callback = None
                        self.adapter.duration_callback = None
                        if temporary:
                            temporary.unlink(missing_ok=True)
            except TranscriptionCancelled:
                pass
            except Exception as exc:
                LOGGER.exception("%s failed", kind)
                self.emit("model_error" if kind == "load" else "transcribe_error",
                          str(exc) if kind == "load" else (payload[0], str(exc)))
            finally:
                self.tasks.task_done()

    def close(self):
        self.closed.set()
