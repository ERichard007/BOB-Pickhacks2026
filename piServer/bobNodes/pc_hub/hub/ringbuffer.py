# pc_hub/ringbuffer.py
import os
import time
from pathlib import Path
from typing import Optional

class RollingStore:
    def __init__(self, frames_dir: str, audio_dir: str, keep_seconds: int):
        self.frames_dir = frames_dir
        self.audio_dir = audio_dir
        self.keep_seconds = int(keep_seconds)

    def ensure_dirs(self):
        for d in [self.frames_dir, self.audio_dir]:
            os.makedirs(d, exist_ok=True)

    def newest_file(self, dir_path: str) -> Optional[str]:
        p = Path(dir_path)
        if not p.exists():
            return None
        files = list(p.glob("*"))
        if not files:
            return None
        files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return str(files[0])

    def prune_dir(self, dir_path: str):
        now = time.time()
        p = Path(dir_path)
        if not p.exists():
            return {"deleted": 0}

        deleted = 0
        for f in p.glob("*"):
            try:
                if now - f.stat().st_mtime > self.keep_seconds:
                    f.unlink(missing_ok=True)
                    deleted += 1
            except Exception:
                pass
        return {"deleted": deleted}

    def prune(self):
        fr = self.prune_dir(self.frames_dir)
        au = self.prune_dir(self.audio_dir)
        return {"pruned_frames": fr["deleted"], "pruned_audio": au["deleted"]}
