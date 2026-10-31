# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Offline model discovery and adapters. No UI or microphone ownership here."""

from __future__ import annotations

import importlib.util
import json
import logging
import logging.handlers
import os
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from project_paths import ROOT, DEFAULT_MODELS_DIR, DATA_DIR


# ---------------------------------------------------------------------------
# 1. IMPORTS AND CONFIGURATION
# ---------------------------------------------------------------------------
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# Project paths.
TEMP_DIR = DATA_DIR / "temporary"
LOG_PATH = DATA_DIR / "app.log"

SUPPORTED_EXTENSIONS = {
    ".wav", ".mp3", ".mpeg", ".flac", ".ogg", ".m4a", ".mp4", ".webm",
}

LANGUAGE_LABELS = {"fa": "Dari / Persian", "en": "English"}
QWEN_LANGUAGE_NAMES = {"fa": "Persian", "en": "English"}
WHISPER_LANGUAGE_CODES = {"fa": "fa", "en": "en"}


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("voice_typing_offline")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=500_000, backupCount=1, encoding="utf-8"
        )
    except OSError:
        return logger
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s"))
    logger.addHandler(handler)
    return logger


LOGGER = setup_logging()


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def choose_cpu_thread_count() -> int:
    cpu_count = os.cpu_count() or 2
    # Leave some CPU capacity for the editor and the user's destination app.
    return max(1, min(6, cpu_count - 1))


def get_wav_duration_seconds(audio_path: Path) -> float:
    if audio_path.suffix.lower() != ".wav":
        return 0.0
    try:
        with wave.open(str(audio_path), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
    except (wave.Error, OSError, EOFError):
        return 0.0
    return frames / rate if rate > 0 else 0.0


def pretty_model_name(directory_name: str) -> str:
    acronyms = {"asr": "ASR", "stt": "STT", "tts": "TTS", "ctc": "CTC",
                "hf": "HF", "ct2": "CT2"}
    tokens = directory_name.replace("_", " ").replace("-", " ").split()
    parts: list[str] = []
    for token in tokens:
        lower = token.lower()
        if lower in acronyms:
            parts.append(acronyms[lower])
        elif token and token[0].isdigit():
            parts.append(token.upper())
        else:
            parts.append(token[:1].upper() + token[1:])
    return " ".join(parts) if parts else directory_name


def load_audio_mono(audio_path: Path, target_sr: int = 16000):
    """Decode an audio file into mono float32 samples at ``target_sr``.

    Prefer Faster-Whisper's bundled PyAV decoder when installed. This supports
    common audio containers (including MP4/M4A) without requiring a separate
    system FFmpeg installation. The Start file validates decoding as well as
    installed versions, so broken binaries are detected before first use.
    """
    import numpy as np

    faster_whisper_decode_error: Optional[Exception] = None
    if module_available("faster_whisper"):
        try:
            from faster_whisper.audio import decode_audio  # type: ignore
            audio = decode_audio(str(audio_path), sampling_rate=target_sr)
            audio = np.asarray(audio, dtype="float32").reshape(-1)
            if audio.size:
                return audio, int(target_sr)
        except Exception as exc:
            faster_whisper_decode_error = exc
            LOGGER.debug(
                "Faster-Whisper/PyAV could not decode %s; trying other local decoders",
                audio_path, exc_info=True,
            )

    if module_available("librosa"):
        try:
            import librosa  # type: ignore
            audio, sr = librosa.load(str(audio_path), sr=target_sr, mono=True)
            return np.asarray(audio, dtype="float32"), int(sr)
        except Exception:
            LOGGER.debug("librosa could not load %s", audio_path, exc_info=True)

    if module_available("soundfile"):
        try:
            import soundfile as sf  # type: ignore
            audio, sr = sf.read(str(audio_path), dtype="float32", always_2d=False)
            audio = np.asarray(audio, dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            if sr != target_sr:
                n_samples = int(len(audio) * target_sr / sr)
                audio = np.interp(
                    np.linspace(0, len(audio), n_samples, endpoint=False),
                    np.arange(len(audio)),
                    audio,
                ).astype("float32")
                sr = target_sr
            return audio, int(sr)
        except Exception:
            LOGGER.debug("soundfile could not load %s", audio_path, exc_info=True)

    if audio_path.suffix.lower() == ".wav":
        with wave.open(str(audio_path), "rb") as wav_file:
            sr = wav_file.getframerate()
            channels = wav_file.getnchannels()
            width = wav_file.getsampwidth()
            frames = wav_file.readframes(wav_file.getnframes())
        if width == 2:
            audio = np.frombuffer(frames, dtype=np.int16).astype("float32") / 32768.0
        elif width == 4:
            audio = np.frombuffer(frames, dtype=np.int32).astype("float32") / 2147483648.0
        elif width == 1:
            audio = (np.frombuffer(frames, dtype=np.uint8).astype("float32") - 128.0) / 128.0
        else:
            raise ValueError(f"Unsupported WAV sample width: {width}")
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        if sr != target_sr:
            n_samples = int(len(audio) * target_sr / sr)
            audio = np.interp(
                np.linspace(0, len(audio), n_samples, endpoint=False),
                np.arange(len(audio)),
                audio,
            ).astype("float32")
            sr = target_sr
        return audio, int(sr)

    if faster_whisper_decode_error is not None and "metadata_errors" in str(faster_whisper_decode_error):
        raise RuntimeError(
            "Incompatible PyAV/Faster-Whisper versions: the installed PyAV no "
            "longer accepts the metadata_errors argument used by the current "
            "Faster-Whisper decoder. Close the app and run "
            "Start NR Voice Typing.cmd -Repair from the project folder. "
            f"Original error: {faster_whisper_decode_error}"
        ) from faster_whisper_decode_error

    raise RuntimeError(
        "No local audio decoder could read this file. For common compressed "
        "formats, repair/install the Faster-Whisper audio dependencies in the "
        "application's existing Python environment, or convert the file to a "
        "16 kHz WAV. See runtime/data/app.log for decoder details."
    )


def write_float_audio_to_wav(audio, sampling_rate: int, prefix: str = "normalized") -> Path:
    """Save mono float audio as a temporary 16-bit PCM WAV file.

    Qwen's Transformers processor reads file paths and some versions cannot
    decode video containers such as MP4. Normalizing non-WAV inputs through
    the local decoder avoids sending unsupported containers to the processor.
    """
    import numpy as np

    samples = np.asarray(audio, dtype="float32").reshape(-1)
    if not samples.size:
        raise ValueError("The selected audio file contains no decodable audio samples.")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    output_path = TEMP_DIR / (
        f"{prefix}_{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.wav"
    )
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(int(sampling_rate))
        wav_file.writeframes(pcm.tobytes())
    return output_path


# ---------------------------------------------------------------------------
# 4. MODEL DISCOVERY, REGISTRY, AND ADAPTERS
# ---------------------------------------------------------------------------
@dataclass
class ModelInfo:
    key: str
    path: Path
    display_name: str
    family: str
    backend: str
    status: str                       # ready | missing-deps | incomplete | unsupported
    diagnostic: str = ""
    languages: tuple = ("en", "fa")


def _read_config(model_path: Path) -> Optional[dict]:
    """Read config.json if present and valid. Returns None otherwise."""
    config_path = model_path / "config.json"
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def classify_model(model_path: Path) -> Optional[ModelInfo]:
    """Return a ModelInfo for a directory, or None if it is not a model.

    Detection is based on the presence and combination of weight files,
    not only on the folder name or on the contents of config.json.
    This is important for Faster-Whisper/CTranslate2 models whose
    config.json does not always expose a Hugging Face ``model_type``.
    """
    if not model_path.is_dir():
        return None

    # ---- Raw file facts (independent of config.json) ---------------------
    has_safetensors = (model_path / "model.safetensors").is_file()
    has_pytorch_bin = (model_path / "pytorch_model.bin").is_file()
    has_ct2_bin     = (model_path / "model.bin").is_file()
    has_tokenizer   = (model_path / "tokenizer.json").is_file()
    has_vocab       = ((model_path / "vocabulary.txt").is_file()
                       or (model_path / "vocabulary.json").is_file())
    has_hf_weights  = has_safetensors or has_pytorch_bin

    config = _read_config(model_path) or {}
    model_type = str(config.get("model_type", "")).strip().lower()
    architectures = [str(a).lower() for a in (config.get("architectures") or [])]
    arch_joined = " ".join(architectures)

    display_name = pretty_model_name(model_path.name)

    # ---- 1. Faster-Whisper / CTranslate2 --------------------------------
    # A CTranslate2 Whisper model has model.bin and does NOT contain
    # safetensors/pytorch_model.bin. tokenizer.json and one of the
    # vocabulary files are expected alongside it.
    looks_like_ct2 = (
        has_ct2_bin
        and not has_hf_weights
        and (has_tokenizer or has_vocab or "whisper" in model_type)
    )
    if looks_like_ct2:
        missing = []
        if not has_tokenizer:
            missing.append("tokenizer.json")
        if not has_vocab:
            missing.append("vocabulary.txt (or vocabulary.json)")
        if missing:
            return ModelInfo(
                key=str(model_path), path=model_path,
                display_name=display_name, family="faster-whisper",
                backend="faster-whisper", status="incomplete",
                diagnostic=("This folder looks like a Faster-Whisper model "
                            "but is missing: " + ", ".join(missing)),
                languages=("en", "fa"),
            )
        deps_ok = module_available("faster_whisper")
        status = "ready" if deps_ok else "missing-deps"
        diagnostic = "" if deps_ok else (
            "The `faster-whisper` package is not installed in this Python "
            "environment. Install it in the existing .venv (e.g. "
            "`pip install faster-whisper`) and press the refresh button."
        )
        return ModelInfo(
            key=str(model_path), path=model_path,
            display_name=display_name, family="faster-whisper",
            backend="faster-whisper", status=status,
            diagnostic=diagnostic, languages=("en", "fa"),
        )

    # ---- 2. Qwen ASR (transformers layout) ------------------------------
    is_qwen = "qwen" in model_type or "qwen" in arch_joined
    is_asr = "asr" in model_type or "asr" in arch_joined
    if is_qwen and (is_asr or has_safetensors):
        if not has_hf_weights:
            return ModelInfo(
                key=str(model_path), path=model_path,
                display_name=display_name, family="qwen-asr",
                backend="transformers", status="incomplete",
                diagnostic="The Qwen model weights are missing.",
                languages=("en", "fa"),
            )
        deps_ok = module_available("transformers") and module_available("torch")
        status = "ready" if deps_ok else "missing-deps"
        diagnostic = "" if deps_ok else (
            "Install `transformers` and `torch` in the existing .venv."
        )
        return ModelInfo(
            key=str(model_path), path=model_path,
            display_name=display_name, family="qwen-asr",
            backend="transformers", status=status,
            diagnostic=diagnostic, languages=("en", "fa"),
        )

    # ---- 3. Hugging Face Whisper (transformers layout) ------------------
    if "whisper" in model_type and has_hf_weights:
        deps_ok = module_available("transformers") and module_available("torch")
        status = "ready" if deps_ok else "missing-deps"
        diagnostic = "" if deps_ok else (
            "Install `transformers` and `torch` in the existing .venv."
        )
        return ModelInfo(
            key=str(model_path), path=model_path,
            display_name=display_name, family="whisper",
            backend="transformers", status=status,
            diagnostic=diagnostic, languages=("en", "fa"),
        )

    # ---- 4. Generic Whisper detection fallback --------------------------
    # Any folder that contains a whisper-like tokenizer + HF weights and
    # does not declare a model_type is still very likely a Whisper model.
    if has_hf_weights and (
        "whisper" in model_path.name.lower()
        or (model_path / "preprocessor_config.json").is_file()
    ):
        deps_ok = module_available("transformers") and module_available("torch")
        status = "ready" if deps_ok else "missing-deps"
        diagnostic = "" if deps_ok else (
            "Install `transformers` and `torch` in the existing .venv."
        )
        return ModelInfo(
            key=str(model_path), path=model_path,
            display_name=display_name, family="whisper",
            backend="transformers", status=status,
            diagnostic=diagnostic, languages=("en", "fa"),
        )

    return None


def _looks_model_like(path: Path) -> bool:
    """Best-effort guess that a directory is intended to be a speech model."""
    if not path.is_dir():
        return False
    markers = (
        "config.json", "model.bin", "model.safetensors",
        "pytorch_model.bin", "tokenizer.json",
        "vocabulary.txt", "vocabulary.json",
    )
    return any((path / m).is_file() for m in markers)


def discover_models(models_dir: Path):
    """Scan ``models_dir`` and return ``(models, diagnostics)``.

    ``models`` is a list of ``ModelInfo``.
    ``diagnostics`` is a list of ``(path_str, reason)`` tuples describing
    directories that looked model-like but were not recognized.

    - The scan is fully offline.
    - Model files are never modified.
    - The selected folder itself is accepted as a model (so the user can
      pick either ``models/`` or ``models/faster-whisper-small/``).
    - Each real directory is only listed once, even if it appears both as
      a direct child and as a nested child.
    """
    results: list[ModelInfo] = []
    diagnostics: list[tuple[str, str]] = []

    if not models_dir or not models_dir.is_dir():
        return results, diagnostics

    seen_paths: set[str] = set()

    def _add(info: Optional[ModelInfo], diag_path: Optional[Path] = None) -> None:
        if info is not None:
            resolved = str(info.path.resolve())
            if resolved in seen_paths:
                return
            seen_paths.add(resolved)
            results.append(info)
        elif diag_path is not None:
            diagnostics.append((str(diag_path), "files present but format not recognized"))

    # 0. Treat the selected folder itself as a possible model.
    _add(classify_model(models_dir), models_dir if _looks_model_like(models_dir) else None)

    # 1. Scan direct children.
    try:
        entries = sorted(models_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return results, diagnostics

    for entry in entries:
        if not entry.is_dir():
            continue
        info = classify_model(entry)
        if info is not None:
            _add(info)
            continue

        # 2. One level deeper for HF-style nested downloads.
        try:
            children = sorted(entry.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            if _looks_model_like(entry):
                diagnostics.append((str(entry), "files present but format not recognized"))
            continue

        for child in children:
            if child.is_dir():
                child_info = classify_model(child)
                if child_info is not None:
                    _add(child_info)
                elif _looks_model_like(child):
                    diagnostics.append(
                        (str(child), "files present but format not recognized")
                    )
            elif _looks_model_like(entry) and str(entry) not in {d[0] for d in diagnostics}:
                diagnostics.append(
                    (str(entry), "files present but format not recognized")
                )
                break

    return results, diagnostics


def normalize_models_directory(selected_path: Path) -> tuple[Path, Optional[Path]]:
    """Return ``(models_root, selected_model_path_hint)`` for a chosen folder.

    Users may select either the project ``models`` directory or one model
    directory inside it. If the chosen directory itself looks like a model,
    scan its parent and remember that folder as the model to select. This keeps
    a saved path such as ``models/faster-whisper-small`` from hiding sibling
    models such as ``models/qwen3-asr-0.6b``.
    """
    candidate = Path(selected_path).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        candidate = Path(os.path.abspath(str(candidate)))

    if not candidate.is_dir():
        return candidate, None

    # A valid model or a directory containing recognizable model-file markers
    # is an individual model folder, not the collection/root folder.
    if classify_model(candidate) is not None or _looks_model_like(candidate):
        return candidate.parent.resolve(), candidate

    return candidate, None


def safe_whisper_max_new_tokens(model, forced_decoder_ids, preferred_limit: int = 400) -> int:
    """Compute a conservative Whisper generation limit for this model.

    Whisper-small commonly exposes 448 target positions. Reserving room for
    the decoder start/prompt tokens and a small margin prevents the historical
    512-token setting from exceeding the model's decoder context.
    """
    config = getattr(model, "config", None)
    target_positions = getattr(config, "max_target_positions", None)
    try:
        target_positions = int(target_positions)
        if target_positions <= 0:
            raise ValueError
    except (TypeError, ValueError):
        target_positions = 448  # Conservative fallback for standard Whisper.

    # The decoder start token plus language/task prompt take decoder positions.
    prompt_length = 1 + len(forced_decoder_ids or [])
    safe_limit = target_positions - prompt_length - 8
    if safe_limit < 1:
        raise ValueError(
            f"Whisper decoder context ({target_positions} positions) is too small "
            "for the configured language/task prompt."
        )
    return min(int(preferred_limit), safe_limit)


class TranscriptionCancelled(Exception):
    """Raised when the user asks the current transcription to stop."""


def check_cancel_requested(cancel_event) -> None:
    """Stop before starting another expensive part of transcription."""
    if cancel_event is not None and cancel_event.is_set():
        raise TranscriptionCancelled("Transcription was stopped by the user.")


class BaseAdapter:
    family = "base"

    def __init__(self, model_path: Path):
        self.model_path = model_path
        self._lock = threading.Lock()
        self.load_seconds = 0.0
        self.transcribe_seconds = 0.0
        self.audio_seconds = 0.0

    def is_loaded(self) -> bool:
        return False

    def load(self, status_callback: Optional[Callable[[str], None]] = None) -> None:
        raise NotImplementedError

    def transcribe(self, audio_path: Path, language: str,
                   status_callback: Optional[Callable[[str], None]] = None,
                   cancel_event=None) -> str:
        raise NotImplementedError


class QwenASRAdapter(BaseAdapter):
    family = "qwen-asr"

    def __init__(self, model_path: Path):
        super().__init__(model_path)
        self.processor = None
        self.model = None

    def is_loaded(self) -> bool:
        return self.model is not None and self.processor is not None

    def load(self, status_callback=None) -> None:
        with self._lock:
            if self.is_loaded():
                return
            if status_callback:
                status_callback("Loading the local Qwen model (this can take a few minutes on CPU)…")
            start = time.perf_counter()
            try:
                import torch
                from transformers import AutoProcessor, AutoModelForMultimodalLM
            except Exception as exc:
                raise RuntimeError(
                    "Could not import PyTorch/Transformers. Run the app with the "
                    "existing .venv. Details: " + str(exc)
                ) from exc
            try:
                torch.set_num_threads(choose_cpu_thread_count())
            except Exception:
                pass
            try:
                processor = AutoProcessor.from_pretrained(
                    str(self.model_path), local_files_only=True
                )
                model = AutoModelForMultimodalLM.from_pretrained(
                    str(self.model_path), device_map="cpu", local_files_only=True
                )
                model.eval()
            except Exception as exc:
                raise RuntimeError(
                    "The Qwen model files were found but loading failed. "
                    "No download was attempted. Details: " + str(exc)
                ) from exc
            self.processor, self.model = processor, model
            self.load_seconds = time.perf_counter() - start
            LOGGER.info("Qwen model loaded in %.1f s", self.load_seconds)

    def transcribe(self, audio_path, language, status_callback=None, cancel_event=None) -> str:
        if audio_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported audio extension: {audio_path.suffix or '(none)'}")
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise ValueError("The selected audio file does not exist or is empty.")
        check_cancel_requested(cancel_event)
        self.load(status_callback)
        check_cancel_requested(cancel_event)
        language_name = QWEN_LANGUAGE_NAMES.get(language, "English")
        if status_callback:
            status_callback(f"Transcribing ({language_name}) on the CPU…")
        self.audio_seconds = get_wav_duration_seconds(audio_path)
        normalized_audio_path: Optional[Path] = None
        audio_for_processor = audio_path
        start = time.perf_counter()
        try:
            import torch

            # Keep WAV input unchanged. For MP3/MP4/M4A and other containers,
            # decode locally and pass Qwen a standard WAV file. This avoids
            # relying on Transformers/librosa to decode video containers.
            if audio_path.suffix.lower() != ".wav":
                check_cancel_requested(cancel_event)
                audio_array, sample_rate = load_audio_mono(audio_path, target_sr=16000)
                self.audio_seconds = len(audio_array) / float(sample_rate or 1)
                check_cancel_requested(cancel_event)
                normalized_audio_path = write_float_audio_to_wav(
                    audio_array, sample_rate, prefix="qwen_normalized"
                )
                audio_for_processor = normalized_audio_path

            check_cancel_requested(cancel_event)
            inputs = self.processor.apply_transcription_request(
                audio=str(audio_for_processor), language=language_name
            )
            inputs = inputs.to(self.model.device, self.model.dtype)

            # Transformers checks this callback after each newly generated token.
            # This allows the Stop button to interrupt Qwen generation, rather
            # than only discarding the finished transcript.
            stopping_criteria = None
            if cancel_event is not None:
                from transformers import StoppingCriteria, StoppingCriteriaList

                class StopWhenRequested(StoppingCriteria):
                    def __call__(self, input_ids, scores, **kwargs):
                        return torch.full(
                            (input_ids.shape[0],),
                            cancel_event.is_set(),
                            dtype=torch.bool,
                            device=input_ids.device,
                        )

                stopping_criteria = StoppingCriteriaList([StopWhenRequested()])

            with torch.inference_mode():
                generate_options = {"max_new_tokens": 512}
                if stopping_criteria is not None:
                    generate_options["stopping_criteria"] = stopping_criteria
                output_ids = self.model.generate(**inputs, **generate_options)
            prompt_len = inputs["input_ids"].shape[1]
            generated_ids = output_ids[:, prompt_len:]
            decoded = self.processor.decode(
                generated_ids, return_format="transcription_only"
            )
            text = str(decoded[0]).strip() if decoded else ""
        except Exception as exc:
            raise RuntimeError("Qwen transcription failed. Details: " + str(exc)) from exc
        finally:
            if normalized_audio_path is not None:
                try:
                    normalized_audio_path.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning("Could not delete temporary normalized audio %s", normalized_audio_path)
        self.transcribe_seconds = time.perf_counter() - start
        LOGGER.info("Qwen transcribed %s in %.1f s", audio_path.name, self.transcribe_seconds)
        return text


class WhisperHFAdapter(BaseAdapter):
    family = "whisper"

    def __init__(self, model_path: Path):
        super().__init__(model_path)
        self.processor = None
        self.model = None

    def is_loaded(self) -> bool:
        return self.model is not None and self.processor is not None

    def load(self, status_callback=None) -> None:
        with self._lock:
            if self.is_loaded():
                return
            if status_callback:
                status_callback("Loading the Whisper model…")
            start = time.perf_counter()
            try:
                import torch
                from transformers import (
                    WhisperProcessor,
                    WhisperForConditionalGeneration,
                )
            except Exception as exc:
                raise RuntimeError("Could not import Transformers. Details: " + str(exc)) from exc
            try:
                torch.set_num_threads(choose_cpu_thread_count())
            except Exception:
                pass
            try:
                processor = WhisperProcessor.from_pretrained(
                    str(self.model_path), local_files_only=True
                )
                model = WhisperForConditionalGeneration.from_pretrained(
                    str(self.model_path), local_files_only=True
                )
                model.eval()
            except Exception as exc:
                raise RuntimeError("Whisper model loading failed. Details: " + str(exc)) from exc
            self.processor, self.model = processor, model
            self.load_seconds = time.perf_counter() - start
            LOGGER.info("Whisper model loaded in %.1f s", self.load_seconds)

    def transcribe(self, audio_path, language, status_callback=None, cancel_event=None) -> str:
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise ValueError("The selected audio file does not exist or is empty.")
        check_cancel_requested(cancel_event)
        self.load(status_callback)
        check_cancel_requested(cancel_event)
        if status_callback:
            status_callback(f"Transcribing ({LANGUAGE_LABELS.get(language, language)})…")
        import torch
        audio_array, sampling_rate = load_audio_mono(audio_path, target_sr=16000)
        check_cancel_requested(cancel_event)
        self.audio_seconds = len(audio_array) / float(sampling_rate or 1)
        lang_code = WHISPER_LANGUAGE_CODES.get(language, "en")
        start = time.perf_counter()
        try:
            inputs = self.processor(
                audio_array, sampling_rate=sampling_rate, return_tensors="pt"
            )
            forced_ids = self.processor.get_decoder_prompt_ids(
                language=lang_code, task="transcribe"
            )
            max_new_tokens = safe_whisper_max_new_tokens(
                self.model, forced_ids, preferred_limit=400
            )
            LOGGER.debug(
                "Whisper generation limit=%s; model max_target_positions=%s",
                max_new_tokens,
                getattr(getattr(self.model, "config", None), "max_target_positions", "unknown"),
            )
            # Ask Transformers to check the stop flag after every generated token.
            from transformers import StoppingCriteria, StoppingCriteriaList

            class StopWhenRequested(StoppingCriteria):
                def __call__(self, input_ids, scores, **kwargs):
                    return torch.full(
                        (input_ids.shape[0],),
                        cancel_event.is_set() if cancel_event is not None else False,
                        dtype=torch.bool,
                        device=input_ids.device,
                    )

            stopping_criteria = StoppingCriteriaList([StopWhenRequested()])
            with torch.inference_mode():
                generated_ids = self.model.generate(
                    inputs.input_features,
                    forced_decoder_ids=forced_ids,
                    max_new_tokens=max_new_tokens,
                    stopping_criteria=stopping_criteria,
                )
            text = self.processor.batch_decode(
                generated_ids, skip_special_tokens=True
            )[0].strip()
        except Exception as exc:
            raise RuntimeError("Whisper transcription failed. Details: " + str(exc)) from exc
        self.transcribe_seconds = time.perf_counter() - start
        LOGGER.info("Whisper transcribed %s in %.1f s", audio_path.name, self.transcribe_seconds)
        return text


class FasterWhisperAdapter(BaseAdapter):
    """Adapter for CTranslate2 Faster-Whisper models (Systran/faster-whisper-*)."""

    family = "faster-whisper"

    def __init__(self, model_path: Path):
        super().__init__(model_path)
        self.model = None
        self.quality = "Balanced"

    def is_loaded(self) -> bool:
        return self.model is not None

    def load(self, status_callback=None) -> None:
        with self._lock:
            if self.is_loaded():
                return
            if status_callback:
                status_callback("Loading the Faster-Whisper model…")
            start = time.perf_counter()

            try:
                from faster_whisper import WhisperModel  # type: ignore
            except Exception as exc:
                raise RuntimeError(
                    "Could not import faster_whisper. The package is not "
                    "installed in this Python environment. Details: " + str(exc)
                ) from exc

            # Make absolutely sure we hand the library a local directory,
            # never a Hugging Face repo identifier. This keeps the app
            # offline and stops faster-whisper from trying to download.
            resolved = Path(self.model_path).resolve()
            if not resolved.is_dir():
                raise RuntimeError(
                    f"Faster-Whisper model directory does not exist: {resolved}"
                )
            LOGGER.info("Loading Faster-Whisper from %s", resolved)

            try:
                # CPU + INT8 is the safest default on Windows without a GPU.
                self.model = WhisperModel(
                    str(resolved),
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=choose_cpu_thread_count(),
                    num_workers=1,
                    local_files_only=True,
                )
            except TypeError:
                # Older faster-whisper versions may not accept local_files_only.
                try:
                    self.model = WhisperModel(
                        str(resolved), device="cpu", compute_type="int8"
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"Faster-Whisper loading failed for {resolved}. "
                        f"Details: {exc}"
                    ) from exc
            except Exception as exc:
                raise RuntimeError(
                    f"Faster-Whisper loading failed for {resolved}. "
                    f"Details: {exc}"
                ) from exc

            self.load_seconds = time.perf_counter() - start
            LOGGER.info("Faster-Whisper model loaded in %.1f s", self.load_seconds)

    def transcribe(self, audio_path, language, status_callback=None, cancel_event=None) -> str:
        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise ValueError("The selected audio file does not exist or is empty.")
        check_cancel_requested(cancel_event)
        audio, sample_rate = load_audio_mono(audio_path)
        self.audio_seconds = len(audio)/sample_rate
        return self._recognize(audio, language, status_callback, cancel_event)

    def transcribe_audio(self, audio, language, cancel_event=None):
        """Recorded 16 kHz samples go straight to Whisper, with no temporary WAV."""
        self.audio_seconds = len(audio) / 16000
        return self._recognize(audio, language, cancel_event=cancel_event)

    def _recognize(self, audio, language, status_callback=None, cancel_event=None):
        check_cancel_requested(cancel_event)
        self.load(status_callback)
        check_cancel_requested(cancel_event)
        if status_callback:
            status_callback(f"Transcribing ({LANGUAGE_LABELS.get(language, language)})…")
        lang_code = WHISPER_LANGUAGE_CODES.get(language, "en")
        start = time.perf_counter()
        try:
            segments, info = self.model.transcribe(
                audio, language=lang_code,
                beam_size={"Fast": 1, "Balanced": 3, "Accurate": 5}.get(self.quality, 3),
                vad_filter=True,
                # Standard speech probability rejects noise; 400 ms padding
                # protects quiet consonants at the edges of each detected phrase.
                vad_parameters={"threshold": .5,
                                "min_silence_duration_ms": 600, "speech_pad_ms": 400},
                condition_on_previous_text=False,
                # Deterministic decoding avoids random high-temperature guesses
                # on noise and repeated fallback passes on a slow CPU.
                temperature=0.0,
                best_of=1 if self.quality == "Fast" else 5,
                # A short utterance fits in one Whisper window. Skipping timestamp
                # tokens saves decoder work; longer audio keeps segmentation.
                without_timestamps=0 < self.audio_seconds <= 25,
            )
            self.audio_seconds = float(getattr(info, "duration", self.audio_seconds))
            self.timing_audio_seconds = float(getattr(info, "duration_after_vad", self.audio_seconds))
            duration_callback = getattr(self, "duration_callback", None)
            if duration_callback:
                duration_callback(self.timing_audio_seconds)
            # Faster-Whisper generates audio in segments. Check the stop flag
            # between segments so the rest of a long recording is not processed.
            transcript_parts = []
            for segment in segments:
                check_cancel_requested(cancel_event)
                segment_text = segment.text.strip()
                if segment_text:
                    transcript_parts.append(segment_text)
                progress = getattr(self, "progress_callback", None)
                if progress and self.audio_seconds > 0:
                    progress(min(.95, getattr(segment, "end", 0) / self.audio_seconds))
            check_cancel_requested(cancel_event)
            text = " ".join(transcript_parts).strip()
        except TranscriptionCancelled:
            raise
        except Exception as exc:
            if "metadata_errors" in str(exc):
                raise RuntimeError(
                    "Faster-Whisper audio decoding failed because the installed "
                    "PyAV version is incompatible. Close the app and run "
                    "Start NR Voice Typing.cmd -Repair from the project folder. "
                    "Details: " + str(exc)
                ) from exc
            raise RuntimeError("Faster-Whisper transcription failed. Details: " + str(exc)) from exc
        self.transcribe_seconds = time.perf_counter() - start
        LOGGER.info("Faster-Whisper transcribed %.1f s audio in %.1f s",
                    self.audio_seconds, self.transcribe_seconds)
        return text


ADAPTERS: dict[str, type[BaseAdapter]] = {
    "qwen-asr": QwenASRAdapter,
    "whisper": WhisperHFAdapter,
    "faster-whisper": FasterWhisperAdapter,
}


def make_adapter(info: ModelInfo) -> Optional[BaseAdapter]:
    cls = ADAPTERS.get(info.family)
    return cls(info.path) if cls else None


# Explicit exports keep the UI and worker boundaries easy to audit.
__all__ = (
    "DATA_DIR", "DEFAULT_MODELS_DIR", "LOGGER", "ROOT",
    "FasterWhisperAdapter", "TranscriptionCancelled", "discover_models",
    "get_wav_duration_seconds", "load_audio_mono", "make_adapter",
    "normalize_models_directory", "write_float_audio_to_wav",
)
