"""The "green screen explainer" layout: you, cut out, in a corner of the frame, over full-screen
backgrounds that change with each topic (aerial shots, renders, maps, screen recordings, photos).

plan.json:
    "presenter": {"position": "bottom_right" | "bottom_left" | "bottom_center",
                  "size": 0.62,        # how wide your picture is drawn, as a share of the frame width
                  "lift": 0.0},        # move up from the bottom edge (share of the frame height)
    "backdrops": [{"file": "backdrops/aerial.mp4", "start_in_output": 0, "duration": 6,
                   "motion": "zoom_in" | "zoom_out" | "pan_left" | "pan_right" | "none",
                   "transition": "whip" | "fade" | "cut", "src_start": 0}, ...]

Gaps between backdrops show your own background, softly blurred. Files are found next to the
project (the video folder), in its "backdrops" folder, or by absolute path.

Runs right after the cut is joined, before zooms, effects, graphics and captions, so everything
else lands on top as usual. The person cut-out (MediaPipe, once per cut, cached) and the
background track are both cached, so re-renders that only change captions are instant here.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np

import framing

POSITIONS = {"bottom_right": 1.0, "bottom_left": 0.0, "bottom_center": 0.5}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _sig(p: Path) -> list:
    try:
        st = Path(p).stat()
        return [str(p), st.st_mtime_ns, st.st_size]
    except OSError:
        return [str(p)]


def _probe(video: Path) -> tuple[int, int, str, float]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(video)],
                         capture_output=True, text=True).stdout
    d = json.loads(out)
    s = d["streams"][0]
    return int(s["width"]), int(s["height"]), s["r_frame_rate"], float(d["format"]["duration"])


def mask_video(base: Path, out: Path) -> Path:
    """A black-and-white video of where the person is (white = you), at a small size: cheap to
    make and FFmpeg scales it up smoothly. Cached by the base's content."""
    if out.exists():
        return out
    W, H, fps, _ = _probe(base)
    w = 256
    h = int(round(H * w / W / 2) * 2)
    reader = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(base), "-vf", f"scale={w}:{h}",
                               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    part = out.with_name(out.stem + "_part.mp4")
    writer = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
                               "-r", fps, "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "10",
                               "-pix_fmt", "yuv420p", str(part)], stdin=subprocess.PIPE)
    last = None
    try:
        while True:
            raw = reader.stdout.read(w * h * 3)
            if len(raw) < w * h * 3:
                break
            m = framing.person_mask(np.frombuffer(raw, np.uint8).reshape(h, w, 3))
            last = m if last is None else last * 0.4 + m * 0.6
            writer.stdin.write((np.clip((last - 0.35) / 0.35, 0, 1) * 255).astype(np.uint8).tobytes())
    finally:
        reader.stdout.close()
        reader.wait()
        writer.stdin.close()
        writer.wait()
    part.replace(out)
    return out


def _resolve(f: str, edit_dir: Path) -> Path | None:
    p = Path(f)
    if p.is_absolute():
        return p if p.exists() else None
    for c in (edit_dir.parent / f, edit_dir.parent / "backdrops" / f, edit_dir / f):
        if c.exists():
            return c
    return None


def _motion(kind: str, W: int, H: int, d: float, fps: str) -> str:
    """A slow camera move over one backdrop (zoompan: steady output size, works on photos and video)."""
    if kind == "none":
        return f"scale={W}:{H}"
    p = f"min(1,it/{max(0.1, d):.3f})"
    z = {"zoom_in": f"1+0.15*{p}", "zoom_out": f"1.15-0.15*{p}"}.get(kind, "1.15")
    x = {"pan_left": f"(iw-iw/zoom)*(1-{p})", "pan_right": f"(iw-iw/zoom)*{p}"}.get(kind, "(iw-iw/zoom)/2")
    y = "(ih-ih/zoom)/2"
    return f"scale={W * 2}:{H * 2},zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={fps}"


TAIL = 0.3  # every piece runs this much longer, so a transition overlaps it instead of shifting later ones


def background_track(plan: dict, edit_dir: Path, base: Path, out: Path) -> Path:
    """One video, the length of the cut, of every backdrop in turn (gaps: your own room, blurred).
    Each backdrop appears exactly at its start_in_output; whips and fades borrow from the end of the
    piece before, so nothing drifts."""
    W, H, fps, total = _probe(base)
    items = sorted([b for b in plan.get("backdrops") or [] if b.get("file")],
                   key=lambda b: float(b.get("start_in_output", 0)))
    pieces: list[tuple[str, float, str]] = []   # (source description, shown length, transition into it)
    chains: list[str] = []
    inputs = ["-i", str(base)]
    t = 0.0
    plan_pieces = []
    for b in items:
        a = max(t, float(b.get("start_in_output", 0)))
        d = min(float(b.get("duration", 4)), total - a)
        if d <= 0.05:
            continue
        if a - t > 0.05:
            plan_pieces.append(("room", t, a - t, "cut", None))
        plan_pieces.append(("file", a, d, b.get("transition", "whip"), b))
        t = a + d
    if total - t > 0.05 or not plan_pieces:
        plan_pieces.append(("room", t, total - t, "cut", None))
    n_room = sum(1 for k in plan_pieces if k[0] == "room" or _resolve(k[4]["file"], edit_dir) is None)
    if n_room:  # your own room, blurred, for any gaps (none when the backdrops fill the video)
        chains.append(f"[0:v]scale={W // 8 * 2}:{H // 8 * 2},boxblur=12:2,scale={W}:{H},setsar=1,fps={fps},format=yuv420p,"
                      f"split={n_room}" + "".join(f"[room{i}]" for i in range(n_room)))
    else:
        inputs = ["-f", "lavfi", "-i", "nullsrc=s=16x16:d=0.1"]  # input 0 stays a placeholder, unused
    n_in, room = 1, 0
    for i, (kind, a, d, tr, b) in enumerate(plan_pieces):
        tail = TAIL if i < len(plan_pieces) - 1 else 0.0
        src = _resolve(b["file"], edit_dir) if kind == "file" else None
        if kind == "file" and src is None:
            print(f"  presenter: backdrop not found: {b['file']} (showing your room there)")
        if src is None:
            chains.append(f"[room{room}]trim={a:.3f}:{a + d + tail:.3f},setpts=PTS-STARTPTS,settb=AVTB[g{i}]")
            room += 1
        else:
            if src.suffix.lower() in IMAGE_EXTS:
                inputs += ["-loop", "1", "-framerate", fps, "-t", f"{d + tail + 0.2:.3f}", "-i", str(src)]
            else:
                inputs += ["-stream_loop", "-1", "-ss", f"{float(b.get('src_start', 0)):.3f}",
                           "-t", f"{d + tail + 0.2:.3f}", "-i", str(src)]
            fill = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
            chains.append(f"[{n_in}:v]{fill},setsar=1,fps={fps},{_motion(b.get('motion', 'zoom_in'), W, H, d, fps)},"
                          f"setsar=1,format=yuv420p,trim=0:{d + tail:.3f},setpts=PTS-STARTPTS,settb=AVTB[g{i}]")
            n_in += 1
        pieces.append((f"[g{i}]", d, tr))
    # Joined stream so far is always (time shown) + TAIL long; a whip/fade overlaps that tail.
    cur, shown = pieces[0][0], pieces[0][1]
    for i, (lab, d, tr) in enumerate(pieces[1:], 1):
        if tr in ("whip", "fade"):
            kind = "slideleft" if tr == "whip" else "fade"
            chains.append(f"{cur}{lab}xfade=transition={kind}:duration={TAIL}:offset={shown:.3f}[j{i}]")
        else:
            chains.append(f"{cur}trim=0:{shown:.3f},setpts=PTS-STARTPTS,settb=AVTB[c{i}];[c{i}]{lab}concat=n=2:v=1:a=0[j{i}]")
        cur, shown = f"[j{i}]", shown + d
    chains.append(f"{cur}tpad=stop_mode=clone:stop_duration=1,trim=0:{total:.3f},format=yuv420p[bg]")
    part = out.with_name(out.stem + "_part.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chains), "-map", "[bg]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-an", str(part)], check=True)
    part.replace(out)
    return out


def apply(base: Path, plan: dict, edit_dir: Path, draft: bool = False) -> Path:
    """The cut with you placed over the backdrops (sound unchanged). Returns the new base."""
    spec = plan.get("presenter") or {}
    W, H, fps, total = _probe(base)
    cache = edit_dir / "animations"
    cache.mkdir(parents=True, exist_ok=True)
    bkey = _key(_sig(base))
    mask = mask_video(base, cache / f"presenter_mask_{bkey}.mp4")
    files = [_sig(_resolve(b["file"], edit_dir) or Path(b["file"])) for b in plan.get("backdrops") or [] if b.get("file")]
    gkey = _key(bkey, plan.get("backdrops"), files)
    bg = cache / f"presenter_bg_{gkey}.mp4"
    if not bg.exists():
        print(f"  presenter: building the background track ({len(plan.get('backdrops') or [])} backdrop(s))", flush=True)
        background_track(plan, edit_dir, base, bg)
    out = base.with_name(base.stem + f"_presenter_{_key(gkey, spec)}.mp4")
    if out.exists():
        return out
    size = min(1.0, max(0.25, float(spec.get("size", 0.62))))
    pw, ph = int(W * size) // 2 * 2, int(H * size) // 2 * 2
    side = POSITIONS.get(spec.get("position", "bottom_right"), 1.0)
    x = int((W - pw) * side + (0.06 * pw if side == 1.0 else -0.06 * pw if side == 0.0 else 0))
    y = int(H - ph - float(spec.get("lift", 0)) * H)
    graph = (f"[1:v]scale={W}:{H},format=gray[m];[0:v][m]alphamerge,scale={pw}:{ph}[p];"
             f"[2:v][p]overlay={x}:{y}:format=auto,format=yuv420p[v]")
    enc = ["-preset", "ultrafast", "-crf", "20"] if draft else ["-preset", "veryfast", "-crf", "16"]
    part = out.with_name(out.stem + "_part.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(base), "-i", str(mask), "-i", str(bg),
                    "-filter_complex", graph, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", *enc,
                    "-c:a", "copy", "-movflags", "+faststart", str(part)], check=True)
    part.replace(out)
    print(f"  presenter: you {spec.get('position', 'bottom_right').replace('_', ' ')}, {int(size * 100)}% wide")
    return out
