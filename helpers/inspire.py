"""Style samples: "make mine feel like this one."

Point it at videos you admire. It measures how they're edited and turns that into a recipe for
YOUR footage. Nothing from a sample ever goes into your video: not its clips, sound or words.

    python helpers/inspire.py analyze sample.mp4              # what it found, in plain English
    python helpers/inspire.py fetch "<link>" <videos>/inspiration   # save a public video by link
    python helpers/make.py <videos> --inspire sample.mp4      # build with it (or a folder of samples)

Samples can also just sit in <videos>/inspiration/, which make.py picks up by itself.

It measures: length and shape; how often the picture changes, and whether that's a new scene or a
jump within one; how close the framing is; how fast the person talks; whether music runs under the
voice; whether there are on-screen captions, where they sit and their highlight colour; whether
there's big text in the opening; and the colour (brightness, contrast, saturation, warmth).

It can't read a caption's font or describe a joke. Claude fills that in by looking at the contact
sheet (inspiration/<name>_sheet.jpg) and transcript: see the "Style samples" part of SKILL.md.
"""

from __future__ import annotations

import tools_path  # noqa: F401  (the fastest FFmpeg on this computer goes first on PATH)

import argparse
import hashlib
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

import framing

MIN_TEXT = 0.005  # measured: real captions 0.0066-0.019, a moving printed shirt 0.002-0.0034
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi"}
HUES = [("red", 345, 15), ("orange", 15, 40), ("yellow", 40, 70), ("green", 70, 170), ("cyan", 170, 200),
        ("blue", 200, 255), ("purple", 255, 300), ("pink", 300, 345)]
STYLE_BY_COLOUR = {"yellow": "viral", "orange": "viral", "green": "viral_green", "red": "viral_red",
                   "cyan": "viral_cyan", "blue": "viral_cyan", "purple": "viral_purple", "pink": "viral_purple"}


# ---- getting a sample -----------------------------------------------------------------------

def fetch(url: str, out_dir: Path) -> Path:
    """Save a public video from a link, using the free yt-dlp tool."""
    if not re.match(r"https?://", url):
        raise ValueError("That doesn't look like a web link.")
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {"outtmpl": str(out_dir / "sample_%(id)s.%(ext)s"), "format": "best[height<=1080]/best", "noplaylist": True,
            "quiet": True, "no_warnings": True, "noprogress": True, "max_filesize": 500 * 1024 * 1024, "restrictfilenames": True, "socket_timeout": 30, "retries": 2}
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return Path(ydl.prepare_filename(info))
    except DownloadError as e:
        raise RuntimeError("Couldn't get that video. Instagram and TikTok often block this, and private posts never "
                           "work. Save or screen-record it, then drop the file in instead.") from e


def sample_files(where: Path) -> list[Path]:
    if where.is_dir():
        return sorted(p for p in where.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    return [where] if where.suffix.lower() in VIDEO_EXTS and where.exists() else []


# ---- measuring ------------------------------------------------------------------------------

def _frames(video: Path, fps: float, width: int):
    w, h = framing.probe_size(video)
    height = int(round(h * width / w / 2) * 2)
    proc = subprocess.Popen(["ffmpeg", "-v", "quiet", "-i", str(video), "-vf", f"fps={fps},scale={width}:{height}",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size = width * height * 3
    try:
        while True:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                return
            yield np.frombuffer(raw, np.uint8).reshape(height, width, 3)
    finally:
        proc.stdout.close()
        proc.wait()


def picture_changes(video: Path, fps: float = 12.0, skip_band: tuple[float, float] | None = None) -> dict:
    """Every cut or snap in framing: a frame far more different from the last than is usual here.
    A big colour shift means a new scene; otherwise it's a jump within the same shot."""
    gray_prev, hist_prev = None, None
    diffs, hist_diffs = [], []
    for f in _frames(video, fps, 160):
        if skip_band:  # changing captions aren't a change of picture
            f = np.delete(f, range(int(skip_band[0] * f.shape[0]), int(skip_band[1] * f.shape[0])), axis=0)
        gray = f.mean(axis=2)
        hist = np.concatenate([np.histogram(f[..., c], bins=16, range=(0, 255))[0] for c in range(3)]) / f[..., 0].size
        if gray_prev is not None:
            diffs.append(float(np.abs(gray - gray_prev).mean()))
            hist_diffs.append(float(np.abs(hist - hist_prev).sum()))
        gray_prev, hist_prev = gray, hist
    if not diffs:
        return {"times": [], "scene": 0, "jump": 0}
    usual = float(np.median(diffs))
    limit = max(1.0, usual * 2.0)
    times, scene, jump = [], 0, 0
    gap = max(1, int(fps * 0.25))
    last = -gap
    for i, d in enumerate(diffs):
        # A peak: above the limit, the biggest around it, and far enough from the last one.
        # A cut is a single frame of sudden change. A gesture builds and fades over several frames.
        near = diffs[max(0, i - 2):i] + diffs[i + 1:i + 3]
        if d >= limit and d >= 2.0 * max(near or [0]) and i - last >= gap:
            times.append(round((i + 1) / fps, 2))
            if hist_diffs[i] > 1.0:
                scene += 1
            else:
                jump += 1
            last = i
    return {"times": times, "scene": scene, "jump": jump}


def colour_stats(video: Path, start: float = 0.0, end: float | None = None, n: int = 10) -> dict:
    """Brightness, contrast, saturation and warmth over a video, each on a simple scale."""
    dur = _duration(video)
    end = end or dur
    rows = []
    for i in range(n):
        f = framing.grab(video, start + (end - start) * (i + 0.5) / n, 240)
        if f is None:
            continue
        rgb = f.astype(np.float32)
        luma = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
        rows.append((luma.mean(), luma.std(), (rgb.max(axis=2) - rgb.min(axis=2)).mean(), (rgb[..., 0] - rgb[..., 2]).mean()))
    if not rows:
        return {}
    m = np.median(np.array(rows), axis=0)
    return {"brightness": round(float(m[0]), 1), "contrast": round(float(m[1]), 1),
            "saturation": round(float(m[2]), 1), "warmth": round(float(m[3]), 1)}


def find_captions(video: Path, fps: float = 6.0) -> dict | None:
    """Captions are thin bands of hard edges that appear suddenly and then hold exactly still, which
    moving clothes and hands don't. Returns the band's height in the frame, width and highlight
    colour, or None. Checked against clips with known captions (see MIN_TEXT)."""
    stack = np.stack([f for _, f in zip(range(240), _frames(video, fps, 360))])
    if len(stack) < 6:
        return None
    gray = stack.mean(axis=3).astype(np.float32)
    edges = np.abs(np.diff(gray, axis=2)) > 75
    # Hard edges that appear from nothing and then hold exactly still: text does, a shirt moving
    # with the person doesn't.
    still = np.abs(gray[2:, :, 1:] - gray[1:-1, :, 1:]) < 6
    fresh = edges[1:-1] & ~edges[:-2] & edges[2:] & still
    keep = fresh.mean(axis=(1, 2)) < 0.03              # skip cuts, where everything changes at once
    if keep.sum() < 4:
        return None
    fresh = fresh[keep]
    rows = fresh.mean(axis=(0, 2))
    H = rows.size
    window = max(3, int(H * 0.02))
    smooth = np.convolve(rows, np.ones(window) / window, mode="same")
    peak = int(smooth.argmax())
    if smooth[peak] < MIN_TEXT:
        return None
    y0, y1 = peak, peak
    while y0 > 0 and smooth[y0 - 1] >= smooth[peak] * 0.35:
        y0 -= 1
    while y1 < H - 1 and smooth[y1 + 1] >= smooth[peak] * 0.35:
        y1 += 1
    y1 += 1
    height = (y1 - y0) / H
    if height > 0.16:
        return None
    area = fresh[:, y0:y1]
    active = area.mean(axis=(1, 2)) > smooth[peak] * 0.25
    widths = []
    for frame in area[active]:
        cols = np.where(frame.any(axis=0))[0]
        if cols.size:
            widths.append((np.percentile(cols, 97) - np.percentile(cols, 3)) / stack.shape[2])
    # The highlight: vivid pixels in the band that come and go. Text colour, not the shirt behind it.
    band = stack[:, y0:y1].astype(np.float32)
    still = np.median(band, axis=0)
    changed = np.abs(band - still).max(axis=3) > 70
    hi, lo = band.max(axis=3), band.min(axis=3)
    vivid = changed & (hi > 170) & ((hi - lo) > 110)
    colour = None
    if vivid.sum() > 0.0015 * vivid.size:
        px = band[vivid]
        r, g, b = px[..., 0], px[..., 1], px[..., 2]
        top = px.max(axis=1)
        c = top - px.min(axis=1) + 1e-6
        hue = np.where(top == r, ((g - b) / c) % 6, np.where(top == g, (b - r) / c + 2, (r - g) / c + 4)) * 60
        counts = {name: int(((hue >= a) & (hue < b)).sum()) if a < b else int(((hue >= a) | (hue < b)).sum())
                  for name, a, b in HUES}
        colour = max(counts, key=counts.get)
    return {"y": round((y0 + y1) / 2 / H, 3), "width": round(float(np.median(widths)), 3) if widths else None,
            "highlight": colour, "on_screen": round(float(active.mean()), 2)}


def opening_text(video: Path, captions: dict | None) -> bool:
    """Big words in the first two seconds, away from where the captions sit (a hook title)?"""
    frames = [f for f in _frames_until(video, 2.0)]
    if len(frames) < 3:
        return False
    gray = np.stack(frames).mean(axis=3).astype(np.float32)
    edges = (np.abs(np.diff(gray, axis=2)) > 70).mean(axis=0)
    rows = edges.mean(axis=1)
    H = rows.size
    keep = np.ones(H, bool)
    if captions:
        keep[max(0, int((captions["y"] - 0.07) * H)):int((captions["y"] + 0.07) * H)] = False
    upper = rows[: int(H * 0.5)][keep[: int(H * 0.5)]]
    return bool(upper.size and np.convolve(upper, np.ones(9) / 9, mode="same").max() > 0.09)


def _frames_until(video: Path, seconds: float):
    for i, f in enumerate(_frames(video, 4.0, 360)):
        if i >= seconds * 4:
            return
        yield f


def speech_stats(video: Path) -> dict:
    """How fast they talk, what they say first, and whether sound carries on between the words."""
    from transcribe import DEFAULT_MODEL, load_local_model
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-map", "0:a:0?", "-ac", "1", "-ar", "16000",
                          "-f", "s16le", "-"], capture_output=True).stdout
    if len(raw) < 32000:
        return {"has_sound": False}
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    segments, _ = load_local_model(DEFAULT_MODEL).transcribe(audio, beam_size=1, vad_filter=True, word_timestamps=True)
    words = [w for s in segments for w in (s.words or [])]
    out: dict = {"has_sound": True, "words": len(words)}
    if len(words) >= 5:
        talking = sum(w.end - w.start for w in words)
        span = max(1.0, words[-1].end - words[0].start)
        out["words_per_second"] = round(len(words) / span, 2)
        out["transcript"] = "".join(w.word for w in words).strip()
        # Level in the gaps compared with the level of speech. A steady bed means music.
        # In the gaps between words: music is tonal (a few strong notes), room noise is a flat hiss.
        flat, level = [], []
        for a, b in zip(words, words[1:]):
            i, j = int((a.end + 0.03) * 16000), int((b.start - 0.03) * 16000)
            for k in range(i, j - 2048, 1024):
                seg = audio[k:k + 2048] * np.hanning(2048)
                power = np.abs(np.fft.rfft(seg))[13:512] ** 2 + 1e-12
                flat.append(float(np.exp(np.log(power).mean()) / power.mean()))
                level.append(float(np.sqrt((seg ** 2).mean())))
        if len(flat) >= 4:
            out["gap_ratio"] = round(statistics.median(flat), 4)
            # Measured: room noise 0.12-0.13, chords under a voice 0.03. A very faint bed can slip through.
            out["music_under_voice"] = out["gap_ratio"] < 0.07
        out["talking_share"] = round(talking / span, 2)
    return out


def contact_sheet(video: Path, out: Path, times: list[float], n: int = 12) -> None:
    """Frames for Claude to look at: spread evenly, weighted to the opening, which matters most."""
    dur = _duration(video)
    points = sorted({round(x, 2) for x in [0.2, 0.8, 1.6, 2.6] + [dur * (i + 1) / (n - 3) for i in range(n - 4)] if x < dur})
    tiles = [framing.grab(video, t, 200) for t in points]
    tiles = [Image.fromarray(t) for t in tiles if t is not None]
    if not tiles:
        return
    w, h = tiles[0].size
    cols = 6
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (w * cols, h * rows))
    for i, t in enumerate(tiles):
        sheet.paste(t, (w * (i % cols), h * (i // cols)))
    sheet.save(out, quality=85)


def _duration(video: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                          str(video)], capture_output=True, text=True).stdout.strip()
    return float(out or 0)


def analyze(video: Path, refresh: bool = False) -> dict:
    """Everything measured about one sample. Remembered beside it, because this takes a little while."""
    key = hashlib.sha1(f"{video.name}|{video.stat().st_size}|4".encode()).hexdigest()[:8]
    cache = video.with_name(f"{video.stem}_{key}.analysis.json")
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))
    dur = _duration(video)
    w, h = framing.probe_size(video)
    caps = find_captions(video)
    changes = picture_changes(video, skip_band=(max(0.0, caps["y"] - 0.07), min(1.0, caps["y"] + 0.07)) if caps else None)
    n_shots = len(changes["times"]) + 1
    face = framing.face(str(video), 0.0, dur, 8)
    a = {"file": video.name, "duration": round(dur, 1), "width": w, "height": h,
         "changes": changes["times"], "scene_changes": changes["scene"], "jump_cuts": changes["jump"],
         "avg_shot": round(dur / n_shots, 2), "changes_per_10s": round(len(changes["times"]) / max(1.0, dur) * 10, 1),
         "face": face, "captions": caps, "opening_title": opening_text(video, caps),
         "colour": colour_stats(video), "speech": speech_stats(video)}
    contact_sheet(video, video.with_name(f"{video.stem}_sheet.jpg"), changes["times"])
    cache.write_text(json.dumps(a, indent=1, ensure_ascii=False), encoding="utf-8")
    return a


# ---- from measurements to an edit -----------------------------------------------------------

def combine(items: list[dict]) -> dict:
    """Several samples become one target: the typical value of each measure."""
    if len(items) == 1:
        return items[0]

    def med(get):
        vals = [v for v in (get(a) for a in items) if v is not None]
        return statistics.median(vals) if vals else None
    first = items[0]
    caps = [a["captions"] for a in items if a.get("captions")]
    hues = [c["highlight"] for c in caps if c.get("highlight")]
    faces = [a["face"] for a in items if a.get("face")]
    out = dict(first)
    out.update(file=f"{len(items)} samples", duration=med(lambda a: a["duration"]), avg_shot=med(lambda a: a["avg_shot"]),
               changes_per_10s=med(lambda a: a["changes_per_10s"]),
               scene_changes=med(lambda a: a["scene_changes"]), jump_cuts=med(lambda a: a["jump_cuts"]),
               opening_title=sum(bool(a.get("opening_title")) for a in items) * 2 > len(items),
               colour={k: med(lambda a, k=k: (a.get("colour") or {}).get(k)) for k in ("brightness", "contrast", "saturation", "warmth")},
               speech={"words_per_second": med(lambda a: (a.get("speech") or {}).get("words_per_second")),
                       "music_under_voice": sum(bool((a.get("speech") or {}).get("music_under_voice")) for a in items) * 2 > len(items)},
               face={"h": statistics.median(f["h"] for f in faces), "x": 0.5, "y": statistics.median(f["y"] for f in faces)} if faces else None,
               captions=({"y": statistics.median(c["y"] for c in caps), "width": statistics.median(c["width"] or 0.5 for c in caps),
                          "highlight": max(set(hues), key=hues.count) if hues else None} if len(caps) * 2 >= len(items) else None))
    return out


def aspect_of(w: int, h: int) -> str:
    r = w / h
    return min({"9:16": 9 / 16, "4:5": 4 / 5, "1:1": 1.0, "16:9": 16 / 9}.items(), key=lambda kv: abs(kv[1] - r))[0]


def colour_adjust(sample: dict, mine: dict) -> dict:
    """Sliders that move your footage's colour toward the sample's. Kept gentle: different lighting
    can't be fully matched, and pushing hard looks worse than getting most of the way."""
    if not sample or not mine:
        return {}
    clamp = lambda v, m=0.45: round(max(-m, min(m, v)), 2)  # noqa: E731
    out = {"brightness": clamp((sample["brightness"] - mine["brightness"]) / 255 / 0.25),
           "contrast": clamp((sample["contrast"] / max(1.0, mine["contrast"]) - 1) / 0.5),
           "saturation": clamp(sample["saturation"] / max(1.0, mine["saturation"]) - 1),
           "temperature": clamp((sample["warmth"] - mine["warmth"]) / 45)}
    return {k: v for k, v in out.items() if abs(v) >= 0.04}


def recipe(a: dict, mine_colour: dict | None = None, my_rate: float | None = None) -> tuple[dict, list[str]]:
    """Preset settings from the measurements, plus plain-English notes on each decision."""
    notes: list[str] = []
    p: dict = {"target": {"aspect": aspect_of(a["width"], a["height"])}}
    if a.get("duration"):
        p["target"]["max_duration_s"] = int(round(a["duration"] * 1.25 / 5) * 5) or 15
        notes.append(f"about the same length: aim for {p['target']['max_duration_s']}s or under (sample is {a['duration']:.0f}s)")

    shot = a.get("avg_shot") or 5
    if shot < 1.5:
        p["cuts"] = {"max_gap": 0.2, "min_section": 0.8}
    elif shot < 3:
        p["cuts"] = {"max_gap": 0.3, "min_section": 1.2}
    elif shot < 6:
        p["cuts"] = {"max_gap": 0.45, "min_section": 1.5}
    else:
        p["cuts"] = {"max_gap": 0.8, "min_section": 3.0}
    notes.append(f"pacing: the picture changes every {shot:.1f}s, so pauses over {p['cuts']['max_gap']}s get cut")

    scenes, jumps = a.get("scene_changes") or 0, a.get("jump_cuts") or 0
    p["zooms"] = {"auto": False}
    if jumps >= 2 and jumps >= scenes:
        p["zooms"] = {"auto": True, "on_cuts": True, "scale": 1.12}
        notes.append("framing: it mostly jumps within one shot, so yours changes framing on the cuts too")
    elif scenes > jumps and scenes >= 3:
        notes.append(f"B-roll: it cuts to about {int(scenes)} different scenes. Add extra clips to match; Claude can place them")
    else:
        notes.append("framing: it's mostly steady, so zooms stay off")

    f = a.get("face")
    if f and f.get("h"):
        notes.append(f"framing: the face fills {'most of' if f['h'] > 0.28 else 'about a fifth of' if f['h'] > 0.14 else 'a small part of'} the height")

    caps = a.get("captions")
    if caps:
        hl = caps.get("highlight")
        style = STYLE_BY_COLOUR.get(hl or "", "clean")
        # A wide line is several words at a time; a narrow one is a word or two.
        words = 2 if (caps.get("width") or 0.5) < 0.35 else 3 if (caps.get("width") or 0.5) < 0.6 else 5
        p["captions"] = {"style": style, "position": {"x": 0.5, "y": caps["y"]}, "max_words": words, "auto_emphasis": True}
        notes.append(f"captions: about {int(caps['y'] * 100)}% down, around {words} words at a time"
                     + (f", highlighted in {hl}" if hl else ", no colour highlight") + f" (closest style: {style})")
    else:
        notes.append("captions: none found in the sample, but yours stay on because they help viewers who watch muted. "
                     "Turn them off in the Captions tab if you want to match it")

    p["graphics"] = {"progress": False, "cta": False}
    p["title"] = {"behind_subject": True, "font": "sans", "duration": 2.0} if a.get("opening_title") else {}
    notes.append("opening: it has big text up front, so a hook title goes in if you give one" if a.get("opening_title")
                 else "opening: no big title found, so yours has none")

    speech = a.get("speech") or {}
    p["audio"] = {"voice": "clean", "music_volume_db": -24}
    if speech.get("music_under_voice"):
        p["audio"]["music"] = "auto"
        notes.append("sound: music runs under the voice. Yours gets one if you have a track (the sample's own sound isn't used)")
    else:
        notes.append("sound: no obvious music under the voice")
    wps = speech.get("words_per_second")
    if wps and my_rate and my_rate < wps * 0.92 and my_rate < 3.5:  # never rush someone already quick
        p["pace_exact"] = round(min(1.12, wps / my_rate), 2)
        notes.append(f"pace: it talks at {wps:.1f} words a second and you at {my_rate:.1f}, so yours plays at {p['pace_exact']}×")
    elif wps:
        notes.append(f"pace: it talks at {wps:.1f} words a second; yours stays natural")

    adj = colour_adjust(a.get("colour") or {}, mine_colour or {})
    p["grade"] = None
    p["adjust"] = adj
    if adj:
        notes.append("colour: moved toward the sample (" + ", ".join(f"{k} {v:+.2f}" for k, v in adj.items()) + ")")
    else:
        notes.append("colour: already close to the sample, so left alone")
    return p, notes


def describe(a: dict) -> str:
    speech = a.get("speech") or {}
    caps = a.get("captions")
    lines = [f"{a['file']}: {a['duration']}s, {a['width']}x{a['height']}",
             f"  picture changes: {len(a.get('changes') or [])} (every {a['avg_shot']}s) - {a['jump_cuts']} jumps in the same shot, {a['scene_changes']} new scenes",
             f"  face: {a.get('face') or 'none found'}",
             f"  captions: {caps or 'none found'}",
             f"  big text in the opening: {'yes' if a.get('opening_title') else 'no'}",
             f"  colour: {a.get('colour')}",
             f"  speech: {speech.get('words_per_second', '?')} words/s, music under voice: {speech.get('music_under_voice', 'unknown')}"]
    if speech.get("transcript"):
        lines.append(f"  opens with: {speech['transcript'][:140]}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Learn an editing style from sample videos")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("analyze")
    p.add_argument("sample", type=Path)
    p.add_argument("--refresh", action="store_true")
    p = sub.add_parser("fetch")
    p.add_argument("url")
    p.add_argument("out_dir", type=Path)
    args = ap.parse_args()
    if args.cmd == "fetch":
        try:
            print(f"saved {fetch(args.url, args.out_dir.resolve())}")
        except (ValueError, RuntimeError) as e:
            sys.exit(str(e))
        return
    files = sample_files(args.sample.resolve())
    if not files:
        sys.exit(f"no video found at {args.sample}")
    items = [analyze(f, args.refresh) for f in files]
    for a in items:
        print(describe(a))
    _, notes = recipe(combine(items))
    print("recipe:")
    for n in notes:
        print(f"  - {n}")


if __name__ == "__main__":
    main()
