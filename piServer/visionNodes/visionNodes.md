# Pi Vision Node (sender)

Captures:
- Low-res JPEG frames continuously and POSTs to the PC hub
- Audio WAV chunks continuously and POSTs to the PC hub
- Optional “hi-res burst” ring buffer upload when camera is covered

## Requirements
### System packages
- `ffmpeg`
- `alsa-utils` (arecord)
- camera enabled + working (/dev/videoX)

### Python
- Python 3
- `pip install -r requirements-pi.txt`

## Configure
Copy `.env.example` to `.env` and set:
- PC_IP, PC_PORT
- AUDIO_DEVICE (e.g. plughw:CARD=B101,DEV=0)
- CAM_INDEX (or a /dev/video path if your script supports it)

## Run
```bash
./run_pi.sh
````
