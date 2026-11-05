"""Stickers and emoji: small animated pictures over the video.

plan.json:
    "stickers": [
      {"emoji": "🔥", "start_in_output": 3.2, "duration": 1.2, "x": 0.8, "y": 0.2, "size": 0.2, "anim": "pop"},
      {"file": "logo.png", "start_in_output": 0, "duration": 5, "x": 0.85, "y": 0.1, "size": 0.15, "anim": "none"}
    ],
    "captions": {"emoji": true}     # also drop a matching emoji above the captions, automatically

x / y = centre as fractions of the frame; size = width as a fraction of the frame width.
anim: pop | bounce | float | spin | wiggle | pulse | slide | fade | none.
Colour emoji come from the system's emoji font, drawn here with Pillow (FFmpeg's text tools
can't draw colour emoji). Each sticker is rendered once to an alpha clip and cached in animations/.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

EMOJI_FONTS = ["C:/Windows/Fonts/seguiemj.ttf", "/System/Library/Fonts/Apple Color Emoji.ttc",
               "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"]
ANIMS = ("pop", "bounce", "float", "spin", "wiggle", "pulse", "slide", "fade", "none")

# Spoken word -> emoji, for captions "emoji": true. Only words a viewer would picture.
EMOJI_WORDS = {
    "money": "💰", "cash": "💰", "dollars": "💵", "dollar": "💵", "rich": "🤑", "paid": "💸", "free": "🎁",
    "fire": "🔥", "hot": "🔥", "best": "🏆", "win": "🏆", "winning": "🏆", "won": "🏆", "champion": "🏆",
    "love": "❤️", "heart": "❤️", "happy": "😄", "funny": "😂", "laugh": "😂", "sad": "😢", "crazy": "🤯",
    "insane": "🤯", "shocking": "😱", "scary": "😱", "secret": "🤫", "quiet": "🤫", "think": "🤔", "idea": "💡",
    "ideas": "💡", "smart": "🧠", "brain": "🧠", "learn": "📚", "book": "📖", "time": "⏰", "hour": "⏰",
    "hours": "⏰", "minutes": "⏱️", "fast": "⚡", "quick": "⚡", "speed": "⚡", "rocket": "🚀", "growth": "📈",
    "grow": "📈", "up": None, "down": None, "goal": "🎯", "target": "🎯", "focus": "🎯", "strong": "💪",
    "strength": "💪", "work": "💼", "business": "💼", "phone": "📱", "video": "🎬", "camera": "📷", "music": "🎵",
    "food": "🍔", "eat": "🍽️", "coffee": "☕", "car": "🚗", "house": "🏠", "home": "🏠", "world": "🌍",
    "star": "⭐", "stars": "⭐", "new": "✨", "magic": "✨", "wrong": "❌", "mistake": "❌", "mistakes": "❌",
    "stop": "🛑", "never": "🚫", "right": None, "yes": "✅", "correct": "✅", "warning": "⚠️", "danger": "⚠️",
    "football": "🏈", "basketball": "🏀", "soccer": "⚽", "game": "🎮", "ai": "🤖", "robot": "🤖", "computer": "💻",
    "code": "💻", "question": "❓", "why": "🤔", "look": "👀", "watch": "👀", "see": None, "dead": "💀",
}


def emoji_image(emoji: str, px: int) -> Image.Image:
    """A colour emoji on a transparent square, `px` wide."""
    for path in EMOJI_FONTS:
        if Path(path).exists():
            # Bitmap colour fonts only come in fixed sizes; draw large and shrink for clean edges.
            for size in (256, 137, 109):
                try:
                    font = ImageFont.truetype(path, size)
                    break
                except OSError:
                    font = None
            if font is None:
                continue
            canvas = int(size * 1.6)
            im = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
            ImageDraw.Draw(im).text((canvas // 2, canvas // 2), emoji, font=font, embedded_color=True, anchor="mm")
            box = im.getbbox()
            if box:
                im = im.crop(box)
                side = max(im.size)
                sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
                sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
                return sq.resize((px, px), Image.LANCZOS)
    raise RuntimeError("no colour emoji font on this computer")


def _frame_state(anim: str, t: float, dur: float) -> tuple[float, float, float, float, float]:
    """(scale, angle, dx, dy, alpha) at time t. dx/dy are fractions of the sticker's size."""
    in_t = min(1.0, t / 0.18)
    out_t = min(1.0, (dur - t) / 0.12)
    alpha = min(1.0, out_t * 1.0 if anim != "none" else 1.0)
    if anim == "none":
        return 1, 0, 0, 0, 1
    if anim == "fade":
        return 1, 0, 0, 0, min(1.0, t / 0.25, (dur - t) / 0.25)
    if anim == "pop":
        over = 1 + 0.25 * math.sin(in_t * math.pi)  # overshoot, then settle
        return in_t * over, 0, 0, 0, alpha
    if anim == "bounce":
        return 1, 0, 0, -abs(math.sin(t * 6.5)) * 0.28 * math.exp(-t * 0.6), alpha
    if anim == "float":
        return min(1, in_t * 1.1), 0, 0, -0.18 * math.sin(t * 2.4), alpha
    if anim == "spin":
        return in_t, (1 - in_t) * -360 + 10 * math.sin(t * 4), 0, 0, alpha
    if anim == "wiggle":
        return min(1, in_t * 1.1), 14 * math.sin(t * 14), 0, 0, alpha
    if anim == "pulse":
        return min(1, in_t) * (1 + 0.1 * math.sin(t * 9)), 0, 0, 0, alpha
    if anim == "slide":
        return 1, 0, (1 - in_t) ** 2 * 2.2, 0, alpha
    return 1, 0, 0, 0, alpha


def render_sticker(s: dict, out_dir: Path, W: int, H: int, fps: str, base_dir: Path) -> dict | None:
    dur = float(s.get("duration", 1.5))
    px = max(16, int(W * float(s.get("size", 0.2))))
    anim = s.get("anim", "pop")
    key = hashlib.sha1(json.dumps([s, W, H, fps], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:10]
    # Room around the sticker for overshoot, swing and bounce.
    side = int(px * (3.2 if anim == "slide" else 1.8)) // 2 * 2
    out = out_dir / f"sticker_{key}.mov"
    cx, cy = float(s.get("x", 0.5)) * W, float(s.get("y", 0.2)) * H
    place = {"file": str(out), "start_in_output": float(s.get("start_in_output", 0)), "duration": dur,
             "x": int(cx - side / 2), "y": int(cy - side / 2)}
    if out.exists():
        return place
    if s.get("emoji"):
        art = emoji_image(s["emoji"], px)
    else:
        p = Path(s.get("file", ""))
        p = p if p.is_absolute() else base_dir / p
        if not p.exists():
            print(f"  sticker: missing {s.get('file')}")
            return None
        art = Image.open(p).convert("RGBA")
        art = art.resize((px, max(1, int(art.height * px / art.width))), Image.LANCZOS)
    num, _, den = fps.partition("/")
    rate = float(num) / float(den or 1)
    writer = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                               "-s", f"{side}x{side}", "-r", fps, "-i", "-", "-c:v", "qtrle", "-pix_fmt", "argb",
                               str(out)], stdin=subprocess.PIPE)
    for i in range(max(1, int(round(dur * rate)))):
        scale, angle, dx, dy, alpha = _frame_state(anim, i / rate, dur)
        frame = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        w, h = max(1, int(art.width * scale)), max(1, int(art.height * scale))
        piece = art.resize((w, h), Image.BILINEAR)
        if angle:
            piece = piece.rotate(angle, expand=True, resample=Image.BILINEAR)
        if alpha < 1:
            piece.putalpha(piece.getchannel("A").point(lambda v, a=alpha: int(v * a)))
        frame.alpha_composite(piece, (max(0, (side - piece.width) // 2 + int(dx * px)),
                                      max(0, (side - piece.height) // 2 + int(dy * px)))) \
            if piece.width <= side and piece.height <= side else None
        writer.stdin.write(frame.tobytes())
    writer.stdin.close()
    writer.wait()
    return place


def auto_emoji(plan: dict, edit_dir: Path, W: int, H: int, min_gap: float = 2.5) -> list[dict]:
    """A matching emoji just above the captions when a picture-worthy word is spoken."""
    import captions
    brand = captions.load_brand(edit_dir)
    s = captions.settings(plan, brand)
    if not s.get("emoji"):
        return []
    words = captions.output_words(plan, edit_dir, s.get("corrections") or [])
    writer = captions.Writer(brand, s, W, H)
    writer.prepare_spots(plan, edit_dir)
    out, last = [], -99.0
    for w in words:
        e = EMOJI_WORDS.get(re.sub(r"[^a-z]", "", w["text"].lower()))
        if e and w["start"] - last >= min_gap:
            cap_y = writer.y_at(w["start"])
            # Captions in the Best spot sit just under the chin, so an emoji above them would land
            # on the face: put it underneath instead, as long as that clears the apps' own text.
            below = cap_y + writer.size * 1.75
            y = below if writer.spots and below < H * 0.84 else cap_y - writer.size * 1.75
            if y < H * 0.05:
                y = below
            out.append({"emoji": e, "start_in_output": round(w["start"], 2), "duration": 1.0,
                        "x": round(writer.x / W, 4) if writer.spots else 0.5, "y": round(y / H, 4),
                        "size": 0.13, "anim": "pop"})
            last = w["start"]
    return out


def build_sticker_overlays(plan: dict, edit_dir: Path, W: int, H: int, fps: str) -> list[dict]:
    items = list(plan.get("stickers") or []) + auto_emoji(plan, edit_dir, W, H)
    if not items:
        return []
    out_dir = edit_dir / "animations"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for s in items:
        ov = render_sticker(s, out_dir, W, H, fps, edit_dir.parent)
        if ov:
            out.append(ov)
    print(f"stickers → {len(out)}")
    return out
