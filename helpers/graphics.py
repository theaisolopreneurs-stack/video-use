"""Motion graphics from plan.json "graphics" via HyperFrames (HTML + GSAP -> alpha video).

    "graphics": [
      {"type": "stat", "value": 0, "suffix": " and 4", "label": "Texans this season",
       "start_in_output": 11.0, "duration": 2.2, "position": "upper"},
      {"type": "prompt_box", "text": "How do we fix the Texans?", "start_in_output": 15.2, "duration": 3}
    ]

Types (graphics/templates.js): text_card, stat, timer, bar_chart, checklist, prompt_box,
chat, app_window, pip, collage, cta, lower_third, emoji, progress, pill_stack, pills, word_stamp,
comment_drop, voice_note, incoming_call, keyboard, file_drop, screenplay, intertitle.
running_list (a side list that highlights the current item for a long stretch) is drawn as still
pictures instead (see running_list_overlays), since a long browser recording would be slow. position: upper (default)
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
         "pip", "collage", "cta", "lower_third", "emoji", "progress",
         "pill_stack", "pills", "word_stamp", "comment_drop", "voice_note", "incoming_call", "keyboard",
         "file_drop", "screenplay", "intertitle", "running_list")
# Not recorded in the browser: drawn by FFmpeg (progress) or as still pictures (running_list).
STILL = {"progress", "running_list"}
# What the Studio offers in its Graphics picker: a label, example wording, how long it usually
# stays, and which fields can be edited (text = one line, lines = one item per line, number).
KIT = {
    "pills": ("Corner tags", 3.5, {"items": ["20 video effects in 20 seconds", "built by *Claude*"]}, {"items": "lines"}),
    "pill_stack": ("Stacked label pills", 3.2, {"lines": ["Comment *EDIT*", "to get access to these", "+ 40 more"], "position": "lower"}, {"lines": "lines"}),
    "word_stamp": ("Word stamp", 2.6, {"kicker": "word", "text": "STAMP"}, {"kicker": "text", "text": "text"}),
    "comment_drop": ("Comment drop", 3.4, {"text": "EDIT", "reply": "EDIT to get the full guide", "handle": "you", "replier": "yourhandle"},
                     {"text": "text", "reply": "text", "handle": "text", "replier": "text"}),
    "voice_note": ("Voice note", 3.0, {"seconds": 7, "label": "Voice note"}, {"label": "text", "seconds": "number"}),
    "incoming_call": ("Incoming call", 2.8, {"name": "Incoming call", "sub": "mobile", "declined_text": "Call declined"},
                      {"name": "text", "sub": "text", "declined_text": "text"}),
    "keyboard": ("Phone keyboard typing", 3.0, {"text": "Predict keys"}, {"text": "text"}),
    "file_drop": ("File drop", 3.2, {"name": "raw-footage.mov", "size": "24.3 MB", "label": "0:32 of raw footage", "position": "middle"},
                  {"name": "text", "size": "text", "label": "text"}),
    "screenplay": ("Screenplay page", 3.0, {"heading": "INT. STUDIO - DAY", "character": "YOU", "paren": "(to camera)",
                                            "text": "This is the line you say, typed like a film script.", "highlight": "film script."},
                   {"heading": "text", "character": "text", "paren": "text", "text": "text", "highlight": "text"}),
    "intertitle": ("Intertitle card", 2.6, {"text": "Intertitle."}, {"text": "text", "sub": "text"}),
    "running_list": ("Running checklist", 0, {"title": "In this video", "footer": ""}, {"title": "text", "items": "lines", "footer": "text"}),
    "text_card": ("Big text card", 2.5, {"text": "The big idea", "sub": "and why it matters"}, {"text": "text", "sub": "text"}),
    "stat": ("Counting number", 2.5, {"value": 100, "suffix": "%", "label": "of people"}, {"value": "number", "prefix": "text", "suffix": "text", "label": "text"}),
    "checklist": ("Checklist", 3.0, {"title": "The plan", "items": ["Step one", "Step two", "Step three"]}, {"title": "text", "items": "lines"}),
    "prompt_box": ("AI prompt box", 3.0, {"text": "Write me a viral hook"}, {"text": "text"}),
    "chat": ("Chat bubbles", 3.5, {"messages": [{"from": "user", "text": "Can you edit my video?"}, {"from": "ai", "text": "Done in 30 seconds."}]}, {}),
    "cta": ("Call to action", 3.0, {"text": "Follow for more", "comment": "EDIT", "handle": "you"}, {"text": "text", "comment": "text", "handle": "text"}),
    "lower_third": ("Name and handle", 3.0, {"name": "Your Name", "handle": "@yourhandle"}, {"name": "text", "handle": "text"}),
    "emoji": ("Big emoji", 1.5, {"emoji": "🔥"}, {"emoji": "text"}),
}


def graphic_kit() -> list[dict]:
    return [{"id": k, "label": v[0], "duration": v[1], "defaults": v[2], "fields": v[3]} for k, v in KIT.items()]


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


def layout(W: int, H: int, side_panels: bool) -> dict:
    """Where graphics go for a canvas shape, so they stay off the speaker's face.

    9:16: full size above the face. 4:5 / 1:1: smaller and higher (less room above the
    face). 16:9 with a blurred fill (vertical source): in the left-hand panel.
    """
    r = W / H
    if r < 0.65:
        return {"scale": 1.0, "tops": POSITIONS, "left": None}
    if r < 1.2:
        k = 0.78 if r < 0.9 else 0.58
        return {"scale": k, "tops": {"upper": 0.03, "middle": 0.1, "lower": 0.2}, "left": None}
    if side_panels:
        return {"scale": 0.62, "tops": {"upper": 0.18, "middle": 0.32, "lower": 0.46}, "left": (0.0, 0.33)}
    return {"scale": 0.7, "tops": {"upper": 0.05, "middle": 0.2, "lower": 0.35}, "left": None}


def composition(items: list[dict], brand: dict, W: int, H: int, fps: str, lay: dict | None = None,
                list_side: str | None = None) -> str:
    c = brand.get("colors") or {}
    f = brand.get("fonts") or {}
    total = sum(i["_dur"] for i in items)
    lay = lay or layout(W, H, False)
    side = (f'box.style.left = "{lay["left"][0] * 100:.1f}%"; box.style.right = "auto"; '
            f'box.style.width = "{(lay["left"][1] - lay["left"][0]) * 100:.1f}%";') if lay["left"] else ""
    css_vars = (f"--u:{min(W, H) / 1080 * lay['scale']:.5f}px;--primary:{c.get('primary', '#1F5C4A')};"
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
<div id="root" class="{f"list-{list_side}" if list_side else ""}" data-composition-id="main" data-start="0" data-duration="{total:.3f}" data-width="{W}" data-height="{H}" data-fps="{fps}"></div>
<script>
const items = {data};
const root = document.getElementById("root");
const tl = gsap.timeline({{ paused: true }});
items.forEach((g, i) => {{
  const box = document.createElement("div");
  box.className = "slot clip";
  box.dataset.start = g._t0; box.dataset.duration = g._dur; box.dataset.trackIndex = String(i);
  if (g.type === "progress") {{ box.style.inset = "0"; }}
  else {{ box.style.top = ({json.dumps(lay["tops"])}[g.position] ?? {lay["tops"]["upper"]}) * 100 + "%"; {side} }}
  root.appendChild(box);
  (window.HF_TEMPLATES[g.type] || window.HF_TEMPLATES.text_card)(g, box, tl, g._t0, g._dur);
}});
window.__timelines["main"] = tl; tl.seek(0);
</script></body></html>
"""


def _frame_at(plan: dict, edit_dir: Path, t_out: float, out: Path) -> bool:
    """A still of your own video at output second t_out (for the file-drop card)."""
    import framing
    from plan import range_len, resolve
    t = 0.0
    for r in plan.get("ranges") or []:
        d = range_len(r)
        if t <= t_out < t + d or r is plan["ranges"][-1]:
            src = resolve(plan["sources"][r["source"]], edit_dir)
            img = framing.grab(src, float(r["start"]) + min(d, max(0.0, t_out - t)) * float(r.get("speed") or 1), width=540)
            if img is None:
                return False
            from PIL import Image
            out.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(img).save(out, quality=88)
            return True
        t += d
    return False


def running_list_overlays(plan: dict, edit_dir: Path, W: int, H: int) -> list[dict]:
    """The side list ("IN THIS VIDEO  03 / 20"): one picture per moment, the current item lit.

    {"type": "running_list", "title": "In this video", "items": [{"text": "Live cursor", "at": 0.0}, ...],
     "footer": "+ 40 more", "side": "right"|"left", "start_in_output", "duration"}
    Each item is current from its "at" (seconds in the finished video) until the next one starts.
    """
    from PIL import Image, ImageDraw, ImageFont
    out: list[dict] = []
    brand = load_brand(edit_dir)
    colors = brand.get("colors") or {}
    for gi, g in enumerate(x for x in plan.get("graphics") or [] if x.get("type") == "running_list"):
        items = [i if isinstance(i, dict) else {"text": str(i)} for i in g.get("items") or []]
        if not items:
            continue
        g0 = float(g.get("start_in_output", 0))
        dur = float(g.get("duration") or 0) or max(1.0, float(plan.get("total_duration_s") or 0) - g0)
        g1 = g0 + dur
        n = len(items)
        for k, it in enumerate(items):  # spread any items without a time evenly
            it.setdefault("at", g0 + dur * k / n)
        key = hashlib.sha1(json.dumps([g, colors, W, H], sort_keys=True).encode()).hexdigest()[:10]
        folder = edit_dir / "animations" / f"list_{key}"
        folder.mkdir(parents=True, exist_ok=True)
        u = min(W, H) / 1080
        fs = max(10, int(26 * u))
        bold = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf" if Path("C:/Windows/Fonts/segoeuib.ttf").exists()
                                  else "DejaVuSans-Bold.ttf", fs)
        reg = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf" if Path("C:/Windows/Fonts/segoeui.ttf").exists()
                                 else "DejaVuSans.ttf", fs)
        mono = ImageFont.truetype("C:/Windows/Fonts/consolab.ttf" if Path("C:/Windows/Fonts/consolab.ttf").exists()
                                  else "DejaVuSansMono-Bold.ttf", max(9, int(19 * u)))
        line = int(fs * 1.42)
        pw = int(W * 0.31) if W < H else int(W * 0.2)
        ph = int(line * n + 110 * u + (line * 1.4 if g.get("footer") else 0))
        ph = min(ph, int(H * 0.9))
        x0 = W - pw - int(28 * u) if g.get("side", "right") == "right" else int(28 * u)
        y0 = int(H * 0.025)
        prim = colors.get("primary", "#1F5C4A")
        accent = colors.get("accent", "#F2C14E")

        def rgb(hx, a=255):
            hx = hx.lstrip("#")
            return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16), a)
        times = [float(i["at"]) for i in items]
        for k in range(n):
            f = folder / f"state_{k:02d}.png"
            if not f.exists():
                img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                d.rounded_rectangle([x0, y0, x0 + pw, y0 + ph], radius=int(22 * u), fill=rgb(prim, 235))
                pad = int(20 * u)
                title = str(g.get("title") or "In this video").upper()
                d.text((x0 + pad, y0 + pad), title, font=mono, fill=rgb(accent))
                cnt = f"{k + 1:02d} / {n:02d}"
                d.text((x0 + pw - pad - d.textlength(cnt, font=mono), y0 + pad), cnt, font=mono, fill=(255, 255, 255, 200))
                y = y0 + pad + int(46 * u)
                for j, it in enumerate(items):
                    if y + line > y0 + ph - pad:
                        break
                    txt = str(it.get("text", ""))
                    if j == k:
                        d.rounded_rectangle([x0 + pad // 2, y - int(3 * u), x0 + pw - pad // 2, y + line - int(5 * u)],
                                            radius=int(8 * u), fill=(255, 255, 255, 255))
                        d.text((x0 + pad, y), f"→ {txt}", font=bold, fill=(20, 20, 20, 255))
                    else:
                        # done: a drawn tick (fonts often lack the symbol); still to come: a dot
                        tx, ty, sz = x0 + pad, y + line * 0.18, fs * 0.62
                        if j < k:
                            d.line([(tx, ty + sz * 0.55), (tx + sz * 0.38, ty + sz * 0.9), (tx + sz, ty + sz * 0.1)],
                                   fill=rgb(accent), width=max(2, int(3 * u)), joint="curve")
                        else:
                            r_ = max(2, int(3 * u))
                            d.ellipse([tx + sz * 0.4 - r_, ty + sz * 0.5 - r_, tx + sz * 0.4 + r_, ty + sz * 0.5 + r_], fill=(255, 255, 255, 120))
                        d.text((x0 + pad + int(fs * 1.05), y), txt, font=reg if j > k else bold,
                               fill=(255, 255, 255, 150 if j > k else 235))
                    y += line
                if g.get("footer"):
                    ft = str(g["footer"])
                    tw = d.textlength(ft, font=bold)
                    yb = y0 + ph - pad - line
                    d.rounded_rectangle([x0 + pad, yb, x0 + pad + tw + pad, yb + line], radius=int(8 * u), fill=rgb(accent))
                    d.text((x0 + pad + pad // 2, yb + int(2 * u)), ft, font=bold, fill=(20, 20, 20, 255))
                img.save(f)
            a = max(g0, times[k]) if k else g0
            b = min(g1, times[k + 1]) if k + 1 < n else g1
            if b > a:
                out.append({"file": str(f), "start_in_output": round(a, 3), "duration": round(b - a, 3), "type": "running_list"})
    return out


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
    W, H = framing.probe_size(base)
    stills = running_list_overlays(plan, edit_dir, W, H)
    graphics = [g for g in plan.get("graphics") or [] if g.get("type") and g["type"] not in STILL]
    if not graphics:
        return stills
    fps = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=r_frame_rate", "-of", "default=nw=1:nk=1", str(base)],
                         capture_output=True, text=True).stdout.strip() or "30/1"
    total_out = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                      "-of", "default=nw=1:nk=1", str(base)],
                                     capture_output=True, text=True).stdout.strip() or 0)
    brand = load_brand(edit_dir)

    lists = [g for g in plan.get("graphics") or [] if g.get("type") == "running_list"]
    list_side = (lists[0].get("side") or "right") if lists and W < H else None
    # Each graphic is cached on its own (by its content, not where it sits on the timeline), so moving
    # one or changing one's words re-records only that one.
    common = [list_side, brand.get("colors"), brand.get("fonts"), W, H, fps, draft, (plan.get("target") or {}).get("aspect"),
              Path(__file__).stat().st_mtime, (GFX / "templates.js").stat().st_mtime, (GFX / "templates.css").stat().st_mtime]
    cache = edit_dir / "animations" / "gfx"
    cache.mkdir(parents=True, exist_ok=True)

    def file_sig(f):
        try:
            q = Path(f) if Path(f).is_absolute() else edit_dir.parent / f
            return [str(f), q.stat().st_mtime_ns]
        except OSError:
            return [str(f)]

    want = []
    for g in graphics:
        dur = max(0.5, min(float(g.get("duration", 2.5)), total_out or 1e9))
        body = {k: v for k, v in g.items() if k != "start_in_output" or g["type"] == "file_drop"}
        files = [file_sig(f) for f in ([g["src"]] if g.get("src") else []) + list(g.get("srcs") or [])]
        key = hashlib.sha1(json.dumps([body, dur, files, common], sort_keys=True, default=str).encode()).hexdigest()[:12]
        want.append((g, dur, cache / f"{key}_{g['type']}.mov"))
    missing = [w for w in want if not w[2].exists()]
    if missing:
        proj = edit_dir / "animations" / "gfx_work"
        shutil.rmtree(proj, ignore_errors=True)
        proj.mkdir(parents=True)
        for f in ("templates.js", "templates.css"):
            shutil.copyfile(GFX / f, proj / f)
        shutil.copyfile(GFX / "node_modules" / "gsap" / "dist" / "gsap.min.js", proj / "gsap.min.js")
        items, t = [], 0.0
        for g, dur, _ in missing:
            g = dict(g)
            if g.get("src"):
                g["src"] = _asset(g["src"], edit_dir, proj / "assets")
            if g.get("srcs"):
                g["srcs"] = [s_ for s_ in (_asset(s_, edit_dir, proj / "assets") for s_ in g["srcs"]) if s_]
            if g["type"] == "file_drop" and not g.get("src"):  # the card shows a frame of your own video
                still = proj / "assets" / f"frame_{len(items)}.jpg"
                if _frame_at(plan, edit_dir, float(g.get("start_in_output", 0)) + 1.5, still):
                    g["src"] = f"assets/{still.name}"
            g["_t0"], g["_dur"] = round(t, 3), dur
            items.append(g)
            t += dur
        first = plan["ranges"][0]
        src = Path(plan["sources"][first["source"]])
        src = src if src.is_absolute() else edit_dir / src
        aspect = (plan.get("target") or {}).get("aspect") or ""
        padded = bool(aspect) and framing.reframe_pad(src, aspect, W, H) is not None
        (proj / "index.html").write_text(composition(items, brand, W, H, fps, layout(W, H, padded), list_side), encoding="utf-8")
        packed = proj / "packed.mov"
        print(f"graphics: rendering {len(items)} new graphic(s) with HyperFrames ({t:.1f}s of animation)"
              + (f"; {len(want) - len(missing)} unchanged, reused" if len(want) > len(missing) else "") + "…", flush=True)
        res = subprocess.run(_hyperframes() + ["render", str(proj), "--format", "mov", "--quality",
                                               "draft" if draft else "looks", "--fps", fps, "-o", str(packed)],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             env={**os.environ, "HYPERFRAMES_SKIP_SKILLS": "1", "DO_NOT_TRACK": "1",
                                  "HYPERFRAMES_TELEMETRY_DISABLED": "1"})
        if res.returncode != 0 or not packed.exists():
            raise RuntimeError("HyperFrames render failed: " + (res.stderr or res.stdout)[-1500:])
        for g, (_, _, out) in zip(items, missing):
            part = out.with_name(out.stem + "_part.mov")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{g['_t0']:.3f}", "-i", str(packed),
                            "-t", f"{g['_dur']:.3f}", "-c", "copy", str(part)], check=True)
            part.replace(out)
        shutil.rmtree(proj, ignore_errors=True)
    else:
        print(f"graphics: all {len(want)} unchanged, reused")
    clips = [{"file": str(out), "start_in_output": float(g.get("start_in_output", 0.0)), "duration": dur, "type": g["type"]}
             for g, dur, out in want]
    return clips + stills
