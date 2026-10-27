"""Voice Typing Offline — multi-model edition.

A local, offline speech-to-text desktop application for Dari (دری) and
English. It discovers speech-recognition models inside the project's
existing ``models/`` folder, lets the user pick one at runtime, and
performs all inference on this computer. No network access is used.

The file is organized in six sections:

    1. Imports and configuration
    2. Themes and fonts
    3. Small helpers
    4. Model discovery, registry, and per-family adapters
    5. The application window (``VoiceTypingApp``)
    6. Application startup
"""

from __future__ import annotations

import importlib.util
import json
import logging
import logging.handlers
import os
import queue
import sqlite3
import threading
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


# ---------------------------------------------------------------------------
# 1. IMPORTS AND CONFIGURATION
# ---------------------------------------------------------------------------
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# Project paths.
ROOT = Path(__file__).resolve().parent
DEFAULT_MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "history.sqlite3"
SETTINGS_PATH = DATA_DIR / "settings.json"
TEMP_DIR = DATA_DIR / "temporary"
LOG_PATH = DATA_DIR / "app.log"

SUPPORTED_EXTENSIONS = {
    ".wav", ".mp3", ".mpeg", ".flac", ".ogg", ".m4a", ".mp4", ".webm",
}
HISTORY_LIMIT = 200

DEFAULT_WINDOW_WIDTH = 960
DEFAULT_WINDOW_HEIGHT = 640
SCREEN_MARGIN = 50
LEFT_COLUMN_MIN_WIDTH = 380
RIGHT_COLUMN_MIN_WIDTH = 440
FILE_LABEL_WRAP = 320
SETTINGS_VERSION = 4

SPACE_TINY = 4
SPACE_SMALL = 8
SPACE_MEDIUM = 12
SPACE_LARGE = 16
SPACE_XLARGE = 24
CARD_PADDING = 10

BUTTON_PADDING = (14, 8)
PRIMARY_BUTTON_PADDING = (18, 11)

START_WRAP_WIDTH = RIGHT_COLUMN_MIN_WIDTH - 2 * CARD_PADDING - 4

LANGUAGE_ORDER = ("fa", "en")
LANGUAGE_LABELS = {"fa": "دری  Dari", "en": "English"}

QWEN_LANGUAGE_NAMES = {"fa": "Persian", "en": "English"}
WHISPER_LANGUAGE_CODES = {"fa": "fa", "en": "en"}

LEGACY_QWEN_MODEL_NAME = "Qwen3-ASR-0.6B"


# ---------------------------------------------------------------------------
# 2. THEMES AND FONTS
# ---------------------------------------------------------------------------
THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#0b1220", "card": "#121b2f", "card_alt": "#0d1526",
        "border": "#22304d", "input": "#0d1526", "input_border": "#2a3a5e",
        "text": "#e5e9f0", "muted": "#8b9ab5",
        "accent": "#2dd4bf", "accent_hover": "#5eead4",
        "accent_pressed": "#14b8a6", "accent_text": "#04211d",
        "accent_soft": "#10303a",
        "secondary": "#1c2842", "secondary_hover": "#27375a",
        "secondary_pressed": "#16203a",
        "disabled_bg": "#18223a", "disabled_text": "#5b6985",
        "danger": "#ef4444", "danger_hover": "#f87171",
        "danger_pressed": "#dc2626", "danger_soft": "#3a1620",
        "warning_soft": "#33290f", "error_bg": "#2a1420",
        "success": "#34d399", "warning": "#fbbf24", "info": "#60a5fa",
        "selection": "#1f4f63", "selection_text": "#ffffff",
        "badge_ready_text": "#34d399", "badge_wait_text": "#fbbf24",
        "badge_bad_text": "#f87171",
        "badge_ready_bg": "#10303a", "badge_wait_bg": "#33290f",
        "badge_bad_bg": "#3a1620",
    },
    # The light theme uses soft gray surfaces to reduce eye strain.
    "light": {
        "bg": "#e9edf3", "card": "#f5f7fa", "card_alt": "#edf1f6",
        "border": "#cbd4e0", "input": "#fbfcfe", "input_border": "#b9c5d5",
        "text": "#18223a", "muted": "#5c6b84",
        "accent": "#0d9488", "accent_hover": "#0f766e",
        "accent_pressed": "#115e59", "accent_text": "#ffffff",
        "accent_soft": "#d1fae5",
        "secondary": "#e6ebf3", "secondary_hover": "#d5dce6",
        "secondary_pressed": "#bcc6d3",
        "disabled_bg": "#e6ebf3", "disabled_text": "#9aa7b8",
        "danger": "#dc2626", "danger_hover": "#b91c1c",
        "danger_pressed": "#991b1b", "danger_soft": "#fee2e2",
        "warning_soft": "#fef3c7", "error_bg": "#fef2f2",
        "success": "#059669", "warning": "#b45309", "info": "#2563eb",
        "selection": "#99f6e4", "selection_text": "#0f172a",
        "badge_ready_text": "#065f46", "badge_wait_text": "#92400e",
        "badge_bad_text": "#991b1b",
        "badge_ready_bg": "#d1fae5", "badge_wait_bg": "#fef3c7",
        "badge_bad_bg": "#fee2e2",
    },
}

FONT_NORMAL = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 17, "bold")
FONT_HEADING = ("Segoe UI", 11, "bold")
FONT_HINT = ("Segoe UI", 11)
FONT_EDITOR = ("Segoe UI", 12)
FONT_EDITOR_DARI = ("Segoe UI", 13)
FONT_PRIMARY_BUTTON = ("Segoe UI", 11, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_BADGE = ("Segoe UI", 9, "bold")

PERSIAN_FONT_CANDIDATES = (
    "Vazirmatn", "Vazir", "Tahoma", "Segoe UI",
    "Noto Naskh Arabic", "Noto Sans Arabic", "Arial",
)


# ---------------------------------------------------------------------------
# 3. SMALL HELPERS
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    logger = logging.getLogger("voice_typing_offline")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        DATA_DIR.mkdir(exist_ok=True)
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
    return max(1, min(6, cpu_count))


def get_supported_formats_text() -> str:
    return ", ".join(ext.strip(".").upper() for ext in sorted(SUPPORTED_EXTENSIONS))


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


def format_seconds(seconds: float) -> str:
    return f"{seconds:.1f} s"


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


def make_friendly_error_message(technical_message: str) -> str:
    """Convert technical failures into actionable UI messages.

    Keep specific dependency/audio-decoder errors ahead of broader model errors;
    otherwise a PyAV decoding problem is incorrectly described as a model-load
    failure, which makes troubleshooting unnecessarily difficult.
    """
    lowered = technical_message.lower()
    if "metadata_errors" in lowered or ("pyav" in lowered and "incompatib" in lowered):
        return (
            "The installed PyAV version is incompatible with this Faster-Whisper "
            "audio decoder. Close the app and run repair_desktop_pyav.bat from "
            "the project folder. See data/app.log for details."
        )
    if "unsupported audio extension" in lowered:
        return ("This audio format is not supported. Please choose one of: "
                + get_supported_formats_text() + ".")
    if "does not exist or is empty" in lowered:
        return "The audio file could not be found, or it is empty. Please choose it again."
    if "faster_whisper" in lowered and "could not import" in lowered:
        return ("The `faster-whisper` package is not installed in this "
                "Python environment. Install it in the environment used to run the app.")
    if "transcription failed" in lowered:
        return "Audio transcription failed. Check that the audio file is valid and see data/app.log for the technical details."
    if "faster-whisper" in lowered or "faster_whisper" in lowered:
        return "The Faster-Whisper model could not be loaded. See the log file for details."
    if "missing" in lowered and "weight" in lowered:
        return "The model files are incomplete."
    if "import" in lowered and ("transformers" in lowered or "torch" in lowered):
        return "The speech libraries could not be started. Run the app with the environment used by your project."
    if "failed to load" in lowered or "loading failed" in lowered:
        return "The model could not be loaded. See the log file in the data folder for details."
    if "no model selected" in lowered:
        return "Please select a model from the list first."
    return "Something went wrong. The log file in the data folder has more details."


def load_audio_mono(audio_path: Path, target_sr: int = 16000):
    """Decode an audio file into mono float32 samples at ``target_sr``.

    Prefer Faster-Whisper's bundled PyAV decoder when installed. This supports
    common audio containers (including MP4/M4A) without requiring a separate
    system FFmpeg installation. The dependency repair script constrains PyAV
    to a version compatible with the installed Faster-Whisper release.
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
            "repair_desktop_pyav.bat from the project folder. "
            f"Original error: {faster_whisper_decode_error}"
        ) from faster_whisper_decode_error

    raise RuntimeError(
        "No local audio decoder could read this file. For common compressed "
        "formats, repair/install the Faster-Whisper audio dependencies in the "
        "application's existing Python environment, or convert the file to a "
        "16 kHz WAV. See data/app.log for decoder details."
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


def _path_is_within(path: Path, directory: Path) -> bool:
    """Return True when ``path`` resolves inside ``directory``."""
    try:
        Path(path).resolve().relative_to(Path(directory).resolve())
        return True
    except (OSError, ValueError):
        return False


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
        self.load(status_callback)
        check_cancel_requested(cancel_event)
        if status_callback:
            status_callback(f"Transcribing ({LANGUAGE_LABELS.get(language, language)})…")
        self.audio_seconds = get_wav_duration_seconds(audio_path)
        lang_code = WHISPER_LANGUAGE_CODES.get(language, "en")
        start = time.perf_counter()
        try:
            segments, _info = self.model.transcribe(
                str(audio_path), language=lang_code, beam_size=5
            )
            # Faster-Whisper generates audio in segments. Check the stop flag
            # between segments so the rest of a long recording is not processed.
            transcript_parts = []
            for segment in segments:
                check_cancel_requested(cancel_event)
                segment_text = segment.text.strip()
                if segment_text:
                    transcript_parts.append(segment_text)
            check_cancel_requested(cancel_event)
            text = " ".join(transcript_parts).strip()
        except Exception as exc:
            if "metadata_errors" in str(exc):
                raise RuntimeError(
                    "Faster-Whisper audio decoding failed because the installed "
                    "PyAV version is incompatible. Close the app and run "
                    "repair_desktop_pyav.bat from the project folder. "
                    "Details: " + str(exc)
                ) from exc
            raise RuntimeError("Faster-Whisper transcription failed. Details: " + str(exc)) from exc
        self.transcribe_seconds = time.perf_counter() - start
        LOGGER.info("Faster-Whisper transcribed %s in %.1f s",
                    audio_path.name, self.transcribe_seconds)
        return text


ADAPTERS: dict[str, type[BaseAdapter]] = {
    "qwen-asr": QwenASRAdapter,
    "whisper": WhisperHFAdapter,
    "faster-whisper": FasterWhisperAdapter,
}


def make_adapter(info: ModelInfo) -> Optional[BaseAdapter]:
    cls = ADAPTERS.get(info.family)
    return cls(info.path) if cls else None


# ---------------------------------------------------------------------------
# 5. THE APPLICATION WINDOW
# ---------------------------------------------------------------------------
class VoiceTypingApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Voice Typing Offline")

        # Optional Windows window/taskbar icon. Place app.ico next to this file.
        app_icon_path = ROOT / "app.ico"
        if app_icon_path.is_file():
            try:
                self.iconbitmap(default=str(app_icon_path))
            except tk.TclError:
                LOGGER.warning("Could not load app icon: %s", app_icon_path)

        self.events: queue.Queue = queue.Queue()
        self.current_file: Optional[Path] = None
        self.recording_stream = None
        self.recording_chunks: list = []
        self.recording_lock = threading.Lock()
        self.busy = False
        # This event asks the worker to discard its result when it finishes.
        # Most model libraries cannot interrupt a running inference safely.
        self.transcription_cancel_event = None
        self.model_loading = False
        self.recording_started_at = 0.0
        self.microphone_muted = False
        self.resume_mode = False
        self.history_window: Optional[tk.Toplevel] = None
        self.history_transcripts: dict[str, str] = {}
        self.start_size = (DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        self._settings_migrated = False

        self.theme_name = "dark"
        self.language_code = "en"
        self.models_dir = DEFAULT_MODELS_DIR
        self.preferred_model_key: Optional[str] = None

        self.available_models: list[ModelInfo] = []
        self.models_by_display: dict[str, ModelInfo] = {}
        self.current_info: Optional[ModelInfo] = None
        self.current_adapter: Optional[BaseAdapter] = None
        self.adapter_lock = threading.Lock()

        self._themed_widgets: list[tuple[tk.Widget, dict]] = []
        self.theme = THEMES[self.theme_name]

        self._load_settings()
        self.theme = THEMES.get(self.theme_name, THEMES["dark"])

        self.configure(bg=self.theme["bg"])
        self._setup_styles()
        self._setup_database()
        self._delete_temporary_recordings()
        self._build_ui()
        self._setup_window_size()
        # Persist a normalized legacy model path only after the window has its
        # real dimensions, so migration does not save a 1x1 startup geometry.
        if self._settings_migrated:
            self._save_settings()
        self._refresh_models(initial=True)

        self.after(100, self._poll_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- styles ---------------------------------------------------------
    def _setup_styles(self):
        theme = self.theme
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("TFrame", background=theme["bg"])
        style.configure("Card.TFrame", background=theme["card"])
        style.configure("Header.TFrame", background=theme["bg"])

        style.configure("TLabel", background=theme["bg"], foreground=theme["text"], font=FONT_NORMAL)
        style.configure("Title.TLabel", font=FONT_TITLE, foreground=theme["text"], background=theme["bg"])
        style.configure("Sub.TLabel", foreground=theme["muted"], background=theme["bg"], font=FONT_SMALL)
        style.configure("CardTitle.TLabel", font=FONT_HEADING, foreground=theme["text"], background=theme["card"])
        style.configure("CardText.TLabel", foreground=theme["text"], background=theme["card"])
        style.configure("CardMuted.TLabel", foreground=theme["muted"], background=theme["card"], font=FONT_SMALL)
        style.configure("Status.TLabel", foreground=theme["muted"], background=theme["card"], font=FONT_NORMAL)
        style.configure("ModelStatus.TLabel", foreground=theme["muted"], background=theme["card"], font=FONT_SMALL)

        style.configure("BadgeReady.TLabel",
                        foreground=theme["badge_ready_text"],
                        background=theme["badge_ready_bg"],
                        font=FONT_BADGE, padding=(SPACE_MEDIUM, 6))
        style.configure("BadgeWait.TLabel",
                        foreground=theme["badge_wait_text"],
                        background=theme["badge_wait_bg"],
                        font=FONT_BADGE, padding=(SPACE_MEDIUM, 6))
        style.configure("BadgeBad.TLabel",
                        foreground=theme["badge_bad_text"],
                        background=theme["badge_bad_bg"],
                        font=FONT_BADGE, padding=(SPACE_MEDIUM, 6))

        self._configure_button_style(style, "TButton",
                                     background=theme["secondary"],
                                     hover=theme["secondary_hover"],
                                     pressed=theme["secondary_pressed"],
                                     text=theme["text"], border=theme["border"],
                                     font=FONT_NORMAL, padding=BUTTON_PADDING)
        self._configure_button_style(style, "Accent.TButton",
                                     background=theme["accent"],
                                     hover=theme["accent_hover"],
                                     pressed=theme["accent_pressed"],
                                     text=theme["accent_text"], border=theme["accent"],
                                     font=FONT_PRIMARY_BUTTON, padding=PRIMARY_BUTTON_PADDING)
        self._configure_button_style(style, "Danger.TButton",
                                     background=theme["danger"],
                                     hover=theme["danger_hover"],
                                     pressed=theme["danger_pressed"],
                                     text="#ffffff", border=theme["danger"],
                                     font=FONT_BOLD, padding=BUTTON_PADDING)
        self._configure_button_style(style, "Subtle.TButton",
                                     background=theme["card"],
                                     hover=theme["secondary"],
                                     pressed=theme["secondary_pressed"],
                                     text=theme["muted"], border=theme["border"],
                                     font=FONT_NORMAL, padding=BUTTON_PADDING)
        style.map("Subtle.TButton",
                  foreground=[("disabled", theme["disabled_text"]), ("active", theme["text"])])

        style.configure("TCombobox",
                        fieldbackground=theme["input"],
                        background=theme["secondary"],
                        foreground=theme["text"],
                        arrowcolor=theme["accent"],
                        bordercolor=theme["input_border"],
                        lightcolor=theme["input"],
                        darkcolor=theme["input"],
                        padding=6)
        style.map("TCombobox",
                  fieldbackground=[("readonly", theme["input"])],
                  foreground=[("readonly", theme["text"])],
                  selectbackground=[("readonly", theme["input"])],
                  selectforeground=[("readonly", theme["text"])])

        style.configure("Horizontal.TProgressbar",
                        troughcolor=theme["input"],
                        background=theme["accent"],
                        bordercolor=theme["border"],
                        lightcolor=theme["accent"],
                        darkcolor=theme["accent"],
                        thickness=8)

        style.configure("Vertical.TScrollbar",
                        background=theme["secondary"],
                        troughcolor=theme["input"],
                        bordercolor=theme["input"],
                        arrowcolor=theme["muted"],
                        lightcolor=theme["secondary"],
                        darkcolor=theme["secondary"])
        style.map("Vertical.TScrollbar", background=[("active", theme["secondary_hover"])])

        style.configure("Treeview",
                        background=theme["input"],
                        fieldbackground=theme["input"],
                        foreground=theme["text"],
                        bordercolor=theme["border"],
                        rowheight=28,
                        font=FONT_NORMAL)
        style.map("Treeview",
                  background=[("selected", theme["selection"])],
                  foreground=[("selected", theme["selection_text"])])
        style.configure("Treeview.Heading",
                        background=theme["secondary"],
                        foreground=theme["text"],
                        relief="flat",
                        font=FONT_BOLD,
                        padding=(8, 6))
        style.map("Treeview.Heading", background=[("active", theme["secondary_hover"])])

        self._apply_listbox_options()

    def _apply_listbox_options(self):
        theme = self.theme
        self.option_add("*TCombobox*Listbox.background", theme["input"])
        self.option_add("*TCombobox*Listbox.foreground", theme["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", theme["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", theme["accent_text"])

    def _configure_button_style(self, style, name, *, background, hover, pressed,
                                text, border, font, padding):
        style.configure(
            name, font=font, padding=padding,
            background=background, foreground=text,
            bordercolor=border,
            lightcolor=background, darkcolor=background,
            focuscolor=background, borderwidth=1, relief="flat", anchor="center",
        )
        style.map(
            name,
            background=[("disabled", self.theme["disabled_bg"]),
                        ("pressed", pressed), ("active", hover)],
            foreground=[("disabled", self.theme["disabled_text"])],
            bordercolor=[("disabled", border), ("pressed", pressed), ("active", hover)],
            lightcolor=[("disabled", self.theme["disabled_bg"]),
                        ("pressed", pressed), ("active", hover)],
            darkcolor=[("disabled", self.theme["disabled_bg"]),
                       ("pressed", pressed), ("active", hover)],
        )

    def _themed(self, widget, **mapping):
        self._themed_widgets.append((widget, mapping))
        self._apply_theme_to_widget(widget, mapping)
        return widget

    def _apply_theme_to_widget(self, widget, mapping):
        kwargs = {}
        for key, theme_key in mapping.items():
            value = self.theme.get(theme_key)
            if value is not None:
                kwargs[key] = value
        if kwargs:
            try:
                widget.configure(**kwargs)
            except tk.TclError:
                pass

    def _switch_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.theme = THEMES[self.theme_name]
        self.configure(bg=self.theme["bg"])
        self._setup_styles()
        for widget, mapping in list(self._themed_widgets):
            try:
                if widget.winfo_exists():
                    self._apply_theme_to_widget(widget, mapping)
            except tk.TclError:
                pass
        self._draw_header_branding()
        self._draw_theme_icon()
        self._draw_refresh_icon()
        self._draw_folder_icon()
        for code, button in self.lang_buttons.items():
            button.configure(
                style="Accent.TButton" if code == self.language_code else "TButton"
            )
        self._apply_text_direction()
        self._update_empty_state()
        self._save_settings()

    # ---- database -------------------------------------------------------
    def _setup_database(self):
        DATA_DIR.mkdir(exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                filename TEXT NOT NULL,
                model_name TEXT NOT NULL,
                transcript TEXT NOT NULL
            )""")

    # ---- UI construction ------------------------------------------------
    def _create_card(self, parent, expand=False, pady=(0, SPACE_MEDIUM)):
        border = tk.Frame(parent)
        self._themed(border, bg="card", highlightbackground="border")
        border.configure(highlightthickness=1)
        if expand:
            border.pack(fill="both", expand=True, pady=pady)
        else:
            border.pack(fill="x", pady=pady)
        inner = ttk.Frame(border, style="Card.TFrame", padding=CARD_PADDING)
        inner.pack(fill="both", expand=True)
        return inner

    def _icon_button_hover(self, canvas, hovered):
        """Give small icon buttons a gentle hover background."""
        color_key = "secondary_hover" if hovered else "secondary"
        try:
            canvas.configure(bg=self.theme[color_key])
        except tk.TclError:
            pass

    def _draw_theme_icon(self):
        """Draw a sun or moon icon for the day/night theme switch."""
        canvas = self.theme_button
        canvas.delete("all")
        canvas.configure(bg=self.theme["secondary"])
        color = self.theme["warning"] if self.theme_name == "dark" else self.theme["accent"]

        if self.theme_name == "dark":
            # A sun means that clicking will switch to the light theme.
            canvas.create_oval(12, 10, 22, 20, fill=color, outline=color)
            for x1, y1, x2, y2 in (
                (17, 4, 17, 8), (17, 22, 17, 26),
                (7, 15, 10, 15), (24, 15, 28, 15),
                (10, 8, 12, 10), (22, 20, 25, 23),
                (10, 22, 12, 20), (22, 10, 25, 7),
            ):
                canvas.create_line(x1, y1, x2, y2, fill=color, width=2, capstyle="round")
        else:
            # A crescent moon means that clicking will switch to the dark theme.
            canvas.create_oval(8, 5, 25, 25, fill=color, outline=color)
            canvas.create_oval(
                14, 3, 28, 18,
                fill=self.theme["secondary"],
                outline=self.theme["secondary"],
            )

    def _draw_refresh_icon(self):
        """Draw a circular refresh arrow on the model refresh control."""
        canvas = self.refresh_button
        canvas.delete("all")
        canvas.configure(bg=self.theme["secondary"])
        color = self.theme["accent"]
        canvas.create_arc(
            6, 4, 26, 24,
            start=40,
            extent=300,
            style="arc",
            outline=color,
            width=2,
        )
        canvas.create_polygon(
            22, 4, 28, 6, 24, 11,
            fill=color,
            outline=color,
        )

    def _draw_folder_icon(self):
        """Draw a simple folder outline for choosing the models directory."""
        canvas = self.locate_button
        canvas.delete("all")
        canvas.configure(bg=self.theme["secondary"])
        color = self.theme["accent"]
        canvas.create_polygon(
            4, 8, 12, 8, 15, 11, 27, 11,
            27, 22, 4, 22,
            fill=self.theme["accent_soft"],
            outline=color,
            width=2,
        )
        canvas.create_line(4, 12, 27, 12, fill=color, width=1)

    def _draw_header_branding(self):
        """Redraw the NR Company logo after theme changes instead of replacing it with the fallback mic."""
        canvas = getattr(self, "_icon_canvas", None)
        if canvas is None:
            return
        try:
            canvas.delete("all")
            canvas.configure(bg=self.theme["bg"])
            logo = getattr(self, "_header_logo_source", None)
            if logo is not None:
                # Keep the full logo visible within the 64px header icon.
                factor = max(1, (max(logo.width(), logo.height()) + 63) // 64)
                if factor > 1:
                    display_logo = logo.subsample(factor, factor)
                else:
                    display_logo = logo
                self._header_logo = display_logo  # retain Tk image reference
                canvas.create_image(32, 32, image=self._header_logo)
            else:
                self._draw_mic_icon(canvas)
        except tk.TclError:
            pass

    def _draw_mic_icon(self, canvas):
        """Draw a simple microphone logo using Tkinter vector shapes."""
        theme = self.theme
        canvas.configure(bg=theme["bg"])
        canvas.delete("all")

        # Scale the original 56-pixel design to the current canvas size.
        scale = min(canvas.winfo_reqwidth(), canvas.winfo_reqheight()) / 56

        def point(value):
            return int(round(value * scale))

        canvas.create_oval(
            point(2), point(2), point(54), point(54),
            fill=theme["accent_soft"],
            outline=theme["accent"],
            width=max(1, point(2)),
        )
        canvas.create_oval(
            point(22), point(12), point(34), point(30),
            fill=theme["accent"],
            outline=theme["accent"],
        )
        canvas.create_arc(
            point(17), point(20), point(39), point(42),
            start=180,
            extent=180,
            style="arc",
            outline=theme["accent"],
            width=max(1, point(2)),
        )
        canvas.create_line(
            point(28), point(42), point(28), point(46),
            fill=theme["accent"],
            width=max(1, point(2)),
        )
        canvas.create_line(
            point(22), point(46), point(34), point(46),
            fill=theme["accent"],
            width=max(1, point(2)),
        )

    def _build_ui(self):
        """Build a compact, single-page layout that adapts to window resizing."""
        outer = ttk.Frame(self, padding=(12, 8))
        outer.pack(fill="both", expand=True)

        # Keep the title, history, theme and model status at the top.
        self._build_header(outer)

        # Keep model and audio controls in compact rows above the editor.
        self._build_model_card(outer)
        self._build_input_card(outer)

        # The recording buttons stay pinned to the bottom and remain visible.
        self._build_footer(outer)

        # The transcript editor expands to use the remaining window space.
        self._build_transcript_card(outer)

    def _build_header(self, parent):
        """Show the app identity and a few always-available actions."""
        header = ttk.Frame(parent, style="Header.TFrame")
        header.pack(fill="x", pady=(0, 6))

        self._icon_canvas = tk.Canvas(
            header,
            width=64,
            height=64,
            highlightthickness=0,
        )
        self._themed(self._icon_canvas, bg="bg")
        self._icon_canvas.pack(side="left", padx=(0, 9))
        logo_path = ROOT / "nr_company_logo.png"
        self._header_logo_source = None
        if logo_path.is_file():
            try:
                self._header_logo_source = tk.PhotoImage(file=str(logo_path))
            except tk.TclError:
                self._header_logo_source = None
        self._draw_header_branding()

        title_box = ttk.Frame(header, style="Header.TFrame")
        title_box.pack(side="left", fill="y")

        ttk.Label(
            title_box,
            text="Voice Typing",
            style="Title.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            title_box,
            text="Offline speech-to-text  ·  دری / English",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(1, 0))

        # These buttons stay available even when the window is small.
        self.theme_button = tk.Canvas(
            header,
            width=34,
            height=32,
            highlightthickness=1,
            takefocus=True,
            cursor="hand2",
        )
        self._themed(
            self.theme_button,
            bg="secondary",
            highlightbackground="border",
        )
        self.theme_button.pack(side="right", padx=(6, 0))
        self.theme_button.bind("<Button-1>", lambda event: self._switch_theme())
        self.theme_button.bind("<Return>", lambda event: self._switch_theme())
        self.theme_button.bind("<space>", lambda event: self._switch_theme())
        self.theme_button.bind(
            "<Enter>", lambda event: self._icon_button_hover(self.theme_button, True)
        )
        self.theme_button.bind(
            "<Leave>", lambda event: self._icon_button_hover(self.theme_button, False)
        )
        self._draw_theme_icon()

        ttk.Button(
            header,
            text="▤ History",
            command=self._open_history_window,
        ).pack(side="right", padx=(6, 0))

        ttk.Button(
            header,
            text="ⓘ About app",
            command=self._open_about_window,
        ).pack(side="right", padx=(6, 0))

        self.model_badge = ttk.Label(
            header,
            text="●  Checking model…",
            style="BadgeWait.TLabel",
        )
        self.model_badge.pack(side="right", padx=(0, 8))

    def _open_about_window(self):
        """Show the user guide and editable author-information placeholders."""
        window = tk.Toplevel(self)
        window.title("About · Voice Typing Offline")
        window.geometry("760x650")
        window.minsize(560, 460)
        window.transient(self)
        window.configure(bg=self.theme["bg"])

        outer = ttk.Frame(window, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Voice Typing Offline", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="User guide and application information",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        guide_tab = ttk.Frame(notebook, padding=12)
        about_tab = ttk.Frame(notebook, padding=12)
        notebook.add(guide_tab, text="How to use")
        notebook.add(about_tab, text="About me / About app")

        guide_text = tk.Text(
            guide_tab, wrap="word", relief="flat", bd=0, padx=12, pady=10,
            font=FONT_NORMAL, spacing1=2, spacing3=4,
        )
        guide_scroll = ttk.Scrollbar(guide_tab, orient="vertical", command=guide_text.yview)
        guide_text.configure(yscrollcommand=guide_scroll.set)
        guide_text.pack(side="left", fill="both", expand=True)
        guide_scroll.pack(side="right", fill="y")
        self._themed(guide_text, bg="input", fg="text", insertbackground="accent")
        guide_text.insert("1.0", """WELCOME

Voice Typing Offline converts speech into text on your computer using a locally installed speech-recognition model. After setup and model download, speech recognition is designed to work offline.

1. FIRST-TIME SETUP
• Run the project's installer/setup steps and let it install the required Python packages.
• Download a supported model using the provided model downloader, then wait until the model status says it is ready.
• Keep the model files inside the project's models folder. Model files can be large and may take time to download.

2. CHOOSE A MODEL AND LANGUAGE
• Choose a model from the model selector.
• Select the language you will speak: Dari or English. Recognition quality depends on the model, microphone, background noise, and speaking clearly.
• If a model is not ready, follow the status or error message before recording.

3. RECORDING YOUR VOICE
• Click Start recording to begin a new recording. Starting a new recording clears the text box.
• Speak clearly and keep a reasonable distance from the microphone. The animated waveform and large timer indicate that recording is active.
• Click Mute mic to temporarily stop capturing microphone audio. Click Unmute mic to capture audio again. Muted time is not captured.
• Click Stop & transcribe when you finish. The app stops recording and sends the captured audio to the selected local model for transcription. Wait for the result.
• Click Resume to record another segment and append its recognized text to the existing text instead of clearing it. Stop & transcribe each segment before resuming.

4. EDITING AND USING TEXT
• The Recognized text area is editable. You can correct words, add punctuation, or arrange paragraphs yourself.
• Use Copy text to copy the result to the clipboard. Other available buttons may let you save or manage text, depending on the controls shown in the app.
• Use History to review previously saved transcriptions.

5. LIGHT AND DARK MODE
• Click the theme button in the top-right corner (sun/moon icon) to switch between dark and light appearance. Click again to switch back.

6. TRANSCRIBING AN AUDIO FILE
• Use the audio-file selection control to choose a supported audio file.
• Select the language and model, then start transcription using the available Transcribe control. Supported formats are listed in the app.
• Long files may take time to process. Do not close the app while transcription is running.

7. STATUS, ERRORS, AND PRIVACY
• Read the status messages below the recording controls. They report model readiness, recording, and transcription progress.
• If the microphone does not work, check Windows microphone permissions, the selected input device, and that the required audio packages are installed.
• Speech recognition uses the local model for inference. Downloading Python packages or model files during setup still requires an internet connection.
• Temporary recordings are used during processing. Avoid recording sensitive information on a shared computer.

8. QUICK WORKFLOW
New text: choose language/model → Start recording → speak → Stop & transcribe.
Continue text: Resume → speak → Stop & transcribe. Repeat as needed.
""")
        guide_text.configure(state="disabled")

        about_label = ttk.Label(
            about_tab,
            text=(
                "Enter your own details in the placeholders below before sharing this app. "
                "These fields intentionally contain no personal information."
            ),
            style="CardMuted.TLabel", wraplength=620, justify="left",
        )
        about_label.pack(anchor="w", pady=(0, 10))
        about_text = tk.Text(
            about_tab, wrap="word", relief="flat", bd=0, padx=12, pady=10,
            font=FONT_NORMAL, height=12,
        )
        about_text.pack(fill="both", expand=True)
        self._themed(about_text, bg="input", fg="text", insertbackground="accent")
        about_text.insert("1.0", """ABOUT THE CREATOR
Name: [Nooraqa Rasouli]
Role / title: [Company Founter/ Software Engineer]
Contact / website: [+93_764_891_609]

ABOUT THIS APP
Concept / idea provided by: [Nooraqa Rasouli]
Developed by: [Nooraqa Rasouli]
Purpose: Offline speech-to-text for Dari and English.
Version: [1.0]
Credits / acknowledgements: [To Chatgpt and othe AI Platforms for Handle Errors Clear Codes.]
Dedicated to: my dear family, with thanks for their endless support.
""")
        close_row = ttk.Frame(outer)
        close_row.pack(fill="x", pady=(10, 0))
        ttk.Button(close_row, text="Close", command=window.destroy).pack(side="right")

    def _build_model_card(self, parent):
        """Place model selection and language buttons on one compact row."""
        card = self._create_card(parent, pady=(0, 6))

        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")

        ttk.Label(
            row,
            text="⚙ Model",
            style="CardTitle.TLabel",
        ).pack(side="left", padx=(0, 8))

        self.model_var = tk.StringVar(value="")
        self.model_box = ttk.Combobox(
            row,
            textvariable=self.model_var,
            state="readonly",
            width=28,
        )
        self.model_box.pack(side="left", fill="x", expand=True)
        self.model_box.bind("<<ComboboxSelected>>", self._on_model_selected)

        # A circular arrow makes the refresh action easy to recognize.
        # Canvas icons stay crisp and familiar without adding another package.
        self.refresh_button = tk.Canvas(
            row,
            width=32,
            height=28,
            highlightthickness=1,
            takefocus=True,
            cursor="hand2",
        )
        self._themed(
            self.refresh_button,
            bg="secondary",
            highlightbackground="border",
        )
        self.refresh_button.pack(side="left", padx=(5, 3))
        self.refresh_button.bind(
            "<Button-1>", lambda event: self._refresh_models()
        )
        self.refresh_button.bind(
            "<Return>", lambda event: self._refresh_models()
        )
        self.refresh_button.bind(
            "<space>", lambda event: self._refresh_models()
        )
        self.refresh_button.bind(
            "<Enter>", lambda event: self._icon_button_hover(self.refresh_button, True)
        )
        self.refresh_button.bind(
            "<Leave>", lambda event: self._icon_button_hover(self.refresh_button, False)
        )
        self._draw_refresh_icon()

        self.locate_button = tk.Canvas(
            row,
            width=32,
            height=28,
            highlightthickness=1,
            takefocus=True,
            cursor="hand2",
        )
        self._themed(
            self.locate_button,
            bg="secondary",
            highlightbackground="border",
        )
        self.locate_button.pack(side="left", padx=(0, 7))
        self.locate_button.bind(
            "<Button-1>", lambda event: self._choose_models_directory()
        )
        self.locate_button.bind(
            "<Return>", lambda event: self._choose_models_directory()
        )
        self.locate_button.bind(
            "<space>", lambda event: self._choose_models_directory()
        )
        self.locate_button.bind(
            "<Enter>", lambda event: self._icon_button_hover(self.locate_button, True)
        )
        self.locate_button.bind(
            "<Leave>", lambda event: self._icon_button_hover(self.locate_button, False)
        )
        self._draw_folder_icon()

        separator = tk.Frame(row, width=1, height=26)
        self._themed(separator, bg="border")
        separator.pack(side="left", padx=(0, 8))

        ttk.Label(
            row,
            text="Language",
            style="CardMuted.TLabel",
        ).pack(side="left", padx=(0, 6))

        self.lang_buttons: dict[str, ttk.Button] = {}
        short_labels = {"fa": "دری", "en": "EN"}
        for code in LANGUAGE_ORDER:
            button = ttk.Button(
                row,
                text=short_labels[code],
                width=5,
                command=lambda c=code: self._set_language(c),
            )
            button.pack(side="left", padx=(0, 3))
            self.lang_buttons[code] = button
            button.configure(
                style="Accent.TButton" if code == self.language_code else "TButton"
            )

        # Keep diagnostic details visible below the controls without taking
        # up the height of the old two-column model card.
        status_row = ttk.Frame(card, style="Card.TFrame")
        status_row.pack(fill="x", pady=(5, 0))

        self.model_status_var = tk.StringVar(value="Scanning…")
        self.model_status_label = ttk.Label(
            status_row,
            textvariable=self.model_status_var,
            style="ModelStatus.TLabel",
            justify="left",
        )
        self.model_status_label.pack(side="left", fill="x", expand=True)
        self.model_status_label.bind(
            "<Configure>",
            lambda event: self.model_status_label.configure(
                wraplength=max(180, event.width - 8)
            ),
        )

        self.language_hint_var = tk.StringVar(value="")
        self.language_hint_label = ttk.Label(
            status_row,
            textvariable=self.language_hint_var,
            style="CardMuted.TLabel",
            justify="right",
        )
        self.language_hint_label.pack(side="right", padx=(8, 0))

    def _build_input_card(self, parent):
        """Show audio-file controls in one short, responsive row."""
        card = self._create_card(parent, pady=(0, 6))
        row = ttk.Frame(card, style="Card.TFrame")
        row.pack(fill="x")

        self.file_label_var = tk.StringVar(value="No audio file selected")
        self.file_label = ttk.Label(
            row,
            textvariable=self.file_label_var,
            style="CardText.TLabel",
            justify="left",
            wraplength=360,
        )
        self.file_label.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.file_label.bind(
            "<Configure>",
            lambda event: self.file_label.configure(
                wraplength=max(160, event.width - 8)
            ),
        )

        self.choose_button = ttk.Button(
            row,
            text="▣ Choose audio",
            command=self._choose_file,
        )
        self.choose_button.pack(side="left", padx=(0, 5))

        self.transcribe_button = ttk.Button(
            row,
            text="▶ Transcribe",
            style="Accent.TButton",
            command=self._start_file_transcription,
        )
        self.transcribe_button.pack(side="left", padx=(0, 5))

        self.cancel_transcription_button = ttk.Button(
            row,
            text="■ Cancel",
            style="Danger.TButton",
            command=self._cancel_transcription,
            state="disabled",
        )
        self.cancel_transcription_button.pack(side="left")

        ttk.Label(
            card,
            text="Audio formats: " + get_supported_formats_text(),
            style="CardMuted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

    def _build_transcript_card(self, parent):
        """Create the editable transcript area and its quick-action buttons."""
        card = self._create_card(parent, expand=True, pady=0)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1, minsize=60)

        ttk.Label(
            card,
            text="✎  Recognized text  ·  editable",
            style="CardTitle.TLabel",
        ).grid(row=0, column=0, sticky="w")

        text_frame = tk.Frame(card)
        self._themed(
            text_frame,
            bg="input",
            highlightbackground="input_border",
        )
        text_frame.configure(highlightthickness=1)
        text_frame.grid(
            row=1,
            column=0,
            sticky="nsew",
            pady=(7, 7),
        )

        self.result_text = tk.Text(
            text_frame,
            wrap="word",
            undo=True,
            width=10,
            height=4,
            font=FONT_EDITOR,
            relief="flat",
            bd=0,
            padx=12,
            pady=8,
            highlightthickness=0,
            spacing3=2,
        )
        self._themed(
            self.result_text,
            bg="input",
            fg="text",
            insertbackground="accent",
            selectbackground="selection",
            selectforeground="selection_text",
        )
        self.result_text.tag_configure(
            "rtl",
            justify="right",
            lmargin1=8,
            lmargin2=8,
            rmargin=8,
        )
        self.result_text.tag_configure("ltr", justify="left")

        scroll = ttk.Scrollbar(text_frame, command=self.result_text.yview)
        self.result_text.configure(yscrollcommand=scroll.set)
        self.result_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.empty_label = tk.Label(
            self.result_text,
            text=(
                "Your transcript will appear here.\n"
                "Choose an audio file or press Start recording."
            ),
            font=FONT_HINT,
            justify="center",
        )
        self._themed(self.empty_label, bg="input", fg="muted")
        self.empty_label.bind("<Button-1>", self._focus_editor)
        self.result_text.bind("<KeyRelease>", self._update_empty_state)
        self.result_text.bind("<ButtonRelease-1>", self._update_empty_state)
        self.result_text.bind("<<Modified>>", self._on_text_modified)
        self._update_empty_state()

        separator = tk.Frame(card, height=1)
        self._themed(separator, bg="border")
        separator.grid(row=2, column=0, sticky="ew")

        self.action_bar = ttk.Frame(card, style="Card.TFrame")
        self.action_bar.grid(
            row=3,
            column=0,
            sticky="ew",
            pady=(7, 0),
        )

        self.copy_button = ttk.Button(
            self.action_bar,
            text="▢ Copy text",
            command=self._copy_text,
        )
        self.save_button = ttk.Button(
            self.action_bar,
            text="⇩ Save TXT",
            command=self._save_text,
        )
        self.clear_button = ttk.Button(
            self.action_bar,
            text="⌫ Clear text",
            style="Subtle.TButton",
            command=self._clear_text,
        )
        self.action_buttons = [
            self.copy_button,
            self.save_button,
            self.clear_button,
        ]
        self.action_bar_width = 0
        self._arrange_buttons_in_rows(self.action_buttons, 100000)
        self.action_bar.bind("<Configure>", self._on_action_bar_resize)

        # The error panel is kept inside the editor area so errors do not hide
        # the always-visible microphone controls in the bottom toolbar.
        self.error_frame = tk.Frame(card)
        self._themed(
            self.error_frame,
            bg="error_bg",
            highlightbackground="danger",
        )
        self.error_frame.configure(highlightthickness=1)
        self.error_frame.grid(
            row=4,
            column=0,
            sticky="ew",
            pady=(7, 0),
        )

        self.error_title_var = tk.StringVar(value="")
        self.error_message_var = tk.StringVar(value="")
        text_box = tk.Frame(self.error_frame)
        self._themed(text_box, bg="error_bg")
        text_box.pack(
            side="left",
            fill="x",
            expand=True,
            padx=8,
            pady=7,
        )

        self.error_title_label = tk.Label(
            text_box,
            textvariable=self.error_title_var,
            font=FONT_BOLD,
            anchor="w",
        )
        self._themed(self.error_title_label, bg="error_bg", fg="danger_hover")
        self.error_title_label.pack(fill="x")

        self.error_message_label = tk.Label(
            text_box,
            textvariable=self.error_message_var,
            font=FONT_NORMAL,
            anchor="w",
            justify="left",
            wraplength=420,
            height=2,
        )
        self._themed(self.error_message_label, bg="error_bg", fg="text")
        self.error_message_label.pack(fill="x", pady=(2, 0))
        self.error_message_label.bind("<Configure>", self._on_error_message_resize)

        ttk.Button(
            self.error_frame,
            text="ⓘ Details",
            command=self._show_error_details,
        ).pack(side="right", padx=8)
        self.error_frame.grid_remove()

    def _build_footer(self, parent):
        """Build a fixed bottom bar for recording and live status."""
        border = tk.Frame(parent)
        self._themed(border, bg="card", highlightbackground="border")
        border.configure(highlightthickness=1)
        border.pack(side="bottom", fill="x", pady=(6, 0))

        inner = ttk.Frame(
            border,
            style="Card.TFrame",
            padding=(8, 7),
        )
        inner.pack(fill="both", expand=True)

        live_panel = ttk.Frame(inner, style="Card.TFrame")
        live_panel.pack(side="top", fill="x", pady=(0, 8))
        self.recording_timer_var = tk.StringVar(value="00:00")
        self.recording_timer_label = ttk.Label(
            live_panel, textvariable=self.recording_timer_var,
            style="Title.TLabel", font=("Segoe UI", 24, "bold"),
        )
        self.recording_timer_label.pack(side="left", padx=(2, 16))
        self.waveform_canvas = tk.Canvas(
            live_panel, height=34, width=250, highlightthickness=0, bd=0,
        )
        self._themed(self.waveform_canvas, bg="card")
        self.waveform_canvas.pack(side="left", padx=(0, 6), fill="x", expand=False)
        self.waveform_canvas.bind("<Configure>", lambda event: self._draw_recording_waveform())
        self._waveform_phase = 0

        recording_controls = ttk.Frame(inner, style="Card.TFrame")
        recording_controls.pack(side="top", fill="x")

        self.record_button = ttk.Button(
            recording_controls,
            text="●  Start recording",
            style="Accent.TButton",
            command=self._start_recording,
        )
        self.record_button.pack(side="left", padx=(0, 5))

        self.stop_record_button = ttk.Button(
            recording_controls,
            text="■  Stop & transcribe",
            style="Danger.TButton",
            command=self._stop_recording,
            state="disabled",
        )
        self.stop_record_button.pack(side="left", padx=(0, 5))

        self.resume_record_button = ttk.Button(
            recording_controls,
            text="▶  Resume",
            command=self._resume_recording,
            state="disabled",
        )
        self.resume_record_button.pack(side="left", padx=(0, 5))

        self.mute_button = ttk.Button(
            recording_controls,
            text="🔇  Mute mic",
            command=self._toggle_microphone_mute,
        )
        self.mute_button.pack(side="left")

        status_box = ttk.Frame(inner, style="Card.TFrame")
        status_box.pack(side="top", fill="x", expand=False, pady=(7, 0))

        self.progress = ttk.Progressbar(
            status_box,
            mode="indeterminate",
        )
        self.progress.pack(fill="x", pady=(0, 3))

        self.status_var = tk.StringVar(value="Ready.")
        self.status_label = ttk.Label(
            status_box,
            textvariable=self.status_var,
            style="Status.TLabel",
            justify="left",
            anchor="w",
            wraplength=400,
        )
        self.status_label.pack(fill="x")
        self.status_label.bind("<Configure>", self._on_status_resize)

        self.timing_var = tk.StringVar(value="")

    def _fit_label_to_width(self, event, label):
        try:
            label.configure(wraplength=max(120, event.width - 4))
        except tk.TclError:
            pass

    def _on_status_resize(self, event):
        self._fit_label_to_width(event, self.status_label)

    def _on_timing_resize(self, event):
        self._fit_label_to_width(event, self.timing_label)

    def _on_error_message_resize(self, event):
        self._fit_label_to_width(event, self.error_message_label)

    def _arrange_buttons_in_rows(self, buttons, available_width):
        for button in buttons:
            button.grid_forget()
        row, column, used = 0, 0, 0
        for button in buttons:
            needed = button.winfo_reqwidth() + SPACE_SMALL
            if column > 0 and used + needed > available_width:
                row += 1
                column = 0
                used = 0
            button.grid(row=row, column=column, sticky="w",
                        padx=(0, SPACE_SMALL), pady=(0, SPACE_SMALL))
            column += 1
            used += needed

    def _on_action_bar_resize(self, event):
        if event.width == self.action_bar_width:
            return
        self.action_bar_width = event.width
        self._arrange_buttons_in_rows(self.action_buttons, event.width)

    def _update_empty_state(self, event=None):
        text = self.result_text.get("1.0", "end-1c")
        if text.strip():
            self.empty_label.place_forget()
        else:
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def _focus_editor(self, event=None):
        self.result_text.focus_set()

    def _on_text_modified(self, event=None):
        try:
            self.result_text.edit_modified(False)
        except tk.TclError:
            return
        self._apply_text_direction()
        self._update_empty_state()

    def _apply_text_direction(self):
        text_widget = self.result_text
        try:
            text_widget.tag_remove("rtl", "1.0", "end")
            text_widget.tag_remove("ltr", "1.0", "end")
            if self.language_code == "fa":
                text_widget.tag_add("rtl", "1.0", "end")
                text_widget.configure(font=self._persian_font())
            else:
                text_widget.tag_add("ltr", "1.0", "end")
                text_widget.configure(font=FONT_EDITOR)
        except tk.TclError:
            pass

    def _persian_font(self) -> tuple:
        try:
            installed = set(tk.font.families(self)) if hasattr(tk, "font") else set()
        except Exception:
            installed = set()
        for family in PERSIAN_FONT_CANDIDATES:
            if family in installed:
                return (family, 13)
        return FONT_EDITOR_DARI

    # ---- model status ---------------------------------------------------
    def _update_model_badge(self, text, state):
        style = {"ready": "BadgeReady.TLabel",
                 "wait": "BadgeWait.TLabel",
                 "bad": "BadgeBad.TLabel"}.get(state, "BadgeWait.TLabel")
        try:
            self.model_badge.configure(text="●  " + text, style=style)
        except tk.TclError:
            pass

    def _set_model_status(self, text, tone="info"):
        colors = {"info": self.theme["muted"], "working": self.theme["info"],
                  "success": self.theme["success"], "warning": self.theme["warning"],
                  "error": self.theme["danger_hover"]}
        self.model_status_var.set(text)
        try:
            self.model_status_label.configure(foreground=colors.get(tone, self.theme["muted"]))
        except tk.TclError:
            pass

    def _show_status(self, message, tone="info"):
        colors = {"info": self.theme["muted"], "working": self.theme["info"],
                  "success": self.theme["success"], "warning": self.theme["warning"],
                  "error": self.theme["danger_hover"]}
        self.status_var.set(message)
        try:
            self.status_label.configure(foreground=colors.get(tone, self.theme["muted"]))
        except tk.TclError:
            pass

    def _show_error(self, title, message):
        """Show a short error summary and keep full details available."""
        self.error_details = str(message)
        self.error_title_var.set(str(title))

        # A compact summary prevents long errors from pushing controls off-screen.
        summary = " ".join(str(message).split())
        if len(summary) > 105:
            summary = summary[:102].rstrip() + "…"
        self.error_message_var.set(summary)
        self.error_frame.grid()
        self._show_status(title, "error")

    def _show_error_details(self):
        """Open the complete error text in a normal Windows dialog."""
        title = self.error_title_var.get() or "Voice Typing error"
        details = getattr(self, "error_details", self.error_message_var.get())
        messagebox.showerror(title, details, parent=self)

    def _hide_error(self):
        self.error_frame.grid_remove()

    def _update_timing_text(self):
        parts = []
        adapter = self.current_adapter
        if adapter is not None:
            if adapter.load_seconds > 0:
                parts.append("Model load: " + format_seconds(adapter.load_seconds))
            if adapter.transcribe_seconds > 0:
                parts.append("Transcription: " + format_seconds(adapter.transcribe_seconds))
            if adapter.audio_seconds > 0:
                parts.append("Audio: " + format_seconds(adapter.audio_seconds))
        self.timing_var.set("  ·  ".join(parts))

    # ---- discovery ------------------------------------------------------
    def _refresh_models(self, initial=False):
        self._update_model_badge("Scanning…", "wait")
        self._set_model_status("Scanning the models folder…", "working")
        threading.Thread(
            target=self._scan_models_worker,
            args=(self.models_dir, initial),
            daemon=True,
        ).start()

    def _scan_models_worker(self, models_dir: Path, initial: bool):
        try:
            models, diagnostics = discover_models(models_dir)
            self.events.put(("models_scanned", (models, str(models_dir), initial, diagnostics)))
        except Exception as exc:
            LOGGER.exception("Model discovery failed")
            self.events.put(("models_scan_failed", (str(exc), str(models_dir))))

    def _handle_models_scanned(self, payload):
        models, models_dir_str, initial, diagnostics = payload
        self.available_models = models
        # preferred_model_key is updated when the user selects a model folder or
        # changes roots. Do not override it with a stale current_info from the
        # previous scan/root.
        previous_key = self.preferred_model_key

        self.models_by_display = {}
        display_names = []
        for info in models:
            name = info.display_name
            base = name
            counter = 2
            while name in self.models_by_display:
                name = f"{base} ({counter})"
                counter += 1
            self.models_by_display[name] = info
            display_names.append(name)

        if not models:
            self.model_box["values"] = ["No compatible model found"]
            self.model_var.set("No compatible model found")
            self.model_box.configure(state="disabled")
            self._update_model_badge("No model found", "bad")

            detail = ""
            if diagnostics:
                detail = "\n\nDirectories seen but not recognized:\n"
                for path_str, reason in diagnostics[:6]:
                    detail += f"  • {path_str}\n    ({reason})\n"
            self._set_model_status(
                f"No compatible speech model was found in:\n{models_dir_str}",
                "error",
            )
            self._show_error(
                "No compatible local model found",
                f"The application scanned:\n{models_dir_str}\n\n"
                "Place a Qwen-ASR, Whisper, or Faster-Whisper folder inside "
                "that directory, or use the folder button to pick a different "
                "models directory. Nothing is downloaded automatically."
                + detail,
            )
            self._set_busy(False)
            return

        self.model_box.configure(state="readonly")
        self.model_box["values"] = display_names

        chosen_display = None
        if previous_key:
            for name, info in self.models_by_display.items():
                if info.key == previous_key:
                    chosen_display = name
                    break
        if chosen_display is None:
            ready = [n for n, i in self.models_by_display.items() if i.status == "ready"]
            chosen_display = ready[0] if ready else display_names[0]

        self.model_var.set(chosen_display)
        self._hide_error()
        self._on_model_selected(initial=initial)

        self._show_status(f"Detected {len(models)} local model(s) in {models_dir_str}.", "info")

    def _handle_models_scan_failed(self, payload):
        message, models_dir_str = payload
        self._update_model_badge("Scan failed", "bad")
        self._set_model_status(f"Could not scan: {models_dir_str}", "error")
        self._show_error(
            "The models folder could not be scanned",
            f"{message}\n\nFolder: {models_dir_str}",
        )

    def _choose_models_directory(self):
        chosen = filedialog.askdirectory(
            title="Choose a folder that contains speech-recognition models",
            mustexist=True,
        )
        if not chosen:
            return

        chosen_path = Path(chosen)
        normalized_root, selected_model_hint = normalize_models_directory(chosen_path)
        self.models_dir = normalized_root

        if selected_model_hint is not None:
            # The selected folder is itself a model; scan its parent so sibling
            # models remain visible while keeping this model selected.
            self.preferred_model_key = str(selected_model_hint.resolve())
        elif self.preferred_model_key:
            # Keep the current selection only if it belongs to the newly chosen
            # root. Otherwise let discovery choose a usable model in that root.
            if not _path_is_within(Path(self.preferred_model_key), self.models_dir):
                self.preferred_model_key = None

        self._save_settings()
        self._refresh_models()

    def _on_model_selected(self, event=None, initial=False):
        display = self.model_var.get()
        info = self.models_by_display.get(display)
        if info is None:
            return
        self.preferred_model_key = info.key

        with self.adapter_lock:
            self.current_adapter = None
            self.current_info = info
            adapter = make_adapter(info)
            self.current_adapter = adapter

        if info.status == "ready":
            self._set_model_status(f"●  {info.display_name} — {info.family}", "success")
        elif info.status == "missing-deps":
            self._set_model_status(
                f"●  {info.display_name} — {info.diagnostic or 'missing dependency'}",
                "warning",
            )
        elif info.status == "incomplete":
            self._set_model_status(
                f"●  {info.display_name} — {info.diagnostic or 'incomplete'}",
                "error",
            )
        else:
            self._set_model_status(
                f"●  {info.display_name} — {info.diagnostic or info.status}",
                "info",
            )

        self._update_language_hint()

        if info.status == "ready" and adapter is not None:
            self._start_model_load(info)
        else:
            self.model_loading = False
            self._update_model_badge("Model unavailable", "bad")
            if info.diagnostic:
                self._show_error("This model cannot be used", info.diagnostic)

        if not initial:
            self._save_settings()

    def _start_model_load(self, info):
        self.model_loading = True
        self._update_model_badge("Loading model…", "wait")
        self.progress.start(12)
        self._show_status(f"Loading {info.display_name} in the background…", "working")
        threading.Thread(
            target=self._model_load_worker,
            args=(self.current_adapter, info.display_name),
            daemon=True,
        ).start()

    def _model_load_worker(self, adapter, display_name):
        try:
            adapter.load(lambda msg: self.events.put(("status", msg)))
            self.events.put(("model_ready", display_name))
        except Exception as exc:
            LOGGER.exception("Model %s failed to load", display_name)
            self.events.put(("model_failed", str(exc)))

    # ---- language -------------------------------------------------------
    def _set_language(self, code, save=True, initial=False):
        if code not in LANGUAGE_LABELS:
            return
        if not initial and code == self.language_code:
            return
        self.language_code = code
        for c, button in self.lang_buttons.items():
            try:
                button.configure(style="Accent.TButton" if c == code else "TButton")
            except tk.TclError:
                pass
        self._apply_text_direction()
        self._update_language_hint()
        if save:
            self._save_settings()

    def _update_language_hint(self):
        info = self.current_info
        if info is None:
            self.language_hint_var.set("")
            return
        if self.language_code in info.languages:
            label = LANGUAGE_LABELS[self.language_code]
            self.language_hint_var.set(f"Active language: {label}")
        else:
            self.language_hint_var.set(
                "This model is not known to support the selected language."
            )

    # ---- button states / window / settings ------------------------------
    def _update_button_states(self):
        is_recording = self.recording_stream is not None
        can_start = not self.busy and not is_recording
        state_start = "normal" if can_start else "disabled"
        state_stop = "normal" if is_recording else "disabled"
        for button in (self.transcribe_button, self.choose_button, self.record_button):
            try:
                button.configure(state=state_start)
            except tk.TclError:
                pass
        try:
            self.stop_record_button.configure(state=state_stop)
            resume_state = "normal" if (not self.busy and not is_recording) else "disabled"
            self.resume_record_button.configure(state=resume_state)
        except (tk.TclError, AttributeError):
            pass
        try:
            # The cancel button is useful only during model transcription.
            cancel_state = "normal" if self.busy and self.transcription_cancel_event else "disabled"
            self.cancel_transcription_button.configure(state=cancel_state)
        except (tk.TclError, AttributeError):
            pass

    def _set_busy(self, busy):
        self.busy = busy
        self._update_button_states()
        try:
            if busy:
                self.progress.start(12)
            else:
                self.progress.stop()
        except tk.TclError:
            pass

    def _setup_window_size(self):
        """Open at a compact size while allowing the user to resize the window."""
        self.state("normal")
        self.update_idletasks()

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        max_w = max(640, screen_w - SCREEN_MARGIN)
        max_h = max(480, screen_h - SCREEN_MARGIN)

        # Keep a practical minimum, but do not require the old, large layout.
        min_w = min(800, max_w)
        min_h = min(540, max_h)
        self.minsize(min_w, min_h)

        wanted_w, wanted_h = self.start_size
        width = max(min_w, min(wanted_w, DEFAULT_WINDOW_WIDTH, max_w))
        height = max(min_h, min(wanted_h, DEFAULT_WINDOW_HEIGHT, max_h))
        self.start_size = (width, height)

        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - 15)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _load_settings(self):
        try:
            settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(settings, dict):
            return
        version = settings.get("settings_version", 0)
        width = settings.get("width")
        height = settings.get("height")
        if version >= SETTINGS_VERSION and isinstance(width, int) and isinstance(height, int):
            self.start_size = (width, height)
        theme = settings.get("theme")
        if theme in THEMES:
            self.theme_name = theme
        lang = settings.get("language")
        if lang in LANGUAGE_LABELS:
            self.language_code = lang
        models_dir = settings.get("models_dir")
        selected_hint: Optional[Path] = None
        if isinstance(models_dir, str) and models_dir.strip():
            candidate = Path(models_dir).expanduser()
            if candidate.is_dir():
                normalized_root, selected_hint = normalize_models_directory(candidate)
                self.models_dir = normalized_root
                if normalized_root != candidate.resolve():
                    self._settings_migrated = True
                    LOGGER.warning(
                        "Migrated models_dir from an individual model folder to its parent: %s -> %s",
                        candidate, normalized_root,
                    )
            else:
                LOGGER.warning("Saved models_dir does not exist: %s; using default: %s",
                               candidate, DEFAULT_MODELS_DIR)
                if DEFAULT_MODELS_DIR.is_dir():
                    self.models_dir = DEFAULT_MODELS_DIR.resolve()
                    self._settings_migrated = True

        key = settings.get("selected_model_key")
        if isinstance(key, str) and key:
            self.preferred_model_key = key
        elif selected_hint is not None:
            self.preferred_model_key = str(selected_hint.resolve())

    def _save_settings(self):
        width, height = self.start_size
        if self.state() == "normal":
            try:
                width = self.winfo_width()
                height = self.winfo_height()
            except tk.TclError:
                pass
        settings = {
            "settings_version": SETTINGS_VERSION,
            "width": width, "height": height,
            "theme": self.theme_name, "language": self.language_code,
            "models_dir": str(self.models_dir),
            "selected_model_key": self.preferred_model_key,
        }
        try:
            DATA_DIR.mkdir(exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        except OSError:
            pass

    # ---- file / transcribe ----------------------------------------------
    def _choose_file(self):
        filetypes = [("Audio files",
                      "*.wav *.mp3 *.mpeg *.flac *.ogg *.m4a *.mp4 *.webm"),
                     ("All files", "*.*")]
        chosen = filedialog.askopenfilename(title="Choose an audio file",
                                            filetypes=filetypes)
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            self._show_error("This file cannot be used",
                             make_friendly_error_message("Unsupported audio extension"))
            return
        try:
            size = path.stat().st_size
        except OSError:
            LOGGER.exception("Could not read the selected file")
            self._show_error("This file cannot be opened",
                             "The audio file could not be opened. Please choose it again.")
            return
        self.current_file = path
        self.file_label_var.set(f"{path.name}  ·  {size:,} bytes")
        self._hide_error()
        self._show_status("Audio file selected. Click Transcribe file.", "info")

    def _start_file_transcription(self):
        if self.busy:
            return
        if self.current_file is None:
            self._show_status("Please choose an audio file first.", "warning")
            return
        if self.current_adapter is None or self.current_info is None:
            self._show_error("No model selected",
                             "Please select a recognition model first.")
            return
        if self.current_info.status != "ready":
            self._show_error("This model cannot be used",
                             self.current_info.diagnostic or
                             "The selected model is not ready to use.")
            return
        self._hide_error()
        self._set_busy(True)
        if self.model_loading:
            self._show_status("Waiting for the model to finish loading…", "working")
        else:
            self._show_status("Preparing transcription…", "working")
        self.transcription_cancel_event = threading.Event()
        cancel_event = self.transcription_cancel_event
        self._update_button_states()
        threading.Thread(
            target=self._transcribe_worker,
            args=(self.current_adapter, self.current_file, self.language_code, cancel_event),
            daemon=True,
        ).start()

    def _cancel_transcription(self):
        """Ask the current transcription to stop and discard its result."""
        cancel_event = self.transcription_cancel_event
        if not self.busy or cancel_event is None:
            return
        cancel_event.set()
        self._show_status(
            "Stopping… the model will stop at the next safe point. No result will be saved.",
            "warning",
        )
        self.cancel_transcription_button.configure(state="disabled")

    def _transcribe_worker(self, adapter, audio_path, language, cancel_event):
        try:
            text = adapter.transcribe(
                audio_path,
                language,
                lambda msg: self.events.put(("status", msg)),
                cancel_event=cancel_event,
            )
            if cancel_event.is_set():
                self.events.put(("cancelled", None))
            else:
                self.events.put(("result", (text, audio_path.name)))
        except Exception as exc:
            if cancel_event.is_set():
                self.events.put(("cancelled", None))
            else:
                LOGGER.exception("Transcription failed for %s", audio_path)
                self.events.put(("error", str(exc)))

    # ---- recording ------------------------------------------------------
    def _start_recording(self, resume=False):
        if self.busy or self.recording_stream is not None:
            return
        self.resume_mode = resume
        if not resume:
            self.result_text.delete("1.0", "end")
            self._update_empty_state()
        if self.current_adapter is None or self.current_info is None:
            self._show_error("No model selected",
                             "Please select a recognition model before recording.")
            return
        if self.current_info.status != "ready":
            self._show_error("This model cannot be used",
                             self.current_info.diagnostic or
                             "The selected model is not ready.")
            return
        try:
            import sounddevice as sd
            import numpy as np  # noqa: F401
        except Exception:
            LOGGER.exception("The microphone packages could not be imported")
            self._show_error(
                "The microphone is not available",
                "Recording needs the sounddevice package in the existing .venv. "
                "Nothing was installed automatically.",
            )
            return
        try:
            with self.recording_lock:
                self.recording_chunks = []
            def callback(indata, frames, time_info, status):
                if status:
                    self.events.put(("status", f"Microphone status: {status}"))
                if not self.microphone_muted:
                    with self.recording_lock:
                        self.recording_chunks.append(indata.copy())
            self.recording_stream = sd.InputStream(
                samplerate=16000, channels=1, dtype="float32", callback=callback
            )
            self.recording_stream.start()
        except Exception:
            LOGGER.exception("The microphone could not be started")
            self.recording_stream = None
            self._show_error("The microphone could not be started",
                             "Check that a microphone is connected and allowed to be used.")
            return
        self._hide_error()
        self.recording_started_at = time.time()
        self.recording_timer_var.set("00:00")
        self._update_button_states()
        self._update_recording_timer()

    def _resume_recording(self):
        self._start_recording(resume=True)

    def _toggle_microphone_mute(self):
        self.microphone_muted = not self.microphone_muted
        self.mute_button.configure(
            text="🔊  Unmute mic" if self.microphone_muted else "🔇  Mute mic"
        )
        self._show_status(
            "Microphone muted. Audio is not being captured." if self.microphone_muted
            else "Microphone unmuted.",
            "warning" if self.microphone_muted else "success",
        )

    def _update_recording_timer(self):
        if self.recording_stream is None:
            self.recording_timer_var.set("00:00")
            self._draw_recording_waveform(active=False)
            return
        elapsed = int(time.time() - self.recording_started_at)
        minutes, seconds = divmod(elapsed, 60)
        self.recording_timer_var.set(f"{minutes:02d}:{seconds:02d}")
        self._show_status(
            f"Recording… {minutes:02d}:{seconds:02d}   Speak, then click Stop and transcribe.",
            "warning",
        )
        self._draw_recording_waveform(active=True)
        self.after(150, self._update_recording_timer)

    def _draw_recording_waveform(self, active=None):
        """Draw a compact, restrained waveform while recording is active."""
        canvas = getattr(self, "waveform_canvas", None)
        if canvas is None:
            return
        try:
            canvas.delete("wave")
            width = max(80, canvas.winfo_width())
            height = max(28, canvas.winfo_height())
            if active is None:
                active = self.recording_stream is not None

            # Fewer, shorter bars keep the recording indicator from dominating the UI.
            bar_count = max(12, min(30, width // 9))
            gap = 4
            bar_width = max(2, min(4, (width - (bar_count - 1) * gap) // bar_count))
            total_width = bar_count * bar_width + (bar_count - 1) * gap
            start_x = max(2, (width - total_width) // 2)
            center = height // 2
            accent = self.theme.get("accent", "#2dd4bf")
            muted = self.theme.get("border", "#526078")
            phase = self._waveform_phase

            for i in range(bar_count):
                if active:
                    import math
                    envelope = 0.25 + 0.75 * abs(math.sin(i * 0.48 + phase))
                    envelope *= 0.55 + 0.45 * abs(math.sin(i * 0.21 - phase * 0.65))
                    bar_height = max(4, int((height - 12) * envelope))
                else:
                    bar_height = 3 + (i % 2)
                x = start_x + i * (bar_width + gap)
                canvas.create_line(
                    x, center - bar_height // 2, x, center + bar_height // 2,
                    fill=accent if active else muted,
                    width=bar_width, capstyle=tk.ROUND, tags="wave",
                )
            if active:
                self._waveform_phase += 0.18
        except tk.TclError:
            pass

    def _save_recording_as_wav(self, chunks):
        import numpy as np
        audio = np.concatenate(chunks, axis=0).reshape(-1)
        clipped = np.clip(audio, -1.0, 1.0)
        pcm = (clipped * 32767).astype(np.int16)
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        temp_path = TEMP_DIR / f"recording_{int(time.time())}.wav"
        with wave.open(str(temp_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(pcm.tobytes())
        return temp_path

    def _delete_temporary_recordings(self, keep_path=None):
        if not TEMP_DIR.is_dir():
            return
        for recording in TEMP_DIR.glob("recording_*.wav"):
            if keep_path is not None and recording == keep_path:
                continue
            try:
                recording.unlink()
            except OSError:
                LOGGER.warning("Could not delete the temporary recording: %s", recording)

    def _stop_recording(self):
        stream = self.recording_stream
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            LOGGER.exception("Stopping the microphone failed")
            self._show_error("Recording problem",
                             "The microphone could not be stopped cleanly.")
        finally:
            self.recording_stream = None
            self._update_button_states()
        with self.recording_lock:
            chunks = self.recording_chunks
            self.recording_chunks = []
        if not chunks:
            self._show_status("No microphone audio was captured.", "warning")
            return
        try:
            temp_path = self._save_recording_as_wav(chunks)
        except Exception:
            LOGGER.exception("The recording could not be saved")
            self._show_error("The recording could not be saved",
                             "Please check that there is free disk space and try again.")
            return
        self._delete_temporary_recordings(keep_path=temp_path)
        self.current_file = temp_path
        self.file_label_var.set(f"Microphone recording  ·  {temp_path.name}")
        if self.current_adapter is None or self.current_info is None:
            self._show_error("No model selected", "Please select a model first.")
            return
        self._hide_error()
        self._set_busy(True)
        self._show_status("Preparing transcription…", "working")
        self.transcription_cancel_event = threading.Event()
        cancel_event = self.transcription_cancel_event
        self._update_button_states()
        threading.Thread(
            target=self._transcribe_worker,
            args=(self.current_adapter, temp_path, self.language_code, cancel_event),
            daemon=True,
        ).start()

    # ---- events ---------------------------------------------------------
    def _poll_events(self):
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(kind, payload)
            except Exception:
                LOGGER.exception("Could not handle event: %s", kind)
        self.after(100, self._poll_events)

    def _handle_event(self, kind, payload):
        if kind == "status":
            self._show_status(payload, "working")
        elif kind == "result":
            if self.transcription_cancel_event is not None and \
                    self.transcription_cancel_event.is_set():
                self._handle_event("cancelled", None)
            else:
                self._handle_result(payload)
        elif kind == "error":
            self._handle_error(payload)
        elif kind == "cancelled":
            self.transcription_cancel_event = None
            self._set_busy(False)
            self._show_status("Transcription stopped. No result was saved to history.", "warning")
        elif kind == "model_ready":
            self._handle_model_ready(payload)
        elif kind == "model_failed":
            self._handle_model_failed(payload)
        elif kind == "models_scanned":
            self._handle_models_scanned(payload)
        elif kind == "models_scan_failed":
            self._handle_models_scan_failed(payload)

    def _handle_result(self, payload):
        text, filename = payload
        if self.resume_mode:
            existing_text = self.result_text.get("1.0", "end-1c").rstrip()
            self.result_text.insert("end", (" " if existing_text and text else "") + text)
        else:
            self.result_text.delete("1.0", "end")
            self.result_text.insert("1.0", text)
        self.resume_mode = False
        self._apply_text_direction()
        self._update_empty_state()
        full_text = self.result_text.get("1.0", "end-1c")
        model_name = self.current_info.display_name if self.current_info else LEGACY_QWEN_MODEL_NAME
        self._save_history(filename, model_name, full_text)
        self.transcription_cancel_event = None
        self._set_busy(False)
        self._update_timing_text()
        if text.strip():
            took = format_seconds(self.current_adapter.transcribe_seconds) if self.current_adapter else ""
            message = f"Finished: {filename}" + (f" in {took}" if took else "") + \
                      ". You can edit, copy, or save the transcript."
            self._show_status(message, "success")
        else:
            self._show_status(f"No speech was recognized in {filename}.", "warning")

    def _handle_error(self, technical_message):
        LOGGER.error("Transcription error: %s", technical_message)
        self.resume_mode = False
        self.transcription_cancel_event = None
        self._set_busy(False)
        self._show_error("Transcription failed",
                         make_friendly_error_message(technical_message))

    def _handle_model_ready(self, display_name):
        self.model_loading = False
        self._update_model_badge(f"{display_name} ready", "ready")
        self._update_timing_text()
        if not self.busy:
            self.progress.stop()
            load_time = format_seconds(self.current_adapter.load_seconds) \
                if self.current_adapter else ""
            suffix = f" (loaded in {load_time})" if load_time else ""
            self._show_status(f"{display_name} is ready{suffix}.", "success")

    def _handle_model_failed(self, technical_message):
        self.model_loading = False
        self._update_model_badge("Model failed to load", "bad")
        if not self.busy:
            self.progress.stop()
            self._show_error("The model could not be loaded",
                             make_friendly_error_message(technical_message))

    def _save_history(self, filename, model_name, transcript):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "INSERT INTO history(created_at, filename, model_name, transcript) "
                    "VALUES (?, ?, ?, ?)",
                    (datetime.now().isoformat(timespec="seconds"),
                     filename, model_name, transcript),
                )
        except sqlite3.Error as exc:
            LOGGER.exception("The history could not be saved")
            self._show_status(f"Transcription finished but history failed: {exc}", "warning")

    # ---- text actions ---------------------------------------------------
    def _copy_text(self):
        text = self.result_text.get("1.0", "end-1c")
        if not text.strip():
            self._show_status("There is no transcript to copy yet.", "warning")
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self._show_status("Transcript copied to clipboard.", "success")

    def _save_text(self):
        text = self.result_text.get("1.0", "end-1c")
        if not text.strip():
            self._show_status("There is no transcript to save yet.", "warning")
            return
        path = filedialog.asksaveasfilename(
            title="Save transcript", defaultextension=".txt",
            filetypes=[("Text file", "*.txt")], initialfile="transcript.txt",
        )
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
            self._show_status(f"Saved transcript to {path}", "success")
        except OSError as exc:
            LOGGER.exception("Saving the transcript failed")
            self._show_error("The file could not be saved",
                             f"Please choose another folder. ({exc.strerror})")

    def _clear_text(self):
        if self.result_text.get("1.0", "end-1c").strip() and \
                not messagebox.askyesno("Clear transcript",
                                       "Clear the current transcript?"):
            return
        self.result_text.delete("1.0", "end")
        self._update_empty_state()
        self._show_status("Transcript cleared.", "info")

    # ---- history --------------------------------------------------------
    def _load_history_rows(self):
        query = ("SELECT id, created_at, filename, model_name, transcript "
                 "FROM history ORDER BY id DESC LIMIT ?")
        try:
            with sqlite3.connect(DB_PATH) as conn:
                return conn.execute(query, (HISTORY_LIMIT,)).fetchall()
        except sqlite3.Error:
            LOGGER.exception("The history could not be read")
            return []

    def _open_history_window(self):
        if self.history_window is not None and self.history_window.winfo_exists():
            self.history_window.lift()
            self.history_window.focus_set()
            return
        window = tk.Toplevel(self)
        window.title("History · Voice Typing Offline")
        window.geometry("860x560")
        window.minsize(640, 440)
        window.transient(self)
        self._themed(window, bg="bg")
        self.history_window = window
        outer = ttk.Frame(window, padding=20)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Saved transcriptions",
                  style="Title.TLabel", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Select a row to preview it, then use it in the editor.",
                  style="Sub.TLabel").pack(anchor="w", pady=(2, 12))
        list_frame = ttk.Frame(outer)
        list_frame.pack(fill="x")
        self.history_list = ttk.Treeview(
            list_frame, columns=("time", "file", "model"),
            show="headings", height=7, selectmode="browse",
        )
        self.history_list.heading("time", text="Date and time")
        self.history_list.heading("file", text="Audio file")
        self.history_list.heading("model", text="Model")
        self.history_list.column("time", width=170, stretch=False)
        self.history_list.column("file", width=380)
        self.history_list.column("model", width=180, stretch=False)
        list_scroll = ttk.Scrollbar(list_frame, command=self.history_list.yview)
        self.history_list.configure(yscrollcommand=list_scroll.set)
        self.history_list.pack(side="left", fill="x", expand=True)
        list_scroll.pack(side="right", fill="y")
        button_row = ttk.Frame(outer)
        button_row.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(button_row, text="Use this transcript",
                   style="Accent.TButton",
                   command=self._use_selected_history).pack(side="left")
        ttk.Button(button_row, text="Delete selected",
                   style="Danger.TButton",
                   command=self._delete_selected_history).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Delete all",
                   style="Danger.TButton",
                   command=self._delete_all_history).pack(side="left", padx=(8, 0))
        ttk.Button(button_row, text="Close",
                   command=window.destroy).pack(side="right")
        preview_frame = tk.Frame(outer)
        self._themed(preview_frame, bg="input", highlightbackground="input_border")
        preview_frame.configure(highlightthickness=1)
        preview_frame.pack(fill="both", expand=True, pady=(12, 0))
        self.history_preview = tk.Text(preview_frame, wrap="word", font=FONT_NORMAL,
                                       relief="flat", bd=0, padx=12, pady=10,
                                       highlightthickness=0, state="disabled")
        self._themed(self.history_preview, bg="input", fg="text",
                     selectbackground="selection")
        preview_scroll = ttk.Scrollbar(preview_frame, command=self.history_preview.yview)
        self.history_preview.configure(yscrollcommand=preview_scroll.set)
        self.history_preview.pack(side="left", fill="both", expand=True)
        preview_scroll.pack(side="right", fill="y")
        self._fill_history_list()
        self.history_list.bind("<<TreeviewSelect>>", self._on_history_select)

    def _fill_history_list(self):
        # Clear old visible rows before loading the latest database records.
        for item_id in self.history_list.get_children():
            self.history_list.delete(item_id)
        self.history_transcripts = {}
        rows = self._load_history_rows()
        for row_id, created_at, filename, model_name, transcript in rows:
            item_id = str(row_id)
            self.history_transcripts[item_id] = transcript
            readable = created_at.replace("T", "  ")
            self.history_list.insert("", "end", iid=item_id,
                                     values=(readable, filename, model_name or ""))
        if rows:
            self.history_list.selection_set(str(rows[0][0]))
        else:
            self._set_history_preview("No saved transcriptions yet.")

    def _set_history_preview(self, text):
        self.history_preview.configure(state="normal")
        self.history_preview.delete("1.0", "end")
        self.history_preview.insert("1.0", text)
        self.history_preview.configure(state="disabled")

    def _on_history_select(self, event=None):
        selection = self.history_list.selection()
        if not selection:
            return
        self._set_history_preview(self.history_transcripts.get(selection[0], ""))

    def _delete_selected_history(self):
        """Delete the selected history item after asking for confirmation."""
        selection = self.history_list.selection()
        if not selection:
            messagebox.showinfo("History", "Select a history item first.", parent=self.history_window)
            return
        row_id = int(selection[0])
        if not messagebox.askyesno(
            "Delete history item",
            "Delete this saved transcription? This cannot be undone.",
            parent=self.history_window,
        ):
            return
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM history WHERE id = ?", (row_id,))
            self._fill_history_list()
            self._set_history_preview("Select a saved transcription to preview it.")
            self._show_status("History item deleted.", "success")
        except sqlite3.Error as exc:
            LOGGER.exception("Could not delete history item")
            messagebox.showerror("History", f"Could not delete this item.\n{exc}", parent=self.history_window)

    def _delete_all_history(self):
        """Delete all saved history items after a confirmation prompt."""
        if not self._load_history_rows():
            messagebox.showinfo("History", "There is no history to delete.", parent=self.history_window)
            return
        if not messagebox.askyesno(
            "Delete all history",
            "Delete every saved transcription? This cannot be undone.",
            parent=self.history_window,
        ):
            return
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("DELETE FROM history")
            self._fill_history_list()
            self._set_history_preview("No saved transcriptions yet.")
            self._show_status("All history was deleted.", "success")
        except sqlite3.Error as exc:
            LOGGER.exception("Could not delete history")
            messagebox.showerror("History", f"Could not delete history.\n{exc}", parent=self.history_window)

    def _use_selected_history(self):
        selection = self.history_list.selection()
        if not selection:
            return
        transcript = self.history_transcripts.get(selection[0], "")
        self.result_text.delete("1.0", "end")
        self.result_text.insert("1.0", transcript)
        self._apply_text_direction()
        self._update_empty_state()
        self._show_status("A saved transcript was placed in the editor.", "success")
        self.history_window.destroy()

    # ---- close ----------------------------------------------------------
    def _on_close(self):
        if self.busy:
            if not messagebox.askyesno(
                "Transcription is running",
                "A transcription is still running. If you close now, it "
                "will be stopped.\n\nClose the app anyway?",
            ):
                return
        stream = self.recording_stream
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self._delete_temporary_recordings()
        self._save_settings()
        self.destroy()


# ---------------------------------------------------------------------------
# 6. APPLICATION STARTUP
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        app = VoiceTypingApp()
        app.mainloop()
    except Exception as exc:
        LOGGER.exception("The app could not start")
        try:
            messagebox.showerror("Voice Typing Offline", str(exc))
        except Exception:
            print(f"Voice Typing Offline failed to start: {exc}")
        raise