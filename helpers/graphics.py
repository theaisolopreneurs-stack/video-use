"""Motion graphics from plan.json "graphics" via HyperFrames (HTML + GSAP -> alpha video).

    "graphics": [
      {"type": "stat", "value": 0, "suffix": " and 4", "label": "Texans this season",
       "start_in_output": 11.0, "duration": 2.2, "position": "upper"},
      {"type": "prompt_box", "text": "How do we fix the Texans?", "start_in_output": 15.2, "duration": 3}
    ]

Types (graphics/templates.js): text_card, stat, timer, bar_chart, checklist, prompt_box,
chat, app_window, pip, collage, cta, lower_third, emoji, progress. position: upper (default)
| middle | lower. pip/collage take image or video files via "src" / "srcs" (paths relative to
the project folder, or absolute).

All graphics in a plan render in ONE HyperFrames pass (Chrome start-up is the slow part):
each gets a slot on a packed timeline, the transparent ProRes result is cut into one clip
per graphic, and render.py overlays each at its start_in_output. Cached by content hash
in <edit>/animations/gfx_<hash>/.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from captions import load_brand

REPO = Path(__file__).resolve().parent.parent
GFX = REPO / "graphics"
TYPES = ("text_card", "stat", "timer", "bar_chart", "checklist", "prompt_box", "chat", "app_window",
         "pip", "collage", "cta", "lower_third", "emoji", "progress")
POSITIONS = {"upper": 0.12, "middle": 0.33, "lower": 0.52}


def _hyperframes() -> list[str]:
    exe = GFX / "node_modules" / ".bin" / ("hyperframes.cmd" if os.name == "nt" else "hyperframes")
    if not exe.exists():
        raise RuntimeError("HyperFrames is not installed: run `npm install` in the graphics/ folder")
    return [str(exe)]


def _asset(p: str, edit_dir: Path, assets: Path) -> str:
    """Copy a referenced image/video into the composition and return its relative path."""
    src = Path(p)
    if not src.is_absolute():
        src = next((c for c in (edit_dir.parent / p, edit_dir / p, REPO / p) if c.exists()), edit_dir.parent / p)
    if not src.exists():
        print(f"  graphics: missing file {p}")
        return ""
    assets.mkdir(exist_ok=True)
    dst = assets / f"{hashlib.sha1(str(src).encode()).hexdigest()[:8]}{src.suffix.lower()}"
    if not dst.exists():
        shutil.copyfile(src, dst)
    return f"assets/{dst.name}"


def composition(items: list[dict], brand: dict, W: int, H: int, fps: str) -> str:
    c = brand.get("colors") or {}
    f = brand.get("fonts") or {}
    total = sum(i["_dur"] for i in items)
    css_vars = (f"--u:{min(W, H) / 1080:.5f}px;--primary:{c.get('primary', '#1F5C4A')};"
                f"--accent:{c.get('accent', '#F2C14E')};--ink:{c.get('text', '#FFFFFF')};"
                f"--panel-ink:{c.get('pill_text', '#F6F1E7')};"
                f"--sans:'{(f.get('sans') or {}).get('family', 'Segoe UI')}',sans-serif;"
                f"--serif:'{(f.get('serif') or {}).get('family', 'Georgia')}',serif;")
    data = json.dumps([{k: v for k, v in i.items() if not k.startswith("_") or k in ("_t0", "_dur")} for i in items])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width={W}, height={H}">
<script src="gsap.min.js"></script><script src="templates.js"></script>
<link rel="stylesheet" href="templates.css">
<style>html,body{{width:{W}px;height:{H}px}} :root{{{css_vars}}}</style></head><body>
<div id="root" data-composition-id="main" data-start="0" data-duration="{total:.3f}" data-width="{W}" data-height="{H}" data-fps="{fps}"></div>
<script>
const items = {data};
const root = document.getElementById("root");
const tl = gsap.timeline({{ paused: true }});
items.forEach((g, i) => {{
  const box = document.createElement("div");
  box.className = "slot clip";
  box.dataset.start = g._t0; box.dataset.duration = g._dur; box.dataset.trackIndex = String(i);
  if (g.type === "progress") {{ box.style.inset = "0"; }}
  else {{ box.style.top = ({json.dumps(POSITIONS)}[g.position] ?? 0.12) * 100 + "%"; }}
  root.appendChild(box);
  (window.HF_TEMPLATES[g.type] || window.HF_TEMPLATES.text_card)(g, box, tl, g._t0, g._dur);
}});
window.__timelines["main"] = tl; tl.seek(0);
</script></body></html>
"""


def progress_chain(plan: dict, edit_dir: Path, W: int, H: int, fps: str, total: float) -> str | None:
    """Filter-graph tail that slides a brand-colour bar along the bottom over the whole video.

    Drawn by FFmpeg (an overlay whose x moves with t) rather than HyperFrames: a full-length
    browser capture would cost more than the rest of the graphics put together.
    Returns text meant to follow a video chain: "...[_z];color=...[_pb];[_z][_pb]overlay=...".
    """
    if not any(g.get("type") == "progress" for g in plan.get("graphics") or []):
        return None
    accent = (load_brand(edit_dir).get("colors") or {}).get("accent", "#F2C14E")
    h = max(4, int(round(min(W, H) / 1080 * 12)))
    return (f"[_z];color=c={accent}:s={W}x{h}:r={fps}:d={total:.3f}[_pb];"
            f"[_z][_pb]overlay=x='-w+W*t/{total:.3f}':y=H-h")


def build_graphics_overlays(plan: dict, edit_dir: Path, base: Path, draft: bool = False) -> list[dict]:
    """Render every plan graphic; return overlay dicts for render.py ([] if none)."""
    import framing
    graphics = [g for g in plan.get("graphics") or [] if g.get("type") and g["type"] != "progress"]
    if not graphics:
        return []
    W, H = framing.probe_size(base)
    fps = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=r_frame_rate", "-of", "default=nw=1:nk=1", str(base)],
                         capture_output=True, text=True).stdout.strip() or "30/1"
    total_out = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                      "-of", "default=nw=1:nk=1", str(base)],
                                     capture_output=True, text=True).stdout.strip() or 0)
    brand = load_brand(edit_dir)

    key_src = json.dumps([graphics, brand.get("colors"), brand.get("fonts"), W, H, fps, draft,
                          (GFX / "templates.js").stat().st_mtime, (GFX / "templates.css").stat().st_mtime],
                         sort_keys=True)
    proj = edit_dir / "animations" / f"gfx_{hashlib.sha1(key_src.encode()).hexdigest()[:10]}"
    manifest = proj / "clips.json"
    if manifest.exists():
        return json.loads(manifest.read_text(encoding="utf-8"))

    proj.mkdir(parents=True, exist_ok=True)
    for f in ("templates.js", "templates.css"):
        shutil.copyfile(GFX / f, proj / f)
    shutil.copyfile(GFX / "node_modules" / "gsap" / "dist" / "gsap.min.js", proj / "gsap.min.js")

    items, t = [], 0.0
    for g in graphics:
        g = dict(g)
        dur = max(0.5, min(float(g.get("duration", 2.5)), total_out or 1e9))
        if g.get("src"):
            g["src"] = _asset(g["src"], edit_dir, proj / "assets")
        if g.get("srcs"):
            g["srcs"] = [s for s in (_asset(s, edit_dir, proj / "assets") for s in g["srcs"]) if s]
        g["_t0"], g["_dur"] = round(t, 3), dur
        items.append(g)
        t += dur
    (proj / "index.html").write_text(composition(items, brand, W, H, fps), encoding="utf-8")

    packed = proj / "packed.mov"
    print(f"graphics: rendering {len(items)} graphic(s) with HyperFrames ({t:.1f}s of animation)…", flush=True)
    res = subprocess.run(_hyperframes() + ["render", str(proj), "--format", "mov", "--quality",
                                           "draft" if draft else "looks", "--fps", fps, "-o", str(packed)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env={**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1", "DO_NOT_TRACK": "1",
                              "HYPERFRAMES_TELEMETRY_DISABLED": "1"})
    if res.returncode != 0 or not packed.exists():
        raise RuntimeError(f"HyperFrames render failed:\n{(res.stderr or res.stdout)[-1500:]}")

    clips = []
    for i, g in enumerate(items):
        out = proj / f"g{i:02d}_{g['type']}.mov"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{g['_t0']:.3f}", "-i", str(packed),
                        "-t", f"{g['_dur']:.3f}", "-c", "copy", str(out)], check=True)
        clips.append({"file": str(out), "start_in_output": float(g.get("start_in_output", 0.0)),
                      "duration": g["_dur"], "type": g["type"]})
    manifest.write_text(json.dumps(clips, indent=2), encoding="utf-8")
    print(f"graphics → {proj.name} ({len(clips)} clip(s))")
    return clips
