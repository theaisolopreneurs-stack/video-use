"""Remove or replace the background behind the speaker, no green screen needed.

plan.json, for the whole video or one section:
    "background": "blur"
    "background": {"mode": "color", "color": "#10231C"}
    "background": {"mode": "image", "file": "office.jpg"}
    "background": {"mode": "video", "file": "beach.mp4"}
    "background": {"mode": "blur", "amount": 30}

Works frame by frame with the same person cut-out that puts hook titles behind the speaker
(MediaPipe's selfie model). It's built for a person facing the camera: objects they hold can
get cut off, and it's the slowest thing in the editor, so try it on one section first.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import framing


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _reader(path: Path, W: int, H: int, loop: bool = False, image: bool = False) -> subprocess.Popen:
    src = ["-loop", "1", "-i", str(path)] if image else (["-stream_loop", "-1"] if loop else []) + ["-i", str(path)]
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    return subprocess.Popen(["ffmpeg", "-v", "error", *src, "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                            stdout=subprocess.PIPE)


def _resolve(file: str, edit_dir: Path) -> Path:
    p = Path(file)
    if p.is_absolute():
        return p
    return next((c for c in (edit_dir.parent / file, edit_dir / file) if c.exists()), edit_dir.parent / file)


def replace_background(seg: Path, spec, edit_dir: Path, draft: bool = False) -> None:
    """Rewrite the section's clip in place with the new background; sound stays as it was."""
    spec = {"mode": spec} if isinstance(spec, str) else dict(spec)
    mode = spec.get("mode", "blur")
    W, H = framing.probe_size(seg)
    fps = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=r_frame_rate", "-of", "default=nw=1:nk=1", str(seg)],
                         capture_output=True, text=True).stdout.strip() or "30/1"
    tmp = seg.with_name(seg.stem + "_bg.mp4")
    reader = _reader(seg, W, H)
    bg_reader = None
    solid = None
    if mode == "color":
        solid = np.full((H, W, 3), _rgb(spec.get("color", "#000000")), dtype=np.float32)
    elif mode in ("image", "video"):
        bg = _resolve(spec.get("file", ""), edit_dir)
        if not bg.exists():
            print(f"  background: missing {spec.get('file')}, leaving this section as it is")
            reader.stdout.close()
            reader.wait()
            return
        bg_reader = _reader(bg, W, H, loop=mode == "video", image=mode == "image")
    writer = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", fps, "-i", "-",
         "-i", str(seg), "-map", "0:v", "-map", "1:a?", "-c:a", "copy", "-c:v", "libx264",
         "-preset", "ultrafast" if draft else "fast", "-crf", "28" if draft else "20", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(tmp)], stdin=subprocess.PIPE)
    amount = float(spec.get("amount", 26)) / 4
    small = (max(64, W // 2), max(64, H // 2))
    last = None
    n = 0
    while True:
        raw = reader.stdout.read(W * H * 3)
        if len(raw) < W * H * 3:
            break
        frame = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
        m = framing.person_mask(np.asarray(Image.fromarray(frame).resize(small)))
        last = m if last is None else last * 0.45 + m * 0.55  # steadies flicker at the edges
        mask = np.asarray(Image.fromarray((np.clip((last - 0.35) / 0.4, 0, 1) * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR)
                          .filter(ImageFilter.GaussianBlur(2)), dtype=np.float32)[..., None] / 255.0
        if mode == "blur":
            lo = Image.fromarray(frame).resize((W // 4, H // 4), Image.BILINEAR).filter(ImageFilter.GaussianBlur(amount))
            back = np.asarray(lo.resize((W, H), Image.BILINEAR), dtype=np.float32)
        elif solid is not None:
            back = solid
        else:
            b = bg_reader.stdout.read(W * H * 3)
            if len(b) == W * H * 3:
                solid_frame = np.frombuffer(b, np.uint8).reshape(H, W, 3).astype(np.float32)
                back = solid_frame
                if mode == "image":  # one still is enough
                    solid = solid_frame
                    bg_reader.stdout.close()
                    bg_reader.kill()
                    bg_reader = None
            else:
                back = np.zeros((H, W, 3), np.float32)
        writer.stdin.write((frame * mask + back * (1 - mask)).astype(np.uint8).tobytes())
        n += 1
    writer.stdin.close()
    writer.wait()
    reader.stdout.close()
    reader.wait()
    if bg_reader:
        bg_reader.stdout.close()
        bg_reader.kill()
    tmp.replace(seg)
    print(f"        background: {mode} ({n} frames)")
