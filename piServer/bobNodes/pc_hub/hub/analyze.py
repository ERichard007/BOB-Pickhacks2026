import os, time, json, subprocess, tempfile, glob
from dataclasses import dataclass
from typing import Any

import cv2
from ultralytics import YOLO

# ---------------- config ----------------

@dataclass(frozen=True)
class AnalyzeConfig:
    # model/weights paths
    yolo_weights: str = os.getenv("YOLO_WEIGHTS", os.path.expanduser("~/pc_hub/yolov8n.pt"))

    whisper_bin: str = os.getenv("WHISPER_BIN", os.path.expanduser("~/pc_hub/whisper.cpp/build/bin/whisper-cli"))
    whisper_model: str = os.getenv("WHISPER_MODEL", os.path.expanduser("~/pc_hub/models/whisper/ggml-base.en-q8_0.bin"))

    llama_cli: str = os.getenv("LLAMA_CLI", os.path.expanduser("~/pc_hub/llama.cpp/build/bin/llama-cli"))
    llm_model: str = os.getenv("LLM_MODEL", os.path.expanduser("~/pc_hub/models/llm/qwen2.5-3b-instruct-q5_k_m.gguf"))

    # VLM (prefer mtmd)
    mtmd_cli: str = os.getenv("MTMD_CLI", os.path.expanduser("~/pc_hub/llama.cpp/build/bin/llama-mtmd-cli"))
    qwen2vl_cli: str = os.getenv("QWEN2VL_CLI", os.path.expanduser("~/pc_hub/llama.cpp/build/bin/llama-qwen2vl-cli"))
    vlm_model_dir: str = os.getenv("VLM_MODEL_DIR", os.path.expanduser("~/pc_hub/models/vlm/Qwen2-VL-2B-Instruct"))

    # behavior
    llm_ngl: int = int(os.getenv("LLM_NGL", "999"))  # offload many layers to GPU
    whisper_threads: int = int(os.getenv("WHISPER_THREADS", "4"))

    # hackathon triggers (simple)
    trigger_words = tuple(w.strip().lower() for w in os.getenv(
        "TRIGGER_WORDS",
        "help,emergency,call 911,i fell,falling,can't breathe,heart,stroke"
    ).split(","))

cfg = AnalyzeConfig()

# ---------------- shared singletons ----------------

_yolo_model: YOLO | None = None

def _get_yolo() -> YOLO:
    global _yolo_model
    if _yolo_model is None:
        _yolo_model = YOLO(cfg.yolo_weights)
    return _yolo_model

# ---------------- helpers ----------------

def newest_file(d: str) -> str | None:
    files = glob.glob(os.path.join(d, "*"))
    if not files:
        return None
    files.sort(key=lambda p: os.stat(p).st_mtime, reverse=True)
    return files[0]

def newest_files_since(d: str, seconds: float) -> list[str]:
    now = time.time()
    out = []
    for p in glob.glob(os.path.join(d, "*")):
        try:
            if now - os.stat(p).st_mtime <= seconds:
                out.append(p)
        except FileNotFoundError:
            pass
    out.sort(key=lambda p: os.stat(p).st_mtime)  # oldest->newest
    return out

def concat_wavs(wav_paths: list[str], out_path: str) -> None:
    """Concatenate WAVs that share format (rate/channels/sample width)."""
    import wave
    if not wav_paths:
        raise ValueError("no wavs to concat")
    params = None
    frames = []
    for p in wav_paths:
        with wave.open(p, "rb") as wf:
            if params is None:
                params = wf.getparams()
            else:
                if wf.getparams()[:4] != params[:4]:
                    raise ValueError(f"WAV format mismatch: {p}")
            frames.append(wf.readframes(wf.getnframes()))
    with wave.open(out_path, "wb") as out:
        out.setparams(params)
        for fr in frames:
            out.writeframes(fr)

def run(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr

# ---------------- AI steps ----------------

def yolo_detect(image_path: str) -> dict[str, Any]:
    model = _get_yolo()
    img = cv2.imread(image_path)
    if img is None:
        return {"error": f"could not read image {image_path}"}
    res = model.predict(source=img, verbose=False)[0]

    names = res.names  # id->name
    dets = []
    if res.boxes is not None and len(res.boxes) > 0:
        for b in res.boxes:
            xyxy = b.xyxy[0].tolist()
            conf = float(b.conf[0])
            cls_id = int(b.cls[0])
            dets.append({
                "label": names.get(cls_id, str(cls_id)),
                "conf": conf,
                "xyxy": [float(x) for x in xyxy],
            })

    # sort by confidence
    dets.sort(key=lambda d: d["conf"], reverse=True)
    return {"detections": dets, "count": len(dets)}

def whisper_transcribe(wav_path: str) -> dict[str, Any]:
    cmd = [
        cfg.whisper_bin,
        "-m", cfg.whisper_model,
        "-f", wav_path,
        "-t", str(cfg.whisper_threads),
        "--output-txt",
        "--output-file", wav_path + ".out"
    ]
    rc, out, err = run(cmd, timeout=240)
    txt_path = wav_path + ".out.txt"
    text = ""
    if os.path.exists(txt_path):
        text = open(txt_path, "r", encoding="utf-8", errors="ignore").read().strip()
    return {"rc": rc, "text": text, "stderr_tail": err[-800:]}

def choose_vlm_binary() -> str | None:
    if os.path.exists(cfg.mtmd_cli) and os.access(cfg.mtmd_cli, os.X_OK):
        return cfg.mtmd_cli
    if os.path.exists(cfg.qwen2vl_cli) and os.access(cfg.qwen2vl_cli, os.X_OK):
        return cfg.qwen2vl_cli
    return None

def vlm_describe(image_path: str) -> dict[str, Any]:
    """
    NOTE: llama.cpp mtmd CLI flags vary a bit across versions.
    We implement a conservative call pattern that works on most builds:
      -m <model_dir_or_file> -i <image> -p <prompt>
    If your mtmd build uses different flags, we can adjust quickly.
    """
    binpath = choose_vlm_binary()
    if not binpath:
        return {"error": "no VLM binary found (llama-mtmd-cli or llama-qwen2vl-cli)"}

    prompt = "Describe the scene briefly and mention any hazards, injuries, falls, stairs, or weapons."
    # try a couple common flag patterns
    candidates = [
        [binpath, "-m", cfg.vlm_model_dir, "-i", image_path, "-p", prompt, "-n", "80"],
        [binpath, "-m", cfg.vlm_model_dir, "--image", image_path, "-p", prompt, "-n", "80"],
        [binpath, "-m", cfg.vlm_model_dir, "-i", image_path, "--prompt", prompt, "-n", "80"],
    ]
    last = {"rc": 1, "stdout": "", "stderr": ""}
    for cmd in candidates:
        try:
            rc, out, err = run(cmd, timeout=240)
            if rc == 0 and out.strip():
                return {"rc": rc, "text": out.strip(), "bin": os.path.basename(binpath)}
            last = {"rc": rc, "stdout": out[-800:], "stderr": err[-800:]}
        except Exception as e:
            last = {"rc": 1, "stdout": "", "stderr": str(e)}
    return {"error": "VLM call failed; flags may differ on your build", **last, "bin": os.path.basename(binpath)}

def llm_reason(yolo_json: dict[str, Any], transcript: str, vlm_text: str | None) -> dict[str, Any]:
    det_summary = ", ".join([d["label"] for d in yolo_json.get("detections", [])[:8]]) or "none"
    prompt = f"""You are an emergency incident summarizer.
Given:
- Objects detected: {det_summary}
- Transcript (recent): {transcript or "(none)"}
- Scene description: {vlm_text or "(not available)"}

Return ONLY JSON with keys:
severity (low|medium|high), likely_event, key_evidence (array), recommended_action (array), urgent (true|false).
Be cautious: if uncertain, say so. Keep it short.
"""
    cmd = [
        cfg.llama_cli,
        "-m", cfg.llm_model,
        "-p", prompt,
        "-n", "180",
        "-ngl", str(cfg.llm_ngl),
        "--temp", "0.2",
    ]
    rc, out, err = run(cmd, timeout=240)

    # Try to extract JSON blob from output
    text = out.strip()
    j = None
    if "{" in text and "}" in text:
        start = text.find("{")
        end = text.rfind("}")
        blob = text[start:end+1]
        try:
            j = json.loads(blob)
        except Exception:
            j = {"raw": text}
    else:
        j = {"raw": text}

    return {"rc": rc, "json": j, "stderr_tail": err[-800:]}

def triggered(transcript: str) -> bool:
    t = (transcript or "").lower()
    return any(w in t for w in cfg.trigger_words)

# ---------------- orchestrator ----------------

def analyze_latest(frames_dir: str, audio_dir: str, audio_seconds: int = 5, use_vlm: bool = True) -> dict[str, Any]:
    frame = newest_file(frames_dir)
    if not frame:
        return {"error": "no frames yet"}

    # YOLO on newest frame
    y = yolo_detect(frame)

    # gather recent audio wav chunks and concat
    recent_audio = newest_files_since(audio_dir, seconds=float(audio_seconds))
    transcript = ""
    w = {"rc": 0, "text": ""}

    with tempfile.TemporaryDirectory() as td:
        wav_out = os.path.join(td, "window.wav")
        # only concat .wav files (your Pi sender should send wav chunks)
        wavs = [p for p in recent_audio if p.lower().endswith(".wav")]
        if wavs:
            try:
                concat_wavs(wavs, wav_out)
                w = whisper_transcribe(wav_out)
                transcript = w.get("text", "")
            except Exception as e:
                w = {"error": f"audio concat/transcribe failed: {e}", "paths": wavs[-10:]}

        # VLM
        v = {"skipped": True}
        vlm_text = None
        if use_vlm:
            v = vlm_describe(frame)
            vlm_text = v.get("text") if isinstance(v, dict) else None

        # LLM reasoning
        r = llm_reason(y, transcript, vlm_text)

    return {
        "frame_path": frame,
        "audio_files_used": recent_audio[-12:],  # last few paths
        "yolo": y,
        "whisper": w,
        "vlm": v,
        "reasoning": r,
        "trigger_word": triggered(transcript),
        "ts": time.time(),
    }
