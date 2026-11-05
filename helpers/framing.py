"""Framing: find the face, punch in on it, reframe landscape to vertical, cut out the person.

Used by render.py for plan.json renders:

- Zooms (plan "zooms", output time):
      {"start_in_output": 11.0, "duration": 1.8, "scale": 1.15, "style": "punch"}
  punch = instant cut-in (classic jump-cut emphasis), push = smooth ramp in.
  Centred on the speaker's face so the zoom never crops the head.
- Reframe: when plan target aspect is 9:16 and a source is landscape, each
  segment is cropped to vertical around the face (median position in that range).
- Person mask: MediaPipe selfie segmenter, for titles that sit behind the speaker.
  The model (~250 KB, Google) is downloaded once into <repo>/models/.

Usage:
    python helpers/framing.py face <video> [start end]     # print face centre (0-1) + size
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
MODELS = REPO / "models"
SEGMENTER_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/"
                 "selfie_segmenter/float16/latest/selfie_segmenter.tflite")
FACE_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
            "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite")


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def pw_expr(var: str, pts: list[tuple[float, float]]) -> str:
    """Piecewise-linear FFmpeg expression through (time, value) points (keyframes)."""
    pts = sorted(pts)
    expr = f"{pts[-1][1]:.4f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(pts, pts[1:]))):
        slope = (v1 - v0) / max(1e-4, t1 - t0)
        expr = f"if(lt({var},{t1:.3f}),{v0:.4f}+({slope:.4f})*({var}-{t0:.3f}),{expr})"
    return f"if(lt({var},{pts[0][0]:.3f}),{pts[0][1]:.4f},{expr})"


def model_file(name: str, url: str) -> Path:
    """A small MediaPipe model, downloaded once from Google's model bucket."""
    MODELS.mkdir(exist_ok=True)
    path = MODELS / name
    if not path.exists():
        print(f"downloading {name} -> {path}", flush=True)
        urllib.request.urlretrieve(url, path)
    return path


# ---- frames ---------------------------------------------------------------

def probe_size(video: Path) -> tuple[int, int]:
    """Display size (rotation applied)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
                          "-of", "json", str(video)], capture_output=True, text=True, encoding="utf-8").stdout
    st = json.loads(out)["streams"][0]
    w, h = st["width"], st["height"]
    if any(abs(int(sd.get("rotation", 0))) in (90, 270) for sd in st.get("side_data_list", [])):
        w, h = h, w
    return w, h


def grab(video: Path, t: float, width: int = 480) -> np.ndarray | None:
    """One frame as an RGB array, scaled to `width` (ffmpeg applies rotation)."""
    w, h = probe_size(video)
    height = int(round(h * width / w / 2) * 2)
    seek = [] if Path(video).suffix.lower() in IMAGE_EXTS else ["-ss", f"{max(0.0, t):.3f}"]
    raw = subprocess.run(["ffmpeg", "-v", "error", *seek, "-i", str(video),
                          "-frames:v", "1", "-vf", f"scale={width}:{height}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    if len(raw) != width * height * 3:
        return None
    return np.frombuffer(raw, np.uint8).reshape(height, width, 3)


# ---- faces ----------------------------------------------------------------

@lru_cache(maxsize=1)
def _face_detector():
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision
    opts = vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=str(model_file("blaze_face_short_range.tflite", FACE_URL))),
        min_detection_confidence=0.5)
    return mp, vision.FaceDetector.create_from_options(opts)


@lru_cache(maxsize=256)
def face(video: str, start: float = 0.0, end: float | None = None, samples: int = 6) -> dict | None:
    """Median face centre (x, y as 0-1 of the frame) and face height (0-1) over a time range."""
    mp, detector = _face_detector()
    v = Path(video)
    if end is None:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(v)], capture_output=True, text=True)
        end = float(out.stdout.strip() or 1)
    hits = []
    for i in range(samples):
        t = start + (end - start) * (i + 0.5) / samples
        img = grab(v, t, width=960)
        if img is None:
            continue
        H, W = img.shape[:2]
        # The short-range detector squeezes its input to 128x128, which shrinks faces in a
        # wide frame to a few pixels; scan overlapping square tiles instead.
        n_tiles = 1 if W <= H * 1.2 else int(np.ceil(W / H)) + 1
        best = None
        for x0 in np.linspace(0, max(0, W - min(W, H)), n_tiles).astype(int):
            tile = img[:, x0:x0 + min(W, H)]
            for d in detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,
                                              data=np.ascontiguousarray(tile))).detections:
                bb = d.bounding_box
                if best is None or bb.width * bb.height > best[2] * best[3]:  # biggest face = speaker
                    best = (bb.origin_x + x0, bb.origin_y, bb.width, bb.height)
        if best:
            x, y, w, h = best
            hits.append(((x + w / 2) / W, (y + h / 2) / H, h / H))
    if not hits:
        return None
    a = np.median(np.array(hits), axis=0)
    return {"x": round(float(a[0]), 4), "y": round(float(a[1]), 4), "h": round(float(a[2]), 4)}


# ---- zooms ----------------------------------------------------------------

def zoom_center(plan: dict, edit_dir: Path, t_out: float) -> tuple[float, float]:
    """Face centre (0-1 of the output frame) at output time t, from the range playing then."""
    from plan import range_len
    offset = 0.0
    for r in plan["ranges"]:
        d = range_len(r)
        if offset <= t_out < offset + d:
            src = Path(plan["sources"][r["source"]])
            src = src if src.is_absolute() else edit_dir / src
            f = face(str(src), float(r["start"]), float(r["end"]), 4)
            if f:
                # Aim a little below the eyes-centre so chin and mouth stay in frame.
                return f["x"], min(0.75, f["y"] + f["h"] * 0.15)
            break
        offset += d
    return 0.5, 0.4


def zoom_filter(plan: dict, edit_dir: Path, W: int, H: int, fps: str) -> str | None:
    """A zoompan chain implementing every zoom; None if there are none.

    Each zoom shows a window (1/scale of the frame) centred on the face, clamped to the
    frame edges. `push` ramps the scale over the first 60% of the zoom.
    """
    zooms = [z for z in plan.get("zooms") or [] if float(z.get("duration", 0)) > 0]
    pulses = beat_pulses(plan, edit_dir)
    if not zooms and not pulses:
        return None
    terms_s, terms_x, terms_y, terms_on = [], [], [], []
    for z in zooms:
        a = float(z["start_in_output"])
        b = a + float(z["duration"])
        s = float(z.get("scale", 1.15))
        cx, cy = zoom_center(plan, edit_dir, a + 0.01)
        style = z.get("style", "punch")
        if z.get("keyframes"):  # [{"t": seconds into the zoom, "scale": 1.3, "x": 0.5, "y": 0.4}, ...]
            kf = z["keyframes"]
            rel = f"(it-{a:.3f})"
            s_expr = pw_expr(rel, [(float(k["t"]), float(k.get("scale", 1))) for k in kf])
            cx = pw_expr(rel, [(float(k["t"]), float(k.get("x", cx))) for k in kf])
            cy = pw_expr(rel, [(float(k["t"]), float(k.get("y", cy))) for k in kf])
        elif style == "push":
            ramp = max(0.2, (b - a) * 0.6)
            s_expr = f"(1+({s - 1:.4f})*min(1,(it-{a:.3f})/{ramp:.3f}))"
        elif style == "pull":  # starts close, eases back out
            s_expr = f"({s:.4f}-({s - 1:.4f})*min(1,(it-{a:.3f})/{max(0.2, (b - a) * 0.6):.3f}))"
        elif style == "drift":  # a slow creep across the whole window
            s_expr = f"(1+({s - 1:.4f})*(it-{a:.3f})/{b - a:.3f})"
        else:
            s_expr = f"{s:.4f}"
        on = f"between(it,{a:.3f},{b:.3f})"
        terms_s.append(f"{on}*({s_expr}-1)")
        terms_x.append(f"{on}*({cx if isinstance(cx, str) else format(cx, '.4f')})")
        terms_y.append(f"{on}*({cy if isinstance(cy, str) else format(cy, '.4f')})")
        terms_on.append(on)
    for t, s in pulses:  # a little kick on the beat that decays away
        terms_s.append(f"between(it,{t:.3f},{t + 0.18:.3f})*{s - 1:.4f}*(1-(it-{t:.3f})/0.18)")
    ZERO = ["0"]
    Z = f"1+{'+'.join(terms_s)}"
    # The zoom window centres on its face while a zoom runs, otherwise on the frame centre.
    rest = f"(1-min(1,{'+'.join(terms_on or ZERO)}))*0.5"
    CX = f"({'+'.join(terms_x or ZERO)}+{rest})"
    CY = f"({'+'.join(terms_y or ZERO)}+{rest})"
    x = f"min(max(0,{CX}*iw-iw/zoom/2),iw-iw/zoom)"
    y = f"min(max(0,{CY}*ih-ih/zoom/2),ih-ih/zoom)"
    # zoompan works in whole input pixels; 2x headroom keeps push zooms from stepping.
    return (f"scale={W * 2}:{H * 2}:flags=bicubic,"
            f"zoompan=z='{Z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fps},setsar=1")


def beat_pulses(plan: dict, edit_dir: Path) -> list[tuple[float, float]]:
    """(time, scale) kicks from plan "beat_sync": {"pulse": 1.04, "every": 2}. Empty if unset."""
    b = plan.get("beat_sync") or {}
    if not b.get("pulse") or not ((plan.get("audio") or {}).get("music") or {}).get("file"):
        return []
    import beats
    times = beats.music_beats(plan, edit_dir)
    return [(t, float(b["pulse"])) for t in times[::max(1, int(b.get("every", 2)))]]


# ---- reframe --------------------------------------------------------------

OUTPUT_SIZES = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080), "16:9": (1920, 1080)}


def output_size(aspect: str | None, draft: bool = False) -> tuple[int, int] | None:
    """Exact output canvas for a target aspect (draft renders at 2/3 size)."""
    if aspect not in OUTPUT_SIZES:
        return None
    w, h = OUTPUT_SIZES[aspect]
    k = 2 / 3 if draft else 1
    return int(w * k) // 2 * 2, int(h * k) // 2 * 2


def reframe_crop(source: Path, start: float, end: float, aspect: str) -> str | None:
    """Crop that fits a source to `aspect` around the face, or None if it already fits.

    Wider source -> crop the sides around the face. Taller source -> crop top/bottom,
    keeping the face in the upper-middle (where it sits in a talking-head frame).
    A much wider target (vertical source -> 16:9) can't be cropped; see reframe_pad.
    """
    if aspect not in OUTPUT_SIZES:
        return None
    w, h = probe_size(source)
    num, den = (int(x) for x in aspect.split(":"))
    target, src = num / den, w / h
    if abs(src - target) < 0.01 or target / src > 2.0:
        return None
    f = face(str(source), start, end)
    if src > target:
        cw = int(h * target) // 2 * 2
        cx = f["x"] if f else 0.5
        x = int(min(max(0, cx * w - cw / 2), w - cw))
        return f"crop={cw}:{h}:{track_x(source, start, end, w, cw) or x}:0"
    ch = int(w / target) // 2 * 2
    cy = f["y"] if f else 0.45
    # 4:5: face a little below centre (headroom for graphics, captions still under the chin).
    # 1:1: face centred; graphics go small at the top and captions near the bottom (captions.py).
    y = int(min(max(0, cy * h - ch * (0.56 if target < 0.9 else 0.5)), h - ch))
    return f"crop={w}:{ch}:0:{y}"


def track_x(source: Path, start: float, end: float, w: int, cw: int) -> str | None:
    """If the speaker moves around in this section, a crop position that follows them (smart
    reframe); None if they stay put, so a still shot doesn't get a wandering crop."""
    if end - start < 2.5:
        return None
    n = min(24, max(4, int((end - start) / 1.0)))
    step = (end - start) / n
    pts = []
    for i in range(n):
        t = i * step
        f = face(str(source), start + t, start + t + step, 1)
        if f:
            pts.append((t + step / 2, f["x"]))
    if len(pts) < 3 or max(p[1] for p in pts) - min(p[1] for p in pts) < 0.06:
        return None
    # Smooth, then glide between positions so the virtual camera pans rather than jitters.
    xs = [p[1] for p in pts]
    smooth = [float(np.mean(xs[max(0, i - 1):i + 2])) for i in range(len(xs))]
    keys = [(t, min(max(0.0, x * w - cw / 2), float(w - cw))) for (t, _), x in zip(pts, smooth)]
    return "'" + pw_expr("t", keys) + "'"


def fit_canvas(W: int, H: int, fill: str = "blur", color: str = "#000000") -> str:
    """Show the whole picture on the canvas, CapCut's "Canvas": over a blurred copy or a colour."""
    fg = f"scale={W}:{H}:force_original_aspect_ratio=decrease,setsar=1"
    if fill == "blur":
        return (f"split[fg][bg];[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=24:2,eq=brightness=-0.12[bgb];[fg]{fg}[fgs];[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    return f"{fg},pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={color.replace('#', '0x')}"


def reframe_pad(source: Path, aspect: str, W: int, H: int) -> str | None:
    """Vertical source into a much wider frame: the video centred over a blurred copy of itself."""
    if aspect not in OUTPUT_SIZES:
        return None
    w, h = probe_size(source)
    num, den = (int(x) for x in aspect.split(":"))
    if (num / den) / (w / h) <= 2.0:
        return None
    return (f"split[fg][bg];[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"boxblur=24:2,eq=brightness=-0.12[bgb];[fg]scale=-2:{H}[fgs];[bgb][fgs]overlay=(W-w)/2:0")


# ---- person mask ----------------------------------------------------------

@lru_cache(maxsize=1)
def _segmenter():
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision
    model = model_file("selfie_segmenter.tflite", SEGMENTER_URL)
    opts = vision.ImageSegmenterOptions(base_options=BaseOptions(model_asset_path=str(model)),
                                        running_mode=vision.RunningMode.IMAGE,
                                        output_confidence_masks=True)
    return mp, vision.ImageSegmenter.create_from_options(opts)


def person_mask(rgb: np.ndarray) -> np.ndarray:
    """Float mask 0-1 (1 = person) for an RGB frame."""
    mp, seg = _segmenter()
    res = seg.segment(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
    # A real copy: the view points into memory MediaPipe frees once `res` goes away.
    return np.array(res.confidence_masks[0].numpy_view(), dtype=np.float32).reshape(rgb.shape[:2])


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "face":
        start = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
        end = float(sys.argv[4]) if len(sys.argv) > 4 else None
        print(face(str(Path(sys.argv[2]).resolve()), start, end))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
