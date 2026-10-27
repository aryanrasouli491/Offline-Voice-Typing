from __future__ import annotations
import json, os, secrets, threading, time, logging
from pathlib import Path
from typing import Optional
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "backend" / "local_config.json"
MODEL_ROOT = ROOT / "models"
SAMPLE_RATE = 16000
CHUNK_SECONDS = 5
OVERLAP_SECONDS = 0.8
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voice-typing")

def load_config():
    if not CONFIG_PATH.exists():
        return {"token": "", "allowed_origins": []}
    try: return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception: return {"token": "", "allowed_origins": []}
config = load_config()
TOKEN = config.get("token", "")
ALLOWED_ORIGINS = set(config.get("allowed_origins", []))
app = FastAPI(title="Voice Typing Local Engine", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=list(ALLOWED_ORIGINS), allow_credentials=False,
                   allow_methods=["GET","POST","OPTIONS"], allow_headers=["Content-Type","X-Local-Token"])
lock = threading.RLock()
state = {"state":"stopped","language":"fa","model":"small","device":"cpu","compute_type":"int8",
         "final_text":"","partial_text":"","error":None,"model_available":False}
model: Optional[WhisperModel] = None
audio_stream = None
audio_queue: list[np.ndarray] = []
worker: Optional[threading.Thread] = None
stop_event = threading.Event()
recording_started = 0.0

class StartRequest(BaseModel):
    language: str = Field(pattern="^(fa|en)$")
    model: str = Field(pattern="^(base|small|medium)$")
    mode: str = Field(default="append", pattern="^(append|replace)$")
    punctuation: bool = True

def require_auth(x_local_token: str = Header(default="")):
    if not TOKEN or not secrets.compare_digest(x_local_token, TOKEN):
        raise HTTPException(status_code=401, detail="Invalid local access token. Check Connection & settings.")
def model_path(name: str) -> Path:
    return MODEL_ROOT / name

def load_local_model(name: str):
    global model
    path = model_path(name)
    if not path.is_dir() or not any(path.iterdir()):
        raise FileNotFoundError(f"Model '{name}' is not installed. Run setup_windows.bat while online.")
    # Path-only loading prevents a remote model name from triggering downloads.
    candidate = WhisperModel(str(path), device="cpu", compute_type="int8", local_files_only=True)
    with lock:
        model = candidate
        state["model"] = name
        state["loaded_model_name"] = name
        state["device"] = "cpu (INT8)"
        state["model_available"] = True

def audio_callback(indata, frames, time_info, status):
    if status: log.warning("Audio input status: %s", status)
    with lock:
        if state["state"] in ("listening","processing"):
            audio_queue.append(indata[:,0].copy())

def transcribe_worker():
    global model
    samples: list[np.ndarray] = []
    processed = 0
    while not stop_event.is_set() or audio_queue:
        block = None
        with lock:
            if audio_queue: block = audio_queue.pop(0)
        if block is not None: samples.append(block)
        total = sum(len(x) for x in samples)
        # Wait for enough *new* audio; samples already seen remain in `samples`.
        # Without this delta check the worker would spin on the same old chunk.
        if (total - processed >= CHUNK_SECONDS*SAMPLE_RATE) or (stop_event.is_set() and total > processed):
            audio = np.concatenate(samples).astype(np.float32)
            end = len(audio) if stop_event.is_set() else max(processed, len(audio)-int(OVERLAP_SECONDS*SAMPLE_RATE))
            segment_audio = audio[processed:end]
            processed = end
            if len(segment_audio) >= int(0.5*SAMPLE_RATE):
                with lock: state["state"]="processing"
                try:
                    with lock: active_model=model; lang=state["language"]
                    if active_model is None: raise RuntimeError("The local model is not loaded.")
                    segments, _ = active_model.transcribe(segment_audio, language=lang, task="transcribe",
                        beam_size=3, vad_filter=True, condition_on_previous_text=False)
                    result = "".join(s.text for s in segments).strip()
                    if result:
                        with lock:
                            if state["final_text"] and not state["final_text"].endswith((" ","\n")):
                                state["final_text"] += " "
                            state["final_text"] += result
                    with lock: state["partial_text"]=""
                except Exception as e:
                    log.exception("Transcription failed")
                    with lock: state["error"]=str(e)
                with lock:
                    if not stop_event.is_set(): state["state"]="listening"
            # Retain a small overlap for the next chunk; processed points at the next unseen sample.
        else:
            time.sleep(0.04)
    with lock: state["state"]="stopped"; state["partial_text"]=""

@app.get("/status")
def status(x_local_token: str = Header(default="")):
    require_auth(x_local_token)
    selected = state["model"]
    available = model_path(selected).is_dir() and any(model_path(selected).iterdir())
    return {"state":state["state"],"model":selected,"model_available":available,
            "device":state["device"],"error":state["error"],"offline":True}

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
        if state["state"] in ("listening","processing"):
            raise HTTPException(409, "Recording is already active.")
        loaded_model_name = state.get("loaded_model_name")
        state.update({"state":"stopped","language":req.language,"error":None,"partial_text":""})
        if req.mode == "replace": state["final_text"]=""
        if loaded_model_name != req.model:
            state["model_available"]=False
        state["model"]=req.model
    try:
        if model is None or loaded_model_name != req.model or not state["model_available"]:
            load_local_model(req.model)
        with lock:
            audio_queue=[]; stop_event=threading.Event()
            audio_stream=sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                        blocksize=1024, callback=audio_callback)
            audio_stream.start()
            state["state"]="listening"
            worker=threading.Thread(target=transcribe_worker, daemon=True)
            worker.start()
        return {"ok":True,"state":"listening","mode":req.mode}
    except FileNotFoundError as e:
        with lock: state["state"]="stopped"; state["model_available"]=False; state["error"]=str(e)
        raise HTTPException(400, detail=str(e))
    except Exception as e:
        with lock: state["state"]="stopped"; state["error"]=str(e)
        log.exception("Unable to start recording")
        raise HTTPException(500, detail=f"Could not start recording: {e}")

@app.post("/stop")
def stop(x_local_token: str = Header(default="")):
    global audio_stream
    require_auth(x_local_token)
    with lock:
        if state["state"] not in ("listening","processing"):
            return {"ok":True,"state":"stopped"}
        stop_event.set()
        stream=audio_stream
        audio_stream=None
    try:
        if stream:
            stream.stop(); stream.close()
    except Exception: log.exception("Error closing audio stream")
    if worker and worker.is_alive(): worker.join(timeout=30)
    with lock: state["state"]="stopped"
    return {"ok":True,"state":"stopped"}

@app.get("/health")
def health():
    # Deliberately returns no private status or token; useful for local diagnostics.
    return {"ok":True,"service":"Voice Typing Local Engine"}

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing backend/local_config.json. Run setup_windows.bat first.")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
