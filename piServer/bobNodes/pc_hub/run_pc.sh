#!/usr/bin/env bash
export WAKE_WORD="help Bob"
export KEEP_MINUTES=10
export WHISPER_WINDOW_SEC=2
export WHISPER_STEP_SEC=1
export INCIDENT_AUDIO_SECONDS=5
export INCIDENT_FRAMES_N=4
export WEBHOOK_URL="http://[website_host_ip]:[website_host_port]/api/pi/info"
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
exec uvicorn hub.server:app --host "${HUB_HOST:-0.0.0.0}" --port "${HUB_PORT:-8000}"
