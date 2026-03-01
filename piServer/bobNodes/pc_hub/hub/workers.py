# pc_hub/workers.py
import os
import io
import re
import json
import time
import uuid
import shutil
import zipfile
import asyncio
import tempfile
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from collections import deque
from typing import Optional, Any

import cv2
import requests

# ----------------------------
# Event model
# ----------------------------

@dataclass
class Event:
    ts: float
    type: str              # "wake_word" | "covered" | etc
    severity: str          # "info" | "warning" | "urgent"
    details: dict
    evidence: dict         # file paths, etc
    summary: Optional[str] = None
    incident_zip: Optional[str] = None


# ----------------------------
# Text helpers
# ----------------------------

def _normalize_words(s: str) -> list[str]:
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.split()

def contains_wake_word(text: str, wake_word: str) -> bool:
    if not wake_word:
        return False
    t = " ".join(_normalize_words(text))
    w = " ".join(_normalize_words(wake_word))
    return w in t

def stitch_transcripts(prev: str, cur: str, max_overlap: int = 12) -> str:
    a = _normalize_words(prev)
    b = _normalize_words(cur)
    if not a:
        return cur.strip()
    if not b:
        return prev.strip()

    best = 0
    m = min(max_overlap, len(a), len(b))
    for k in range(1, m + 1):
        if a[-k:] == b[:k]:
            best = k
    if best:
        merged = a + b[best:]
        return " ".join(merged).strip()

    return (prev.strip() + " " + cur.strip()).strip()

def clamp_words(text: str, max_words: int = 500) -> str:
    w = text.split()
    if len(w) <= max_words:
        return text
    return " ".join(w[-max_words:])


# ----------------------------
# Whisper.cpp runner
# ----------------------------

class WhisperRunner:
    def __init__(self, whisper_bin: str, model_path: str):
        self.whisper_bin = whisper_bin
        self.model_path = model_path

    def transcribe(self, wav_path: str) -> str:
        cmd = [
            self.whisper_bin,
            "-m", self.model_path,
            "-f", wav_path,
            "-nt",
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        out = (p.stdout or "").strip()
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if not lines:
            return ""
        # whisper.cpp sometimes prints extra info; take last non-empty line
        return lines[-1]


# ----------------------------
# llama.cpp escalation (SLM)
# ----------------------------

class Escalator:
    def __init__(self, llama_cli: str, llm_model: str, incident_upload_url: str = ""):
        self.llama_cli = llama_cli
        self.llm_model = llm_model
        self.incident_upload_url = incident_upload_url

    def summarize_with_llm(self, prompt: str, max_tokens: int = 180) -> str:
        if not self.llama_cli or not os.path.exists(self.llama_cli):
            return "(llama-cli missing)"
        cmd = [
            self.llama_cli,
            "-m", self.llm_model,
            "-p", prompt,
            "-n", str(max_tokens),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True)
        out = (p.stdout or "").strip()
        return out[-4000:] if out else ""

    def upload_incident_zip(self, zip_path: str, event: Event) -> bool:
        """
        Upload ZIP + meta JSON to your HTTP endpoint.
        Expects: multipart file field "file" plus "meta".
        """
        if not self.incident_upload_url:
            return False

        try:
            with open(zip_path, "rb") as f:
                files = {"file": ("incident.zip", f, "application/zip")}
                data = {"meta": json.dumps(asdict(event))}
                r = requests.post(self.incident_upload_url, files=files, data=data, timeout=15)
                return 200 <= r.status_code < 300
        except Exception:
            return False


# ----------------------------
# Audio window merge (re-encode)
# ----------------------------

def build_audio_window_wav(wavs: list[tuple[float, str]], window_seconds: float, sample_rate: int = 16000) -> str | None:
    """
    Robust audio window builder.
    Re-encodes to avoid broken WAV concat.
    """
    if not wavs:
        return None

    now = time.time()
    since = now - window_seconds

    # keep files in time window
    chosen = [(ts, p) for (ts, p) in wavs if ts >= since and os.path.exists(p)]
    if not chosen:
        chosen = wavs[-int(window_seconds + 1.5):]

    chosen_paths = [p for _, p in chosen if os.path.exists(p) and os.path.getsize(p) > 2000]
    if not chosen_paths:
        return None

    tmpdir = tempfile.mkdtemp(prefix="audwin_")
    list_txt = Path(tmpdir) / "list.txt"
    out_wav = str(Path(tmpdir) / "window.wav")

    with open(list_txt, "w") as f:
        for p in chosen_paths:
            f.write(f"file '{p}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(list_txt),
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        out_wav
    ]

    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    if os.path.exists(out_wav) and os.path.getsize(out_wav) > 2000:
        return out_wav

    shutil.rmtree(tmpdir, ignore_errors=True)
    return None

def cleanup_audio_window(path: Optional[str]):
    if not path:
        return
    try:
        shutil.rmtree(str(Path(path).parent), ignore_errors=True)
    except Exception:
        pass


# ----------------------------
# Background AI controller
# ----------------------------

class BackgroundAI:
    def __init__(self, cfg, store, yolo_model, whisper: WhisperRunner, escalator: Escalator):
        self.cfg = cfg
        self.store = store
        self.yolo = yolo_model
        self.whisper = whisper
        self.escalator = escalator

        self.frame_queue: asyncio.Queue[tuple[float, str]] = asyncio.Queue(maxsize=128)

        # Rolling indexes (ts, path)
        self.frames = deque(maxlen=400)  # plenty
        self.audio = deque(maxlen=1200)  # plenty

        # YOLO cache: frame_path -> detections dict
        self.last_detections: dict[str, Any] = {}

        # Whisper state
        self.latest_stitched = ""
        self.wake_word = cfg.wake_word

        self.transcript_tail = deque(maxlen=40)  # ~40 steps (with step=1s = ~40s)
        self.last_event_ts = 0.0

        # ensure dirs exist
        for d in [
            cfg.frames_dir, cfg.audio_dir, cfg.hires_dir,
            cfg.frames_json_dir, cfg.frames_annotated_dir,
            cfg.state_dir, cfg.events_dir, cfg.incidents_dir
        ]:
            os.makedirs(d, exist_ok=True)

        # If transcript files not present, create them
        Path(cfg.transcript_jsonl).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg.transcript_tail_txt).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg.transcript_jsonl).touch(exist_ok=True)
        Path(cfg.transcript_tail_txt).touch(exist_ok=True)

    # ---- ingestion hooks ----

    async def on_new_frame(self, ts: float, path: str, meta: str = ""):
        self.frames.append((ts, path, meta))
        try:
            self.frame_queue.put_nowait((ts, path))
        except asyncio.QueueFull:
            # drop one oldest from queue
            try:
                _ = self.frame_queue.get_nowait()
            except Exception:
                pass
            try:
                self.frame_queue.put_nowait((ts, path))
            except Exception:
                pass

    async def on_new_audio(self, ts: float, path: str):
        self.audio.append((ts, path))

    # ---- YOLO consumer ----

    async def run_yolo_consumer(self):
        while True:
            ts, path = await self.frame_queue.get()
            try:
                det = await asyncio.to_thread(self._yolo_detect, path)
                self.last_detections[path] = det
                await asyncio.to_thread(self._save_frame_artifacts, ts, path, det)
            except Exception:
                pass

    def _yolo_detect(self, img_path: str):
        res = self.yolo(img_path, verbose=False)
        items = []
        for r in res:
            names = getattr(r, "names", {}) or {}
            boxes = getattr(r, "boxes", None)
            if boxes is None:
                continue
            for b in boxes:
                cls = int(b.cls[0])
                conf = float(b.conf[0])
                xyxy = [float(x) for x in b.xyxy[0]]
                items.append({
                    "label": names.get(cls, str(cls)),
                    "conf": conf,
                    "xyxy": xyxy
                })
        items.sort(key=lambda x: x["conf"], reverse=True)
        return items[:40]

    def _save_frame_artifacts(self, ts: float, img_path: str, det: list[dict]):
        # JSON
        base = Path(img_path).name
        jpath = Path(self.cfg.frames_json_dir) / (base + ".json")
        payload = {
            "ts": ts,
            "image": img_path,
            "detections": det,
        }
        with open(jpath, "w") as f:
            json.dump(payload, f)

        # Annotated JPEG
        apath = Path(self.cfg.frames_annotated_dir) / base
        try:
            img = cv2.imread(img_path)
            if img is None:
                return
            for d in det:
                x1, y1, x2, y2 = map(int, d["xyxy"])
                label = f'{d["label"]} {d["conf"]:.2f}'
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(img, label, (x1, max(15, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            cv2.imwrite(str(apath), img)
        except Exception:
            pass

    # ---- Whisper sliding window ----

    async def run_whisper_loop(self):
        window = float(self.cfg.whisper_window_sec)
        step = float(self.cfg.whisper_step_sec)

        while True:
            t0 = time.time()

            # build last window wav from audio deque (chronological)
            wavs = [(ts, p) for (ts, p) in self.audio]
            wavs.sort(key=lambda x: x[0])
            window_wav = await asyncio.to_thread(build_audio_window_wav, wavs, window, 16000)

            text = ""
            if window_wav:
                try:
                    text = await asyncio.to_thread(self.whisper.transcribe, window_wav)
                finally:
                    cleanup_audio_window(window_wav)

            # stitch + clamp
            prev = self.latest_stitched
            self.latest_stitched = clamp_words(stitch_transcripts(prev, text), 500)

            # keep tail
            self.transcript_tail.append({"ts": time.time(), "window_text": text, "stitched": self.latest_stitched})
            await asyncio.to_thread(self._append_transcript_logs, text)

            # wake word
            if contains_wake_word(self.latest_stitched, self.wake_word):
                await self.handle_event("wake_word", "urgent", {"wake_word": self.wake_word})

            dt = time.time() - t0
            await asyncio.sleep(max(0.0, step - dt))

    def _append_transcript_logs(self, window_text: str):
        rec = {
            "ts": time.time(),
            "window_text": window_text,
            "stitched": self.latest_stitched,
        }
        with open(self.cfg.transcript_jsonl, "a") as f:
            f.write(json.dumps(rec) + "\n")

        # also keep a readable tail
        # overwrite tail file each time with the last ~40 entries
        try:
            with open(self.cfg.transcript_tail_txt, "w") as f:
                for item in list(self.transcript_tail)[-40:]:
                    f.write(f'[{time.strftime("%H:%M:%S", time.localtime(item["ts"]))}] {item["window_text"]}\n')
                f.write("\n--- STITCHED (latest) ---\n")
                f.write(self.latest_stitched + "\n")
        except Exception:
            pass

    # ---- Incident building / escalation ----

    async def handle_event(self, etype: str, severity: str, details: dict):
        now = time.time()
        if now - self.last_event_ts < 2.0:
            return
        self.last_event_ts = now

        # Collect evidence: last N frames, last X seconds audio
        frames = list(self.frames)
        frames.sort(key=lambda x: x[0])

        last_frames = frames[-self.cfg.incident_frames_n:] if frames else []
        frame_paths = [p for (_, p, _meta) in last_frames if os.path.exists(p)]
        frame_metas = [m for (_ts, _p, m) in last_frames]

        # audio window wav for last incident_audio_seconds
        wavs = [(ts, p) for (ts, p) in self.audio]
        wavs.sort(key=lambda x: x[0])
        incident_wav = await asyncio.to_thread(build_audio_window_wav, wavs, self.cfg.incident_audio_seconds, 16000)

        # build prompt for SLM
        det_bundle = []
        for fp in frame_paths:
            det_bundle.append({
                "frame": fp,
                "detections": self.last_detections.get(fp, [])
            })

        tail_lines = [t["window_text"] for t in list(self.transcript_tail)[-10:] if t.get("window_text")]
        tail_text = "\n".join(tail_lines).strip()

        prompt = f"""
You are an emergency event summarizer.
Event type: {etype}
Severity: {severity}

Wake word configured: {self.wake_word}

Recent stitched transcript:
{self.latest_stitched}

Recent transcript tail (last ~10s windows):
{tail_text}

Recent detections for last frames:
{json.dumps(det_bundle, indent=2)[:3500]}

Frame sender meta for last frames:
{json.dumps(frame_metas, indent=2)[:2000]}

Explain in 1-3 sentences what likely happened and what to do next.
If uncertain, state the uncertainty and what additional signal would help.
""".strip()

        summary = await asyncio.to_thread(self.escalator.summarize_with_llm, prompt)

        ev = Event(
            ts=now,
            type=etype,
            severity=severity,
            details=details,
            evidence={
                "frames": frame_paths,
                "audio_window_wav": incident_wav or "",
                "stitched": self.latest_stitched,
            },
            summary=summary,
        )

        # Persist event + build incident zip
        zip_path = await asyncio.to_thread(self._build_incident_zip, ev, frame_paths, incident_wav)
        ev.incident_zip = zip_path

        # Write event JSON for local viewing
        await asyncio.to_thread(self._write_event_record, ev)

        # Upload to website if configured
        if zip_path:
            await asyncio.to_thread(self.escalator.upload_incident_zip, zip_path, ev)

        # cleanup temp audio window
        if incident_wav:
            cleanup_audio_window(incident_wav)

    def _write_event_record(self, ev: Event):
        name = f"event_{int(ev.ts*1000)}_{ev.type}_{uuid.uuid4().hex}.json"
        path = Path(self.cfg.events_dir) / name
        with open(path, "w") as f:
            json.dump(asdict(ev), f, indent=2)

    def _build_incident_zip(self, ev: Event, frame_paths: list[str], window_wav: Optional[str]) -> Optional[str]:
        inc_id = f"incident_{int(ev.ts*1000)}_{ev.type}_{uuid.uuid4().hex[:8]}"
        inc_dir = Path(self.cfg.incidents_dir) / inc_id
        inc_dir.mkdir(parents=True, exist_ok=True)

        # subfolders
        (inc_dir / "frames").mkdir(exist_ok=True)
        (inc_dir / "frames_annotated").mkdir(exist_ok=True)
        (inc_dir / "frames_json").mkdir(exist_ok=True)
        (inc_dir / "audio").mkdir(exist_ok=True)
        (inc_dir / "transcript").mkdir(exist_ok=True)

        # copy frames + artifacts
        for fp in frame_paths:
            try:
                bn = Path(fp).name
                shutil.copy2(fp, inc_dir / "frames" / bn)

                j = Path(self.cfg.frames_json_dir) / (bn + ".json")
                if j.exists():
                    shutil.copy2(j, inc_dir / "frames_json" / (bn + ".json"))

                a = Path(self.cfg.frames_annotated_dir) / bn
                if a.exists():
                    shutil.copy2(a, inc_dir / "frames_annotated" / bn)
            except Exception:
                pass

        # copy audio window
        if window_wav and os.path.exists(window_wav):
            try:
                shutil.copy2(window_wav, inc_dir / "audio" / "last_5s.wav")
            except Exception:
                pass

        # transcript snapshots
        try:
            shutil.copy2(self.cfg.transcript_tail_txt, inc_dir / "transcript" / "tail.txt")
        except Exception:
            pass

        # Write a stitched snapshot + tail JSON
        try:
            with open(inc_dir / "transcript" / "stitched.txt", "w") as f:
                f.write(ev.evidence.get("stitched", "") + "\n")
            with open(inc_dir / "transcript" / "tail.json", "w") as f:
                json.dump(list(self.transcript_tail), f, indent=2)
        except Exception:
            pass

        # manifest
        try:
            with open(inc_dir / "incident.json", "w") as f:
                json.dump(asdict(ev), f, indent=2)
        except Exception:
            pass

        # zip it
        zip_path = str(Path(self.cfg.incidents_dir) / f"{inc_id}.zip")
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
                for root, _dirs, files in os.walk(inc_dir):
                    for fn in files:
                        full = Path(root) / fn
                        rel = full.relative_to(inc_dir)
                        z.write(str(full), str(rel))
            # optional: keep folder for inspection, or delete it
            # shutil.rmtree(inc_dir, ignore_errors=True)
            return zip_path
        except Exception:
            return None
