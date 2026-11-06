"""Talk instead of type: turn a short voice recording from the editor's mic buttons into text.

Uses the same free speech model as the video transcription, on this computer. The recording
goes to the editor's own local server and nowhere else, and is deleted straight after.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading

import numpy as np

MAX_BYTES = 100 * 1024 * 1024  # about an hour of browser audio; a note is far smaller
_lock = threading.Lock()


def to_text(data: bytes) -> str:
    """Whatever the browser recorded (WebM, Ogg, MP4...) -> what was said."""
    from transcribe import DEFAULT_MODEL, load_local_model
    fd, path = tempfile.mkstemp(suffix=".audio")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                             capture_output=True, check=True).stdout
    finally:
        os.unlink(path)
    if len(raw) < 16000:  # under half a second of sound
        return ""
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    with _lock:  # one at a time: the model is shared
        model = load_local_model(DEFAULT_MODEL)
        segments, _ = model.transcribe(audio, beam_size=1, vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()
