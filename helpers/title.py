"""Hook title, optionally *behind* the speaker (text sits between background and person).

plan.json:
    "title": {"text": "HOW TO FIX THE TEXANS", "start_in_output": 0.0, "duration": 2.5,
              "behind_subject": true, "font": "serif", "color": "text",
              "position": "upper", "size": 150}

font: serif | sans (brand fonts). color: a brand colour name (text, accent, primary,
pill_text) or "#RRGGBB". position: upper | middle. size: px at 1080 wide.

Builds <edit>/animations/title_<hash>.mov (RGBA) for the window, then render.py
overlays it like any animation. With behind_subject, each frame's alpha is cut
where MediaPipe's selfie segmenter sees the person, so the speaker stays in front.
Zooms active during the title are applied first, so the cut-out lines up.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import framing
from captions import load_brand

POSITIONS = {"upper": 0.27, "middle": 0.45}


def _color(brand: dict, name: str) -> str:
    return name if name.startswith("#") else (brand.get("colors") or {}).get(name, "#FFFFFF")


def title_image(t: dict, brand: dict, W: int, H: int) -> Image.Image:
    """Full-frame RGBA layer with the title text (wrapped, centred, soft shadow)."""
    fonts = brand.get("fonts") or {}
    font_spec = fonts.get(t.get("font", "serif")) or {}
    size = int(t.get("size", 150) * W / 1080)
    try:
        import fonts
        font = ImageFont.truetype(fonts.path(font_spec.get("file") or "georgiaz.ttf"), size)
    except OSError:
        font = ImageFont.load_default(size=size)
    text = t["text"].upper() if t.get("uppercase", True) else t["text"]

    # Greedy wrap to 88% of the width.
    words, lines = text.split(), [""]
    for w in words:
        trial = (lines[-1] + " " + w).strip()
        if font.getlength(trial) <= W * 0.88 or not lines[-1]:
            lines[-1] = trial
        else:
            lines.append(w)
    line_h = int(size * 1.02)
    y0 = int(H * POSITIONS.get(t.get("position", "upper"), 0.27) - line_h * len(lines) / 2)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(layer), ImageDraw.Draw(shadow)
    fill = _color(brand, t.get("color", "text"))
    for i, ln in enumerate(lines):
        x = (W - font.getlength(ln)) / 2
        y = y0 + i * line_h
        ds.text((x + size * 0.03, y + size * 0.04), ln, font=font, fill=(0, 0, 0, 150))
        d.text((x, y), ln, font=font, fill=fill)
    shadow = shadow.filter(ImageFilter.GaussianBlur(size * 0.04))
    return Image.alpha_composite(shadow, layer)


def build_title_overlay(plan: dict, edit_dir: Path, base: Path) -> dict | None:
    t = plan.get("title") or {}
    if not t.get("text"):
        return None
    brand = load_brand(edit_dir)
    W, H = framing.probe_size(base)
    fps = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=r_frame_rate", "-of", "default=nw=1:nk=1", str(base)],
                         capture_output=True, text=True).stdout.strip() or "30/1"
    a = float(t.get("start_in_output", 0.0))
    dur = float(t.get("duration", 2.5))

    key = hashlib.sha1(json.dumps([t, brand.get("colors"), brand.get("fonts"), plan.get("zooms"),
                                   plan.get("ranges"), W, H], sort_keys=True).encode()).hexdigest()[:10]
    out = edit_dir / "animations" / f"title_{key}.mov"
    if out.exists():
        return {"file": str(out), "start_in_output": a, "duration": dur}
    out.parent.mkdir(parents=True, exist_ok=True)

    layer = np.asarray(title_image(t, brand, W, H), dtype=np.float32)
    text_alpha = layer[..., 3:4] / 255.0

    # Frames of the (zoomed) base in the title window, for the person mask.
    zoom = framing.zoom_filter(plan, edit_dir, W, H, fps)
    vf = (f"{zoom}," if zoom else "") + f"trim=start={a:.3f}:duration={dur:.3f},setpts=PTS-STARTPTS"
    behind = t.get("behind_subject", True)
    reader = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(base), "-vf", vf, "-f", "rawvideo",
                               "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE) if behind else None
    writer = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                               "-s", f"{W}x{H}", "-r", fps, "-i", "-", "-c:v", "qtrle", "-pix_fmt", "argb",
                               str(out)], stdin=subprocess.PIPE)
    num, _, den = fps.partition("/")
    n_frames = max(1, int(round(dur * float(num) / float(den or 1))))
    fade = max(1, int(n_frames * 0.12))
    for i in range(n_frames):
        alpha = text_alpha
        if behind:
            raw = reader.stdout.read(W * H * 3)
            if len(raw) == W * H * 3:
                frame = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
                small = np.asarray(Image.fromarray(frame).resize((W // 2, H // 2)))
                m = framing.person_mask(small)
                m = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR)
                               .filter(ImageFilter.GaussianBlur(2)), dtype=np.float32)[..., None] / 255.0
                alpha = text_alpha * (1.0 - m)
        ramp = min(1.0, (i + 1) / fade, (n_frames - i) / fade)  # fade in and out
        rgba = np.concatenate([layer[..., :3], alpha * 255.0 * ramp], axis=2).astype(np.uint8)
        writer.stdin.write(rgba.tobytes())
    writer.stdin.close()
    writer.wait()
    if reader:
        reader.stdout.close()
        reader.wait()
    print(f"title → {out.name} ({n_frames} frames{', behind speaker' if behind else ''})")
    return {"file": str(out), "start_in_output": a, "duration": dur}
