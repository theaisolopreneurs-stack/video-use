"""Hand swipe reveal: swipe your hand across the camera and a line follows it, changing the video
behind it (darker, black and white, a new look... whatever you're saying at that moment).

plan.json "effects":
    {"type": "hand_wipe", "start_in_output": 12.4, "reveal": "auto", "duration": null}
        reveal: "auto" picks from your words (dark -> dim, black and white -> bw, vintage -> old_film...),
                or any effect name (bw, dim, blur...), or a look ("look:moody_teal")
        duration: how long the new look stays (null = to the end of the video)
        line: true (default) draws the bright line at the edge
And a transition between sections, revealing the next clip: "transition": {"type": "hand_swipe"}.

Finds the hand with MediaPipe's hand model (about 8 MB from Google, downloaded once into models/)
and follows it frame by frame. With no hand in view it falls back to a quick straight wipe.

    python helpers/handwipe.py find <edit>/plan.json      # list the swipes in the footage
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np

import framing

HAND_URL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
            "hand_landmarker/float16/latest/hand_landmarker.task")

# Words said around a swipe -> what the swipe reveals.
REVEAL_WORDS = [
    (("black and white", "black & white", "grayscale", "greyscale", "no color", "no colour"), "bw"),
    (("dark", "darker", "night", "moody", "shadow", "lights out", "dim"), "dim"),
    (("vintage", "old school", "retro", "throwback", "film"), "old_film"),
    (("blur", "blurry", "focus"), "blur"),
    (("warm", "warmer", "summer", "sunset", "golden"), "warm_tint"),
    (("cold", "cool", "colder", "winter", "ice"), "cool_tint"),
    (("glitch", "broken", "error"), "glitch"),
    (("dream", "dreamy", "magic", "glow"), "glow"),
    (("sketch", "drawing", "pencil"), "sketch"),
    (("comic", "cartoon"), "comic"),
    (("heat", "thermal", "hot"), "thermal"),
    (("upside down", "flip"), "mirror"),
]


@lru_cache(maxsize=1)
def _detector():
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision
    opts = vision.HandLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(framing.model_file("hand_landmarker.task", HAND_URL))),
        num_hands=2, min_hand_detection_confidence=0.4, min_hand_presence_confidence=0.4)
    return mp, vision.HandLandmarker.create_from_options(opts)


def hand_track(video: Path, start: float, end: float, fps: float = 15.0) -> list[tuple[float, float]]:
    """(time, x) of the hand's centre, x as 0-1 across the frame, for every frame it's found in."""
    mp, det = _detector()
    w, h = framing.probe_size(video)
    width = 320
    height = int(round(h * width / w / 2) * 2)
    dur = max(0.05, end - start)
    proc = subprocess.Popen(["ffmpeg", "-v", "quiet", "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}", "-i", str(video),
                             "-vf", f"fps={fps},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                            stdout=subprocess.PIPE)
    out, i = [], 0
    while True:
        raw = proc.stdout.read(width * height * 3)
        if len(raw) < width * height * 3:
            break
        frame = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
        res = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame)))
        if res.hand_landmarks:
            # The hand that's furthest from the frame centre is the one doing the swipe, usually.
            xs = [float(np.mean([p.x for p in hand])) for hand in res.hand_landmarks]
            out.append((round(max(0.0, start) + i / fps, 3), max(xs, key=lambda x: abs(x - 0.5))))
        i += 1
    proc.stdout.close()
    proc.wait()
    return out


def motion_track(video: Path, start: float, end: float, fps: float = 30.0) -> list[tuple[float, float]]:
    """(time, x) of a big fast-moving thing (a blurred hand) for every frame it's in.

    A real swipe is often just a blur for 2-4 frames, which the hand model can't see. Each frame is
    compared with the median of the frames around it (the scene without the hand); the middle of
    what's different is where the hand is."""
    w, h = framing.probe_size(video)
    width = 160
    height = int(round(h * width / w / 2) * 2)
    start = max(0.0, start)
    proc = subprocess.Popen(["ffmpeg", "-v", "quiet", "-ss", f"{start:.3f}", "-t", f"{max(0.05, end - start):.3f}", "-i", str(video),
                             "-vf", f"fps={fps},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                            stdout=subprocess.PIPE)
    out, half = [], int(fps * 0.4)
    buf, first, done = [], 0, False  # buf holds frames first .. first+len(buf)-1
    i = 0  # the frame being measured
    while True:
        if not done:
            raw = proc.stdout.read(width * height * 3)
            if len(raw) < width * height * 3:
                done = True
            else:
                buf.append(np.frombuffer(raw, np.uint8).reshape(height, width, 3))
        if not done and first + len(buf) - 1 < i + half:
            continue  # still reading ahead
        if i >= first + len(buf):
            break
        around = buf[max(0, i - half - first):i + half + 1 - first]
        if len(around) >= 5:
            f = buf[i - first].astype(np.int16)
            diff = np.abs(f - np.median(np.stack(around), axis=0)).max(axis=2) > 45
            share = diff.mean()
            # A hand covers a good chunk of the frame; a whole-frame change is the camera moving.
            if 0.06 <= share <= 0.7:
                cols = diff.sum(axis=0).astype(float)
                out.append((round(start + i / fps, 3), float((cols * np.arange(width)).sum() / cols.sum() / width)))
        i += 1
        while first < i - half:
            buf.pop(0)
            first += 1
    proc.stdout.close()
    proc.wait()
    return out


def track_swipe(video: Path, start: float, end: float, fps: float = 15.0):
    """find_swipe on the hand model's track, or on the motion track when the hand is a blur."""
    swipe = find_swipe(hand_track(video, start, end, fps))
    if swipe:
        return swipe, "following your hand"
    swipe = find_swipe(motion_track(video, start, end), max_time=0.8)
    return swipe, "following your hand (fast swipe)" if swipe else "no hand found, straight wipe"


def find_swipe(points: list[tuple[float, float]], min_travel: float = 0.35, max_time: float = 1.4):
    """The first quick sweep across the frame: (start, end, direction, path). direction +1 = to the right."""
    best = None
    for i in range(len(points)):
        for j in range(i + 2, len(points)):
            (t0, x0), (t1, x1) = points[i], points[j]
            if t1 - t0 > max_time:
                break
            if t1 - t0 > 0 and abs(x1 - x0) >= min_travel:
                seg = points[i:j + 1]
                steps = np.diff([p[1] for p in seg])
                direction = 1 if x1 > x0 else -1
                if (np.sign(steps) == direction).mean() >= 0.7 and (best is None or abs(x1 - x0) > best[4]):
                    best = (t0, t1, direction, seg, abs(x1 - x0))
        if best:
            break
    if not best:
        return None
    t0, t1, direction, seg, _ = best
    # Carry the line from the frame edge to the frame edge, so the reveal always completes.
    rate = (seg[-1][1] - seg[0][1]) / max(0.05, seg[-1][0] - seg[0][0])
    lead = abs((seg[0][1] - (0 if direction > 0 else 1)) / rate) if rate else 0.1
    tail = abs(((1 if direction > 0 else 0) - seg[-1][1]) / rate) if rate else 0.1
    path = [(seg[0][0] - min(0.4, lead), 0.0 if direction > 0 else 1.0)] + seg + \
           [(seg[-1][0] + min(0.4, tail), 1.0 if direction > 0 else 0.0)]
    return path[0][0], path[-1][0], direction, path


def straight_path(t: float, direction: int = 1, length: float = 0.45) -> list[tuple[float, float]]:
    return [(t, 0.0 if direction > 0 else 1.0), (t + length, 1.0 if direction > 0 else 0.0)]


def reveal_from_words(plan: dict, edit_dir: Path, t: float) -> str:
    import captions
    words = captions.output_words(plan, edit_dir, (plan.get("captions") or {}).get("corrections") or [])
    near = " ".join(w["text"].lower() for w in words if t - 2.5 <= w["start"] <= t + 2.5)
    near = re.sub(r"[^a-z& ]", " ", near)
    for keys, effect in REVEAL_WORDS:
        if any(re.search(rf"\b{re.escape(k)}\b", near) for k in keys):
            return effect
    return "dim"


def reveal_chain(name: str, W: int, H: int) -> str:
    if name.startswith("look:"):
        from looks import look_filter
        return look_filter(name)
    from effects import EFFECTS
    return EFFECTS.get(name, EFFECTS["dim"])[1](W, H, 1.0)


def line_expr(path: list[tuple[float, float]], tvar: str) -> str:
    return framing.pw_expr(tvar, path)


def wipe_effect(e: dict, plan: dict, idx: int, W: int, H: int) -> str:
    """Filter-graph text (continuing a chain) for one hand swipe reveal."""
    edit_dir = Path(plan.get("_edit_dir") or ".")
    base = plan.get("_base")
    t = float(e.get("start_in_output", 0.0))
    path, how = e.get("path"), "your path"
    if not path and base:
        swipe, how = track_swipe(Path(base), t - 0.8, t + 1.6)
        path = swipe[3] if swipe else None
    path = [tuple(p) for p in path] if path else straight_path(t, int(e.get("direction", 1)))
    if how.startswith("following"):
        # Trail the hand by a frame, so the hand drags the line rather than chasing it.
        lag = 1 / (_fps(Path(base)) if base else 30.0)
        path = [(pt + lag, x) for pt, x in path]
    t0, t1 = path[0][0], path[-1][0]
    direction = 1 if path[-1][1] > path[0][1] else -1
    end = t0 + float(e["duration"]) if e.get("duration") else 1e9
    reveal = e.get("reveal") or "auto"
    if reveal == "auto":
        reveal = reveal_from_words(plan, edit_dir, t0)
    chain = reveal_chain(reveal, W, H).replace("@", f"hw{idx}_")
    # The blend filter's own clock (T) runs wrong for some phone footage, so time comes from the
    # frame count instead: N / frames per second.
    fps = _fps(Path(base)) if base else 30.0
    P = line_expr(path, f"(N/{fps:.5f})")
    side = f"lt(X/W,{P})" if direction > 0 else f"gt(X/W,{P})"  # the side the hand has passed
    expr = f"if({side},B,A)"
    if e.get("line", True):
        expr = f"if(lt(abs(X/W-({P})),0.005),255,{expr})"
    a = f"hw{idx}"
    print(f"  hand swipe at {t0:.2f}-{t1:.2f}s ({how if path else 'straight wipe'}), reveals {reveal}")
    return (f",split[{a}o][{a}b];[{a}b]{chain},split[{a}c][{a}d];[{a}o]split[{a}o1][{a}o2];"
            f"[{a}o1]format=gbrp[{a}g1];[{a}c]format=gbrp[{a}g2];"
            f"[{a}g1][{a}g2]blend=all_expr='{expr}':enable='between(t,{t0:.3f},{t1:.3f})',format=yuv420p[{a}w];"
            f"[{a}o2][{a}w]overlay=enable='between(t,{t0:.3f},{t1:.3f})'[{a}x];"
            f"[{a}x][{a}d]overlay=enable='between(t,{t1:.3f},{end:.3f})'")


def xfade_expr(seg: Path, d: float) -> str:
    """xfade custom expression for a hand swipe into the next section, following the hand in the
    last moments of the outgoing clip (straight wipe if there's no hand)."""
    dur = _duration(seg)
    swipe, _ = track_swipe(seg, max(0.0, dur - d - 0.8), dur)
    if swipe:
        # Time within the transition = P * d, starting at dur - d in the outgoing clip.
        path = [(max(0.0, min(1.0, (t - (dur - d)) / d)), x) for t, x in swipe[3]]
        direction = swipe[2]
    else:
        path, direction = [(0.0, 0.0), (1.0, 1.0)], 1
    P = framing.pw_expr("P", path)
    side = f"lt(X/W,{P})" if direction > 0 else f"gt(X/W,{P})"
    white = "if(eq(PLANE,0),255,128)"
    return f"if(lt(abs(X/W-({P})),0.005),{white},if({side},B,A))"


def _fps(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                          "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True).stdout.strip() or "30/1"
    num, _, den = out.partition("/")
    return float(num) / float(den or 1)


def _duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(p)],
                         capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def find_in_plan(plan: dict, edit_dir: Path) -> list[dict]:
    """Every hand swipe in the kept footage, on the output timeline."""
    from plan import play_pos, range_len
    found, offset = [], 0.0
    for r in plan["ranges"]:
        src = Path(plan["sources"][r["source"]])
        src = src if src.is_absolute() else edit_dir / src
        if src.suffix.lower() in framing.IMAGE_EXTS or r.get("reverse"):
            offset += range_len(r)
            continue
        swipe, _ = track_swipe(src, float(r["start"]), float(r["end"]), fps=8)
        if swipe:
            found.append({"start_in_output": round(offset + play_pos(r, swipe[0] + 0.4), 2), "source": r["source"]})
        offset += range_len(r)
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description="Hand swipe reveals")
    ap.add_argument("cmd", choices=["find"])
    ap.add_argument("plan", type=Path)
    args = ap.parse_args()
    path = args.plan.resolve()
    plan = json.loads(path.read_text(encoding="utf-8"))
    hits = find_in_plan(plan, path.parent)
    for h in hits:
        print(f"swipe at {h['start_in_output']:.2f}s ({h['source']}): reveals {reveal_from_words(plan, path.parent, h['start_in_output'])}")
    if not hits:
        print("no hand swipes found")


if __name__ == "__main__":
    main()
