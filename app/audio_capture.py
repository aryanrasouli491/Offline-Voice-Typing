# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Bounded microphone capture. The callback never runs inference or touches Tk."""
from __future__ import annotations
import threading
import queue
from dataclasses import dataclass
import numpy as np

SAMPLE_RATE = 16000
MAX_RECORD_SECONDS = 120
NO_SPEECH_SECONDS = 10


@dataclass
class AudioSnapshot:
    level: float
    elapsed: float
    silence: float
    heard_speech: bool
    stop_reason: str = ""
    warning: str = ""
    error: str = ""


class AudioBuffer:
    def __init__(self, threshold=0.008, silence_seconds=2.0, live=False):
        self.threshold = threshold
        self.silence_seconds = silence_seconds
        self.live = live
        self.lock = threading.Lock()
        self.chunks = []
        self.archive = []
        self.preview_at = 0
        self.samples = self.chunk_samples = self.speech_samples = self.last_voice_sample = 0
        self.level = 0.0
        self.heard_speech = False
        self.warning = ""
        self.error = ""

    def feed(self, audio, status="", speech=None):
        data = np.asarray(audio, dtype=np.float32).reshape(-1).copy()
        if not len(data):
            return
        rms = float(np.sqrt(np.mean(data * data)))
        with self.lock:
            if self.samples >= MAX_RECORD_SECONDS * SAMPLE_RATE:
                return
            data = data[:MAX_RECORD_SECONDS * SAMPLE_RATE - self.samples]
            self.samples += len(data)
            self.chunk_samples += len(data)
            self.chunks.append(data)
            self.archive.append(data)  # Shared arrays, not a second copy of the audio.
            # Visualization is intentionally more sensitive than speech gating.
            # Quiet laptop microphones should visibly confirm that audio is being
            # captured even when the RMS is still below the recognition threshold.
            visual_ratio = rms / max(self.threshold, 0.002)
            self.level = min(1.0, visual_ratio ** 0.45 * 0.58) if rms >= 0.00025 else 0.0
            if (rms >= self.threshold) if speech is None else speech:
                self.speech_samples += len(data)
                self.last_voice_sample = self.samples
                if self.speech_samples >= 0.15 * SAMPLE_RATE:
                    self.heard_speech = True
            if status:
                self.warning = str(status)

    def snapshot(self):
        with self.lock:
            elapsed = self.samples / SAMPLE_RATE
            silence = (self.samples - self.last_voice_sample) / SAMPLE_RATE
            reason = ""
            if elapsed >= MAX_RECORD_SECONDS:
                reason = "Recording limit reached (2 minutes)"
            elif not self.heard_speech and elapsed >= NO_SPEECH_SECONDS:
                reason = "No speech detected"
            elif self.heard_speech and silence >= self.silence_seconds:
                reason = "Silence detected"
            return AudioSnapshot(self.level, elapsed, silence, self.heard_speech, reason, self.warning, self.error)

    def recorded_audio(self):
        """A bounded snapshot for export; includes all unmuted parts of this session."""
        with self.lock:
            chunks = list(self.archive)
        return np.concatenate(chunks) if chunks else None

    def take_preview(self):
        """Keep complete sentence context. Preview never consumes final audio.

        Increasing intervals bound repeated decoding to less than twice the
        segment length, even on a slow laptop. The UI allows one pending preview.
        """
        with self.lock:
            if not self.live or self.speech_samples < .15 * SAMPLE_RATE:
                return None
            duration = self.chunk_samples / SAMPLE_RATE
            silence = (self.samples - self.last_voice_sample) / SAMPLE_RATE
            interval = max(6, self.preview_at * 2)
            if duration < interval or (self.preview_at and silence < .55 and duration < interval + 2):
                return None
            self.preview_at = duration
            return np.concatenate(self.chunks)

    def resume(self):
        """A pause must not count against the next silence deadline."""
        with self.lock:
            self.last_voice_sample = self.samples
            self.level = 0

    def take_phrase(self, final=False):
        """Consume samples once, retaining neither old audio nor overlapping text."""
        with self.lock:
            silence = (self.samples - self.last_voice_sample) / SAMPLE_RATE
            ready = self.live and (self.chunk_samples >= 12 * SAMPLE_RATE or
                                  (silence >= 0.55 and self.chunk_samples >= SAMPLE_RATE))
            if not final and not ready:
                return None
            chunks, self.chunks = self.chunks, []
            speech = self.speech_samples
            self.chunk_samples = self.speech_samples = 0
            self.preview_at = 0
        if not chunks or speech < 0.15 * SAMPLE_RATE:
            return None
        return np.concatenate(chunks)


class SpeechGate:
    """Streaming Silero VAD using the small ONNX sessions already bundled with Whisper.

    This recognizes speech, not speaker identity. All recurrent state is per
    microphone, while the inference sessions are shared with the decoder VAD.
    """
    def __init__(self, threshold):
        from faster_whisper.vad import get_vad_model
        self.model = get_vad_model()
        self.minimum_rms = max(.001, threshold * .5)
        self.reset()

    def reset(self):
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros(64, dtype=np.float32)
        self.tail = np.empty(0, dtype=np.float32)
        self.speaking = False

    def __call__(self, data):
        audio = np.concatenate((self.tail, data))
        decisions = []
        offset = 0
        while offset + 512 <= len(audio):
            block = audio[offset:offset+512]
            framed = np.concatenate((self.context, block))[None, :]
            encoded = self.model.encoder_session.run(None, {"input": framed})[0]
            out, self.state = self.model.decoder_session.run(None, {"input": encoded.reshape(1, 128), "state": self.state})
            probability = float(out.reshape(-1)[0])
            # Hysteresis preserves word endings while noise cannot continually
            # reset the silence timer just because it moves the visual meter.
            self.speaking = probability >= (.35 if self.speaking else .5)
            decisions.append(self.speaking and float(np.sqrt(np.mean(block * block))) >= self.minimum_rms)
            self.context = block[-64:]
            offset += 512
        self.tail = audio[offset:]
        return any(decisions)


class Microphone:
    def __init__(self, buffer: AudioBuffer):
        self.buffer = buffer
        self.stream = None
        self.muted = False
        self.capture_lock = threading.Lock()
        self.audio_queue = queue.Queue(maxsize=100)
        self.capture_thread = None
        self.gate = None

    def start(self, device=None):
        import sounddevice as sd
        self.gate = SpeechGate(self.buffer.threshold)
        self.capture_thread = threading.Thread(target=self._consume, daemon=True, name="voice-activity")
        self.capture_thread.start()
        def callback(indata, frames, time_info, status):
            # The real-time callback only copies into a bounded queue. VAD and
            # inference never run on the audio device's callback thread.
            with self.capture_lock:
                if not self.muted:
                    try:
                        self.audio_queue.put_nowait((indata[:, 0].copy(), str(status) if status else ""))
                    except queue.Full:
                        self.buffer.error = "Audio processing fell behind. Please restart recording."
        stream = None
        try:
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                    blocksize=800, device=device, callback=callback)
            stream.start()
        except Exception:
            if stream is not None:
                stream.close()
            self._end_consumer()
            raise
        self.stream = stream

    def _consume(self):
        while True:
            item = self.audio_queue.get()
            try:
                if item is None:
                    return
                if isinstance(item, threading.Event):
                    self.gate.reset()
                    item.set()
                    continue
                data, status = item
                self.buffer.feed(data, status, speech=self.gate(data))
            except Exception as exc:
                self.buffer.error = f"Voice detection failed: {exc}"
            finally:
                self.audio_queue.task_done()

    def _end_consumer(self):
        if self.capture_thread:
            self.audio_queue.put(None, timeout=2)
            self.capture_thread.join(timeout=2)
            if self.capture_thread.is_alive():
                raise RuntimeError("Audio processing did not stop in time.")
            self.capture_thread = None

    def set_muted(self, muted):
        with self.capture_lock:
            self.muted = bool(muted)
        if muted and self.capture_thread:
            # Drain accepted pre-mute samples before the UI submits transcription.
            barrier = threading.Event()
            self.audio_queue.put(barrier, timeout=2)
            if not barrier.wait(2):
                raise RuntimeError("Microphone pause timed out.")
        if not muted:
            self.buffer.resume()

    def close(self):
        stream, self.stream = self.stream, None
        with self.capture_lock:
            self.muted = True
        try:
            if stream is not None:
                try:
                    stream.stop()
                finally:
                    stream.close()
        finally:
            self._end_consumer()


def input_devices():
    import sounddevice as sd
    hosts = sd.query_hostapis()
    result = {"Default microphone": None}
    for index, info in enumerate(sd.query_devices()):
        if info["max_input_channels"] > 0:
            label = f'{info["name"]} · {hosts[info["hostapi"]]["name"]}'
            result[label] = index
    return result
