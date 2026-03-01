#AI Components Used (What / Where / Why)

This project is a **Pi → PC “vision + audio” pipeline**:
- **Pi (edge node)** captures low-res frames + short audio chunks and streams them to
- **PC (hub)** which runs the AI models locally (GPU + CPU), stores results, and escalates “important moments.”

The goal is **real-time-ish monitoring** with **fast local inference**, plus an **event escalation path** when something important is detected (wake word, camera covered, suspicious scene, etc.).

---

## System Overview

### Data flows
1. **Pi** posts:
   - `/frame` (JPEG frames, low-res, e.g. 640×360 @ ~4 FPS)
   - `/audio` (WAV chunks, e.g. 1s chunks @ 16kHz mono)
   - `/hires_burst` (zip of recent hi-res frames when a trigger happens like camera covered)

2. **PC** saves:
   - raw frames/audio
   - per-frame detections (JSON)
   - rolling transcript (“stitched”)
   - event packages (zip + summary)

3. **PC** triggers “important events” from:
   - Wake word detection (“help bob”)
   - Camera covered heuristic
   - YOLO detections meeting rules (e.g., person present + context)
   - (Optional) additional heuristics / rules later

4. When an event triggers:
   - gather context window (last ~5 seconds frames + audio)
   - summarize with an LLM (short explanation / what likely happened)
   - optionally upload to a webhook endpoint

---

## Model 1 — Object Detection (Vision)
### YOLOv8n (Ultralytics)
- **Model**: `yolov8n.pt`
- **Runs on**: **PC GPU** (RTX 3090) via PyTorch/Ultralytics
- **Used for**:
  - Detecting objects in incoming frames (people, phones, chairs, etc.)
  - Producing bounding boxes + labels + confidence
  - Serving as the primary “what is in the image right now?” signal
- **Why this model**:
  - `v8n` (“nano”) is **fast** and lightweight
  - Works well as a general-purpose detector without huge compute cost
  - Good for “always-on” detection at multiple FPS

**Outputs stored** (recommended):
- `frames/<timestamp>.jpg`
- `detections/<same_timestamp>.json` containing:
  - list of `{label, conf, xyxy}` detections
  - optionally derived flags like `has_person`, `is_dark_frame`, etc.

---

## Model 2 — Speech-to-Text (Audio)
### whisper.cpp (C++ Whisper)
- **Runtime**: `whisper.cpp` compiled binary (e.g. `whisper-cli`)
- **Model file** (current): `ggml-base.en-q8_0.bin`
- **Runs on**: **PC CPU** (usually best to keep Whisper CPU and vision GPU)
- **Used for**:
  - Continuous transcription on overlapping windows:
    - “transcribe last 2 seconds every 1 second”
  - Wake-word detection from the rolling transcript:
    - wake word: **“help bob”**
  - Producing a rolling “stitched transcript” for context around events
- **Why whisper.cpp**:
  - Fully local, no cloud dependency
  - Efficient C++ inference
  - Stable for streaming chunk workflows
- **Why base.en (Q8)**:
  - Better accuracy than tiny
  - Still reasonably fast on modern CPUs
  - Quantized to reduce CPU cost

**Note (recommended option):**
If Whisper cannot keep up at your cadence (2s every 1s), switch to:
- `ggml-tiny.en-q8_0.bin` (or even q5/q4)
This usually fixes real-time drift/backlog.

**Outputs stored** (recommended):
- `runtime/state/transcript_tail.txt` (rolling “last N seconds” view)
- `runtime/state/transcript_full.txt` (append-only “full session transcript”)
- `runtime/state/whisper_segments.jsonl` (each line = a transcription job result with timestamps)

---

## Model 3 — Local “Reasoning / Summary” LLM (Escalation)
### Qwen2.5 3B Instruct (GGUF via llama.cpp)
- **Model**: `qwen2.5-3b-instruct-q5_k_m.gguf`
- **Runs on**: **PC** via `llama.cpp` CLI
  - Typically CPU, optionally GPU-accelerated depending on your llama.cpp build
- **Used for**:
  - **Event summarization only** (not continuous)
  - Given:
    - last transcript text
    - YOLO detections
    - simple sensor flags (camera covered / audio anomalies)
  - Produce:
    - “What likely happened?” in 1–3 sentences
    - “What to do next?” or “What info is missing?”
- **Why this model**:
  - 3B is small enough to run locally with decent speed
  - Instruct-tuned so it follows “summarize and advise” prompts well
  - GGUF makes it easy to run on many systems

**Outputs stored** (recommended):
- `events/<event_id>/summary.txt`
- `events/<event_id>/event.json` (includes model outputs + metadata)

---

## (Optional / In Progress) Model 4 — Vision-Language Model (VLM)
### Qwen2-VL-2B-Instruct (Transformers)
- **Model folder**: `models/vlm/Qwen2-VL-2B-Instruct/`
- **Runs on**: **PC GPU** (Transformers + PyTorch)
- **Intended use**:
  - Higher-level image understanding than YOLO labels:
    - “Is someone fallen near stairs?”
    - “Is the camera pointed at a hallway or a person?”
  - Used on:
    - latest frame
    - or “event frames” only (recommended)
- **Why it’s optional**:
  - VLMs are heavier than YOLO and can increase latency
  - For real-time, best strategy is:
    - YOLO always-on
    - VLM only on triggers / sampled frames

**Status**:
- If your Transformers setup is currently failing to load or mismatched, keep VLM disabled until stabilized.

---

## Model Responsibilities Summary (Quick Table)

| Component | Model | Where it runs | Always-on? | What it does |
|---|---|---:|---:|---|
| Object detection | YOLOv8n | PC GPU | Yes | Boxes + labels for each incoming frame |
| Speech-to-text | whisper.cpp (base.en/tiny.en) | PC CPU | Yes | Rolling transcript + wake word trigger |
| Event summarizer | Qwen2.5 3B Instruct (GGUF) | PC | No (events only) | “What happened?” short summary + next steps |
| Vision-language (optional) | Qwen2-VL-2B | PC GPU | No (events only) | Higher-level image descriptions |

---

## Why this model mix works well
This is a “fast-first, heavy-on-demand” architecture:

- **YOLO** gives you cheap, high-frequency scene signals.
- **Whisper** gives you continuous speech context + wake word detection.
- **LLM summarizer** only runs when needed, so you don’t waste compute constantly.
- **(Optional) VLM** is reserved for the moments where you really need deep scene understanding.

This makes it much more likely to stay responsive in real time.

---

## Notes on “Important Moments”
An “important moment” is defined by triggers and policy rules, e.g.:
- Wake word detected: **“help bob”**
- Camera covered detected for N consecutive frames
- “Person detected + unusual audio”
- Future ideas  would  be things like “person + fallen posture heuristic” or “sudden motion + thud sound”

On trigger, the system packages:
- last ~5s audio window
- last ~4–15 frames (low-res)
- YOLO JSON outputs for those frames
- transcript snippet + stitched transcript
- LLM summary

…and optionally POSTs it to a webhook for a website dashboard.

---

## File locations (recommended conventions)
- Models:
  - `pc_hub/models/whisper/`
  - `pc_hub/models/llm/`
  - `pc_hub/models/vlm/` (optional)
  - `pc_hub/yolov8n.pt`
- Runtime state:
  - `pc_hub/runtime/state/`
- Stored media:
  - `pc_hub/runtime/frames/`
  - `pc_hub/runtime/audio/`
  - `pc_hub/runtime/detections/`
- Events:
  - `pc_hub/runtime/events/<event_id>/`

---

## Configuration knobs you’ll likely change
- Wake word:
  - `WAKE_WORD="help bob"`
- Whisper cadence:
  - `WHISPER_WINDOW_SEC=2`
  - `WHISPER_STEP_SEC=1`
- Frame rate / resolution:
  - `FRAME_INTERVAL_SEC=0.25`
  - `LOW_W=640 LOW_H=360`
- Event evidence windows:
  - `EVENT_WINDOW_SEC=5` (suggested)
  - `EVENT_FRAMES_MAX=15`
- Webhook:
  - `WEBHOOK_URL="http://your-server/upload"`

---
