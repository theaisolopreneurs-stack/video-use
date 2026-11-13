"""Your voice, cloned, saying a new script: free and on your own computer (Chatterbox, MIT licence).

    python helpers/voice_clone.py install                         # one time, ~2 GB (asks first)
    python helpers/voice_clone.py enroll "Alan" <video or audio> [--start 164.7 --duration 18]
    python helpers/voice_clone.py speak "Alan" script.txt out.wav
    python helpers/voice_clone.py list

A voice is a 10-30 second clip of the person talking clearly (no music), saved in
<library>/voices/<name>.wav. Every clip Chatterbox makes carries an inaudible watermark saying it's
AI-generated. Only clone voices of people who've agreed to it.

Chatterbox needs PyTorch 2.6, which the editor's own Python doesn't use, so it lives in its own
environment in ~/.cache/ai-video-editor/voice-env. On a Windows ARM laptop it runs through Windows'
x64 translation (there's no ARM build of that PyTorch): about 3 minutes per 45 seconds of speech.
On a Mac (Apple chip), an Intel/AMD PC or with an NVIDIA card it's much quicker.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HELPERS = Path(__file__).resolve().parent
ENV = Path.home() / ".cache" / "ai-video-editor" / "voice-env"
PY = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def library() -> Path:
    try:
        import library as lib
        return Path(lib.default_library())
    except Exception:
        return Path.home() / "Videos" / "AI Video Editor"


def voices_dir() -> Path:
    d = library() / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def installed() -> bool:
    return PY.exists() and subprocess.run([str(PY), "-c", "import chatterbox"], capture_output=True).returncode == 0


def install() -> None:
    """Make the voice environment: Python 3.12 + PyTorch 2.6 (CPU) + Chatterbox."""
    import shutil
    uv = shutil.which("uv")
    if not uv:
        sys.exit("needs uv (the editor's setup installs it)")
    # The editor's own Python 3.12 (x64 on Windows, including ARM laptops: Chatterbox's PyTorch has
    # no Windows ARM build), else whatever uv finds.
    base = sys.executable
    subprocess.run([uv, "venv", "--clear", "--python", base, str(ENV)], check=True)
    idx = [] if sys.platform == "darwin" else ["--index-url", "https://download.pytorch.org/whl/cpu"]
    subprocess.run([uv, "pip", "install", "--python", str(PY), "torch==2.6.0", "torchaudio==2.6.0", *idx], check=True)
    subprocess.run([uv, "pip", "install", "--python", str(PY), "chatterbox-tts"], check=True)
    print(f"voice cloning installed → {ENV}")


def enroll(name: str, media: Path, start: float | None = None, duration: float | None = None) -> Path:
    """Save a clean voice sample: mono, 24 kHz, low rumble cut, light denoise, steady level."""
    out = voices_dir() / f"{name}.wav"
    cut = (["-ss", f"{start:.2f}"] if start is not None else []) + (["-t", f"{duration:.2f}"] if duration else ["-t", "30"])
    subprocess.run(["ffmpeg", "-v", "error", "-y", *cut, "-i", str(media), "-vn",
                    "-af", "highpass=f=80,afftdn=nf=-25,loudnorm=I=-18:TP=-2", "-ac", "1", "-ar", "24000", str(out)],
                   check=True)
    return out


def speak(name: str, text: str, out: Path, model: str = "turbo") -> Path:
    """`text` in the voice called `name`, written to `out` (48 kHz wav)."""
    ref = voices_dir() / f"{name}.wav"
    if not ref.exists():
        raise FileNotFoundError(f"no voice called {name!r}: enroll a sample first ({voices_dir()})")
    if not installed():
        raise RuntimeError("voice cloning isn't installed yet: python helpers/voice_clone.py install")
    out.parent.mkdir(parents=True, exist_ok=True)
    txt = out.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    raw = out.with_name(out.stem + "_24k.wav")
    subprocess.run([str(PY), str(HELPERS / "voice_worker.py"), str(ref), str(txt), str(raw), model], check=True,
                   env={**os.environ, "PYTHONUTF8": "1", "TQDM_DISABLE": "1"})
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-ar", "48000", "-ac", "2", str(out)], check=True)
    raw.unlink(missing_ok=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Free voice cloning (Chatterbox)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install")
    sub.add_parser("list")
    e = sub.add_parser("enroll")
    e.add_argument("name")
    e.add_argument("media", type=Path)
    e.add_argument("--start", type=float)
    e.add_argument("--duration", type=float)
    s = sub.add_parser("speak")
    s.add_argument("name")
    s.add_argument("script", type=Path, help="a text file with what to say")
    s.add_argument("out", type=Path)
    s.add_argument("--model", default="turbo", choices=["turbo", "original"])
    a = ap.parse_args()
    if a.cmd == "install":
        install()
    elif a.cmd == "list":
        print("\n".join(p.stem for p in sorted(voices_dir().glob("*.wav"))) or "(no voices yet)")
    elif a.cmd == "enroll":
        print(f"saved → {enroll(a.name, a.media, a.start, a.duration)}")
    else:
        print(f"saved → {speak(a.name, a.script.read_text(encoding='utf-8'), a.out, a.model)}")


if __name__ == "__main__":
    main()
