"""Transitions and 3D moves for the creator-reel set (phase 4), drawn frame by frame like layers.py:

    letter_dive      a giant word with your video inside the letters; the camera dives through one
    paper_tear       the picture rips away like paper to show the next shot
    title_card_dive  a title card, then your video in a phone that flies up to fill the screen
    storyboard_deck  cards with frames from your own video fan in
    layer_explode    the frame tilts into 3D and splits into layers: room, you, text

The first three work as transitions between scenes too (a scene's "transition": "paper_tear"):
layers.build_layer_overlays places them on the cut (TRANSITIONS: seconds before the cut, length).
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw

from layers import MONO, SERIF, _clean_plate, _ease, _font, comp, over

KIT = {
    "letter_dive": ("Letter dive (fly through a word)", 1.3, {"text": "dive."}, {"text": "text"}),
    "paper_tear": ("Paper tear (picture rips away)", 0.7, {}, {}),
    "title_card_dive": ("Title card dive (card, then phone)", 1.8, {"text": "TITLE CARD", "sub": "DIVE"}, {"text": "text", "sub": "text"}),
    "storyboard_deck": ("Storyboard deck (cards fan in)", 2.2, {"text": "TITLE CARD"}, {"text": "text"}),
    "layer_explode": ("Layer explode (3D layers)", 2.0, {"text": "layer explode"}, {"text": "text"}),
}
# As a transition on a cut: (seconds before the cut it starts, how long it lasts)
TRANSITIONS = {"paper_tear": (0.0, 0.7), "letter_dive": (0.65, 1.3), "title_card_dive": (0.9, 1.8)}
# These need the frame just before their window (the picture that tears away, etc.)
NEEDS_PREV = {"paper_tear"}
CREAM = np.array([240, 234, 220], np.float32)


def fx_paper_tear(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    paper = st.get("prev", img)
    if "tear" not in st:
        rng = np.random.default_rng(4)
        ys = np.arange(H, dtype=np.float32)
        rough = np.cumsum(rng.normal(0, W * 0.006, H)).astype(np.float32)
        rough -= np.linspace(rough[0], rough[-1], H)
        line = W * 0.5 + rough + W * 0.06 * np.sin(ys / H * 5.2)
        xx = np.arange(W, dtype=np.float32)[None, :]
        st["left"] = (xx < line[:, None]).astype(np.float32)
        st["dist"] = xx - line[:, None]
        st["fib"] = rng.random((H, W)).astype(np.float32)
    p = _ease(t / (dur * 0.85))
    dx = W * 0.75 * p
    ang = 6 * p
    out = img.copy()
    for side, sgn in ((st["left"], -1), (1 - st["left"], 1)):
        # each half: the old picture with a white torn edge, pulled away and turned a little
        edge = np.clip(1 - np.abs(st["dist"]) / (W * 0.012 + W * 0.008 * st["fib"]), 0, 1)
        piece = paper * (1 - edge[..., None]) + np.array([250, 248, 240], np.float32) * edge[..., None]
        alpha = np.clip(side + edge * 0.0, 0, 1)
        cx = W * (0.25 if sgn < 0 else 0.75)
        M = cv2.getRotationMatrix2D((cx, H * 0.5), sgn * ang, 1.0)
        M[0, 2] += sgn * dx
        M[1, 2] += -H * 0.04 * p
        pw = cv2.warpAffine(piece, M, (W, H), borderMode=cv2.BORDER_CONSTANT)
        aw = cv2.warpAffine(alpha, M, (W, H), borderMode=cv2.BORDER_CONSTANT)
        shadow = cv2.GaussianBlur(aw, (0, 0), W * 0.012) * 0.45
        out = out * (1 - shadow[..., None])
        out = comp(out, pw, aw)
    return out


def _fit_word(d: ImageDraw.ImageDraw, word: str, font_name: str, W: float, start: float):
    size = start
    f = _font(font_name, size)
    while d.textlength(word, font=f) > W and size > 12:
        size *= 0.93
        f = _font(font_name, size)
    return f, size


def fx_letter_dive(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    if "mask" not in st:
        word = str(g.get("text") or "dive.")
        m = Image.new("L", (W, H), 0)
        d = ImageDraw.Draw(m)
        f, size = _fit_word(d, word, "georgiab.ttf", W * 0.94, W * 0.7)
        d.text((W / 2, H * 0.5), word, font=f, fill=255, anchor="mm")
        mask = np.asarray(m, np.float32) / 255
        # dive through the thickest part of the middle of the word
        dist = cv2.distanceTransform((mask > 0.5).astype(np.uint8), cv2.DIST_L2, 5)
        band = dist.copy()
        band[:, : int(W * 0.25)] = 0
        band[:, int(W * 0.75):] = 0
        y, x = np.unravel_index(np.argmax(band), band.shape)
        st.update(mask=mask, centre=(float(x), float(y)), thick=float(dist.max()))
    mask, (cx, cy) = st["mask"], st["centre"]
    show = _ease(t / 0.2)                      # the paper and letters come in
    dive = _ease((t - dur * 0.45) / (dur * 0.5))  # then the camera flies through a letter
    s = 1 + (W / max(2.0, st["thick"])) * 1.4 * dive ** 2.2
    M = np.float32([[s, 0, (1 - s) * cx + (W / 2 - cx) * dive], [0, s, (1 - s) * cy + (H / 2 - cy) * dive]])
    ms = cv2.warpAffine(mask, M, (W, H), flags=cv2.INTER_LINEAR)
    page = comp(CREAM.reshape(1, 1, 3) * np.ones_like(img), img, ms)
    return comp(img, page, show * (1 - _ease((t - (dur - 0.08)) / 0.08)))


def _rounded_mask(w: int, h: int, r: int) -> np.ndarray:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    return np.asarray(m, np.float32) / 255


def fx_title_card_dive(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    u = min(W, H) / 1080
    card_end, phone_end = dur * 0.35, dur * 0.62
    page = CREAM.reshape(1, 1, 3) * np.ones((H, W, 3), np.float32)
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    ink = (40, 54, 46, 255)
    frames = int(t * 30)
    d.text((W / 2, H * 0.38), f"00:00:{frames // 30:02d}:{frames % 30:02d}", font=_font(MONO, 26 * u), fill=ink, anchor="mm")
    if t < card_end:
        words = str(g.get("text") or "TITLE CARD").split()
        f, size = _fit_word(d, max(words, key=len), "georgia.ttf", W * 0.8, 150 * u)
        shown = words[: max(1, int(len(words) * _ease(t / (card_end * 0.6)) + 0.999))]
        for k, w_ in enumerate(shown):
            d.text((W / 2, H * 0.47 + k * size * 1.05), w_, font=f, fill=ink, anchor="mm")
        out = over(page, lay)
        return comp(img, out, _ease(t / 0.12))
    # the phone: your video in a small screen, then it flies up to fill the frame
    grow = _ease((t - phone_end) / (dur - phone_end))
    pin = _ease((t - card_end) / 0.18)
    s0 = 0.42
    s = s0 + (1.0 / 0.94 - s0) * grow
    pw, ph = int(W * s), int(H * s)
    small = cv2.resize(img, (pw, ph), interpolation=cv2.INTER_AREA)
    bez = max(2, int(14 * u * (1 - grow)))
    x0, y0 = (W - pw) // 2, int((H - ph) * (0.42 * (1 - grow) + 0.5 * grow))
    out = over(page, lay) if grow < 0.98 else img.copy()
    if grow < 0.98:
        label = str(g.get("sub") or "")
        if label:
            ld = ImageDraw.Draw(lab := Image.new("RGBA", (W, H), (0, 0, 0, 0)))
            ld.text((W / 2, y0 + ph + 70 * u), label, font=_font("georgia.ttf", 90 * u), fill=(40, 54, 46, int(255 * (1 - grow))), anchor="mm")
            out = over(out, lab)
        r = max(4, int(46 * u * s / s0))
        frame_m = _rounded_mask(pw + 2 * bez, ph + 2 * bez, r + bez)
        screen_m = _rounded_mask(pw, ph, r)
        ys, xs = slice(max(0, y0 - bez), min(H, y0 + ph + bez)), slice(max(0, x0 - bez), min(W, x0 + pw + bez))
        fy0, fx0 = ys.start - (y0 - bez), xs.start - (x0 - bez)
        fm = frame_m[fy0:fy0 + ys.stop - ys.start, fx0:fx0 + xs.stop - xs.start] * pin
        out[ys, xs] = comp(out[ys, xs], np.full((ys.stop - ys.start, xs.stop - xs.start, 3), 22, np.float32), fm)
        sy, sx = slice(max(0, y0), min(H, y0 + ph)), slice(max(0, x0), min(W, x0 + pw))
        sm = screen_m[sy.start - y0:sy.stop - y0, sx.start - x0:sx.stop - x0] * pin
        out[sy, sx] = comp(out[sy, sx], small[sy.start - y0:sy.stop - y0, sx.start - x0:sx.stop - x0], sm)
    return out


def fx_storyboard_deck(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    u = min(W, H) / 1080
    if "cards" not in st:
        thumbs = []
        for p in g.get("_thumbs") or []:
            try:
                thumbs.append(Image.open(p).convert("RGB"))
            except OSError:
                pass
        if not thumbs:
            thumbs = [Image.fromarray(img.astype(np.uint8))]
        cw, ch = int(W * 0.17), int(W * 0.17 * 16 / 9)
        cards = []
        for k in range(5):
            c = Image.new("RGBA", (cw + int(10 * u), ch + int(10 * u)), (255, 255, 255, 255))
            if k == 2:  # one title card in the middle of the deck
                cd = ImageDraw.Draw(c)
                cd.rectangle([5 * u, 5 * u, c.width - 5 * u, c.height - 5 * u], fill=(240, 234, 220, 255))
                words = str(g.get("text") or "TITLE CARD").split()
                f, _ = _fit_word(cd, max(words, key=len), "georgia.ttf", cw * 0.8, 44 * u)
                for j, w_ in enumerate(words[:3]):
                    cd.text((c.width / 2, c.height * 0.42 + j * 48 * u), w_, font=f, fill=(40, 54, 46, 255), anchor="mm")
            else:
                im = thumbs[k % len(thumbs)]
                sc = max(cw / im.width, ch / im.height)
                im = im.resize((int(im.width * sc) + 1, int(im.height * sc) + 1))
                im = im.crop(((im.width - cw) // 2, (im.height - ch) // 2, (im.width - cw) // 2 + cw, (im.height - ch) // 2 + ch))
                c.paste(im, (int(5 * u), int(5 * u)))
            cards.append(c)
        st["cards"] = cards
    lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out_e = _ease((dur - t) / 0.3)
    n = len(st["cards"])
    for k, c in enumerate(st["cards"]):
        p = _ease((t - 0.08 * k) / 0.35) * out_e
        if p <= 0.01:
            continue
        rot = (k - (n - 1) / 2) * -4 * p
        cc = c.rotate(rot, expand=True, resample=Image.BICUBIC)
        x = W * (0.08 + 0.84 * k / (n - 1)) - cc.width / 2
        y = H * 0.72 + (1 - p) * H * 0.35 - cc.height / 2 + abs(k - (n - 1) / 2) * 12 * u
        shadow = Image.new("RGBA", cc.size, (0, 0, 0, int(90 * p)))
        shadow.putalpha(Image.fromarray((np.asarray(cc.getchannel("A"), np.float32) * 0.35 * p).astype(np.uint8)))
        lay.alpha_composite(shadow, (int(max(0, x + 8 * u)), int(max(0, y + 10 * u))))
        cc.putalpha(Image.fromarray((np.asarray(cc.getchannel("A"), np.float32) * p).astype(np.uint8)))
        lay.alpha_composite(cc, (int(max(0, min(W - cc.width, x))), int(max(0, min(H - cc.height, y)))))
    return over(img, lay)


def _project(Wd, Hd, W, H, theta, phi, z, dx):
    """Screen corners of a W x H picture plane turned by theta (around the vertical axis) and phi
    (forward tilt), pushed back by z and sideways by dx, seen from a camera at distance D."""
    D = 2.2 * W
    pts = []
    for X, Y in ((-W / 2, -H / 2), (W / 2, -H / 2), (W / 2, H / 2), (-W / 2, H / 2)):
        x1, z1 = X * math.cos(theta) + z * math.sin(theta), -X * math.sin(theta) + z * math.cos(theta)
        y2, z2 = Y * math.cos(phi) - z1 * math.sin(phi), Y * math.sin(phi) + z1 * math.cos(phi)
        k = D / (D + z2)
        pts.append((Wd / 2 + (x1 + dx) * k * 0.72, Hd / 2 + y2 * k * 0.72))
    return np.float32(pts)


def fx_layer_explode(img, a, face, t, dur, g, st):
    H, W = img.shape[:2]
    u = min(W, H) / 1080
    plate = _clean_plate(img, a, st)
    if "text" not in st:
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        words = str(g.get("text") or "layer explode").split()
        for k, w_ in enumerate(words[:3]):
            d.text((W * 0.08, H * 0.1 + k * 120 * u), w_, font=_font(SERIF, 130 * u), fill=(250, 246, 236, 255))
        y = H * 0.82
        for k in range(48):  # a little sound wave, the "sound" layer
            hgt = (20 + 70 * abs(math.sin(k * 0.7) * math.cos(k * 0.23))) * u
            x = W * 0.12 + k * W * 0.016
            d.rectangle([x, y - hgt, x + W * 0.008, y + hgt], fill=(214, 92, 92, 230))
        st["text"] = np.asarray(lay, np.float32)
    spread = math.sin(min(1.0, t / dur) * math.pi)          # apart, then back together
    theta = math.radians(38) * spread
    phi = math.radians(10) * spread
    bg = np.full((H, W, 3), (14, 26, 22), np.float32)
    person = np.dstack([img, a * 255])
    text = st["text"]
    planes = [(np.dstack([plate, np.full((H, W), 255, np.float32)]), "ROOM"), (person, "YOU"), (text, "TYPE")]
    out = bg
    src = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
    for k, (rgba, label) in enumerate(planes):
        z = (1 - k) * W * 0.55 * spread
        dst = _project(W, H, W, H, theta, phi, z, (k - 1) * W * 0.1 * spread)
        Mp = cv2.getPerspectiveTransform(src, dst)
        warped = cv2.warpPerspective(rgba, Mp, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
        out = comp(out, warped[..., :3], warped[..., 3] / 255)
        if spread > 0.05:  # the plane's outline and name, like a design tool
            lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            dl = ImageDraw.Draw(lay)
            al = int(200 * spread)
            dl.line([tuple(p) for p in dst] + [tuple(dst[0])], fill=(220, 230, 225, al), width=max(1, int(2 * u)))
            dl.text((dst[0][0] + 8 * u, dst[0][1] - 30 * u), label, font=_font(MONO, 22 * u), fill=(220, 230, 225, al))
            out = over(out, lay)
    return comp(img, out, min(1.0, spread * 4))


EFFECTS = {"letter_dive": fx_letter_dive, "paper_tear": fx_paper_tear, "title_card_dive": fx_title_card_dive,
           "storyboard_deck": fx_storyboard_deck, "layer_explode": fx_layer_explode}
