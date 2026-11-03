"""CapCut-style visual effects for a time window, and B-roll cutaways.

plan.json:
    "effects": [
      {"type": "shake", "start_in_output": 10.9, "duration": 0.4, "intensity": 1.0},
      {"type": "vhs"}                                   # no time = whole video
    ],
    "broll": [
      {"source": "drone", "src_start": 12.0, "start_in_output": 6.2, "duration": 2.5},
      {"file": "screens/dashboard.mp4", "start_in_output": 15.0, "duration": 3, "mode": "pip"}
    ]

Effects are applied to the picture after zooms and before graphics/captions (so text
never shakes or glitches). Each runs only inside its window: the frame is split, the
effect is applied to one copy, and that copy is shown between start and end.
Range transitions of type glitch / shake / flash_cut are turned into effects at the cut.

B-roll plays another clip full-frame (or picture-in-picture) over the main video while
the main audio carries on, the classic "cutaway". Sources can be plan source names or
files; clips are cropped to fill the canvas.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# name -> (label, filter builder(W, H, k)) ; k = intensity (default 1)
EFFECTS = {
    "shake": ("Camera shake", lambda W, H, k:
              f"crop=w=iw*{1 - 0.05 * k:.3f}:h=ih*{1 - 0.05 * k:.3f}:"
              f"x='(iw-ow)/2+(iw-ow)/2*sin(t*53)':y='(ih-oh)/2+(ih-oh)/2*cos(t*41)',scale={W}:{H}"),
    "glitch": ("Glitch", lambda W, H, k:
               f"rgbashift=rh={int(14 * k)}:bh={-int(14 * k)}:gv={int(5 * k)},"
               f"noise=alls={int(28 * k)}:allf=t,eq=contrast=1.15"),
    "rgb_split": ("RGB split", lambda W, H, k: f"rgbashift=rh={int(9 * k)}:bh={-int(9 * k)}"),
    "flash": ("White flash", lambda W, H, k: f"eq=brightness={0.5 * k:.2f}:saturation=0.6"),
    "vhs": ("VHS", lambda W, H, k:
            f"noise=alls={int(14 * k)}:allf=t,chromashift=cbh={int(5 * k)}:crh={-int(5 * k)},"
            "eq=saturation=0.75:contrast=1.06,gblur=sigma=0.7,curves=preset=vintage"),
    "grain": ("Film grain", lambda W, H, k: f"noise=alls={int(12 * k)}:allf=t+u"),
    "vignette": ("Vignette", lambda W, H, k: f"vignette=angle={0.45 + 0.3 * k:.2f}"),
    "blur": ("Blur", lambda W, H, k: f"gblur=sigma={12 * k:.1f}"),
    "bw": ("Black & white", lambda W, H, k: "hue=s=0"),
    "sepia": ("Sepia", lambda W, H, k: "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131"),
    "pixelate": ("Pixelate", lambda W, H, k:
                 f"scale=iw/{max(4, int(20 * k))}:-2:flags=neighbor,scale={W}:{H}:flags=neighbor"),
    "invert": ("Invert", lambda W, H, k: "negate"),
    "light_leak": ("Light leak", lambda W, H, k:
                   f"colorbalance=rs={0.25 * k:.2f}:gs={0.08 * k:.2f}:bs={-0.15 * k:.2f}:rm={0.15 * k:.2f}:"
                   f"bm={-0.1 * k:.2f},eq=brightness={0.06 * k:.2f}"),
    "mirror": ("Mirror", lambda W, H, k: "hflip"),
    "letterbox": ("Cinema bars", lambda W, H, k:
                  f"drawbox=x=0:y=0:w=iw:h=ih*{0.1 * k:.3f}:color=black:t=fill,"
                  f"drawbox=x=0:y=ih*{1 - 0.1 * k:.3f}:w=iw:h=ih*{0.1 * k + 0.01:.3f}:color=black:t=fill"),
}
CUT_EFFECTS = {"glitch": ("glitch", 0.25), "shake": ("shake", 0.3), "flash_cut": ("flash", 0.12)}


def effect_list() -> list[dict]:
    return [{"id": k, "label": v[0]} for k, v in EFFECTS.items()]


def effects_chain(plan: dict, W: int, H: int) -> str:
    """Filter-graph text that continues a video chain: one split/overlay per effect window."""
    from plan import range_len
    items = list(plan.get("effects") or [])
    t = 0.0
    for i, r in enumerate(plan.get("ranges") or []):
        tr = r.get("transition") or {}
        kind = tr.get("type") if isinstance(tr, dict) else tr
        if i and kind in CUT_EFFECTS:
            fx, d = CUT_EFFECTS[kind]
            items.append({"type": fx, "start_in_output": max(0.0, t - d / 2), "duration": d})
        t += range_len(r)
    out = []
    for i, e in enumerate(items):
        if e.get("type") not in EFFECTS:
            continue
        a = float(e.get("start_in_output", 0.0))
        b = a + float(e["duration"]) if e.get("duration") else 1e9
        chain = EFFECTS[e["type"]][1](W, H, float(e.get("intensity", 1.0)))
        out.append(f",split[fx{i}a][fx{i}b];[fx{i}b]{chain}[fx{i}c];"
                   f"[fx{i}a][fx{i}c]overlay=enable='between(t,{a:.3f},{b:.3f})'")
    return "".join(out)


def build_broll_overlays(plan: dict, edit_dir: Path, W: int, H: int, fps: str, draft: bool) -> list[dict]:
    """Cut each B-roll clip to its window, fitted to the canvas (or as a PiP); return overlays."""
    import hashlib
    import json
    out_dir = edit_dir / "animations"
    out_dir.mkdir(parents=True, exist_ok=True)
    overlays = []
    for b in plan.get("broll") or []:
        src = plan.get("sources", {}).get(b.get("source", "")) or b.get("file")
        if not src:
            continue
        p = Path(src)
        if not p.is_absolute():
            p = next((c for c in (edit_dir.parent / src, edit_dir / src) if c.exists()), edit_dir.parent / src)
        if not p.exists():
            print(f"  broll: missing {src}")
            continue
        dur = float(b.get("duration", 2.0))
        key = hashlib.sha1(json.dumps([str(p), b, W, H, fps, draft], sort_keys=True).encode()).hexdigest()[:10]
        clip = out_dir / f"broll_{key}.mp4"
        if b.get("mode") == "pip":
            pw = int(W * 0.46) // 2 * 2
            vf = (f"scale={pw}:-2,pad=iw+{max(4, W // 120) * 2}:ih+{max(4, W // 120) * 2}:"
                  f"{max(4, W // 120)}:{max(4, W // 120)}:white")
        else:
            vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
        if not clip.exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{float(b.get('src_start', 0)):.3f}",
                            "-t", f"{dur:.3f}", "-i", str(p), "-an", "-vf", f"{vf},fps={fps}",
                            "-c:v", "libx264", "-preset", "ultrafast" if draft else "fast", "-crf", "20",
                            "-pix_fmt", "yuv420p", str(clip)], check=True)
        ov = {"file": str(clip), "start_in_output": float(b["start_in_output"]), "duration": dur}
        if b.get("mode") == "pip":
            ov["x"], ov["y"] = b.get("x", "W-w-W*0.05"), b.get("y", "H*0.08")
        overlays.append(ov)
    return overlays
