"""Phase 0 smoke test: proves the free, local toolchain works end to end.

1. Windows text-to-speech makes a short spoken clip with a long pause in it.
2. FFmpeg turns it into a 1080x1920 test video.
3. faster-whisper transcribes it locally with word-level timestamps.
4. auto-editor cuts the silence.
5. ffprobe reports before/after durations.

Run:  uv run python scripts/smoke_test.py
Output lands in footage/_smoke/ (git-ignored).
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "footage" / "_smoke"

SPEECH = (
    "Hi, this is a quick test of the AI video editor. "
    "<break time='2500ms'/> "
    "If you can read this transcript, local transcription is working."
)


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        sys.exit(f"FAILED: {' '.join(cmd)}\n{result.stderr[-2000:]}")
    return result.stdout


def duration(path: Path) -> float:
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


def make_speech(wav: Path) -> None:
    ssml = ("<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' "
            f"xml:lang='en-US'>{SPEECH}</speak>")
    ps = (
        "$ErrorActionPreference = 'Stop'; "
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{wav}'); "
        f"$s.SpeakSsml(\"{ssml}\"); "
        "$s.Dispose()"
    )
    run(["powershell", "-NoProfile", "-Command", ps])
    if not wav.exists() or wav.stat().st_size == 0:
        sys.exit("FAILED: text-to-speech produced an empty file")


def main() -> None:
    for tool in ("ffmpeg", "ffprobe", "auto-editor"):
        if not shutil.which(tool):
            sys.exit(f"FAILED: {tool} is not on PATH")

    OUT.mkdir(parents=True, exist_ok=True)
    wav = OUT / "speech.wav"
    raw = OUT / "raw.mp4"
    cut = OUT / "raw_cut.mp4"

    print("1/4 Generating speech...")
    make_speech(wav)

    print("2/4 Building test video with FFmpeg...")
    run(["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30",
         "-i", str(wav), "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(raw)])

    print("3/4 Transcribing locally with faster-whisper (first run downloads a small model)...")
    import numpy as np
    from faster_whisper import WhisperModel
    # Decode with FFmpeg rather than faster-whisper's PyAV path, which breaks on newer PyAV.
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(raw), "-ac", "1", "-ar", "16000",
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, dtype=np.float32)
    model = WhisperModel("base.en", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio, word_timestamps=True)
    words = [{"word": w.word.strip(), "start": round(float(w.start), 2), "end": round(float(w.end), 2)}
             for seg in segments for w in seg.words]
    (OUT / "words.json").write_text(json.dumps(words, indent=2))
    print("   " + " ".join(w["word"] for w in words))
    print(f"   {len(words)} words, first: {words[0]}, last: {words[-1]}")

    print("4/4 Cutting silence with auto-editor...")
    run(["auto-editor", str(raw), "--margin", "0.2s", "--no-open", "-o", str(cut)])

    before, after = duration(raw), duration(cut)
    print(f"\nPASS  raw {before:.1f}s -> cut {after:.1f}s (removed {before - after:.1f}s of silence)")
    print(f"Files in {OUT}")


if __name__ == "__main__":
    main()
