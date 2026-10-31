# Copyright (c) 2026 Nooraqa Rasouli
# SPDX-License-Identifier: MIT
"""Optional authenticated loopback API with bounded audio and serialized sessions."""
from __future__ import annotations
import json
import secrets
import threading
import time
import logging
from collections import deque
from pathlib import Path
from typing import Optional
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).with_name("local_config.json")
MODEL_ROOT = ROOT / "runtime" / "models"
SAMPLE_RATE = 16000
CHUNK_SECONDS = 5
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voice-typing")

def load_config():
    if not CONFIG_PATH.exists():
        return {"token": "", "allowed_origins": []}
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"token": "", "allowed_origins": []}
    except (OSError, ValueError):
        return {"token": "", "allowed_origins": []}


config = load_config()
TOKEN = config.get("token", "") if isinstance(config.get("token", ""), str) else ""
origins = config.get("allowed_origins", [])
ALLOWED_ORIGINS = [x for x in origins if isinstance(x, str)] if isinstance(origins, list) else []
app = FastAPI(title="Voice Typing Local Engine", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=list(ALLOWED_ORIGINS), allow_credentials=False,
                   allow_methods=["GET","POST","OPTIONS"], allow_headers=["Content-Type","X-Local-Token"])
lock = threading.RLock()
state = {
    "state": "stopped", "language": "fa", "model": "small",
    "device": "cpu", "compute_type": "int8", "final_text": "",
    "partial_text": "", "error": None, "model_available": False,
}
model: Optional[WhisperModel] = None
audio_stream = None
audio_queue: deque[np.ndarray] = deque()
MAX_QUEUED_BLOCKS = 256  # About 16 seconds; prevent unlimited RAM growth on a slow CPU.
worker: Optional[threading.Thread] = None
stop_event = threading.Event()

class StartRequest(BaseModel):
    language: str = Field(pattern="^(fa|en)$")
    model: str = Field(pattern="^(base|small|medium)$")
    mode: str = Field(default="append", pattern="^(append|replace)$")
    punctuation: bool = True

def require_auth(x_local_token: str = Header(default="")):
    if not TOKEN or not secrets.compare_digest(x_local_token.encode("utf-8"), TOKEN.encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid local access token. Check Connection & settings.")
def model_path(name: str) -> Path:
    return MODEL_ROOT / name

def load_local_model(name: str):
    global model
    path = model_path(name)
    if not path.is_dir() or not any(path.iterdir()):
        raise FileNotFoundError(f"Model '{name}' is not installed. Run 3 - Get Model and Start.cmd while online.")
    # Path-only loading prevents a remote model name from triggering downloads.
    candidate = WhisperModel(str(path), device="cpu", compute_type="int8", local_files_only=True)
    with lock:
        model = candidate
        state["model"] = name
        state["loaded_model_name"] = name
        state["device"] = "cpu (INT8)"
        state["model_available"] = True

def audio_callback(indata, frames, time_info, status):
    if status:
        log.warning("Audio input status: %s", status)
    with lock:
        if state["state"] in ("listening","processing"):
            if len(audio_queue) >= MAX_QUEUED_BLOCKS:
                state["error"] = "Audio processing fell behind. Use a smaller model or shorter recordings."
                stop_event.set()
            elif not stop_event.is_set():
                audio_queue.append(indata[:,0].copy())

def transcribe_worker():
    global audio_stream
    samples: list[np.ndarray] = []
    total = 0
    try:
        while True:
            with lock:
                block = audio_queue.popleft() if audio_queue else None
                drained = not audio_queue
            if block is not None:
                samples.append(block)
                total += len(block)
            final = stop_event.is_set() and drained
            if total >= CHUNK_SECONDS*SAMPLE_RATE or (final and total):
                # Release processed audio instead of retaining the entire session.
                audio = np.concatenate(samples).astype(np.float32)
                samples, total = [], 0
                if len(audio) >= int(.25*SAMPLE_RATE):
                    with lock:
                        active_model, lang = model, state["language"]
                        if not stop_event.is_set():
                            state["state"] = "processing"
                    if active_model is None:
                        raise RuntimeError("The local model is not loaded.")
                    segments, _ = active_model.transcribe(audio, language=lang, task="transcribe",
                        beam_size=3, vad_filter=True, condition_on_previous_text=False)
                    result = " ".join(s.text.strip() for s in segments).strip()
                    with lock:
                        if result:
                            state["final_text"] = (state["final_text"] + " " + result).strip()
                        if not stop_event.is_set():
                            state["state"] = "listening"
            if final:
                break
            if block is None:
                time.sleep(.02)
    except Exception as exc:
        log.exception("Transcription failed")
        with lock:
            state["error"] = str(exc)
        stop_event.set()
    finally:
        with lock:
            stream, audio_stream = audio_stream, None
        if stream:
            try:
                try:
                    stream.stop()
                finally:
                    stream.close()
            except Exception:
                log.exception("Error closing audio stream")
        with lock:
            audio_queue.clear()
            state["state"] = "stopped"
            state["partial_text"] = ""

@app.get("/status")
def status(x_local_token: str = Header(default="")):
    require_auth(x_local_token)
    with lock:
        snapshot = {key: state[key] for key in ("state", "model", "device", "error")}
    folder = model_path(snapshot["model"])
    available = all((folder / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json"))
    return {**snapshot, "model_available": available, "offline": True}

@app.get("/transcript")
def transcript(x_local_token: str = Header(default="")):
    require_auth(x_local_token)
    with lock:
        return {k:state[k] for k in ("state","final_text","partial_text","error")}

@app.post("/start")
def start(req: StartRequest, x_local_token: str = Header(default="")):
    global audio_stream, worker, model, stop_event, audio_queue
    require_auth(x_local_token)
    with lock:
        if state["state"] != "stopped" or (worker and worker.is_alive()):
            raise HTTPException(409, "Recording is already active.")
        loaded_model_name = state.get("loaded_model_name")
        stop_event = threading.Event()
        state.update({"state":"loading","language":req.language,"error":None,"partial_text":""})
        if req.mode == "replace":
            state["final_text"] = ""
        if loaded_model_name != req.model:
            state["model_available"]=False
        state["model"]=req.model
    try:
        if model is None or loaded_model_name != req.model or not state["model_available"]:
            load_local_model(req.model)
        with lock:
            if stop_event.is_set():
                state["state"] = "stopped"
                return {"ok":True,"state":"stopped","mode":req.mode}
            audio_queue.clear()
            audio_stream=sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                        blocksize=1024, callback=audio_callback)
            audio_stream.start()
            state["state"]="listening"
            worker=threading.Thread(target=transcribe_worker, daemon=True)
            worker.start()
        return {"ok":True,"state":"listening","mode":req.mode}
    except FileNotFoundError as e:
        with lock:
            state.update(state="stopped", model_available=False, error=str(e))
        raise HTTPException(400, detail=str(e)) from e
    except Exception as e:
        stream, audio_stream = audio_stream, None
        if stream:
            try:
                stream.close()
            except Exception:
                log.exception("Microphone cleanup failed")
        with lock:
            state.update(state="stopped", error=str(e))
        log.exception("Unable to start recording")
        raise HTTPException(500, detail=f"Could not start recording: {e}")

@app.post("/stop")
def stop(x_local_token: str = Header(default="")):
    global audio_stream
    require_auth(x_local_token)
    with lock:
        if state["state"] == "stopped":
            return {"ok":True,"state":"stopped"}
        stop_event.set()
        state["state"] = "stopping"
        stream=audio_stream
        audio_stream=None
    try:
        if stream:
            try:
                stream.stop()
            finally:
                stream.close()
    except Exception:
        log.exception("Error closing audio stream")
    if worker and worker.is_alive():
        worker.join(timeout=1)
    # Keep reporting 'stopping' until the worker has actually drained its audio.
    with lock:
        return {"ok":True,"state":state["state"]}

@app.get("/health")
def health():
    # Deliberately returns no private status or token; useful for local diagnostics.
    return {"ok":True,"service":"Voice Typing Local Engine"}

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing app/backend/local_config.json. Run setup/prepare_backend_config.py first.")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
