"""Render a video from an EDL.

Implements the HEURISTICS render pipeline in the correct order:

  1. Per-segment extract with color grade + 30ms audio fades baked in
  2. Lossless -c copy concat into base.mp4
  3. If overlays or subtitles: single filter graph that overlays animations
     (with PTS shift so frame 0 lands at the overlay window start)
     and applies `subtitles` filter LAST → final.mp4

Optionally builds a master SRT from the per-source transcripts + EDL
output-timeline offsets, applies the proven force_style (2-word
UPPERCASE chunks, Helvetica 18 Bold, MarginV=35).

Usage:
    python helpers/render.py <edl.json> -o final.mp4
    python helpers/render.py <edl.json> -o preview.mp4 --preview
    python helpers/render.py <edl.json> -o final.mp4 --build-subtitles
    python helpers/render.py <edl.json> -o final.mp4 --no-subtitles
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

# Speed: pieces that haven't changed since the last render are reused (each file is named after a
# fingerprint of everything that goes into it), and sections are cut several at a time.
CACHE_VERSION = "3"
WORKERS = max(1, min(int(os.environ.get("RENDER_WORKERS", 0)) or 3, os.cpu_count() or 3))


def _fingerprint(*parts) -> str:
    return hashlib.sha1(json.dumps([CACHE_VERSION, *parts], sort_keys=True, default=str).encode()).hexdigest()[:12]


def _file_sig(p: Path) -> list:
    try:
        st = Path(p).stat()
        return [str(Path(p).resolve()), st.st_mtime_ns, st.st_size]
    except OSError:
        return [str(p)]


def _per_file(fn):
    """Remember an ffprobe answer per file (and per change to it): a render asks the same few
    questions about the source dozens of times."""
    cache: dict = {}

    @functools.wraps(fn)
    def wrapper(video, *a):
        key = (*_file_sig(video), *a)
        if len(key) < 3:
            return fn(video, *a)
        if key not in cache:
            cache[key] = fn(video, *a)
        return cache[key]
    return wrapper


def _reuse(out: Path, key: str) -> bool:
    """True when `out` was built from exactly these inputs last time."""
    k = out.with_name(out.name + ".key")
    return out.exists() and out.stat().st_size > 0 and k.exists() and k.read_text() == key


def _stamp(out: Path, key: str) -> None:
    out.with_name(out.name + ".key").write_text(key)


STANDARD_RATES = ["24000/1001", "24", "25", "30000/1001", "30", "50", "60000/1001", "60"]


def _standard_rate(rate: str) -> str:
    """The nearest everyday frame rate (phones record 29.97-and-a-bit; hardware encoders want 29.97)."""
    f = float(Fraction(rate))
    best = min(STANDARD_RATES, key=lambda r: abs(float(Fraction(r)) - f))
    return best if abs(float(Fraction(best)) - f) / f < 0.005 else f"{f:.3f}"


@functools.lru_cache(maxsize=1)
def hardware_encoder() -> list[str] | None:
    """The computer's own video-encoder chip, if it has one that works (what phone and desktop editing
    apps use to export in seconds). Quality-based mode: every frame kept, no size cap. None means
    "use the software encoder". RENDER_SOFTWARE_ENCODER=1 turns it off."""
    if os.environ.get("RENDER_SOFTWARE_ENCODER"):
        return None
    if os.name == "nt":
        cands = [["-c:v", "h264_mf", "-hw_encoding", "true", "-rate_control", "quality", "-quality", "88"]]
    elif sys.platform == "darwin":
        cands = [["-c:v", "h264_videotoolbox", "-q:v", "62", "-allow_sw", "0"]]
    else:
        cands = []
    for c in cands:
        try:
            r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=1",
                                "-vf", "format=nv12", *c, "-f", "mp4", "-movflags", "frag_keyframe+empty_moov",
                                "-"], capture_output=True, timeout=40)
            if r.returncode == 0 and len(r.stdout) > 20000:
                return c
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def _frame_count(video: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames",
                          "-of", "default=nw=1:nk=1", str(video)], capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else -1


class _RenderLock:
    """One render at a time per project and quality (preview or export), so a background
    "get the export ready" render and the real export share their pieces instead of colliding."""

    def __init__(self, edit_dir: Path, mode: str):
        self.path = edit_dir / f".render_{mode}.lock"

    def __enter__(self):
        self.f = open(self.path, "a+b")
        if os.name == "nt":
            import msvcrt
            import time as _t
            while True:
                try:
                    self.f.seek(0)
                    msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    _t.sleep(0.5)
        else:
            import fcntl
            fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f, fcntl.LOCK_UN)
        finally:
            self.f.close()

try:
    from grade import get_preset, auto_grade_for_clip  # same directory
except Exception:
    def get_preset(name: str) -> str:
        return ""

    def auto_grade_for_clip(video, start=0.0, duration=None, verbose=False):  # type: ignore
        return "eq=contrast=1.03:saturation=0.98", {}


# -------- Subtitle style (bold-overlay, proven at 1920×1080 and 1080×1920) --
#
# MarginV is NOT taste — it is a platform safe-zone rule.
# TikTok / IG Reels / Shorts UI (caption, username, music, right-rail actions)
# covers roughly the bottom ~25–30% of a 1080×1920 frame. Captions placed near
# the bottom edge get clipped or obscured by the UI. libass auto-scales the
# render canvas relative to PlayResY=288, so MarginV=90 lands the caption
# baseline roughly 30% up from the bottom on any aspect — clear of the UI on
# every major vertical-video platform. Do not drop this below ~75 without a
# specific reason.
SUB_FORCE_STYLE = (
    "FontName=Helvetica,FontSize=18,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
    "BorderStyle=1,Outline=2,Shadow=0,"
    "Alignment=2,MarginV=90"
)

# -------- Helpers ------------------------------------------------------------


def run(cmd: list[str], quiet: bool = False) -> None:
    if not quiet:
        print(f"  $ {' '.join(str(c) for c in cmd[:6])}{' …' if len(cmd) > 6 else ''}")
    subprocess.run(cmd, check=True)


def resolve_grade_filter(grade_field: str | None) -> str:
    """The EDL's 'grade' field can be a preset name, a raw ffmpeg filter, or 'auto'.

    Returns the filter string to embed into the per-segment -vf chain.
    For 'auto', returns the sentinel "__AUTO__" which is resolved per-segment.
    """
    if not grade_field:
        return ""
    if grade_field == "auto":
        return "__AUTO__"
    if grade_field.startswith("look:"):
        from looks import look_filter
        return look_filter(grade_field)
    if grade_field.startswith("lut:") or grade_field.endswith(".cube"):
        from grade import lut_filter
        return lut_filter(grade_field)
    # Preset names are short identifiers, filter strings contain '=' or ','.
    if re.fullmatch(r"[a-zA-Z0-9_\-]+", grade_field):
        try:
            return get_preset(grade_field)
        except KeyError:
            print(f"warning: unknown preset '{grade_field}', using as raw filter")
            return grade_field
    return grade_field


def resolve_path(maybe_path: str, base: Path) -> Path:
    """Resolve a path that may be absolute or relative to `base`."""
    p = Path(maybe_path)
    if p.is_absolute():
        return p
    return (base / p).resolve()


def resolve_subtitles_path(maybe_path: str, edit_dir: Path) -> Path:
    """Resolve the EDL's subtitles path: relative to the EDL's directory, else the
    current directory (agents often write "edit/master.srt"). A missing file is an
    error: rendering on without it silently ships a video with no captions."""
    candidates = [resolve_path(maybe_path, edit_dir)]
    if not Path(maybe_path).is_absolute():
        candidates.append(Path(maybe_path).resolve())
    for c in candidates:
        if c.exists():
            return c
    tried = ", ".join(str(c) for c in candidates)
    sys.exit(f"subtitles file in EDL not found (tried {tried}). Fix the path or pass --no-subtitles.")


# -------- HDR → SDR tone mapping (HLG / PQ sources) --------------------------
#
# iPhone defaults to HLG HDR in Rec.2020 (and many mirrorless cameras ship PQ).
# If the source is HDR and we only downconvert bit depth (yuv420p10le → yuv420p)
# without tone-mapping, the output is 8-bit but still carries HLG/PQ transfer
# metadata. Players that honor the metadata (screen recorders, most social
# upload re-encodes) interpret 8-bit values in an HDR container and the result
# looks oversaturated / blown out. QuickTime on macOS can hide this locally —
# screen recording and uploaded renders cannot.
#
# Fix: detect HDR via color_transfer and prepend a zscale+tonemap chain to the
# vf graph so the output is clean Rec.709 SDR.

HWDEC = ["-hwaccel", "auto"] if os.environ.get("RENDER_HWDEC") else []  # opt-in: several at once can stall the GPU decoder
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ (HDR10) and HLG

TONEMAP_CHAIN = (
    "zscale=t=linear:npl=100,"
    "format=gbrpf32le,"
    "zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,"
    "zscale=t=bt709:m=bt709:r=tv,"
    "format=yuv420p"
)


@_per_file
def is_hdr_source(video: Path) -> bool:
    """Return True if the source uses a PQ or HLG transfer function."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_transfer",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip() in HDR_TRANSFERS
    except subprocess.CalledProcessError:
        return False


@_per_file
def is_portrait_source(video: Path) -> bool:
    """Return True if the displayed video is portrait, including rotation."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=width,height:stream_side_data=rotation",
             "-of", "json", str(video)],
            capture_output=True, text=True, check=True,
        )
        streams = json.loads(out.stdout).get("streams") or []
        if not streams:
            return False
        stream = streams[0]
        w, h = int(stream["width"]), int(stream["height"])

        # ffmpeg autorotates display-matrix side data before applying filters.
        # Swap coded dimensions for quarter-turns so the scale axis is selected
        # from the dimensions the filter actually sees. A plain metadata tag is
        # intentionally ignored because it does not guarantee autorotation.
        rotation = 0
        for side_data in stream.get("side_data_list") or []:
            if side_data.get("rotation") is not None:
                rotation = side_data["rotation"]
                break
        if int(round(float(rotation))) % 360 in (90, 270):
            w, h = h, w
        return h > w
    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        OSError,
        OverflowError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return False


def parse_fps(value: str) -> str:
    """Validate and canonicalize an ffmpeg frame rate."""
    text = value.strip()
    if len(text) > 32 or not re.fullmatch(
        r"(?:[0-9]+(?:\.[0-9]+)?|[0-9]+/[0-9]+)", text
    ):
        raise argparse.ArgumentTypeError(
            "FPS must be a positive number or rational, e.g. 30 or 30000/1001"
        )
    try:
        rate = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise argparse.ArgumentTypeError(
            "FPS must be a positive number or rational, e.g. 30 or 30000/1001"
        ) from exc
    if rate <= 0:
        raise argparse.ArgumentTypeError("FPS must be greater than zero")
    # FFmpeg stores video rates as AVRational (signed 32-bit components).
    # Bounding the reduced fraction keeps every accepted canonical value safe
    # for ffmpeg and makes parse_fps(parse_fps(value)) idempotent.
    max_component = 2_147_483_647
    if rate.numerator > max_component or rate.denominator > max_component:
        raise argparse.ArgumentTypeError("FPS precision or magnitude is too large")
    return f"{rate.numerator}/{rate.denominator}"


@_per_file
def probe_source_fps(video: Path) -> str | None:
    """Return an ffmpeg-ready source rate, preferring the average frame rate.

    ``avg_frame_rate`` represents the observed average and is the better default
    for variable-frame-rate inputs. ``r_frame_rate`` remains a fallback for
    streams where the average is unavailable. Values are normalized to an exact
    rational so rates such as ``30000/1001`` survive without rounding.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate,r_frame_rate",
             "-of", "json", str(video)],
            capture_output=True, text=True, check=True,
        )
        streams = json.loads(out.stdout).get("streams") or []
        if not streams:
            return None
        for field in ("avg_frame_rate", "r_frame_rate"):
            value = streams[0].get(field)
            if value and value != "0/0":
                try:
                    return parse_fps(value)
                except argparse.ArgumentTypeError:
                    continue
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return None
    return None


# -------- Per-segment extraction (Rule 2 + Rule 3) --------------------------


def atempo_chain(speed: float) -> list[str]:
    """atempo only takes 0.5-2.0 per instance; chain instances for bigger changes."""
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return parts


# Transition into a section (plan range "transition": {"type", "duration"}). Most are FFmpeg
# xfade wipes; the cut point stays put because both sides borrow a little extra footage.
TRANSITIONS = {
    "crossfade": ("fade", 0.4), "dissolve": ("dissolve", 0.5), "fade_black": ("fadeblack", 0.6),
    "fade_white": ("fadewhite", 0.5), "flash": ("fadewhite", 0.2), "whip_left": ("smoothleft", 0.25),
    "whip_right": ("smoothright", 0.25), "whip_up": ("smoothup", 0.25), "slide_left": ("slideleft", 0.35),
    "slide_right": ("slideright", 0.35), "slide_up": ("slideup", 0.35), "wipe_left": ("wipeleft", 0.4),
    "wipe_right": ("wiperight", 0.4), "circle_open": ("circleopen", 0.5), "circle_close": ("circleclose", 0.5),
    "radial": ("radial", 0.5), "zoom_in": ("zoomin", 0.4), "pixelate": ("pixelize", 0.4),
    "blur": ("hblur", 0.4), "squeeze": ("squeezeh", 0.35), "diagonal": ("diagtl", 0.4),
    "hand_swipe": ("custom", 0.6),  # follows your hand across the frame (handwipe.py)
}


def extract_segment(
    source: Path,
    seg_start: float,
    duration: float,
    grade_filter: str,
    out_path: Path,
    preview: bool = False,
    draft: bool = False,
    rate: str | None = None,
    crop: str | None = None,
    out_size: tuple[int, int] | None = None,
    fx: dict | None = None,
) -> None:
    """Extract a cut range as its own MP4 with grade + 30ms audio fades baked in.

    `-ss` before `-i` for fast accurate seeking. Scale to 1080p from 4K.
    Portrait sources (height > width) are scaled by height to preserve orientation.
    With `out_size` (plan target aspect), `crop` has already fitted the shape (a crop,
    or a blurred-background pad from framing.reframe_pad) and the result is scaled exactly.

    Quality ladder:
      - final (default): 1080p libx264 fast CRF 20
      - preview:         1080p libx264 medium CRF 22 (evaluable for QC)
      - draft:           720p libx264 ultrafast CRF 28 (cut-point check only)
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if fx and fx.get("boomerang"):
        # Plays forward, then the same moment backwards: render it once, then add the reverse.
        total, hold = fx.get("frames"), float(fx.get("hold") or 0.0)
        one = {**fx, "boomerang": False, "hold": 0, "frames": max(1, int(total) // 2) if total else None}
        fwd = out_path.with_name(out_path.stem + "_fwd.mp4")
        extract_segment(source, seg_start, duration, grade_filter, fwd, preview, draft, rate, crop, out_size, one)
        out_rate = rate if rate is not None else (probe_source_fps(fwd) or "30")
        v_tail = (f",tpad=stop_mode=clone:stop_duration={hold + 0.5:.3f}") if (hold or total) else ""
        a_tail = f",apad,atrim=0:{total / Fraction(out_rate):.5f}" if total else (f",apad=pad_dur={hold:.3f}" if hold else "")
        graph = (f"[0:v]split[f][b];[b]reverse[r];[f][r]concat=n=2:v=1:a=0{v_tail}[v];"
                 f"[0:a]asplit[x][y];[y]areverse[ry];[x][ry]concat=n=2:v=0:a=1{a_tail}[a]")
        preset, crf = ("ultrafast", "28") if draft else (("medium", "22") if preview else ("veryfast", "18"))
        subprocess.run(["ffmpeg", "-y", "-i", str(fwd), "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                        "-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p", "-r", out_rate,
                        *(["-frames:v", str(total)] if total else []),
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart",
                        str(out_path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        fwd.unlink(missing_ok=True)
        return

    # A reframe crop (landscape -> vertical, framing.py) makes the output portrait.
    portrait = is_portrait_source(source) or crop is not None
    if out_size:
        scale = f"scale={out_size[0]}:{out_size[1]},setsar=1"
    elif draft:
        scale = "scale=-2:1280" if portrait else "scale=1280:-2"
    else:
        scale = "scale=-2:1920" if portrait else "scale=1920:-2"

    fx = fx or {}
    speed = float(fx.get("speed") or 1.0)
    hold = float(fx.get("hold") or 0.0)

    from plan import IMAGE_EXTS, curve_slices
    is_image = source.suffix.lower() in IMAGE_EXTS
    slices = curve_slices({"start": 0.0, "end": duration, "speed": speed, "speed_curve": fx.get("speed_curve"),
                           "reverse": fx.get("reverse")})
    # Frame rate: use the rate the caller resolved once for the whole render
    # (every segment must share it — concat -c copy in Rule 2 requires a uniform
    # frame rate). When called standalone with no rate, preserve this source's
    # own rate; fall back to 24 only if it can't be probed.
    out_rate = rate if rate is not None else (probe_source_fps(source) or "24")

    vf_parts: list[str] = []
    if fx.get("stabilize"):
        vf_parts.append("deshake=rx=32:ry=32")
    if fx.get("flip") in ("h", "both"):
        vf_parts.append("hflip")
    if fx.get("flip") in ("v", "both"):
        vf_parts.append("vflip")
    rot = float(fx.get("rotate") or 0) % 360
    if rot == 90:
        vf_parts.append("transpose=1")
    elif rot == 270:
        vf_parts.append("transpose=2")
    elif rot == 180:
        vf_parts.append("hflip,vflip")
    elif rot:  # a tilted shot: rotate and zoom just enough to hide the corners
        vf_parts.append(f"rotate={rot}*PI/180:ow=iw:oh=ih")
        grow = abs(__import__("math").cos(rot * 3.14159265 / 180)) + abs(__import__("math").sin(rot * 3.14159265 / 180))
        vf_parts.append(f"scale=iw*{grow:.3f}:-2,crop=iw/{grow:.3f}:ih/{grow:.3f}")
    if crop:
        vf_parts.append(crop)
    if is_hdr_source(source):
        # iPhone HDR -> normal colour. After the crop, so it works on the part of the picture we keep
        # (about a third of a landscape frame for a vertical video): the slowest step, done 2-3x faster.
        vf_parts.append(TONEMAP_CHAIN)
    vf_parts.append(scale)
    if fx.get("gains"):
        r_, g_, b_ = fx["gains"]
        vf_parts.append(f"colorchannelmixer=rr={r_:.3f}:gg={g_:.3f}:bb={b_:.3f}")
    if grade_filter:
        vf_parts.append(grade_filter)
    if fx.get("adjust"):
        from looks import adjust_filter
        chain = adjust_filter(fx["adjust"])
        if chain:
            vf_parts.append(chain)
    if fx.get("motion") and out_size:
        vf_parts.append(motion_filter(fx["motion"], duration / speed, out_size, out_rate))
    if fx.get("reverse"):
        vf_parts.append("reverse")
    if slices:
        X, expr = "(T-STARTT)", ""
        o = sum((b - a) / sp for a, b, sp in slices[:-1])
        a, _, sp = slices[-1]
        expr = f"({o:.4f}+({X}-{a:.4f})/{sp})"
        for a, b, sp in reversed(slices[:-1]):
            o -= (b - a) / sp
            expr = f"if(lt({X},{b:.4f}),{o:.4f}+({X}-{a:.4f})/{sp},{expr})"
        vf_parts.append(f"setpts='({expr})/TB'")
    elif speed != 1.0:
        vf_parts.append(f"setpts=(PTS-STARTPTS)/{speed:.4f}")
    if fx.get("smooth") and not draft and (slices or speed < 1.0):
        vf_parts.append(f"minterpolate=fps={out_rate}:mi_mode=mci:mc_mode=aobmc:vsbmc=1")  # real in-between frames
    if hold > 0:
        vf_parts.append(f"tpad=stop_mode=clone:stop_duration={hold:.3f}")  # freeze the last frame
    exact = fx.get("frames")  # the section's exact length, so small errors can't add up over a video
    if exact:
        vf_parts.append("tpad=stop_mode=clone:stop_duration=0.5")  # never a frame short
    vf = ",".join(vf_parts)

    # 30ms audio fades at both edges (Rule 3) — prevent pops. Speed/reverse/freeze happen first,
    # so the fades land on the edges of what actually plays.
    play = sum((b - a) / sp for a, b, sp in slices) if slices else duration / speed
    af_parts = ["areverse"] if fx.get("reverse") else []
    if speed != 1.0 and not slices:
        af_parts += atempo_chain(speed)
    if fx.get("voice_fx"):
        from audio import voice_fx_filter
        chain = voice_fx_filter(fx["voice_fx"])
        if chain:
            af_parts.append(chain)
    if fx.get("volume") is not None:
        af_parts.append(f"volume={float(fx['volume']):.3f}")
    af_parts.append(f"afade=t=in:st=0:d=0.03,afade=t=out:st={max(0.0, play - 0.03):.3f}:d=0.03")
    if hold > 0:
        af_parts.append(f"apad=pad_dur={hold:.3f}")
    if exact:
        af_parts.append(f"apad,atrim=0:{exact / Fraction(out_rate):.5f}")
    af = ",".join(af_parts)
    has_audio = not is_image and has_audio_stream(source)

    if draft:
        preset, crf = "ultrafast", "28"
    elif preview:
        preset, crf = "medium", "22"
    else:
        preset, crf = "veryfast", "18"  # an in-between file: quick, with quality to spare for the last pass

    # -t is an *input* option so speed changes and freezes can make the output longer.
    # Clips without sound (B-roll, screen recordings) get silence so every segment concats.
    silence = [] if has_audio else ["-f", "lavfi", "-t", f"{duration:.3f}",
                                    "-i", "anullsrc=r=48000:cl=stereo"]
    a_in = "0:a:0" if has_audio else "1:a:0"
    if slices:  # a speed curve: each slice of sound at its own tempo, then joined
        n = len(slices)
        parts = [f"[{a_in}]asplit={n}" + "".join(f"[as{i}]" for i in range(n))]
        for i, (a, b, sp) in enumerate(slices):
            parts.append(f"[as{i}]atrim={a:.4f}:{b:.4f},asetpts=PTS-STARTPTS,{','.join(atempo_chain(sp))}[at{i}]")
        parts.append("".join(f"[at{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1,{af}[aout]")
        audio_args = ["-filter_complex", ";".join(parts), "-map", "0:v:0", "-map", "[aout]"]
    else:
        audio_args = ["-map", "0:v:0", "-map", a_in, "-af", af]
    # The graphics chip decodes phone video (HEVC) when it can; FFmpeg falls back to the CPU if not.
    image_in = ["-loop", "1", "-framerate", out_rate] if is_image else [*HWDEC, "-ss", f"{seg_start:.3f}"]
    cmd = [
        "ffmpeg", "-y",
        *image_in, "-t", f"{duration:.3f}",
        "-i", str(source),
        *silence,
        *audio_args,
        "-vf", vf,
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-r", out_rate, *(["-frames:v", str(exact)] if exact else []),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


@_per_file
def has_audio_stream(source: Path) -> bool:
    return bool(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                                "stream=index", "-of", "csv=p=0", str(source)], capture_output=True, text=True).stdout.strip())


def motion_filter(kind: str, play: float, size: tuple[int, int], rate: str) -> str:
    """Slow camera move over one section (and the classic Ken Burns move for photos)."""
    W, H = size
    D = f"{max(0.1, play):.3f}"
    p = f"min(1,it/{D})"
    z = {"zoom_in": f"1+0.16*{p}", "zoom_out": f"1.16-0.16*{p}"}.get(kind, "1.16")
    x = {"pan_left": f"(iw-iw/zoom)*(1-{p})", "pan_right": f"(iw-iw/zoom)*{p}"}.get(kind, "(iw-iw/zoom)/2")
    y = {"pan_up": f"(ih-ih/zoom)*(1-{p})", "pan_down": f"(ih-ih/zoom)*{p}"}.get(kind, "(ih-ih/zoom)/2")
    return f"scale={W * 2}:{H * 2},zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W}x{H}:fps={rate},setsar=1"


def extract_all_segments(
    edl: dict,
    edit_dir: Path,
    preview: bool,
    draft: bool = False,
    fps: str | None = None,
) -> list[Path]:
    """Extract every EDL range into edit_dir/clips_graded/seg_NN.mp4.
    Returns the ordered list of segment paths.

    If the EDL `grade` is "auto", analyze each segment range with
    `auto_grade_for_clip` and apply a per-segment subtle correction.
    Otherwise, apply the same preset/raw filter to every segment.
    """
    resolved = resolve_grade_filter(edl.get("grade"))
    is_auto = resolved == "__AUTO__"
    clips_dir = edit_dir / (
        "clips_draft" if draft else ("clips_preview" if preview else "clips_graded")
    )
    clips_dir.mkdir(parents=True, exist_ok=True)

    ranges = edl["ranges"]
    sources = edl["sources"]

    # Resolve ONE output frame rate for the entire render and apply it to every
    # segment. The lossless concat (Rule 2, `-c copy`) requires all segments to
    # share a frame rate; probing per-segment would diverge for multi-source
    # EDLs that mix rates (e.g. a 30fps and a 60fps source) and break the concat.
    # Explicit --fps wins; otherwise preserve the first source's rate.
    if fps is not None:
        out_rate = parse_fps(str(fps))
    elif ranges:
        first_src = resolve_path(sources[ranges[0]["source"]], edit_dir)
        out_rate = probe_source_fps(first_src) or "24"
    else:
        out_rate = "24"

    seg_paths: list[Path] = []
    jobs: list[tuple] = []
    print(f"extracting {len(ranges)} segment(s) → {clips_dir.name}/  @ {out_rate} fps"
          f"{' (forced)' if fps is not None else ' (from source)'}")
    if is_auto:
        print("  (auto-grade per segment: analyzing each range)")

    # Transitions: each join borrows footage from both neighbours ("handles") so the cut point,
    # and so every caption/graphic time, stays where it was. Output seconds on each side.
    pre = [0.0] * len(ranges)
    post = [0.0] * len(ranges)
    joins: list[tuple[str, float]] = [("cut", 0.0)]
    for i in range(1, len(ranges)):
        tr = ranges[i].get("transition") or {}
        kind = tr.get("type") if isinstance(tr, dict) else tr
        if kind not in TRANSITIONS or "status" not in edl:
            joins.append(("cut", 0.0))
            continue
        want = float(tr.get("duration") or TRANSITIONS[kind][1]) if isinstance(tr, dict) else TRANSITIONS[kind][1]
        a, b = ranges[i - 1], ranges[i]
        sa, sb = float(a.get("speed") or 1), float(b.get("speed") or 1)
        a_src = resolve_path(sources[a["source"]], edit_dir)
        avail_post = 0.0 if (a.get("hold") or a.get("reverse") or a.get("speed_curve") or a.get("boomerang")) else \
            max(0.0, (_duration(a_src) - float(a["end"])) / sa)
        avail_pre = 0.0 if (b.get("reverse") or b.get("speed_curve") or b.get("boomerang")) else \
            max(0.0, float(b["start"]) / sb)
        p_ = min(avail_pre, want / 2)
        q_ = min(avail_post, want / 2)
        p_ = min(avail_pre, want - q_)
        q_ = min(avail_post, want - p_)
        pre[i], post[i - 1] = round(p_, 3), round(q_, 3)
        joins.append((TRANSITIONS[kind][0], round(p_ + q_, 3)) if p_ + q_ >= 0.08 else ("cut", 0.0))

    from plan import range_len
    rate_f, clock = Fraction(out_rate), 0.0
    for i, r in enumerate(ranges):
        # Whole frames, rounded against the running total: each section's rounding is corrected by
        # the next, so captions and sound stay in step however many sections there are.
        length = range_len(r)
        n_frames = round((clock + length) * rate_f) - round(clock * rate_f) + round((pre[i] + post[i]) * rate_f)
        clock += length
        src_name = r["source"]
        src_path = resolve_path(sources[src_name], edit_dir)
        speed = float(r.get("speed") or 1.0)
        start = float(r["start"]) - pre[i] * speed
        end = float(r["end"]) + post[i] * speed
        duration = end - start
        out_path = clips_dir / f"seg_{i:02d}_{src_name}.mp4"

        if r.get("grade"):  # a look on just this section
            seg_filter = resolve_grade_filter(r["grade"])
        elif is_auto:
            seg_filter, _stats = auto_grade_for_clip(src_path, start=start, duration=duration, verbose=False)
        else:
            seg_filter = resolved

        crop, out_size = None, None
        aspect = (edl.get("target") or {}).get("aspect")
        if "status" in edl and not aspect:
            # Mixed sources need one canvas; take the shape of the first one.
            first = resolve_path(sources[ranges[0]["source"]], edit_dir)
            aspect = "9:16" if is_portrait_source(first) else "16:9"
        if "status" in edl and aspect:
            from framing import output_size, reframe_crop, reframe_pad
            out_size = output_size(aspect, draft)
            if out_size:
                if r.get("fit") or (edl.get("target") or {}).get("fit"):  # show the whole shot on a canvas
                    from framing import fit_canvas
                    crop = fit_canvas(*out_size, r.get("fit") or edl["target"]["fit"], r.get("fill_color") or "#000000")
                else:
                    crop = reframe_crop(src_path, start, end, aspect) or reframe_pad(src_path, aspect, *out_size)

        note = r.get("beat") or r.get("note") or ""
        fx = {k: r.get(k) for k in ("speed", "hold", "stabilize", "reverse", "speed_curve", "smooth", "flip",
                                    "rotate", "volume", "motion", "boomerang")}
        fx["voice_fx"] = r.get("voice_fx") or (edl.get("audio") or {}).get("voice_fx")
        fx["adjust"] = {**(edl.get("adjust") or {}), **(r.get("adjust") or {})}
        fx["frames"] = max(1, n_frames)
        if edl.get("color_match") and "status" in edl:
            from grade import match_gains
            fx["gains"] = match_gains(edl, edit_dir, i)
        spec = r.get("background") or edl.get("background")
        if spec and (spec.get("mode") if isinstance(spec, dict) else spec) in (None, "keep"):
            spec = None
        key = _fingerprint(_file_sig(src_path), round(start, 4), round(duration, 4), seg_filter, crop, out_size,
                           fx, out_rate, preview, draft, spec)
        out_path = clips_dir / f"seg_{src_name}_{key}.mp4"
        reused = out_path.exists() and out_path.stat().st_size > 0
        print(f"  [{i:02d}] {src_name}  {start:7.2f}-{end:7.2f}  ({duration:5.2f}s)  {note}"
              + ("  (unchanged, reused)" if reused else (f"  reframe {crop}" if crop else "")))
        if is_auto and not reused:
            print(f"        grade: {seg_filter or '(none)'}")
        if not reused and all(j[4] != out_path for j in jobs):
            jobs.append((src_path, start, duration, seg_filter, out_path, crop, out_size, fx, spec))
        seg_paths.append(out_path)

    def build(job):
        src_path, start, duration, seg_filter, out_path, crop, out_size, fx, spec = job
        part = out_path.with_name(f"{out_path.stem}_part{os.getpid()}.mp4")
        extract_segment(src_path, start, duration, seg_filter, part, preview=preview, draft=draft,
                        rate=out_rate, crop=crop, out_size=out_size, fx=fx)
        if spec:
            from background import replace_background
            replace_background(part, spec, edit_dir, draft)
        part.replace(out_path)

    if jobs:
        print(f"  cutting {len(jobs)} changed section(s), {min(WORKERS, len(jobs))} at a time"
              + (f"; {len(ranges) - len(jobs)} reused" if len(jobs) < len(ranges) else ""))
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(build, jobs))  # list() re-raises the first failure
    else:
        print("  every section unchanged, reused")
    _tidy_clips(clips_dir, set(seg_paths))

    extract_all_segments.joins = joins  # read by main() to pick lossless concat vs transitions
    extract_all_segments.rate = out_rate
    return seg_paths


def _tidy_clips(clips_dir: Path, keep: set[Path]) -> None:
    """Keep recent versions of sections (so undo is quick too), but don't let the folder grow forever."""
    import time as _t
    for p in clips_dir.glob("*_part*.mp4"):
        if _t.time() - p.stat().st_mtime > 3600:  # a crashed render's leftovers
            p.unlink(missing_ok=True)
    old = sorted((p for p in clips_dir.glob("seg_*.mp4") if p not in keep), key=lambda p: p.stat().st_mtime)
    for p in old[:max(0, len(old) - 3 * len(keep) - 20)]:
        p.unlink(missing_ok=True)


@_per_file
def _duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True)
    try:
        return float(out.stdout.strip() or 0)
    except ValueError:  # a photo has no duration
        return 0.0


def join_with_transitions(segment_paths: list[Path], joins: list[tuple[str, float]], out_path: Path,
                          rate: str, draft: bool) -> None:
    """Join segments with xfade/acrossfade where a transition is set, plain joins elsewhere.

    Segments already carry their handles, so each xfade starts `d` before the end of what's been
    joined so far and the total length equals the plan's.
    """
    inputs, parts = [], []
    for i, p in enumerate(segment_paths):
        inputs += ["-i", str(p)]
        # Same clock (fps then settb, so plain joins and xfades agree), pixel format and colour
        # tags on every clip: phone footage is tagged BT.709, screen/test clips often aren't.
        parts.append(f"[{i}:v]fps={rate},settb=AVTB,format=yuv420p,"
                     f"setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709[v{i}];"
                     f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
    vcur, acur, length = "[v0]", "[a0]", _duration(segment_paths[0])
    for i in range(1, len(segment_paths)):
        kind, d = joins[i]
        nxt = _duration(segment_paths[i])
        if kind == "cut" or d <= 0:
            parts.append(f"{vcur}[v{i}]concat=n=2:v=1:a=0[vj{i}];{acur}[a{i}]concat=n=2:v=0:a=1[aj{i}]")
            length += nxt
        elif kind == "custom":
            from handwipe import xfade_expr
            parts.append(f"{vcur}[v{i}]xfade=transition=custom:expr='{xfade_expr(segment_paths[i - 1], d)}':"
                         f"duration={d:.3f}:offset={length - d:.3f}[vj{i}];"
                         f"{acur}[a{i}]acrossfade=d={d:.3f}[aj{i}]")
            length += nxt - d
        else:
            parts.append(f"{vcur}[v{i}]xfade=transition={kind}:duration={d:.3f}:offset={length - d:.3f}[vj{i}];"
                         f"{acur}[a{i}]acrossfade=d={d:.3f}[aj{i}]")
            length += nxt - d
        vcur, acur = f"[vj{i}]", f"[aj{i}]"
    preset, crf = ("ultrafast", "28") if draft else ("veryfast", "17")
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(parts),
                    "-map", vcur, "-map", acur, "-c:v", "libx264", "-preset", preset, "-crf", crf,
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                    str(out_path)], check=True)


# -------- Lossless concat ----------------------------------------------------


def concat_segments(segment_paths: list[Path], out_path: Path, edit_dir: Path) -> None:
    """Lossless concat via the concat demuxer. No re-encode."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = edit_dir / "_concat.txt"
    concat_list.write_text("".join(f"file '{p.resolve()}'\n" for p in segment_paths))

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    print(f"concat → {out_path.name}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    concat_list.unlink(missing_ok=True)


# -------- Master SRT (Rule 5) ------------------------------------------------


PUNCT_BREAK = set(".,!?;:")


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _words_in_range(transcript: dict, t_start: float, t_end: float) -> list[dict]:
    out: list[dict] = []
    for w in transcript.get("words", []):
        if w.get("type") != "word":
            continue
        ws = w.get("start")
        we = w.get("end")
        if ws is None or we is None:
            continue
        if we <= t_start or ws >= t_end:
            continue
        out.append(w)
    return out


CHUNK_WORDS = 2         # target words per cue
CHUNK_MAX_WORDS = 3     # a too-short chunk may grow to this many words
CHUNK_MIN_S = 0.35      # a cue shorter than this reads as a flash
CHUNK_PAUSE_S = 0.3     # a gap this long between words ends the cue


def chunk_words(words: list[dict]) -> list[list[dict]]:
    """Group transcript words into caption cues.

    A cue closes on trailing punctuation or on a pause before the next word.
    Otherwise it closes at CHUNK_WORDS words, unless it would be on screen for
    less than CHUNK_MIN_S; then it takes up to CHUNK_MAX_WORDS words. So
    "does | is" across a pause stays split, and fast "what a" does not flash.
    """
    words = [w for w in words if (w.get("text") or "").strip()]
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for i, w in enumerate(words):
        current.append(w)
        text = w["text"].strip()
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 0.0
        dur = w["end"] - current[0]["start"]
        if (
            nxt is None
            or text[-1] in PUNCT_BREAK
            or gap >= CHUNK_PAUSE_S
            or len(current) >= CHUNK_MAX_WORDS
            or (len(current) >= CHUNK_WORDS and dur >= CHUNK_MIN_S)
        ):
            chunks.append(current)
            current = []
    return chunks


def build_master_srt(edl: dict, edit_dir: Path, out_path: Path) -> None:
    """Build an output-timeline SRT from per-source transcripts.

    - phrase-aware ~2-word chunks (see chunk_words)
    - UPPERCASE text
    - Output times computed as word.start - segment_start + segment_offset
    """
    transcripts_dir = edit_dir / "transcripts"
    sources = edl["sources"]

    entries: list[tuple[float, float, str]] = []
    seg_offset = 0.0

    for r in edl["ranges"]:
        src_name = r["source"]
        seg_start = float(r["start"])
        seg_end = float(r["end"])
        seg_duration = seg_end - seg_start

        tr_path = transcripts_dir / f"{src_name}.json"
        if not tr_path.exists():
            print(f"  no transcript for {src_name}, skipping captions for this segment")
            seg_offset += seg_duration
            continue

        transcript = json.loads(tr_path.read_text())
        words_in_seg = _words_in_range(transcript, seg_start, seg_end)

        for chunk in chunk_words(words_in_seg):
            local_start = max(seg_start, chunk[0].get("start", seg_start))
            local_end = min(seg_end, chunk[-1].get("end", seg_end))
            out_start = max(0.0, local_start - seg_start) + seg_offset
            out_end = max(0.0, local_end - seg_start) + seg_offset
            if out_end <= out_start:
                out_end = out_start + 0.4
            text = " ".join((w.get("text") or "").strip() for w in chunk)
            text = re.sub(r"\s+", " ", text).strip()
            # Strip trailing punctuation for cleaner uppercase look
            text = text.rstrip(",;:")
            text = text.upper()
            entries.append((out_start, out_end, text))

        seg_offset += seg_duration

    # Sort and write as SRT
    entries.sort(key=lambda e: e[0])
    lines: list[str] = []
    for i, (a, b, t) in enumerate(entries, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(a)} --> {_srt_timestamp(b)}")
        lines.append(t)
        lines.append("")
    out_path.write_text("\n".join(lines))
    print(f"master SRT → {out_path.name} ({len(entries)} cues)")


# -------- Loudness normalization (social-ready audio) -----------------------


# Social-media standard: -14 LUFS integrated, -1 dBTP peak, LRA 11 LU.
# Matches YouTube / Instagram / TikTok / X / LinkedIn normalization targets.
LOUDNORM_I = -14.0
LOUDNORM_TP = -1.5  # the AAC encode adds a few tenths back; QC wants -1 or lower
LOUDNORM_LRA = 11.0


def measure_loudness(video_path: Path) -> dict[str, str] | None:
    """Run ffmpeg loudnorm first pass and parse the JSON measurement.

    Returns a dict with measured_i, measured_tp, measured_lra, measured_thresh,
    target_offset, or None if measurement failed.
    """
    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}:print_format=json"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(video_path),
        "-af", filter_str,
        "-vn", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # loudnorm prints the JSON to stderr at the end of the run
    stderr = proc.stderr

    # Find the JSON block — loudnorm output contains a `{ ... }` block
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        data = json.loads(stderr[start : end + 1])
    except json.JSONDecodeError:
        return None
    needed = {"input_i", "input_tp", "input_lra", "input_thresh", "target_offset"}
    if not needed.issubset(data.keys()):
        return None
    return data


def loudnorm_filter(audio_source: Path, quick: bool) -> str:
    """The loudness filter for the last pass. Quick: one-pass estimate. Otherwise measured first
    (sound only, so it's fast), for exact two-pass levels."""
    base = f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
    m = None if quick else measure_loudness(audio_source)
    if not m:
        return base
    print(f"    measured: I={m['input_i']} LUFS  TP={m['input_tp']}  LRA={m['input_lra']}")
    return (f"{base}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
            f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")


def apply_loudnorm_two_pass(
    input_path: Path,
    output_path: Path,
    preview: bool = False,
) -> bool:
    """Run two-pass loudnorm on input_path, write normalized copy to output_path.

    Returns True on success, False if measurement failed (caller should fall
    back to copying the input unchanged).

    In preview mode, skips the measurement pass and uses a one-pass approximation
    for speed. Final mode always does the proper two-pass.
    """
    if preview:
        # One-pass approximation — faster, slightly less accurate.
        filter_str = f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-nostats",
            "-i", str(input_path),
            "-c:v", "copy",
            "-af", filter_str,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart",
            str(output_path),
        ]
        print(f"  loudnorm (1-pass preview) → {output_path.name}")
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return True

    # Full two-pass
    print(f"  loudnorm pass 1: measuring {input_path.name}")
    measurement = measure_loudness(input_path)
    if measurement is None:
        print("  loudnorm measurement failed — falling back to 1-pass")
        return apply_loudnorm_two_pass(input_path, output_path, preview=True)

    print(f"    measured: I={measurement['input_i']} LUFS  "
          f"TP={measurement['input_tp']}  LRA={measurement['input_lra']}")

    filter_str = (
        f"loudnorm=I={LOUDNORM_I}:TP={LOUDNORM_TP}:LRA={LOUDNORM_LRA}"
        f":measured_I={measurement['input_i']}"
        f":measured_TP={measurement['input_tp']}"
        f":measured_LRA={measurement['input_lra']}"
        f":measured_thresh={measurement['input_thresh']}"
        f":offset={measurement['target_offset']}"
        f":linear=true"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-nostats",
        "-i", str(input_path),
        "-c:v", "copy",
        "-af", filter_str,
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        str(output_path),
    ]
    print(f"  loudnorm pass 2: normalizing → {output_path.name}")
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return True


# -------- Final compositing (Rule 1 + Rule 4) -------------------------------


def build_final_composite(
    base_path: Path,
    overlays: list[dict],
    subtitles_path: Path | None,
    out_path: Path,
    edit_dir: Path,
    base_filter: str | None = None,
    audio_filter: str | None = None,
    draft: bool = False,
) -> None:
    """Final pass: base → zooms (base_filter) → overlays (PTS-shifted) → subtitles LAST → out.

    If there are no zooms, overlays or subtitles, just copy base to out.
    """
    has_overlays = bool(overlays)
    has_subs = subtitles_path is not None and subtitles_path.exists()

    audio_out = (["-af", audio_filter, "-c:a", "aac", "-b:a", "192k", "-ar", "48000"] if audio_filter
                 else ["-c:a", "copy"])
    if not has_overlays and not has_subs and not base_filter:
        # Nothing to draw: keep the picture as it is
        run(["ffmpeg", "-y", "-i", str(base_path), "-map", "0:v", "-map", "0:a", "-c:v", "copy", *audio_out,
             "-movflags", "+faststart", str(out_path)], quiet=True)
        return

    inputs: list[str] = ["-i", str(base_path)]
    for ov in overlays:
        ov_path = resolve_path(ov["file"], edit_dir)
        inputs += ["-i", str(ov_path)]

    filter_parts: list[str] = []
    # PTS-shift every overlay so its frame 0 lands at start_in_output
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        extra = f",{ov['vf']}" if ov.get("vf") else ""
        filter_parts.append(f"[{idx}:v]setpts=PTS-STARTPTS+{t}/TB{extra}[a{idx}]")

    # Zooms act on the picture only, before overlays and captions, so text stays put.
    current = "[0:v]"
    if base_filter:
        filter_parts.append(f"[0:v]{base_filter}[zoomed]")
        current = "[zoomed]"

    # Chain overlays on top of base
    for idx, ov in enumerate(overlays, start=1):
        t = float(ov["start_in_output"])
        dur = float(ov["duration"])
        end = t + dur
        next_label = f"[v{idx}]"
        xy = f":x='{ov['x']}':y='{ov['y']}'" if "x" in ov else ""  # e.g. a picture-in-picture B-roll
        if ov.get("blend"):  # screen, multiply, overlay... over just this window
            filter_parts.append(
                f"{current}format=gbrp,split[bb{idx}][bc{idx}];[a{idx}]format=gbrp[bf{idx}];"
                f"[bb{idx}][bf{idx}]blend=all_mode={ov['blend']}:all_opacity={float(ov.get('opacity', 1)):.2f}:"
                f"eof_action=pass[bd{idx}];"
                f"[bc{idx}][bd{idx}]overlay=enable='between(t,{t:.3f},{end:.3f})',format=yuv420p{next_label}")
        else:
            filter_parts.append(
                f"{current}[a{idx}]overlay=enable='between(t,{t:.3f},{end:.3f})':eval=frame{xy}{next_label}"
            )
        current = next_label

    # Subtitles LAST — Rule 1
    if has_subs and subtitles_path.suffix.lower() == ".ass":
        # Styled captions (captions.py) carry their own look; don't force one.
        from captions import subtitles_filter
        filter_parts.append(f"{current}{subtitles_filter(subtitles_path)}[outv]")
        out_label = "[outv]"
    elif has_subs:
        subs_abs = str(subtitles_path.resolve()).replace(":", r"\:").replace("'", r"\'")
        filter_parts.append(
            f"{current}subtitles='{subs_abs}':force_style='{SUB_FORCE_STYLE}'[outv]"
        )
        out_label = "[outv]"
    else:
        # Rename the last overlay/zoom output to [outv] for consistency
        if has_overlays or base_filter:
            filter_parts.append(f"{current}null[outv]")
            out_label = "[outv]"
        else:
            out_label = "[0:v]"

    filter_complex = ";".join(filter_parts)

    def command(video_args: list[str], graph: str) -> list[str]:
        return ["ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", out_label, "-map", "0:a",
                *video_args, *audio_out, "-movflags", "+faststart", str(out_path)]

    print(f"compositing → {out_path.name}")
    print(f"  overlays: {len(overlays)}, subtitles: {'yes' if has_subs else 'no'}")
    # The export: the computer's encoder chip when it has one (about 3x faster for the whole pass),
    # checked afterwards: if it lost frames or failed, the software encoder does it again.
    hw = None if draft or out_label == "[0:v]" else hardware_encoder()
    if hw:
        want = _frame_count(base_path)
        graph = filter_complex[:filter_complex.rindex("[outv]")] + ",format=nv12[outv]"
        r = subprocess.run(command([*hw, "-r", _standard_rate(probe_source_fps(base_path) or "30")], graph),
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        got = _frame_count(out_path) if r.returncode == 0 else -1
        if r.returncode == 0 and (want < 0 or abs(got - want) <= 2):
            print("  encoded on the graphics chip")
            return
        print(f"  the graphics chip's encoder didn't work out ({'error' if r.returncode else f'{got} of {want} frames'}); "
              "using the software encoder")
    # The draft only has to look right in the Studio; the export keeps full quality.
    soft = ["-c:v", "libx264", *(["-preset", "ultrafast", "-crf", "25"] if draft else ["-preset", "faster", "-crf", "18"]),
            "-pix_fmt", "yuv420p"]
    subprocess.run(command(soft, filter_complex), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


# -------- Main ---------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Render a video from an EDL")
    ap.add_argument("edl", type=Path, help="Path to edl.json")
    ap.add_argument("-o", "--output", type=Path, required=True, help="Output video path")
    ap.add_argument(
        "--preview",
        action="store_true",
        help="Preview mode: 1080p, medium, CRF 22 — evaluable for QC, faster than final.",
    )
    ap.add_argument(
        "--draft",
        action="store_true",
        help="Draft mode: 720p, ultrafast, CRF 28 — cut-point verification only.",
    )
    ap.add_argument(
        "--build-subtitles",
        action="store_true",
        help="Build master.srt from transcripts + EDL offsets before compositing",
    )
    ap.add_argument(
        "--no-subtitles",
        action="store_true",
        help="Skip subtitles even if the EDL references one",
    )
    ap.add_argument(
        "--no-loudnorm",
        action="store_true",
        help="Skip audio loudness normalization. Default is on (-14 LUFS, -1 dBTP, LRA 11).",
    )
    ap.add_argument(
        "--fps",
        type=parse_fps,
        default=None,
        help="Output frame rate. Default: preserve the source's frame rate "
             "(falls back to 24 if it can't be probed). Pass e.g. --fps 30 or "
             "--fps 30000/1001 to force.",
    )
    ap.add_argument(
        "--prepare",
        action="store_true",
        help="Only build the sections, the joined cut and the sound (no output video): run in the "
             "background after the rough cut is approved, so the export itself is quick.",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Render above draft quality even if plan.json status is not rough_approved.",
    )
    args = ap.parse_args()

    edl_path = args.edl.resolve()
    if not edl_path.exists():
        sys.exit(f"edl not found: {edl_path}")

    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    edit_dir = edl_path.parent
    out_path = args.output.resolve()

    # Review gates (plan.json): only the fast draft renders before the user has
    # approved the rough cut. Plain EDLs without a status are not gated.
    status = edl.get("status")
    if status is not None and not args.draft and status not in ("rough_approved", "final") \
            and not args.force:
        sys.exit(f"plan status is '{status}': only --draft renders are allowed until the rough cut "
                 "is approved (helpers/plan.py approve <plan> rough). Use --force to override.")

    mode = "draft" if args.draft else ("preview" if args.preview else "final")
    with _RenderLock(edit_dir, mode):
        _render(args, edl, edit_dir, out_path, status)


def _render(args, edl: dict, edit_dir: Path, out_path: Path, status) -> None:
    # 1. Extract per-segment (auto-grade per range if EDL grade is "auto")
    segment_paths = extract_all_segments(
        edl, edit_dir, preview=args.preview, draft=args.draft, fps=args.fps
    )

    # 2. Concat → base
    if args.draft:
        base_name = "base_draft.mp4"
    elif args.preview:
        base_name = "base_preview.mp4"
    else:
        base_name = "base.mp4"
    base_path = edit_dir / base_name
    joins = getattr(extract_all_segments, "joins", [])
    base_key = _fingerprint([p.name for p in segment_paths], joins, getattr(extract_all_segments, "rate", None))
    if _reuse(base_path, base_key):
        print(f"cut unchanged → reusing {base_name}")
    elif any(kind != "cut" for kind, _ in joins):
        print(f"joining with {sum(k != 'cut' for k, _ in joins)} transition(s) → {base_name}")
        join_with_transitions(segment_paths, joins, base_path, extract_all_segments.rate, args.draft)
        _stamp(base_path, base_key)
    else:
        concat_segments(segment_paths, base_path, edit_dir)
        _stamp(base_path, base_key)

    # 2b. plan.json: audio polish on the continuous voice track (cleanup, music, SFX).
    if status is not None:
        import audio
        if audio.has_work(edl):
            mixed = base_path.with_name(base_path.stem + "_mixed.mp4")
            # Only the sound settings matter here: a caption or title change keeps this file.
            visual = {"title", "texts", "broll", "stickers", "graphics", "status", "version", "changes", "notes",
                      "overlays", "grade", "adjust", "color_match", "background", "total_duration_s", "preset", "look"}
            if not (edl.get("audio") or {}).get("sfx_on_emphasis"):
                visual.add("captions")
            mix_key = _fingerprint(base_key, {k: v for k, v in edl.items() if k not in visual and not k.startswith("_")},
                                   [_file_sig(f) for f in (Path(__file__).with_name("audio.py"),)])
            if _reuse(mixed, mix_key):
                print(f"sound unchanged → reusing {mixed.name}")
                base_path = mixed
            else:
                base_path = audio.mix(base_path, edl, edit_dir, mixed)
                _stamp(mixed, mix_key)

    if args.prepare:
        print(f"ready: sections, cut and sound are built ({base_path.name}); the export will reuse them")
        return

    # 3. Subtitles: build if requested, resolve final path
    subs_path: Path | None = None
    if not args.no_subtitles:
        if status is not None and not args.build_subtitles and not edl.get("subtitles"):
            # plan.json: styled captions from the brand kit + plan "captions" (style "none" = off).
            from captions import build_ass, load_brand, settings
            if settings(edl, load_brand(edit_dir))["style"] != "none" or edl.get("texts"):
                subs_path = edit_dir / "captions.ass"
                n = build_ass(edl, edit_dir, subs_path)
                print(f"captions → {subs_path.name} ({n} events)")
        elif args.build_subtitles:
            subs_path = edit_dir / "master.srt"
            build_master_srt(edl, edit_dir, subs_path)
        elif edl.get("subtitles"):
            subs_path = resolve_subtitles_path(edl["subtitles"], edit_dir)

    # 4. Composite (zooms → overlays → subtitles LAST) → intermediate (pre-loudnorm) path
    overlays = list(edl.get("overlays") or [])
    zoom = None
    if status is not None:
        from framing import probe_size, zoom_filter
        bw, bh = probe_size(base_path)
        zoom = zoom_filter(edl, edit_dir, bw, bh, probe_source_fps(base_path) or "30", 1 if args.draft else 2)
        # Effects (and glitch/shake/flash cuts) act on the picture after zooms, before any text.
        from effects import build_broll_overlays, effects_chain
        edl["_edit_dir"] = str(edit_dir)
        edl["_base"] = str(base_path)
        fx = effects_chain(edl, bw, bh)
        if fx:
            zoom = f"{zoom or 'null'}{fx}"
        # Person-aware effects (things behind you, around your head) replace the picture for their
        # window, so they go under everything else.
        from layers import build_layer_overlays
        overlays = build_layer_overlays(edl, edit_dir, base_path, args.draft) + overlays
        # B-roll cutaways sit under titles and graphics, so they go first in the overlay stack.
        if edl.get("broll"):
            overlays = build_broll_overlays(edl, edit_dir, bw, bh, probe_source_fps(base_path) or "30",
                                            args.draft) + overlays
        # Stickers and emoji (plan "stickers", and captions "emoji": true).
        from stickers import build_sticker_overlays
        sticker_ovs = build_sticker_overlays(edl, edit_dir, bw, bh, probe_source_fps(base_path) or "30")
        # Video fade in/out from black (plan "fade": {"in": 0.5, "out": 1}).
        fd = edl.get("fade") or {}
        if fd.get("in") or fd.get("out"):
            tot = _duration(base_path)
            bits = [f"fade=t=in:st=0:d={float(fd['in']):.2f}"] if fd.get("in") else []
            bits += [f"fade=t=out:st={max(0.0, tot - float(fd['out'])):.2f}:d={float(fd['out']):.2f}"] if fd.get("out") else []
            zoom = f"{zoom or 'null'},{','.join(bits)}"
        # Hook titles (title.py) render as alpha overlays; built here so the rough cut shows them.
        if edl.get("title"):
            from title import build_title_overlay
            ov = build_title_overlay(edl, edit_dir, base_path)
            if ov:
                overlays.append(ov)
        # Motion graphics (graphics.py → HyperFrames), one alpha clip per graphic; the
        # progress bar is drawn by FFmpeg as part of the base chain.
        if edl.get("graphics"):
            from graphics import build_graphics_overlays, progress_chain
            overlays += build_graphics_overlays(edl, edit_dir, base_path, draft=args.draft)
            total = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "default=nw=1:nk=1", str(base_path)],
                                         capture_output=True, text=True).stdout.strip() or 0)
            bar = progress_chain(edl, edit_dir, bw, bh, probe_source_fps(base_path) or "30", total)
            if bar:
                zoom = f"{zoom or 'null'}{bar}"
        overlays += sticker_ovs  # on top of graphics, under the captions
    loud = None
    if not args.no_loudnorm:
        # Loudness goes into the same last pass: the composite's sound is the voice track as it is.
        from audio import loudness_target
        globals()["LOUDNORM_I"] = loudness_target(edl)  # audio "loudness": quieter ... louder (normal = -14)
        print(f"loudness normalization → {LOUDNORM_I:g} LUFS / -1 dBTP / LRA 11"
              + (" (social-ready)" if LOUDNORM_I == -14 else ""))
        loud = loudnorm_filter(base_path, quick=args.draft)
    build_final_composite(base_path, overlays, subs_path, out_path, edit_dir, zoom,
                          audio_filter=loud, draft=args.draft)

    # For plan.json renders, record which cut this file shows, so the Review Studio
    # can tell when the plan has moved on (rough.mp4 -> rough.json).
    if status is not None:
        from plan import cut_signature
        out_path.with_suffix(".json").write_text(
            json.dumps({"cut": cut_signature(edl), "version": edl.get("version")}), encoding="utf-8")

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\ndone: {out_path} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
