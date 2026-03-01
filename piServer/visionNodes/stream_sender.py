#!/usr/bin/env python3
"""
stream_sender_v4.py  (Pi Zero W)
- Uses ONE camera capture thread (prevents /dev/video0 busy + weird slowdowns)
- Sends low-res JPEG frames at a fixed interval (default 0.25s)
- Streams audio as RAW PCM from arecord (no temp wav files), posts in chunks (default 1.0s)
- Keeps a rolling hi-res 1080p JPEG buffer (default 5s @ 3fps) and can burst-upload on “covered”
- Designed to work with your PC drop-in compat routes:
    POST /frame        (field: image, meta)
    POST /audio        (field: audio)
    POST /hires_burst  (field: burst, reason)
"""

import os
import time
import io
import zipfile
import threading
import subprocess
import queue
from collections import deque
import requests
import cv2

# =========================
# CONFIG (edit or env vars)
# =========================

PC_IP   = os.environ.get("PC_IP", "[host_ip]")
PC_PORT = int(os.environ.get("PC_PORT", "8000"))

# Run duration (0 = forever)
MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", "0"))

# Endpoints (compat)
FRAME_URL = f"http://{PC_IP}:{PC_PORT}/frame"
AUDIO_URL = f"http://{PC_IP}:{PC_PORT}/audio"
HIRES_URL = f"http://{PC_IP}:{PC_PORT}/hires_burst"
STATUS_URL = f"http://{PC_IP}:{PC_PORT}/status"

POST_TIMEOUT = float(os.environ.get("POST_TIMEOUT", "8.0"))

# Camera
CAM_INDEX = int(os.environ.get("CAM_INDEX", "0"))

# Low-res send
FRAME_INTERVAL_SEC = float(os.environ.get("FRAME_INTERVAL_SEC", "0.25"))  # 4 FPS send
LOW_W = int(os.environ.get("LOW_W", "640"))
LOW_H = int(os.environ.get("LOW_H", "360"))
LOW_JPEG_QUALITY = int(os.environ.get("LOW_JPEG_QUALITY", "65"))

# Hi-res ring buffer
HIRES_SECONDS = float(os.environ.get("HIRES_SECONDS", "5.0"))
HIRES_FPS = float(os.environ.get("HIRES_FPS", "3.0"))   # 2–5 recommended on Pi Zero
HI_W = int(os.environ.get("HI_W", "1920"))
HI_H = int(os.environ.get("HI_H", "1080"))
HI_JPEG_QUALITY = int(os.environ.get("HI_JPEG_QUALITY", "75"))

# “Covered” heuristic
COVERED_MEAN_MAX = float(os.environ.get("COVERED_MEAN_MAX", "15.0"))
COVERED_VAR_MAX  = float(os.environ.get("COVERED_VAR_MAX",  "20.0"))
COVERED_CONSEC_FRAMES = int(os.environ.get("COVERED_CONSEC_FRAMES", "4"))

# Audio capture
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", "plughw:CARD=B101,DEV=0")
AUDIO_RATE = int(os.environ.get("AUDIO_RATE", "16000"))
AUDIO_CHUNK_SEC = int(float(os.environ.get("AUDIO_CHUNK_SEC", "1")))
AUDIO_Q_MAX = int(os.environ.get("AUDIO_Q_MAX", "8"))

# Networking behavior
DROP_FRAMES_IF_BACKLOG = int(os.environ.get("DROP_FRAMES_IF_BACKLOG", "1"))  # 1=yes

# =========================
# INTERNALS
# =========================

stop_flag = threading.Event()

# Use a Session for connection reuse
sess = requests.Session()

# Hi-res rolling buffer: (ts, jpeg_bytes)
hires_buffer = deque()
hires_lock = threading.Lock()

# Queues to decouple capture from network
frame_q = queue.Queue(maxsize=3)  # low-res frames to send (keep small)
audio_q = queue.Queue(maxsize=AUDIO_Q_MAX)

def log(msg: str):
    print(msg, flush=True)

def reachable() -> bool:
    try:
        sess.get(STATUS_URL, timeout=1)
        return True
    except Exception:
        return False

def make_hires_zip_bytes() -> bytes:
    with hires_lock:
        items = list(hires_buffer)

    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for i, (ts, jpg_bytes) in enumerate(items):
            z.writestr(f"frame_{i:03d}_{int(ts*1000)}.jpg", jpg_bytes)
    return mem.getvalue()

def send_hires_burst(reason: str):
    try:
        zip_bytes = make_hires_zip_bytes()
        files = {"burst": ("hires_last_seconds.zip", zip_bytes, "application/zip")}
        data = {"reason": reason}
        sess.post(HIRES_URL, files=files, data=data, timeout=POST_TIMEOUT)
        log(f"[hires] burst sent ({reason}), {len(zip_bytes)/1e6:.2f} MB")
    except Exception as e:
        log(f"[hires] burst send failed: {e}")

def camera_capture_loop():
    """
    ONE camera open.
    - produces low-res frames for sending at FRAME_INTERVAL_SEC
    - captures hi-res frames into rolling buffer at HIRES_FPS
    """
    # Try to open camera reliably
    cap = cv2.VideoCapture(CAM_INDEX)
    if not cap.isOpened():
        log("ERROR: could not open camera. Is /dev/video0 busy? Try: sudo fuser -v /dev/video0")
        return

    # Ask for hi-res (some cams ignore; we still downscale for low-res)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, HI_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, HI_H)

    hires_interval = 1.0 / max(HIRES_FPS, 0.1)
    max_hires_len = int(HIRES_SECONDS * HIRES_FPS) + 3

    next_low_send = time.monotonic()
    next_hires_cap = time.monotonic()

    covered_count = 0

    while not stop_flag.is_set():
        ok, frame = cap.read()
        if not ok or frame is None:
            # avoid tight loop if camera hiccups
            time.sleep(0.05)
            continue

        now = time.monotonic()

        # --- hi-res buffer capture ---
        if now >= next_hires_cap:
            next_hires_cap += hires_interval
            # encode hi-res JPEG from current frame (frame is whatever camera gives; often 1080p)
            ok2, jpg = cv2.imencode(
                ".jpg", frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), HI_JPEG_QUALITY]
            )
            if ok2:
                with hires_lock:
                    hires_buffer.append((time.time(), jpg.tobytes()))
                    while len(hires_buffer) > max_hires_len:
                        hires_buffer.popleft()

        # --- low-res send frame ---
        if now >= next_low_send:
            next_low_send += FRAME_INTERVAL_SEC

            low = cv2.resize(frame, (LOW_W, LOW_H), interpolation=cv2.INTER_AREA)

            gray = cv2.cvtColor(low, cv2.COLOR_BGR2GRAY)
            mean = float(gray.mean())
            var = float(gray.var())

            if mean <= COVERED_MEAN_MAX and var <= COVERED_VAR_MAX:
                covered_count += 1
            else:
                covered_count = 0

            ok3, lowjpg = cv2.imencode(
                ".jpg", low,
                [int(cv2.IMWRITE_JPEG_QUALITY), LOW_JPEG_QUALITY]
            )
            if ok3:
                meta = {
                    "t": time.time(),
                    "mean": mean,
                    "var": var,
                    "covered_count": covered_count,
                    "low_wh": (LOW_W, LOW_H),
                }

                # queue frame for sender (drop old if backed up)
                if DROP_FRAMES_IF_BACKLOG:
                    while frame_q.qsize() >= frame_q.maxsize:
                        try:
                            frame_q.get_nowait()
                        except Exception:
                            break

                try:
                    frame_q.put_nowait((lowjpg.tobytes(), str(meta)))
                except queue.Full:
                    pass

            if covered_count == COVERED_CONSEC_FRAMES:
                # Send burst once when it first hits the threshold
                send_hires_burst("covered")

        # keep loop from eating CPU if camera FPS is high
        time.sleep(0.001)

    cap.release()

def frame_sender_loop():
    """
    Sends queued low-res frames to PC continuously.
    """
    sent = 0
    last_report = time.time()

    while not stop_flag.is_set():
        try:
            jpg_bytes, meta_str = frame_q.get(timeout=0.5)
        except queue.Empty:
            continue

        try:
            files = {"image": ("frame.jpg", jpg_bytes, "image/jpeg")}
            data = {"meta": meta_str}
            r = sess.post(FRAME_URL, files=files, data=data, timeout=POST_TIMEOUT)
            if r.status_code != 200:
                log(f"[frame] HTTP {r.status_code}")
            else:
                sent += 1
        except Exception as e:
            log(f"[frame] send failed: {e}")

        if time.time() - last_report >= 5.0:
            log(f"[frame] sent ~{sent} frames in last 5s (q={frame_q.qsize()})")
            sent = 0
            last_report = time.time()

def audio_loop():
    """
    Record audio continuously as WAV chunks and POST them.
    Uses stdout piping to avoid empty/truncated temp files.
    """
    while not stop_flag.is_set():
        cmd = [
            "arecord",
            "-D", AUDIO_DEVICE,
            "-f", "S16_LE",
            "-r", str(AUDIO_RATE),
            "-c", "1",
            "-d", str(int(AUDIO_CHUNK_SEC)),
            "-t", "wav",
            "-"  # write WAV to stdout
        ]
        try:
            p = subprocess.run(cmd, capture_output=True, check=False)
            wav_bytes = p.stdout or b""

            # A real WAV chunk should be way bigger than header (44 bytes)
            if len(wav_bytes) < 2000:
                # log the stderr somewhere useful
                try:
                    err = (p.stderr or b"").decode("utf-8", "ignore")
                    print(f"[AUDIO] tiny chunk ({len(wav_bytes)} bytes). arecord stderr:\n{err}")
                except Exception:
                    pass
                # small backoff to avoid hammering
                time.sleep(0.2)
                continue

            files = {"audio": ("chunk.wav", io.BytesIO(wav_bytes), "audio/wav")}
            requests.post(AUDIO_URL, files=files, timeout=POST_TIMEOUT)

        except Exception as e:
            print("[AUDIO] exception:", repr(e))
            time.sleep(0.5)

def audio_sender_loop():
    """
    Posts PCM chunks to PC. (Your PC compat route just saves bytes as .wav right now,
    but it will still accept the upload. If you want, we can tag fmt=pcm later.)
    """
    sent = 0
    last_report = time.time()

    while not stop_flag.is_set():
        try:
            ts, pcm = audio_q.get(timeout=0.5)
        except queue.Empty:
            continue

        try:
            files = {"audio": ("chunk.pcm", pcm, "application/octet-stream")}
            r = sess.post(AUDIO_URL, files=files, timeout=POST_TIMEOUT)
            if r.status_code != 200:
                log(f"[audio] HTTP {r.status_code}")
            else:
                sent += 1
        except Exception as e:
            log(f"[audio] send failed: {e}")

        if time.time() - last_report >= 5.0:
            log(f"[audio] sent ~{sent} chunks in last 5s (q={audio_q.qsize()})")
            sent = 0
            last_report = time.time()

def main():
    log("=== stream_sender_v4 starting ===")
    log(f"PC: {PC_IP}:{PC_PORT}")
    log(f"Endpoints: {FRAME_URL} {AUDIO_URL} {HIRES_URL}")

    if not reachable():
        log("WARNING: PC /status not reachable yet (will still try to send).")

    th_cam   = threading.Thread(target=camera_capture_loop, daemon=True)
    th_fsend = threading.Thread(target=frame_sender_loop, daemon=True)
    th_acap  = threading.Thread(target=audio_loop, daemon=True)
    th_asend = threading.Thread(target=audio_sender_loop, daemon=True)

    start = time.time()
    th_cam.start()
    th_fsend.start()
    th_acap.start()
    th_asend.start()

    try:
        while True:
            if MAX_RUNTIME_SECONDS > 0 and (time.time() - start) >= MAX_RUNTIME_SECONDS:
                break
            time.sleep(0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop_flag.set()
        time.sleep(0.5)
        log("=== stream_sender_v4 stopped ===")

if __name__ == "__main__":
    main()
