"""Text-to-speech voiceover, using the voice already built into the computer (free and offline).

plan.json:
    "audio": {"voiceover": [{"text": "Here's the part nobody tells you.", "at": 3.0}]}

Windows uses its built-in speech voices, macOS `say`, Linux `espeak-ng`. These sound more robotic
than CapCut's cloud voices. Lines are cached in <edit>/animations/.

    python helpers/tts.py "Hello there" out.wav
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


def speak(text: str, edit_dir: Path, voice: str | None = None) -> str:
    """Make a sound file saying `text`; returns its path."""
    out_dir = edit_dir / "animations"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"tts_{hashlib.sha1(f'{voice}|{text}'.encode()).hexdigest()[:10]}.wav"
    if not out.exists():
        synth(text, out, voice)
    return str(out)


def synth(text: str, out: Path, voice: str | None = None) -> None:
    if os.name == "nt":
        # Text goes in through an environment variable, so nothing in it can be read as a command.
        script = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "if ($env:TTS_VOICE) { $s.SelectVoice($env:TTS_VOICE) }; $s.SetOutputToWaveFile($env:TTS_OUT); "
                  "$s.Speak($env:TTS_TEXT); $s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True,
                       env={**os.environ, "TTS_TEXT": text, "TTS_OUT": str(out), "TTS_VOICE": voice or ""})
    elif sys.platform == "darwin":
        aiff = out.with_suffix(".aiff")
        subprocess.run(["say", *(["-v", voice] if voice else []), "-o", str(aiff), "--", text], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(aiff), str(out)], check=True)
        aiff.unlink(missing_ok=True)
    elif shutil.which("espeak-ng"):
        subprocess.run(["espeak-ng", *(["-v", voice] if voice else []), "-w", str(out), "--", text], check=True)
    else:
        raise RuntimeError("no speech voice on this computer (install espeak-ng)")


if __name__ == "__main__":
    synth(sys.argv[1], Path(sys.argv[2]))
    print(sys.argv[2])
