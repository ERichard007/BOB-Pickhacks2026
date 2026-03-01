# pc_hub/config.py
import os
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Config:
    # base runtime root (all saved data goes here)
    base_dir: str = os.environ.get("PC_HUB_BASE", str(Path.home() / "pc_hub" / "runtime"))

    # how long to keep raw frames/audio on disk (minutes)
    keep_minutes: int = int(os.environ.get("KEEP_MINUTES", "10"))

    # wake word
    wake_word: str = os.environ.get("WAKE_WORD", "help bob")

    # whisper sliding window
    whisper_window_sec: float = float(os.environ.get("WHISPER_WINDOW_SEC", "2"))
    whisper_step_sec: float = float(os.environ.get("WHISPER_STEP_SEC", "1"))

    # incident capture window sizes
    incident_audio_seconds: float = float(os.environ.get("INCIDENT_AUDIO_SECONDS", "5"))
    incident_frames_n: int = int(os.environ.get("INCIDENT_FRAMES_N", "4"))

    # external upload destination (your website / webhook that receives ZIP)
    # This should accept multipart with file field "file" and optional JSON "meta"
    incident_upload_url: str = os.environ.get("INCIDENT_UPLOAD_URL", "http://[website_host_ip]:[website_host_port]/api/pi/info")

    # Paths (computed)
    def __post_init__(self):
        base = Path(self.base_dir).expanduser()
        self.frames_dir = str(base / "frames")
        self.audio_dir = str(base / "audio")
        self.hires_dir = str(base / "hires_bursts")

        self.frames_json_dir = str(base / "frames_json")
        self.frames_annotated_dir = str(base / "frames_annotated")

        self.state_dir = str(base / "state")
        self.events_dir = str(base / "events")
        self.incidents_dir = str(base / "incidents")

        # transcript files
        self.transcript_jsonl = str(Path(self.state_dir) / "transcript.jsonl")
        self.transcript_tail_txt = str(Path(self.state_dir) / "transcript_tail.txt")
