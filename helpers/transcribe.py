"""Transcribe a video with word-level timestamps.

Extracts mono 16kHz audio via ffmpeg, then transcribes it with one of two engines
and writes a Scribe-shaped JSON to <edit_dir>/transcripts/<video_stem>.json:

- local (default): faster-whisper on this machine. Free, offline. No speaker
  diarization or audio-event tags; every word is speaker_0.
- scribe: ElevenLabs Scribe (paid, needs ELEVENLABS_API_KEY). Adds diarization
  and audio events.

Both produce `words` entries of type 'word' / 'spacing' with start/end in
seconds, so pack_transcripts.py, render.py and timeline_view.py read either.

Cached: if the output file already exists, transcription is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --model small.en
    python helpers/transcribe.py <video_path> --engine scribe --num-speakers 2
"""

from __future__ import annotations

import argparse
import array
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import requests


SCRIBE_URL = "https://api.elevenlabs.io/v1/speech-to-text"

DEFAULT_MODEL = "small.en"

# Whisper tends to tidy speech into clean prose and drop "um"/"uh". A disfluent
# prompt nudges it to keep them, which the editor needs to find filler cuts.
FILLER_PROMPT = "Umm, let me think, like, hmm... Okay, uh, so here's what I'm, you know, thinking."

_models: dict[str, object] = {}
_models_lock = threading.Lock()


def load_api_key() -> str:
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "ELEVENLABS_API_KEY":
                    return v.strip().strip('"').strip("'")
    v = os.environ.get("ELEVENLABS_API_KEY", "")
    if not v:
        sys.exit("ELEVENLABS_API_KEY not found in .env or environment")
    return v


def count_audio_tracks(video_path: Path) -> int:
    """How many audio streams the container holds."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    )
    return len([ln for ln in out.stdout.splitlines() if ln.strip()])


def peak_dbfs(wav_path: Path) -> float:
    """Peak level of a 16-bit PCM wav, in dBFS. -inf for digital silence."""
    peak = 0
    with wave.open(str(wav_path), "rb") as w:
        # A chunk at a time: batch mode runs several of these at once, and a two-hour
        # take is 230 MB of 16 kHz mono before the array copy doubles it.
        while frames := w.readframes(1 << 16):
            samples = array.array("h", frames)
            peak = max(peak, max(samples), -min(samples))
    return 20 * math.log10(peak / 32768) if peak > 0 else float("-inf")


def extract_audio(video_path: Path, dest: Path, audio_track: int = 0) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-map", f"0:a:{audio_track}",
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def call_scribe(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
) -> dict:
    data: dict[str, str] = {
        "model_id": "scribe_v1",
        "diarize": "true",
        "tag_audio_events": "true",
        "timestamps_granularity": "word",
    }
    if language:
        data["language_code"] = language
    if num_speakers:
        data["num_speakers"] = str(num_speakers)

    with open(audio_path, "rb") as f:
        resp = requests.post(
            SCRIBE_URL,
            headers={"xi-api-key": api_key},
            files={"file": (audio_path.name, f, "audio/wav")},
            data=data,
            timeout=1800,
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Scribe returned {resp.status_code}: {resp.text[:500]}")

    return resp.json()


def load_local_model(model_name: str):
    """Load a faster-whisper model once per process; batch workers share it."""
    with _models_lock:
        if model_name not in _models:
            from faster_whisper import WhisperModel
            _models[model_name] = WhisperModel(
                model_name, device="cpu", compute_type="int8",
                cpu_threads=os.cpu_count() or 4,
            )
        return _models[model_name]


def call_local(audio_path: Path, model_name: str, language: str | None = None) -> dict:
    """Transcribe with faster-whisper and return a Scribe-shaped payload."""
    import numpy as np

    # Hand faster-whisper raw samples; its own PyAV decoder breaks on current PyAV.
    with wave.open(str(audio_path), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    audio = pcm.astype(np.float32) / 32768.0

    model = load_local_model(model_name)
    if model_name.endswith(".en"):
        language = "en"
    segments, info = model.transcribe(
        audio,
        language=language,
        word_timestamps=True,
        initial_prompt=FILLER_PROMPT,
        condition_on_previous_text=False,
        vad_filter=False,
    )

    words: list[dict] = []
    prev_end: float | None = None
    for seg in segments:
        for wd in seg.words or []:
            text = wd.word.strip()
            if not text:
                continue
            start, end = round(float(wd.start), 3), round(float(wd.end), 3)
            if prev_end is not None and start > prev_end:
                words.append({"text": " ", "start": prev_end, "end": start,
                              "type": "spacing", "speaker_id": "speaker_0"})
            words.append({"text": text, "start": start, "end": end, "type": "word",
                          "speaker_id": "speaker_0",
                          "logprob": round(float(np.log(max(wd.probability, 1e-6))), 4)})
            prev_end = end

    return {
        "language_code": info.language,
        "language_probability": round(float(info.language_probability), 4),
        "text": " ".join(w["text"] for w in words if w["type"] == "word"),
        "words": words,
        "engine": f"faster-whisper/{model_name}",
    }


def transcript_path(edit_dir: Path, video: Path, audio_track: int = 0) -> Path:
    """Where a video's transcript lands.

    The track belongs in the name, or a rerun with --audio-track hands back the transcript of
    the track it is meant to replace. Track 0 keeps the plain name, so transcripts made before
    the flag existed stay valid. Batch mode tests its cache with this too — one function, so
    the two cannot drift apart.
    """
    suffix = "" if audio_track == 0 else f".track{audio_track}"
    return edit_dir / "transcripts" / f"{video.stem}{suffix}.json"


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str | None = None,
    language: str | None = None,
    num_speakers: int | None = None,
    verbose: bool = True,
    audio_track: int = 0,
    engine: str = "local",
    model: str = DEFAULT_MODEL,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns existing path immediately if the transcript already exists.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcript_path(edit_dir, video, audio_track)

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    if verbose:
        print(f"  extracting audio from {video.name}", flush=True)

    n_tracks = count_audio_tracks(video)
    if n_tracks > 1 and verbose:
        print(f"  note: {video.name} has {n_tracks} audio tracks, using track "
              f"{audio_track + 1} (--audio-track to change)", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.wav"
        extract_audio(video, audio, audio_track)

        # A silent track returns nothing (and costs money on Scribe), so catch
        # the wrong-track case before transcribing.
        peak = peak_dbfs(audio)
        if peak < -60.0:
            raise RuntimeError(
                f"track {audio_track + 1} of {video.name} is silent "
                f"(peak {peak:.1f} dBFS) - not transcribing. "
                + (f"The file has {n_tracks} audio tracks; try --audio-track "
                   + " or ".join(str(i) for i in range(n_tracks) if i != audio_track) + "."
                   if n_tracks > 1 else "Check the source audio.")
            )

        if engine == "scribe":
            size_mb = audio.stat().st_size / (1024 * 1024)
            if verbose:
                print(f"  uploading {video.stem}.wav ({size_mb:.1f} MB)", flush=True)
            payload = call_scribe(audio, api_key or load_api_key(), language, num_speakers)
        else:
            if verbose:
                print(f"  transcribing locally with {model}", flush=True)
            payload = call_local(audio, model, language)

    out_path.write_text(json.dumps(payload, indent=2))
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        if isinstance(payload, dict) and "words" in payload:
            print(f"    words: {len(payload['words'])}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with word-level timestamps")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument("--engine", choices=["local", "scribe"], default="local",
                    help="local = faster-whisper, free (default). scribe = ElevenLabs, paid.")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"faster-whisper model for --engine local (default {DEFAULT_MODEL}; "
                         "base.en is faster, medium.en more accurate).")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <video_parent>/edit)",
    )
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code (e.g., 'en'). Omit to auto-detect.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Optional number of speakers when known (scribe only). Improves diarization accuracy.",
    )
    ap.add_argument(
        "--audio-track",
        type=int,
        default=0,
        help="Zero-based audio track to transcribe. OBS writes the game on track 0 "
             "and the mic on track 1; without this ffmpeg applies its default audio "
             "stream selection, which picks the track with the most channels.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    api_key = load_api_key() if args.engine == "scribe" else None

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        api_key=api_key,
        language=args.language,
        num_speakers=args.num_speakers,
        audio_track=args.audio_track,
        engine=args.engine,
        model=args.model,
    )


if __name__ == "__main__":
    main()
