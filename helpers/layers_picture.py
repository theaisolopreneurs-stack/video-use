"""Picture effects for the creator-reel set (phase 3), drawn frame by frame like layers.py:

    code_body     you turn into scrolling code characters              {text, color: amber|green|white|own}
    blow_away     you disintegrate into dust and drift away            {back}
    impact_frame  a black-and-white comic panel with speed lines       {text}
    shockwave     a ripple bursts out across the picture               {amount, x, y}
    blade_slice   a knife slash splits the picture for a moment        {angle}
    bank_note     you, engraved on a banknote, with a counting number  {value, title}
    press_front   you on a newspaper front page                        {masthead, headline, sub, date}

Each is f(frame, person alpha, face, t, dur, spec, state) -> frame, registered in layers.EFFECTS.
"""

from __future__ import annotations

import math
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw

from layers import (MONO, SERIF, SERIF_I, _clean_plate, _ease, _font, comp, envelope, over)

KIT = {
    "code_body": ("Code body (you turn into code)", 2.2, {"text": "", "color": "amber"}, {"text": "text", "color": "text"}),
    "blow_away": ("Blow away (you turn to dust)", 1.6, {"back": True}, {}),
    "impact_frame": ("Impact frame (comic panel)", 1.0, {"text": "IMPACT"}, {"text": "text"}),
    "shockwave": ("Shockwave (ripple burst)", 0.9, {"amount": 1.0}, {"amount": "number"}),
    "blade_slice": ("Blade slice (knife slash)", 0.7, {"angle": -18}, {"angle": "number"}),
    "bank_note": ("Bank note (you on money)", 2.0, {"value": 174953, "title": "THE MASTERCLASS"}, {"value": "number", "title": "text"}),
    "press_front": ("Press front (newspaper page)", 2.2, {"masthead": "The Daily Reel", "headline": "BIG NEWS TODAY",
                                                          "sub": "and why it matters, in 30 seconds."},
                    {"masthead": "text", "headline": "text", "sub": "text", "date": "text"}),
}

CODE = ("const reel = await edit({ cuts: true, captions: 'viral' }); if (hook.strong) post(reel); "
        "for (const fx of effects) apply(fx, { at: beat.time }); export default reel; // built by Claude ")
TINTS = {"amber": (255, 196, 92), "green": (90, 255, 140), "white": (240, 240, 240)}


def _glyph_atlas(chars: str, cw: int, ch: int) -> np.ndarray:
    """Each character drawn once as a small grey mask (n, ch, cw), 0-1."""
    f = _font(MONO, ch * 0.95)
    out = np.zeros((len(chars), ch, cw), np.float32)
    for i, c in enumerate(chars):
        im = Image.new("L", (cw, ch), 0)
        ImageDraw.Draw(im).text((cw / 2, ch / 2), c, font=f, fill=255, anchor="mm")
        out[i] = np.asarray(im, np.float32) / 255
    return out


def fx_code_body(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    if "atlas" not in st:
        cw = max(5, W // 64)
        ch = int(cw * 1.6)
        text = (str(g.get("text") or "") + " " + CODE).replace("\n", " ")
        chars = "".join(sorted(set(text)))
        st.update(atlas=_glyph_atlas(chars, cw, ch), cw=cw, ch=ch, idx=np.array([chars.index(c) for c in text]))
    cw, ch, atlas, idx = st["cw"], st["ch"], st["atlas"], st["idx"]
    cols, rows = W // cw, H // ch
    small = cv2.resize(img[:rows * ch, :cols * cw], (cols, rows), interpolation=cv2.INTER_AREA)
    am = cv2.resize(a[:rows * ch, :cols * cw], (cols, rows), interpolation=cv2.INTER_AREA)
    # The text scrolls through the body as it runs.
    shift = int(t * 28)
    k = (np.arange(rows * cols).reshape(rows, cols) + shift) % len(idx)
    glyphs = atlas[idx[k]]                                   # rows, cols, ch, cw
    lum = small.mean(axis=2) / 255
    tint = g.get("color", "amber")
    color = small * 1.5 if tint == "own" else np.array(TINTS.get(tint, TINTS["amber"]), np.float32) * (0.55 + 1.1 * lum)[..., None]
    if tint != "own":
        color = color * 0.7 + small * 0.6  # keep some of the real colour, like the clothes going red
    color = np.clip(color, 0, 255)
    on = (am > 0.45).astype(np.float32)
    tiles = glyphs[..., None] * color[:, :, None, None, :] * on[:, :, None, None, None]
    body = tiles.transpose(0, 2, 1, 3, 4).reshape(rows * ch, cols * cw, 3)
    canvas = img * 0.2
    canvas[:rows * ch, :cols * cw] = np.maximum(canvas[:rows * ch, :cols * cw], body)
    e = envelope(t, dur, 0.18, 0.18)
    return comp(img, canvas, e)


def fx_blow_away(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    plate = _clean_plate(img, a, st)
    if "seed" not in st:
        rng = np.random.default_rng(11)
        st["seed"] = rng
        st["noise"] = rng.random((H // 3 + 1, W // 3 + 1)).astype(np.float32)
    # Each spot of you lets go at its own moment, a sweep from left to right with some randomness.
    go_end = dur * (0.62 if g.get("back", True) else 0.85)
    xs = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    rnd = cv2.resize(st["noise"], (W, H), interpolation=cv2.INTER_NEAREST)
    let_go = (0.15 + 0.65 * xs + 0.35 * rnd) / 1.15 * go_end
    still = (t < let_go).astype(np.float32) * a
    out = comp(plate, img, still)
    # The dust: the pixels that already let go, flying up and to the right and fading.
    step = 3
    ys, xs_ = np.mgrid[0:H:step, 0:W:step]
    al = a[ys, xs_]
    lg = let_go[ys, xs_]
    gone = (al > 0.5) & (t >= lg)
    if gone.any():
        y0, x0 = ys[gone].astype(np.float32), xs_[gone].astype(np.float32)
        tau = t - lg[gone]
        rn = rnd[ys[gone], xs_[gone]]
        nx = x0 + (180 + 260 * rn) * tau * (W / 720) + np.sin(y0 * 0.05 + tau * 6) * 8
        ny = y0 - (60 + 140 * (1 - rn)) * tau * (W / 720) + tau * tau * 40
        life = np.clip(1 - tau / 0.9, 0, 1)
        ok = (nx >= 0) & (nx < W - 2) & (ny >= 0) & (ny < H - 2) & (life > 0)
        cols = img[y0[ok].astype(int), x0[ok].astype(int)]
        lv = life[ok][:, None]
        xi, yi = nx[ok].astype(int), ny[ok].astype(int)
        for dy in (0, 1):
            for dx in (0, 1):
                out[yi + dy, xi + dx] = out[yi + dy, xi + dx] * (1 - lv) + cols * lv
    if g.get("back", True):  # you fade back in at the end, so the next shot doesn't jump
        out = comp(out, img, _ease((t - (dur - 0.3)) / 0.3))
    return out


def fx_impact_frame(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    if "panel" not in st:
        gray = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_RGB2GRAY)
        gray = cv2.bilateralFilter(gray, 9, 60, 60)
        ink = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 21, 7)
        shade = np.where(gray < 70, 0, 255).astype(np.uint8)
        person = np.minimum(ink, shade).astype(np.float32)
        panel = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(panel)
        cx, cy = face[0] * W, face[1] * H
        rng = np.random.default_rng(5)
        for k in range(150):  # speed lines rushing in to the face
            ang = 2 * math.pi * k / 150 + rng.uniform(-0.01, 0.01)
            r0 = max(W, H) * (0.32 + rng.uniform(0, 0.12))
            r1 = max(W, H) * 1.2
            w_ = rng.uniform(0.004, 0.012)
            pts = [(cx + r0 * math.cos(ang), cy + r0 * math.sin(ang)),
                   (cx + r1 * math.cos(ang - w_), cy + r1 * math.sin(ang - w_)),
                   (cx + r1 * math.cos(ang + w_), cy + r1 * math.sin(ang + w_))]
            d.polygon(pts, fill=(0, 0, 0))
        bg = np.asarray(panel, np.float32)
        p3 = np.repeat(person[..., None], 3, axis=2)
        out = comp(bg, p3, np.clip(a * 1.2, 0, 1))
        word = str(g.get("text") or "IMPACT").upper()
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(lay)
        size = W * 0.24
        f = _font("impact.ttf", size)
        while dl.textlength(word, font=f) > W * 0.92 and size > 20:
            size *= 0.92
            f = _font("impact.ttf", size)
        dl.text((W / 2, H * 0.86), word, font=f, fill=(255, 255, 255, 255), anchor="mm",
                stroke_width=max(2, int(size / 14)), stroke_fill=(0, 0, 0, 255))
        st["panel"] = over(out, lay)
    # A hard cut in with a tiny shake, a hard cut out: it's a freeze frame.
    sh = 8 * (1 - _ease(t / 0.18)) * (W / 720)
    M = np.float32([[1.03, 0, -0.015 * W + sh * math.sin(t * 90)], [0, 1.03, -0.015 * H]])
    return cv2.warpAffine(st["panel"], M, (W, H), borderMode=cv2.BORDER_REPLICATE)


def fx_shockwave(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    if "grid" not in st:
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        cx = float(g.get("x", face[0])) * W
        cy = float(g.get("y", face[1])) * H
        dx, dy = xx - cx, yy - cy
        r = np.sqrt(dx * dx + dy * dy) + 1e-3
        st.update(grid=(xx, yy), r=r, ux=dx / r, uy=dy / r)
    xx, yy = st["grid"]
    r, ux, uy = st["r"], st["ux"], st["uy"]
    R = max(W, H) * 1.1 * _ease(t / dur) ** 0.8
    w = W * 0.07
    amp = W * 0.055 * float(g.get("amount", 1)) * (1 - t / dur)
    z = (r - R) / w
    off = amp * np.sin(z * 2.2) * np.exp(-z * z)
    out = cv2.remap(img, xx - ux * off, yy - uy * off, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    glow = np.exp(-z * z) * 30 * (1 - t / dur)
    return np.clip(out + glow[..., None], 0, 255)


def fx_blade_slice(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    if "side" not in st:
        ang = math.radians(float(g.get("angle", -18)))
        nx, ny = -math.sin(ang), math.cos(ang)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        dist = (xx - W / 2) * nx + (yy - H * 0.45) * ny
        st.update(side=(dist > 0).astype(np.float32), dist=dist, n=(nx, ny), along=(math.cos(ang), math.sin(ang)))
    draw = _ease(t / 0.12)                                 # the slash races across
    split = math.sin(min(1.0, max(0.0, (t - 0.08) / (dur - 0.08))) * math.pi)  # halves part, then close
    d = W * 0.06 * split
    ax, ay = st["along"]
    nx, ny = st["n"]  # slide along the cut and apart a little
    Ma = np.float32([[1, 0, ax * d + nx * d * 0.3], [0, 1, ay * d + ny * d * 0.3]])
    Mb = np.float32([[1, 0, -ax * d - nx * d * 0.3], [0, 1, -ay * d - ny * d * 0.3]])
    A = cv2.warpAffine(img, Ma, (W, H), borderMode=cv2.BORDER_REFLECT)
    B = cv2.warpAffine(img, Mb, (W, H), borderMode=cv2.BORDER_REFLECT)
    out = comp(B, A, st["side"])
    # the cut line: dark core, bright edge, drawn from left to right
    line = np.exp(-(st["dist"] / (W * 0.004)) ** 2)
    xs = np.linspace(0, 1, W, dtype=np.float32)[None, :]
    line = line * (xs <= draw) * (1 - _ease((t - (dur - 0.25)) / 0.25))
    out = out * (1 - line[..., None] * 0.9)
    edge = np.exp(-((st["dist"] - W * 0.006) / (W * 0.003)) ** 2) * (xs <= draw)
    return np.clip(out + edge[..., None] * 200 * (1 - _ease((t - 0.3) / 0.3)), 0, 255)


def _engrave(gray: np.ndarray, period: float) -> np.ndarray:
    """Banknote-style line engraving: lines that thicken where the picture is dark."""
    H, W = gray.shape
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    wave = 0.5 + 0.5 * np.cos(2 * np.pi * (yy + 3 * np.sin(xx / 23.0)) / period)
    return (gray / 255.0 < wave * 0.95).astype(np.float32)  # 1 = ink


def fx_bank_note(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    u = min(W, H) / 1080
    green, cream = np.array([38, 74, 52], np.float32), np.array([233, 230, 210], np.float32)
    if "note" not in st:
        note = Image.new("RGB", (W, H), tuple(int(c) for c in cream))
        d = ImageDraw.Draw(note)
        gc = tuple(int(c) for c in green)
        m = 26 * u
        for k, wd in enumerate((6, 2, 1)):
            r = m + k * 14 * u
            d.rounded_rectangle([r, r, W - r, H - r], radius=30 * u, outline=gc, width=max(1, int(wd * u)))
        for k in range(46):  # a guilloche-like border pattern
            y = m + 60 * u + k * (H - 2 * m - 120 * u) / 45
            d.arc([m + 10 * u, y - 14 * u, m + 50 * u, y + 14 * u], 0, 360, fill=gc, width=1)
            d.arc([W - m - 50 * u, y - 14 * u, W - m - 10 * u, y + 14 * u], 0, 360, fill=gc, width=1)
        ox0, oy0, ox1, oy1 = W * 0.15, H * 0.12, W * 0.85, H * 0.58
        d.ellipse([ox0 - 10 * u, oy0 - 10 * u, ox1 + 10 * u, oy1 + 10 * u], outline=gc, width=max(2, int(6 * u)))
        title = str(g.get("title") or "THE MASTERCLASS").upper()
        d.text((W / 2, H * 0.76), title, font=_font(SERIF, 44 * u), fill=gc, anchor="mm")
        d.text((W / 2, H * 0.81), "THIS NOTE IS NOT MONEY · THE NUMBER IS REAL", font=_font(SERIF, 20 * u), fill=gc, anchor="mm")
        oval = Image.new("L", (W, H), 0)
        ImageDraw.Draw(oval).ellipse([ox0, oy0, ox1, oy1], fill=255)
        st.update(note=np.asarray(note, np.float32), oval=np.asarray(oval, np.float32) / 255, box=(ox0, oy0, ox1, oy1))
    ox0, oy0, ox1, oy1 = st["box"]
    # The portrait: your face in the oval, engraved in green.
    gray = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    cx, cy, fh = face[0] * W, face[1] * H, max(0.08, face[2]) * H
    s = (oy1 - oy0) / (fh * 3.2)
    M = np.float32([[s, 0, (ox0 + ox1) / 2 - s * cx], [0, s, (oy0 + oy1) / 2 + fh * 0.3 * s - s * cy]])
    portrait = cv2.warpAffine(gray, M, (W, H), borderMode=cv2.BORDER_REPLICATE)
    portrait = cv2.equalizeHist(portrait.astype(np.uint8)).astype(np.float32)
    ink = _engrave(portrait, max(3.0, 5 * u))
    page = st["note"].copy()
    o = st["oval"][..., None]
    page = page * (1 - o) + (cream * (1 - ink[..., None]) + green * ink[..., None]) * o
    # the counting number
    value = int(float(g.get("value", 174953)) * _ease(t / max(0.3, dur * 0.7)))
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dl = ImageDraw.Draw(lay)
    dl.text((W / 2, H * 0.67), f"{value:,}.", font=_font(SERIF, 120 * u), fill=(38, 74, 52, 255), anchor="mm")
    for (x, y, an) in ((W * 0.12, H * 0.07, "lm"), (W * 0.88, H * 0.07, "rm"), (W * 0.12, H * 0.93, "lm"), (W * 0.88, H * 0.93, "rm")):
        dl.text((x, y), f"{value:,}", font=_font(SERIF, 34 * u), fill=(38, 74, 52, 255), anchor=an)
    page = over(page, lay)
    # it slams in from slightly big and settles
    e = envelope(t, dur, 0.2, 0.2)
    sc = 1 + 0.08 * (1 - _ease(t / 0.3))
    page = cv2.warpAffine(page, np.float32([[sc, 0, (1 - sc) * W / 2], [0, sc, (1 - sc) * H / 2]]), (W, H),
                          borderMode=cv2.BORDER_REPLICATE)
    return comp(img, page, e)


def _halftone(gray: np.ndarray, cell: int) -> np.ndarray:
    """Newspaper dots: one dot per cell, bigger where it's darker. Returns ink 0-1."""
    H, W = gray.shape
    rows, cols = H // cell, W // cell
    small = cv2.resize(gray[:rows * cell, :cols * cell], (cols, rows), interpolation=cv2.INTER_AREA) / 255.0
    yy, xx = np.mgrid[0:cell, 0:cell].astype(np.float32) - (cell - 1) / 2
    rr = np.sqrt(xx * xx + yy * yy) / (cell * 0.72)          # 0 at centre, ~1 at the corners
    dots = (rr[None, None] < np.sqrt(1 - small)[..., None, None]).astype(np.float32)
    out = np.zeros((H, W), np.float32)
    out[:rows * cell, :cols * cell] = dots.transpose(0, 2, 1, 3).reshape(rows * cell, cols * cell)
    return out


def fx_press_front(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    u = min(W, H) / 1080
    paper, ink_c = np.array([238, 232, 216], np.float32), np.array([28, 26, 24], np.float32)
    if "page" not in st:
        page = Image.new("RGB", (W, H), tuple(int(c) for c in paper))
        d = ImageDraw.Draw(page)
        ic = tuple(int(c) for c in ink_c)
        m = 36 * u
        d.text((W / 2, m + 50 * u), str(g.get("masthead") or "The Daily Reel"), font=_font(SERIF, 96 * u), fill=ic, anchor="mm")
        date = str(g.get("date") or time.strftime("%A, %B %d, %Y").upper())
        d.line([m, m + 112 * u, W - m, m + 112 * u], fill=ic, width=max(1, int(3 * u)))
        d.text((W / 2, m + 130 * u), date, font=_font(SERIF, 20 * u), fill=ic, anchor="mm")
        d.line([m, m + 148 * u, W - m, m + 148 * u], fill=ic, width=max(1, int(1 * u)))
        head = str(g.get("headline") or "BIG NEWS TODAY").upper()
        size = 110 * u
        f = _font("timesbd.ttf", size)
        while d.textlength(head, font=f) > W - 2 * m and size > 20:
            size *= 0.92
            f = _font("timesbd.ttf", size)
        d.text((W / 2, m + 220 * u), head, font=f, fill=ic, anchor="mm")
        d.text((W / 2, m + 300 * u), str(g.get("sub") or ""), font=_font(SERIF_I, 46 * u), fill=ic, anchor="mm")
        px0, py0, px1, py1 = W * 0.24, m + 350 * u, W * 0.76, H * 0.74
        filler = ("Claude cut the pauses, found the hook and set the captions in seconds. The edit runs at the pace "
                  "people actually watch, every beat on the line it illustrates. ") * 6
        fcol = _font(SERIF, 17 * u)
        for (x0, x1) in ((m, px0 - 16 * u), (px1 + 16 * u, W - m)):
            y = py0
            words, line_ = filler.split(), ""
            for w in words:
                if d.textlength(line_ + " " + w, font=fcol) > x1 - x0:
                    d.text((x0, y), line_, font=fcol, fill=ic)
                    y += 22 * u
                    line_ = w
                    if y > py1:
                        break
                else:
                    line_ = (line_ + " " + w).strip()
        d.rectangle([px0 - 4 * u, py0 - 4 * u, px1 + 4 * u, py1 + 4 * u], outline=ic, width=max(1, int(2 * u)))
        d.text((W / 2, py1 + 26 * u), "Pictured: the editor at work.", font=_font(SERIF_I, 20 * u), fill=ic, anchor="mm")
        y = py1 + 60 * u
        while y < H - m - 20 * u:
            d.line([m, y, W - m, y], fill=(150, 145, 135), width=1)
            y += 22 * u
        st.update(page=np.asarray(page, np.float32), box=(int(px0), int(py0), int(px1), int(py1)))
    px0, py0, px1, py1 = st["box"]
    bw, bh = px1 - px0, py1 - py0
    # the photo: you, live, printed in halftone
    gray = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    cx, cy, fh = face[0] * W, face[1] * H, max(0.08, face[2]) * H
    s = bh / (fh * 3.4)
    M = np.float32([[s, 0, bw / 2 - s * cx], [0, s, bh * 0.42 - s * cy]])
    photo = cv2.warpAffine(gray, M, (bw, bh), borderMode=cv2.BORDER_REPLICATE).astype(np.float32)
    photo = cv2.equalizeHist(photo.astype(np.uint8)).astype(np.float32)
    dots = _halftone(photo, max(3, int(6 * u)))
    page = st["page"].copy()
    page[py0:py1, px0:px1] = paper * (1 - dots[..., None]) + ink_c * dots[..., None]
    e = envelope(t, dur, 0.22, 0.2)
    sc = 1 + 0.06 * (1 - _ease(t / 0.35))
    page = cv2.warpAffine(page, np.float32([[sc, 0, (1 - sc) * W / 2], [0, sc, (1 - sc) * H / 2]]), (W, H),
                          borderMode=cv2.BORDER_REPLICATE)
    return comp(img, page, e)


EFFECTS = {"code_body": fx_code_body, "blow_away": fx_blow_away, "impact_frame": fx_impact_frame, "shockwave": fx_shockwave,
           "blade_slice": fx_blade_slice, "bank_note": fx_bank_note, "press_front": fx_press_front}
