# pc_hub/app.py
import os, time, uuid
import asyncio
from fastapi import FastAPI, UploadFile, File, Query, Form
from fastapi.responses import ORJSONResponse, FileResponse

from ultralytics import YOLO

from .config import Config
from .ringbuffer import RollingStore
from .workers import BackgroundAI, WhisperRunner, Escalator

cfg = Config()
store = RollingStore(cfg.frames_dir, cfg.audio_dir, keep_seconds=cfg.keep_minutes * 60)
store.ensure_dirs()

# Ensure other dirs exist too
for d in [cfg.hires_dir, cfg.frames_json_dir, cfg.frames_annotated_dir, cfg.state_dir, cfg.events_dir, cfg.incidents_dir]:
    os.makedirs(d, exist_ok=True)

app = FastAPI(default_response_class=ORJSONResponse)

bg: BackgroundAI | None = None

def _ts_name(prefix: str, ext: str) -> str:
    ms = int(time.time() * 1000)
    return f"{prefix}_{ms}_{uuid.uuid4().hex}.{ext}"

@app.on_event("startup")
async def _startup():
    global bg

    # ---- YOLO weights ----
    yolo_path = os.path.expanduser("~/pc_hub/yolov8n.pt")
    yolo = YOLO(yolo_path)

    # ---- Whisper.cpp ----
    whisper_bin = os.path.expanduser("~/pc_hub/whisper.cpp/build/bin/whisper-cli")
    whisper_model = os.path.expanduser("~/pc_hub/models/whisper/ggml-base.en-q8_0.bin")
    whisper = WhisperRunner(whisper_bin, whisper_model)

    # ---- llama.cpp ----
    llama_cli = os.path.expanduser("~/pc_hub/llama.cpp/build/bin/llama-cli")
    llm_model = os.path.expanduser("~/pc_hub/models/llm/qwen2.5-3b-instruct-q5_k_m.gguf")

    escalator = Escalator(
        llama_cli=llama_cli,
        llm_model=llm_model,
        incident_upload_url=cfg.incident_upload_url,
    )

    bg = BackgroundAI(cfg, store, yolo, whisper, escalator)

    # start background loops
    asyncio.create_task(bg.run_yolo_consumer())
    asyncio.create_task(bg.run_whisper_loop())

@app.get("/status")
def status():
    return {
        "ok": True,
        "keep_minutes": cfg.keep_minutes,
        "base_dir": cfg.base_dir,
        "frames_dir": cfg.frames_dir,
        "audio_dir": cfg.audio_dir,
        "wake_word": cfg.wake_word,
        "incident_upload_url_set": bool(cfg.incident_upload_url),
        "newest_frame": store.newest_file(cfg.frames_dir),
        "newest_audio": store.newest_file(cfg.audio_dir),
    }

# -------------------------
# Structured ingest routes
# -------------------------

@app.post("/ingest/frame")
async def ingest_frame(frame: UploadFile = File(...), meta: str = Form("")):
    name = _ts_name("frame", "jpg")
    path = os.path.join(cfg.frames_dir, name)

    data = await frame.read()
    with open(path, "wb") as f:
        f.write(data)

    ts = time.time()
    pr = store.prune()

    if bg is not None:
        await bg.on_new_frame(ts, path, meta=meta)

    return {"saved": path, "bytes": len(data), "meta": meta, **pr}

@app.post("/ingest/audio")
async def ingest_audio(audio: UploadFile = File(...), fmt: str = Query("wav")):
    ext = (fmt or "wav").lower().strip()
    name = _ts_name("audio", ext)
    path = os.path.join(cfg.audio_dir, name)

    data = await audio.read()
    with open(path, "wb") as f:
        f.write(data)

    ts = time.time()
    pr = store.prune()

    if bg is not None:
        await bg.on_new_audio(ts, path)

    return {"saved": path, "bytes": len(data), "fmt": ext, **pr}

# -------------------------
# Pi-compatible routes
# -------------------------

@app.post("/frame")
async def frame_compat(
    image: UploadFile = File(...),   # Pi sends field name "image"
    meta: str = Form(""),
):
    # forward to structured ingest
    return await ingest_frame(frame=image, meta=meta)

@app.post("/audio")
async def audio_compat(
    audio: UploadFile = File(...),   # Pi sends field name "audio"
):
    return await ingest_audio(audio=audio, fmt="wav")

@app.post("/hires_burst")
async def hires_burst_compat(
    burst: UploadFile = File(...),   # Pi sends field name "burst"
    reason: str = Form("covered"),
):
    os.makedirs(cfg.hires_dir, exist_ok=True)
    name = _ts_name(f"hires_{reason}", "zip")
    path = os.path.join(cfg.hires_dir, name)

    data = await burst.read()
    with open(path, "wb") as f:
        f.write(data)

    # Trigger urgent event immediately
    if bg is not None:
        await bg.handle_event("covered", "urgent", {"reason": reason, "burst_path": path})

    return {"saved": path, "bytes": len(data), "reason": reason}

# -------------------------
# Convenience: get latest
# -------------------------

@app.get("/latest/frame")
def latest_frame():
    p = store.newest_file(cfg.frames_dir)
    if not p:
        return ORJSONResponse({"error": "no frames yet"}, status_code=404)
    return FileResponse(p, media_type="image/jpeg")

@app.get("/latest/audio")
def latest_audio():
    p = store.newest_file(cfg.audio_dir)
    if not p:
        return ORJSONResponse({"error": "no audio yet"}, status_code=404)
    return FileResponse(p)

@app.get("/events/latest")
def events_latest():
    # return newest event json (if any)
    import glob
    files = sorted(glob.glob(os.path.join(cfg.events_dir, "event_*.json")), reverse=True)
    if not files:
        return ORJSONResponse({"error": "no events yet"}, status_code=404)
    return FileResponse(files[0], media_type="application/json")
