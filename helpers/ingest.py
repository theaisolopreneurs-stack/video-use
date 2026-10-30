"""One-command ingest for a footage folder.

Runs everything the editor needs before it plans a cut:

1. Inventory: ffprobe every source (duration, resolution, fps, rotation, audio tracks).
2. Transcribe: transcribe_batch (local faster-whisper by default). Cached per file.
3. Pack: pack_transcripts -> <edit>/takes_packed.md.
4. Contact sheets: a grid of evenly spaced, timestamped frames per source
   -> <edit>/storyboard/contact_<stem>.jpg, so the editor (and the Review Studio)
   can see each clip at a glance.
5. Writes <edit>/inventory.md summarising all of it.

Usage:
    python helpers/ingest.py <videos_dir>
    python helpers/ingest.py <videos_dir> --model base.en --frames 12
    python helpers/ingest.py <videos_dir> --skip-transcribe
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from pack_transcripts import format_duration
from transcribe import DEFAULT_MODEL
from transcribe_batch import find_videos

HELPERS = Path(__file__).resolve().parent
THUMB_W = 240


def probe(video: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(video)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout
    data = json.loads(out)
    v = next((s for s in data["streams"] if s["codec_type"] == "video"), {})
    audio = [s for s in data["streams"] if s["codec_type"] == "audio"]
    rotation = 0
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    w, h = v.get("width", 0), v.get("height", 0)
    if abs(rotation) in (90, 270):
        w, h = h, w
    num, _, den = v.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if float(den or 0) else 0.0
    return {
        "name": video.name,
        "duration": float(data["format"].get("duration", 0.0)),
        "width": w,
        "height": h,
        "fps": round(fps, 2),
        "rotation": rotation,
        "audio_tracks": len(audio),
    }


def contact_sheet(video: Path, duration: float, out_path: Path, n_frames: int, cols: int = 6) -> None:
    """Grab n evenly spaced frames and tile them with timestamps."""
    times = [duration * (i + 0.5) / n_frames for i in range(n_frames)]
    thumbs: list[Image.Image] = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(times):
            frame = Path(tmp) / f"{i:03d}.jpg"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
                 "-frames:v", "1", "-vf", f"scale={THUMB_W}:-2", str(frame)],
                check=True,
            )
            thumbs.append(Image.open(frame).convert("RGB"))

    tw, th = thumbs[0].size
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (tw * cols, th * rows), "black")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=18)
    for i, (img, t) in enumerate(zip(thumbs, times)):
        x, y = (i % cols) * tw, (i // cols) * th
        sheet.paste(img, (x, y))
        label = f"{int(t // 60)}:{t % 60:05.2f}"
        draw.rectangle([x, y, x + 86, y + 24], fill="black")
        draw.text((x + 4, y + 2), label, fill="yellow", font=font)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=85)


def run_helper(script: str, *args: str) -> None:
    # Helpers print "→"; on Windows a piped stdout defaults to cp1252 and crashes on it.
    subprocess.run([sys.executable, str(HELPERS / script), *args], check=True,
                   env={**os.environ, "PYTHONUTF8": "1"})


def main() -> None:
    ap = argparse.ArgumentParser(description="Inventory, transcribe, pack, and contact-sheet a footage folder")
    ap.add_argument("videos_dir", type=Path)
    ap.add_argument("--edit-dir", type=Path, default=None, help="default: <videos_dir>/edit")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"faster-whisper model (default {DEFAULT_MODEL})")
    ap.add_argument("--language", default=None)
    ap.add_argument("--frames", type=int, default=12, help="frames per contact sheet (default 12)")
    ap.add_argument("--skip-transcribe", action="store_true")
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    edit_dir = (args.edit_dir or videos_dir / "edit").resolve()
    videos = find_videos(videos_dir)
    if not videos:
        sys.exit(f"no videos found in {videos_dir}")

    print(f"1/4 inventory: {len(videos)} source(s)")
    infos = [probe(v) for v in videos]

    if not args.skip_transcribe:
        print("2/4 transcribing")
        extra = ["--language", args.language] if args.language else []
        run_helper("transcribe_batch.py", str(videos_dir), "--edit-dir", str(edit_dir),
                   "--model", args.model, *extra)
        print("3/4 packing transcripts")
        run_helper("pack_transcripts.py", "--edit-dir", str(edit_dir))

    print("4/4 contact sheets")
    for video, info in zip(videos, infos):
        out = edit_dir / "storyboard" / f"contact_{video.stem}.jpg"
        if not out.exists():
            contact_sheet(video, info["duration"], out, args.frames)
        info["contact_sheet"] = out.relative_to(edit_dir).as_posix()
        tpath = edit_dir / "transcripts" / f"{video.stem}.json"
        if tpath.exists():
            words = [w for w in json.loads(tpath.read_text(encoding="utf-8")).get("words", [])
                     if w.get("type") == "word"]
            info["words"] = len(words)

    lines = ["# Inventory", "",
             f"{len(infos)} source(s), {format_duration(sum(i['duration'] for i in infos))} total.", "",
             "| Source | Length | Size | FPS | Audio tracks | Words | Contact sheet |",
             "|---|---|---|---|---|---|---|"]
    for i in infos:
        lines.append(f"| {i['name']} | {format_duration(i['duration'])} | {i['width']}x{i['height']} "
                     f"| {i['fps']} | {i['audio_tracks']} | {i.get('words', '-')} | {i['contact_sheet']} |")
    (edit_dir / "inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (edit_dir / "inventory.json").write_text(json.dumps(infos, indent=2), encoding="utf-8")
    print(f"\ndone -> {edit_dir / 'inventory.md'}")


if __name__ == "__main__":
    main()
