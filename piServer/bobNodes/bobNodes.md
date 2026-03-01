# PC Hub (FastAPI + YOLO + Whisper + LLM Escalation)

This service receives frames/audio from a Raspberry Pi node and runs:
- YOLO object detection on incoming frames
- whisper.cpp rolling transcription (windowed) + wake word detection ("help bob")
- optional escalation via llama.cpp (small local LLM)
- optional webhook upload of “important moment” bundles

## What runs where
- Pi: capture + send frames/audio
- PC: AI inference + event packaging + webhook

## Requirements
### Python
- Python 3.10+
- `pip install -r requirements-pc.txt`

### External binaries (not installed by pip)
- `whisper.cpp` (built) -> provides `whisper-cli`
- `llama.cpp` (built) -> provides `llama-cli`
- FFmpeg installed (used for audio window concat on PC if enabled)

### Models / Weights
See `MODELS.md` for exact model names + expected local paths.

## Run
1) Copy `.env.example` to `.env` and edit paths / webhook URL.
2) Start server:
```bash
./run_pc.sh
````

## API

* `GET /status`
* `POST /frame` and `POST /audio` (compat endpoints used by Pi)
* `GET /latest/frame`
* `GET /latest/audio`
* `GET /analyze/latest'
