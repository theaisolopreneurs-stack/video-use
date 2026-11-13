"""Effects that need to know where YOU are in the picture: things behind you, around your head,
following your face. Phase 2 of the creator-reel effects.

plan.json "effects" entries, like any effect: {"type": ..., "start_in_output": s, "duration": d, ...fields}

    rack_focus    the background blurs while you stay sharp            {amount}
    dolly_zoom    you stay put while the room grows behind you         {amount}
    backdrop      you in front of other footage or a picture           {src, labels: [{text, at}], dim}
    day_dial      a big clock dial spins in behind you                 {text, color}
    post_wall     a wall of posts fills in behind you, a counter climbs {value, label, day}
    sketch_words  hand-drawn words write themselves behind you         {text, paper}
    orbit_ring    words circle your head, behind and in front          {text}
    clone_row     copies of you appear in a row behind you             {count}
    head_stack    labels stack up above your head                      {items}
    live_cursor   a pointer draws a measured box around your face      {label}

Each effect window is rendered frame by frame in Python from the joined cut (the person cut-out
from MediaPipe, the face from BlazeFace, artwork drawn with PIL) into an ordinary video clip that
render.py lays over the picture for that window. Cached by content in <edit>/animations/layer_*.mp4.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import framing

FONTS = Path("C:/Windows/Fonts")


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for f in (FONTS / name, Path(name)):
        try:
            return ImageFont.truetype(str(f), max(6, int(size)))
        except OSError:
            continue
    return ImageFont.load_default()


SERIF, SERIF_I, SANS, SANS_B, MONO, HAND = ("georgia.ttf", "georgiai.ttf", "segoeui.ttf", "segoeuib.ttf",
                                            "consolab.ttf", "Inkfree.ttf")

# What the Studio offers: label, usual length, example settings, editable fields.
KIT = {
    "rack_focus": ("Rack focus (background blurs)", 2.0, {"amount": 1.0}, {"amount": "number"}),
    "dolly_zoom": ("Dolly zoom (room grows behind you)", 2.0, {"amount": 1.0}, {"amount": "number"}),
    "backdrop": ("Video backdrop (you in front)", 3.0, {"src": "", "labels": [{"text": "01  The raw take", "at": 0.3}], "dim": 0.15},
                 {"src": "text", "labels": "lines"}),
    "day_dial": ("Day dial (clock spins behind you)", 2.2, {"text": "day"}, {"text": "text"}),
    "post_wall": ("Post wall (posts fill in behind you)", 2.5, {"value": 1711, "label": "posts", "day": 564},
                  {"value": "number", "label": "text", "day": "number"}),
    "sketch_words": ("Sketch words (hand-drawn behind you)", 2.5, {"text": "sketch words", "paper": True}, {"text": "text"}),
    "orbit_ring": ("Orbit ring (words circle your head)", 2.5, {"text": "ORBIT RING"}, {"text": "text"}),
    "clone_row": ("Clone row (copies of you)", 2.2, {"count": 4}, {"count": "number"}),
    "head_stack": ("Head stack (labels above your head)", 2.5, {"items": ["Idea", "Script", "Shoot", "Edit", "Post"]}, {"items": "lines"}),
    "live_cursor": ("Live cursor (box around your face)", 2.5, {"label": "Claude"}, {"label": "text"}),
}
FX = set(KIT)


def kit() -> list[dict]:
    return [{"id": k, "label": v[0], "duration": v[1], "defaults": v[2], "fields": v[3]} for k, v in KIT.items()]


# ---- small helpers ------------------------------------------------------------------------------

def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def envelope(t: float, dur: float, fin: float = 0.35, fout: float = 0.3) -> float:
    """0 -> 1 over the first `fin` seconds, back to 0 over the last `fout`."""
    return _ease(t / fin) * _ease((dur - t) / fout)


def comp(bg: np.ndarray, fg: np.ndarray, a) -> np.ndarray:
    """bg where a=0, fg where a=1 (a: a number, or a per-pixel map). OpenCV's blend: ~4x quicker."""
    if not isinstance(a, np.ndarray):
        return bg if a <= 0 else (fg if a >= 1 else cv2.addWeighted(bg, 1 - a, fg, a, 0))
    if a.ndim == 3:
        a = a[..., 0]
    a = np.ascontiguousarray(a, dtype=np.float32)
    bg = np.ascontiguousarray(bg, dtype=np.float32)
    fg = np.ascontiguousarray(np.broadcast_to(fg, bg.shape), dtype=np.float32)
    return cv2.blendLinear(bg, fg, 1.0 - a, a)


def over(img: np.ndarray, layer: Image.Image) -> np.ndarray:
    """An RGBA PIL layer drawn over a float RGB frame (only where the layer has something)."""
    box = layer.getchannel("A").getbbox()
    if not box:
        return img
    x0, y0, x1, y1 = box
    la = np.asarray(layer.crop(box), dtype=np.float32)
    out = img.copy()
    out[y0:y1, x0:x1] = comp(img[y0:y1, x0:x1], la[..., :3], la[..., 3] / 255.0)
    return out


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _zoom(img: np.ndarray, s: float, cx: float, cy: float) -> np.ndarray:
    """Scale an image by s about (cx, cy) (fractions), same size out."""
    H, W = img.shape[:2]
    M = np.float32([[s, 0, (1 - s) * cx * W], [0, s, (1 - s) * cy * H]])
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


class Tracker:
    """Person cut-out and face position for each frame, smoothed so nothing jitters."""

    def __init__(self, W: int, H: int):
        self.W, self.H = W, H
        self.small = (max(96, W // 3), max(96, H // 3))
        self.mask = None
        self.face = None

    def update(self, frame: np.ndarray) -> tuple[np.ndarray, tuple[float, float, float]]:
        sm = cv2.resize(frame, self.small, interpolation=cv2.INTER_AREA)
        m = framing.person_mask(sm)
        self.mask = m if self.mask is None else self.mask * 0.4 + m * 0.6
        a = np.clip((self.mask - 0.35) / 0.4, 0, 1)
        a = cv2.GaussianBlur(cv2.resize(a, (self.W, self.H), interpolation=cv2.INTER_LINEAR), (0, 0), 1.5)
        f = self._face(sm)
        if f:
            self.face = f if self.face is None else tuple(o * 0.75 + n * 0.25 for o, n in zip(self.face, f))
        elif self.face is None:
            ys, xs = np.nonzero(a > 0.5)  # no face found: the top of the person cut-out
            self.face = (float(xs.mean() / self.W), float(ys.min() / self.H + 0.08), 0.12) if len(xs) else (0.5, 0.35, 0.12)
        return a.astype(np.float32), self.face

    def _face(self, sm: np.ndarray):
        mp, det = framing._face_detector()
        h, w = sm.shape[:2]
        side = min(w, h)
        best = None
        for y0 in (0, max(0, (h - side) // 3)):  # the top part of a tall frame, where faces are
            tile = np.ascontiguousarray(sm[y0:y0 + side, :side])
            for d in det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=tile)).detections:
                bb = d.bounding_box
                if best is None or bb.height > best[3]:
                    best = (bb.origin_x, bb.origin_y + y0, bb.width, bb.height)
            if best:
                break
        if not best:
            return None
        x, y, bw, bh = best
        return ((x + bw / 2) / w, (y + bh / 2) / h, bh / h)


# ---- the effects: each draws one frame ------------------------------------------------------------
# f(frame float32 HxWx3 0-255, person alpha HxW, face (cx, cy, h) as fractions, t, dur, spec, st) -> frame
# `st` is a dict kept across the frames of one window (pre-drawn artwork, clean background...).

def fx_rack_focus(img, a, face, t, dur, g, st):
    H = img.shape[0]
    e = envelope(t, dur, 0.4, 0.35)
    if e < 0.01:
        return img
    sigma = max(0.5, 0.014 * H * float(g.get("amount", 1)) * e)
    h, w = img.shape[:2]  # blur a quarter-size copy: looks the same, many times faster
    small = cv2.GaussianBlur(cv2.resize(img, (w // 4, h // 4), interpolation=cv2.INTER_AREA), (0, 0), sigma / 4)
    blur = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR) * (1 - 0.12 * e)
    return comp(blur, img, a)


def _clean_plate(img, a, st):
    """The room without you (filled in from around you), from the first frame of the window."""
    if "plate" not in st:
        H, W = img.shape[:2]
        # Filled in at a very small size and softened: where there's little room showing (a close-up),
        # a smooth out-of-focus fill looks far better than streaks.
        sw, sh = max(16, W // 10), max(16, H // 10)
        small = cv2.resize(img.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_AREA)
        hole = (cv2.resize(a, (sw, sh)) > 0.12).astype(np.uint8) * 255
        hole = cv2.dilate(hole, np.ones((3, 3), np.uint8))
        filled = cv2.inpaint(small, hole, 5, cv2.INPAINT_NS)
        plate = cv2.resize(filled, (W, H), interpolation=cv2.INTER_CUBIC).astype(np.float32)
        soft = cv2.GaussianBlur(plate, (0, 0), W * 0.03)
        near = cv2.GaussianBlur(cv2.resize(a, (W, H)), (0, 0), W * 0.02)[..., None]
        st["plate"] = plate * (1 - near) + soft * near
    return st["plate"]


def fx_dolly_zoom(img, a, face, t, dur, g, st):
    plate = _clean_plate(img, a, st)
    k = 0.38 * float(g.get("amount", 1))
    s = 1 + k * _ease(t / max(0.1, dur * 0.85))
    bg = _zoom(plate, s, face[0], face[1])
    e = _ease((dur - t) / 0.2)  # snap back at the end
    return comp(img, comp(bg, img, a), e)


def _backdrop_frames(g, st, W, H, n):
    """Frames for the backdrop: a video or picture from the plan, else a slow brand-colour gradient."""
    if "bd" in st:
        return
    src = g.get("_src_path")
    if src and Path(src).exists():
        img = Path(src).suffix.lower() in framing.IMAGE_EXTS
        args = ["-loop", "1", "-i", src] if img else ["-stream_loop", "-1", "-ss", f"{float(g.get('src_start', 0)):.2f}", "-i", src]
        raw = subprocess.run(["ffmpeg", "-v", "error", *args, "-frames:v", str(n),
                              "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        frames = [np.frombuffer(raw[i:i + W * H * 3], np.uint8).reshape(H, W, 3).astype(np.float32)
                  for i in range(0, len(raw) - W * H * 3 + 1, W * H * 3)]
        st["bd"] = frames or None
    else:
        st["bd"] = None


def fx_backdrop(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    _backdrop_frames(g, st, W, H, st["n"])
    if st["bd"]:
        bg = st["bd"][min(st["i"], len(st["bd"]) - 1)]
    else:
        yy = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
        c1, c2 = np.array(_rgb(g.get("_primary", "#1F5C4A")), np.float32), np.array([12, 14, 16], np.float32)
        bg = np.broadcast_to(c1 * (1 - yy) * 0.9 + c2 * yy, (H, W, 3)).copy()
    bg = bg * (1 - float(g.get("dim", 0.15)))
    e = envelope(t, dur, 0.3, 0.3)
    out = comp(img, comp(bg, img, a), e)
    labels = [x if isinstance(x, dict) else {"text": str(x)} for x in g.get("labels") or []]
    if labels and e > 0.05:
        cur = [x for x in labels if float(x.get("at", 0)) <= t]
        if cur:
            lab = cur[-1]
            u = min(W, H) / 1080
            layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(layer)
            f = _font(SANS_B, 34 * u)
            txt = str(lab.get("text", ""))
            tw = d.textlength(txt, font=f)
            x0, y0 = (W - tw) / 2 - 30 * u, H * 0.78
            k = _ease((t - float(lab.get("at", 0))) / 0.25) * e
            d.rounded_rectangle([x0, y0, x0 + tw + 60 * u, y0 + 66 * u], radius=14 * u, fill=(12, 26, 20, int(225 * k)))
            d.text((x0 + 30 * u, y0 + 12 * u), txt, font=f, fill=(255, 255, 255, int(255 * k)))
            out = over(out, layer)
    return out


def fx_day_dial(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    R = int(max(W, H) * 0.62)
    if "dial" not in st:
        D = 2 * R
        dial = Image.new("RGBA", (D, D), (0, 0, 0, 0))
        d = ImageDraw.Draw(dial)
        cream, ink = (239, 232, 216, 255), (60, 58, 52, 255)
        d.ellipse([0, 0, D - 1, D - 1], fill=cream, outline=ink, width=max(2, D // 400))
        d.ellipse([D * 0.06, D * 0.06, D * 0.94, D * 0.94], outline=ink, width=max(1, D // 700))
        for k in range(120):  # ticks
            ang = 2 * math.pi * k / 120
            r1 = R * (0.94 if k % 10 else 0.88)
            d.line([(R + R * 0.97 * math.cos(ang), R + R * 0.97 * math.sin(ang)),
                    (R + r1 * math.cos(ang), R + r1 * math.sin(ang))], fill=ink, width=max(1, D // (500 if k % 10 else 300)))
        fnum, fword = _font(SERIF, R * 0.11), _font(SERIF_I, R * 0.075)
        for k in range(12):
            ang = 2 * math.pi * k / 12 - math.pi / 2
            x, y = R + R * 0.76 * math.cos(ang), R + R * 0.76 * math.sin(ang)
            d.text((x, y), str(k or 12), font=fnum, fill=ink, anchor="mm")
        word = str(g.get("text") or "day")
        for k in range(12):  # the word around an inner ring, turned to follow it
            ang = 2 * math.pi * (k + 0.5) / 12 - math.pi / 2
            tile = Image.new("RGBA", (int(R * 0.4), int(R * 0.14)), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((tile.width / 2, tile.height / 2), word, font=fword, fill=ink, anchor="mm")
            tile = tile.rotate(-math.degrees(ang) - 90, expand=True, resample=Image.BICUBIC)
            x, y = R + R * 0.56 * math.cos(ang), R + R * 0.56 * math.sin(ang)
            dial.alpha_composite(tile, (int(x - tile.width / 2), int(y - tile.height / 2)))
        d.ellipse([R * 0.96, R * 0.96, R * 1.04, R * 1.04], fill=ink)
        st["dial"] = dial
    sweep = 360 * min(_ease(t / 0.45), _ease((dur - t) / 0.4))
    if sweep < 1:
        return img
    # Placed and turned in one OpenCV warp straight onto the frame (the image library's rotate was
    # the slowest step), then cut to the sweeping pie with a precomputed angle map.
    if "dial_np" not in st:
        st["dial_np"] = np.asarray(st["dial"], np.float32)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        cx, cy = W * float(g.get("x", 0.45)), H * float(g.get("y", 0.55))
        st["angle"] = (np.degrees(np.arctan2(yy - cy, xx - cx)) + 90) % 360  # 0 at 12 o'clock, clockwise
        st["centre"] = (cx, cy)
    cx, cy = st["centre"]
    M = cv2.getRotationMatrix2D((R, R), 55 * t, 1.0)
    M[0, 2] += cx - R
    M[1, 2] += cy - R
    dial = cv2.warpAffine(st["dial_np"], M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    alpha = dial[..., 3] / 255.0 * (st["angle"] <= sweep)
    return comp(comp(img, dial[..., :3], alpha), img, a)


def fx_post_wall(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    u = min(W, H) / 1080
    if "tiles" not in st:
        rnd = random.Random(7)
        cols, rows = 26, 9
        tw = W * 0.92 / cols
        x0, y0 = W * 0.04, H * 0.06
        pal = [(232, 226, 212), (205, 196, 176), (176, 168, 150), (128, 136, 120), (92, 104, 96), (64, 74, 70), (190, 170, 120)]
        cells = [(x0 + c * tw, y0 + r * tw, rnd.choice(pal)) for r in range(rows) for c in range(cols)]
        rnd.shuffle(cells)
        st["tiles"], st["tw"] = cells, tw
    e_out = _ease((dur - t) / 0.3)
    n = int(len(st["tiles"]) * _ease(t / max(0.3, dur * 0.7)))
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    al = int(255 * e_out)
    tw = st["tw"]
    for x, y, c in st["tiles"][:n]:
        d.rounded_rectangle([x + 1.5 * u, y + 1.5 * u, x + tw - 1.5 * u, y + tw - 1.5 * u], radius=3 * u, fill=(*c, al))
    value = int(float(g.get("value", 1711)) * _ease(t / max(0.3, dur * 0.7)))
    big, small = _font(MONO, 64 * u), _font(MONO, 26 * u)
    txt = f"{value:,}"
    d.text((W * 0.96, H * 0.06 - 8 * u), txt, font=big, fill=(255, 255, 255, al), anchor="rs")
    d.text((W * 0.96, H * 0.06 + 2 * u), str(g.get("label", "posts")).upper(), font=small, fill=(255, 255, 255, int(al * .8)), anchor="ra")
    if g.get("day"):
        d.text((W * 0.04, H * 0.06 - 6 * u), f"DAY {int(float(g['day']) * _ease(t / max(0.3, dur * 0.7)))}", font=small,
               fill=(255, 255, 255, int(al * .8)), anchor="ls")
    return comp(over(img, layer), img, a)


def fx_sketch_words(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    e_in, e_out = _ease(t / 0.2), _ease((dur - t) / 0.3)
    if "paper" not in st:
        ink = (122, 31, 43, 255)
        words = str(g.get("text") or "sketch words").split()
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        size = W * 0.26
        f = _font(HAND, size)
        while max(d.textlength(w, font=f) for w in words) > W * 0.9 and size > 20:
            size *= 0.9
            f = _font(HAND, size)
        y = H * 0.06
        boxes = []
        for w in words[:3]:
            d.text((W * 0.06, y), w, font=f, fill=ink, stroke_width=max(1, int(size / 40)), stroke_fill=ink)
            boxes.append((W * 0.06, y, W * 0.06 + d.textlength(w, font=f), y + size * 1.05))
            y += size * 0.95
        uy = y + size * 0.15  # a scribbled underline
        pts = [(W * 0.06 + k * W * 0.02, uy + math.sin(k * 1.3) * size * 0.03) for k in range(int(boxes[-1][2] / (W * 0.02)))]
        if len(pts) > 1:
            d.line(pts, fill=(190, 160, 40, 255), width=max(2, int(size / 18)), joint="curve")
        st["paper"] = layer
        st["boxes"] = boxes
        rng = np.random.default_rng(3)
        grain = rng.normal(0, 4, (H // 4, W // 4)).astype(np.float32)
        st["bg"] = np.clip(np.array([242, 236, 222], np.float32) + cv2.resize(grain, (W, H))[..., None], 0, 255)
    # Writing on: a reveal that sweeps across each line in turn, like a pen.
    p = _ease(t / max(0.3, dur * 0.6))
    rev = np.zeros((H, W), np.float32)
    nb = len(st["boxes"])
    for k, (x0, y0, x1, y1) in enumerate(st["boxes"]):
        q = min(1.0, max(0.0, p * nb - k))
        if q > 0:
            rev[int(y0):int(y1), int(x0):int(x0 + (x1 - x0 + 20) * q)] = 1
    if p >= 0.999:
        rev[:] = 1
    la = np.asarray(st["paper"], np.float32)
    alpha = la[..., 3] / 255.0 * rev * e_out
    bg = comp(img, st["bg"], e_in * e_out) if g.get("paper", True) else img
    drawn = comp(bg, la[..., :3], alpha)
    return comp(drawn, img, a)


def fx_orbit_ring(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    e = envelope(t, dur, 0.3, 0.3)
    if e < 0.01:
        return img
    word = str(g.get("text") or "ORBIT RING").upper() + " · "
    cx, cy, fh = face[0] * W, face[1] * H, max(0.06, face[2]) * H
    rx, ry = fh * 0.88, fh * 0.24   # a halo hugging the head at eye level
    cy = cy - fh * 0.12
    rot = 1.7 * t
    fsize = fh * 0.3
    text = word * max(1, round(2 * math.pi * rx / (fsize * 0.85) / len(word)))
    back, front = Image.new("RGBA", (W, H), (0, 0, 0, 0)), Image.new("RGBA", (W, H), (0, 0, 0, 0))
    db, df = ImageDraw.Draw(back), ImageDraw.Draw(front)
    n = len(text)
    for k, ch in enumerate(text):
        th = -rot - 2 * math.pi * k / n  # this way round, the front of the ring reads left to right
        depth = math.sin(th)
        x, y = cx + rx * math.cos(th), cy + ry * depth
        f = _font(SERIF, fsize * (0.78 + 0.22 * (depth + 1) / 2))
        al = int(255 * e * (0.55 + 0.45 * (depth + 1) / 2))
        (df if depth > 0 else db).text((x, y), ch, font=f, fill=(250, 246, 236, al), anchor="mm",
                                       stroke_width=max(1, int(fsize / 22)), stroke_fill=(0, 0, 0, int(al * 0.5)))
    return over(comp(over(img, back), img, a), front)


def fx_clone_row(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    e_out = _ease((dur - t) / 0.3)
    count = max(2, min(6, int(float(g.get("count", 4)))))
    slots = []
    for k in range(count):
        side, step = (-1 if k % 2 == 0 else 1), k // 2 + 1
        slots.append((side * step, 0.92 ** step, k))
    slots.sort(key=lambda s_: -abs(s_[0]))  # furthest first, so nearer copies cover them
    out = img
    cx = face[0]
    for off, sc, k in slots:
        appear = _ease((t - 0.12 * k) / 0.3) * e_out
        if appear <= 0.01:
            continue
        dx = off * W * 0.34 * appear
        # scale about the bottom-centre under the face, then slide sideways
        M = np.float32([[sc, 0, (1 - sc) * cx * W + dx], [0, sc, (1 - sc) * H]])
        cp = cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR)
        ca = cv2.warpAffine(a, M, (W, H), flags=cv2.INTER_LINEAR) * appear
        out = comp(out, cp * (0.86 - 0.05 * abs(off)), ca)
    return comp(out, img, a)


def fx_head_stack(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    u = min(W, H) / 1080
    items = [str(x) for x in (g.get("items") or ["Idea", "Script", "Shoot", "Edit", "Post"])][:7]
    e_out = _ease((dur - t) / 0.3)
    cx, cy, fh = face[0] * W, face[1] * H, max(0.06, face[2]) * H
    top = cy - fh * 0.85
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    f = _font(SANS_B, 32 * u)
    step = min(0.28, (dur - 0.6) / max(1, len(items)))
    for k, txt in enumerate(items):
        pop = _ease((t - 0.1 - k * step) / 0.22) * e_out
        if pop <= 0.01:
            continue
        tw = ImageDraw.Draw(layer).textlength(txt, font=f)
        tag = Image.new("RGBA", (int(tw + 46 * u), int(58 * u)), (0, 0, 0, 0))
        td = ImageDraw.Draw(tag)
        td.rounded_rectangle([0, 0, tag.width - 1, tag.height - 1], radius=10 * u, fill=(255, 255, 255, int(245 * pop)),
                             outline=(30, 30, 30, int(120 * pop)), width=max(1, int(2 * u)))
        td.text((tag.width / 2, tag.height / 2), txt, font=f, fill=(20, 20, 20, int(255 * pop)), anchor="mm")
        wob = math.sin(t * 3 + k) * 6 + (k % 2 * 2 - 1) * 5
        tag = tag.rotate(wob, expand=True, resample=Image.BICUBIC)
        sc = 0.6 + 0.4 * pop
        tag = tag.resize((max(1, int(tag.width * sc)), max(1, int(tag.height * sc))), Image.BILINEAR)
        x = cx - tag.width / 2 + math.sin(k * 2.1) * fh * 0.35
        y = top - (k + 1) * 50 * u - tag.height / 2 - (1 - pop) * 30 * u
        layer.alpha_composite(tag, (int(max(0, min(W - tag.width, x))), int(max(0, y))))
    return over(img, layer)


def _cursor(d: ImageDraw.ImageDraw, x: float, y: float, u: float, label: str, al: int):
    s = 34 * u
    pts = [(x, y), (x, y + s * 1.25), (x + s * 0.33, y + s * 0.95), (x + s * 0.55, y + s * 1.45),
           (x + s * 0.75, y + s * 1.36), (x + s * 0.53, y + s * 0.88), (x + s * 0.95, y + s * 0.88)]
    d.polygon(pts, fill=(255, 255, 255, al), outline=(0, 0, 0, al), width=max(1, int(2 * u)))
    if label:
        f = _font(SANS_B, 24 * u)
        tw = d.textlength(label, font=f)
        bx, by = x + s * 0.7, y + s * 1.35
        d.rounded_rectangle([bx, by, bx + tw + 24 * u, by + 40 * u], radius=8 * u, fill=(24, 40, 32, al),
                            outline=(255, 255, 255, int(al * .6)), width=max(1, int(1.5 * u)))
        d.text((bx + 12 * u, by + 20 * u), label, font=f, fill=(255, 255, 255, al), anchor="lm")


def fx_live_cursor(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    u = min(W, H) / 1080
    cx, cy, fh = face[0] * W, face[1] * H, max(0.06, face[2]) * H
    bw, bh = fh * 1.45, fh * 2.0
    bx0, by0 = cx - bw / 2, cy - bh * 0.45
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    e_out = _ease((dur - t) / 0.3)
    al = int(255 * e_out)
    # 0 - 0.35 s: the pointer flies in to the box's top-left; then it drags the box open.
    fly, drag = _ease(t / 0.35), _ease((t - 0.35) / 0.45)
    start = (W * 0.92, H * 0.88)
    if t < 0.35:
        px, py = start[0] + (bx0 - start[0]) * fly, start[1] + (by0 - start[1]) * fly
    else:
        px, py = bx0 + bw * drag, by0 + bh * drag
        x1, y1 = px, py
        line, wl = (255, 255, 255, al), max(1, int(2 * u))
        d.rectangle([bx0, by0, x1, y1], outline=line, width=wl)
        hs = 9 * u
        for hx, hy in [(bx0, by0), (x1, by0), (bx0, y1), (x1, y1), ((bx0 + x1) / 2, by0), ((bx0 + x1) / 2, y1),
                       (bx0, (by0 + y1) / 2), (x1, (by0 + y1) / 2)]:
            d.rectangle([hx - hs, hy - hs, hx + hs, hy + hs], fill=(255, 255, 255, al), outline=(40, 40, 40, al))
        if drag > 0.2:
            scale = 1080 / min(W, H)
            dim = f"{int((x1 - bx0) * scale)} × {int((y1 - by0) * scale)}"
            f = _font(MONO, 24 * u)
            tw = d.textlength(dim, font=f)
            mx = (bx0 + x1) / 2
            d.rounded_rectangle([mx - tw / 2 - 12 * u, by0 - 52 * u, mx + tw / 2 + 12 * u, by0 - 14 * u], radius=6 * u,
                                fill=(214, 170, 72, al))
            d.text((mx, by0 - 33 * u), dim, font=f, fill=(30, 24, 10, al), anchor="mm")
    if t > dur - 0.35:  # the pointer leaves
        k = _ease((t - (dur - 0.35)) / 0.35)
        px, py = px + (start[0] - px) * k, py + (start[1] - py) * k
    _cursor(d, px, py, u, str(g.get("label", "Claude")), 255)
    return over(img, layer)


EFFECTS = {"rack_focus": fx_rack_focus, "dolly_zoom": fx_dolly_zoom, "backdrop": fx_backdrop, "day_dial": fx_day_dial,
           "post_wall": fx_post_wall, "sketch_words": fx_sketch_words, "orbit_ring": fx_orbit_ring,
           "clone_row": fx_clone_row, "head_stack": fx_head_stack, "live_cursor": fx_live_cursor}

# Phase 3, the picture effects (code body, blow away, impact frame...), live in their own file.
import layers_picture as _picture  # noqa: E402

EFFECTS.update(_picture.EFFECTS)
KIT.update(_picture.KIT)
FX.update(_picture.KIT)
# Phase 4, transitions and 3D moves (letter dive, paper tear, title card dive...).
import layers_motion as _motion  # noqa: E402

EFFECTS.update(_motion.EFFECTS)
KIT.update(_motion.KIT)
FX.update(_motion.KIT)
TRANSITION_FX = dict(_motion.TRANSITIONS)  # name -> (seconds before the cut, length)


# ---- rendering a window -----------------------------------------------------------------------------

def render_window(video: Path, t0: float, dur: float, spec: dict, out: Path, fps: str,
                  size: tuple[int, int] | None = None, quality: str = "final") -> Path:
    """Frames t0..t0+dur of `video`, with the effect drawn on, written to `out` (no sound)."""
    W, H = size or framing.probe_size(video)
    fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1" if size else "null"
    reader = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{t0:.3f}", "-t", f"{dur:.3f}", "-i", str(video),
                               "-vf", f"{fit},fps={fps}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + "_part.mp4")
    enc = ["-preset", "ultrafast", "-crf", "24"] if quality == "draft" else ["-preset", "veryfast", "-crf", "16"]
    writer = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                               "-r", fps, "-i", "-", "-c:v", "libx264", *enc, "-pix_fmt", "yuv420p", str(tmp)],
                              stdin=subprocess.PIPE)
    draw = EFFECTS[spec["type"]]
    tr, st = Tracker(W, H), {"n": int(round(dur * float(eval_fps(fps)))) + 2}
    rate = eval_fps(fps)
    if spec["type"] in _motion.NEEDS_PREV:  # the picture just before the window (what tears away)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t0 - 1.5 / rate):.3f}", "-i", str(video),
                              "-frames:v", "1", "-vf", fit, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True).stdout
        if len(raw) >= W * H * 3:
            st["prev"] = np.frombuffer(raw[:W * H * 3], np.uint8).reshape(H, W, 3).astype(np.float32)
    i = 0
    try:
        while True:
            raw = reader.stdout.read(W * H * 3)
            if len(raw) < W * H * 3:
                break
            frame = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
            a, face = tr.update(frame)
            st["i"] = i
            img = draw(frame.astype(np.float32), a, face, i / rate, dur, spec, st)
            writer.stdin.write(np.clip(img, 0, 255).astype(np.uint8).tobytes())
            i += 1
    finally:
        reader.stdout.close()
        reader.wait()
        writer.stdin.close()
        writer.wait()
    tmp.replace(out)
    return out


def _thumbs(video: Path, edit_dir: Path, n: int) -> list[str]:
    out = []
    total = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                  "default=nw=1:nk=1", str(video)], capture_output=True, text=True).stdout.strip() or 0)
    for k in range(n):
        p = edit_dir / "animations" / f"deck_{video.stem}_{k}.jpg"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{total * (k + 0.5) / n:.2f}", "-i", str(video),
                            "-frames:v", "1", "-vf", "scale=360:-2", str(p)], capture_output=True)
        if p.exists():
            out.append(str(p))
    return out


def eval_fps(fps: str) -> float:
    n, _, d = str(fps).partition("/")
    return float(n) / float(d or 1)


def build_layer_overlays(plan: dict, edit_dir: Path, base: Path, draft: bool = False) -> list[dict]:
    """One overlay clip per person-aware effect in the plan ([] if none)."""
    wanted = [e for e in plan.get("effects") or [] if e.get("type") in FX and float(e.get("duration") or 0) > 0]
    # Scene transitions that are really effects (paper tear, letter dive...), placed on their cut.
    from plan import range_len
    t_cut = 0.0
    for i, r in enumerate(plan.get("ranges") or []):
        tr = r.get("transition") or {}
        kind = tr.get("type") if isinstance(tr, dict) else tr
        if i and kind in TRANSITION_FX:
            pre, length = TRANSITION_FX[kind]
            extra = {k: v for k, v in tr.items() if k not in ("type", "duration")} if isinstance(tr, dict) else {}
            wanted.append({"type": kind, "start_in_output": round(max(0.0, t_cut - pre), 3), "duration": length, **extra})
        t_cut += range_len(r)
    if not wanted:
        return []
    fps = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                          "-of", "default=nw=1:nk=1", str(base)], capture_output=True, text=True).stdout.strip() or "30/1"
    st = base.stat()
    from captions import load_brand
    primary = (load_brand(edit_dir).get("colors") or {}).get("primary", "#1F5C4A")
    out = []
    for e in wanted:
        spec = dict(e, _primary=primary)
        if spec.get("src"):
            from background import _resolve
            spec["_src_path"] = str(_resolve(spec["src"], edit_dir))
        if e["type"] == "storyboard_deck":  # frames from across your own video for the cards
            spec["_thumbs"] = _thumbs(base, edit_dir, 4)
        a, d = float(e.get("start_in_output", 0)), float(e["duration"])
        key = hashlib.sha1(json.dumps([spec, str(base), st.st_mtime_ns, st.st_size, draft, Path(__file__).stat().st_mtime],
                                      sort_keys=True, default=str).encode()).hexdigest()[:12]
        clip = edit_dir / "animations" / f"layer_{e['type']}_{key}.mp4"
        if not clip.exists():
            print(f"  {e['type']} at {a:.1f}s ({d:.1f}s)…", flush=True)
            render_window(base, a, d, spec, clip, fps, quality="draft" if draft else "final")
        out.append({"file": str(clip), "start_in_output": a, "duration": d, "type": e["type"]})
    return out
