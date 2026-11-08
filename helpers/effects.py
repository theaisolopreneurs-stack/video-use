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
# "@" in a chain is replaced by a unique number, so an effect can use its own filter labels.
EFFECTS.update({
    "sharpen": ("Sharpen", lambda W, H, k: f"unsharp=5:5:{1.2 * k:.2f}"),
    "smooth_skin": ("Smooth skin", lambda W, H, k:
                    f"split[@o][@s];[@s]smartblur=lr={2.5 * k:.1f}:ls={0.9 * min(k, 1):.2f}:lt=-3.5[@b];"
                    f"[@o][@b]blend=all_opacity=0.65,eq=brightness=0.012:saturation=1.03"),
    "glow": ("Dreamy glow", lambda W, H, k:
             f"split[@o][@s];[@s]gblur=sigma={22 * k:.0f},format=gbrp[@b];[@o]format=gbrp[@g];"
             f"[@g][@b]blend=all_mode=screen:all_opacity={0.45 * min(k, 1.5):.2f},format=yuv420p"),
    "old_film": ("Old film", lambda W, H, k:
                 f"hue=s={max(0, 0.35 - 0.2 * (k - 1)):.2f},curves=preset=vintage,noise=alls={int(22 * k)}:allf=t,"
                 "vignette=PI/3.4,eq=brightness='0.03*sin(t*17)':eval=frame"),
    "strobe": ("Strobe", lambda W, H, k: f"eq=brightness='{0.45 * k:.2f}*gt(sin(t*38),0.2)':eval=frame"),
    "flicker": ("Light flicker", lambda W, H, k:
                f"eq=brightness='{0.07 * k:.3f}*sin(t*29)*sin(t*7)':contrast=1.04:eval=frame"),
    "rainbow": ("Rainbow shift", lambda W, H, k: f"hue=H='{2 * k:.2f}*PI*t'"),
    "hue_shift": ("Colour swap", lambda W, H, k: f"hue=h={int(120 * k)}"),
    "motion_blur": ("Motion blur", lambda W, H, k: f"tmix=frames={max(3, int(6 * k))}"),
    "trails": ("Light trails", lambda W, H, k: f"lagfun=decay={min(0.98, 0.9 + 0.05 * k):.2f}"),
    "fisheye": ("Fisheye", lambda W, H, k:
                f"lenscorrection=k1={-0.35 * k:.2f}:k2={0.1 * k:.2f}:i=bilinear"),
    "mirror_split": ("Mirror split", lambda W, H, k:
                     f"crop=iw/2:ih:0:0,split[@l][@r];[@r]hflip[@f];[@l][@f]hstack,scale={W}:{H},setsar=1"),
    "mirror_quad": ("Kaleidoscope", lambda W, H, k:
                    f"crop=iw/2:ih/2:iw/4:ih/8,split=4[@a][@b][@c][@d];[@b]hflip[@e];[@c]vflip[@f];"
                    f"[@d]hflip,vflip[@g];[@a][@e]hstack[@t];[@f][@g]hstack[@u];[@t][@u]vstack,scale={W}:{H},setsar=1"),
    "split_3": ("Triple screen", lambda W, H, k:
                f"scale={W}:-2,crop={W}:{H // 3}:0:(ih-{H // 3})/2,split=3[@a][@b][@c];[@a][@b][@c]vstack=inputs=3,"
                f"scale={W}:{H},setsar=1"),
    "posterize": ("Poster", lambda W, H, k:
                  "lutrgb=r='bitand(val,224)':g='bitand(val,224)':b='bitand(val,224)'"),
    "comic": ("Comic book", lambda W, H, k:
              "split[@o][@s];[@s]edgedetect=low=0.1:high=0.25,negate,format=gbrp[@e];"
              "[@o]eq=saturation=1.6:contrast=1.2,lutrgb=r='bitand(val,224)':g='bitand(val,224)':b='bitand(val,224)',"
              "format=gbrp[@p];[@p][@e]blend=all_mode=multiply,format=yuv420p"),
    "sketch": ("Pencil sketch", lambda W, H, k: "edgedetect=low=0.08:high=0.2,negate,hue=s=0"),
    "thermal": ("Thermal camera", lambda W, H, k: "format=gray,pseudocolor=preset=turbo,format=yuv420p"),
    "night_vision": ("Night vision", lambda W, H, k:
                     f"hue=s=0,colorchannelmixer=rr=0.15:gg=1.1:bb=0.2,noise=alls={int(18 * k)}:allf=t,vignette=PI/3.5"),
    "spotlight": ("Spotlight", lambda W, H, k: f"vignette=angle={min(1.5, 0.95 * k):.2f}"),
    "dim": ("Dim background", lambda W, H, k: f"eq=brightness={-0.2 * k:.2f}:saturation=0.75"),
    "cool_tint": ("Cool tint", lambda W, H, k: f"colortemperature=temperature={int(9500 + 1500 * k)}"),
    "warm_tint": ("Warm tint", lambda W, H, k: f"colortemperature=temperature={max(2500, int(4000 - 500 * k))}"),
})
CUT_EFFECTS = {"glitch": ("glitch", 0.25), "shake": ("shake", 0.3), "flash_cut": ("flash", 0.12)}


def effect_list() -> list[dict]:
    return [{"id": "hand_wipe", "label": "Hand swipe reveal"}] + [{"id": k, "label": v[0]} for k, v in EFFECTS.items()]


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
        if e.get("type") == "hand_wipe":
            from handwipe import wipe_effect
            out.append(wipe_effect(e, plan, i, W, H))
            continue
        if e.get("type") not in EFFECTS:
            continue
        a = float(e.get("start_in_output", 0.0))
        b = a + float(e["duration"]) if e.get("duration") else 1e9
        chain = EFFECTS[e["type"]][1](W, H, float(e.get("intensity", 1.0))).replace("@", f"fx{i}_")
        reg = e.get("region")
        if reg == "face":  # e.g. hide a face: {"type": "pixelate", "region": "face"}
            from framing import zoom_center
            cx, cy = zoom_center(plan, Path(_edit_dir(plan)), a + 0.01) if _edit_dir(plan) else (0.5, 0.4)
            reg = {"x": cx - 0.18, "y": cy - 0.14, "w": 0.36, "h": 0.28}
        if isinstance(reg, dict):  # only this box: fractions of the frame
            rw, rh = int(float(reg.get("w", 0.3)) * W) // 2 * 2, int(float(reg.get("h", 0.2)) * H) // 2 * 2
            rx, ry = int(max(0.0, float(reg.get("x", 0.35))) * W), int(max(0.0, float(reg.get("y", 0.3))) * H)
            rw, rh = min(rw, W - rx), min(rh, H - ry)
            chain = chain.replace(f"{W}:{H}", f"{rw}:{rh}")
            out.append(f",split[fx{i}a][fx{i}b];[fx{i}b]crop={rw}:{rh}:{rx}:{ry},{chain}[fx{i}c];"
                       f"[fx{i}a][fx{i}c]overlay=x={rx}:y={ry}:enable='between(t,{a:.3f},{b:.3f})'")
            continue
        out.append(f",split[fx{i}a][fx{i}b];[fx{i}b]{chain}[fx{i}c];"
                   f"[fx{i}a][fx{i}c]overlay=enable='between(t,{a:.3f},{b:.3f})'")
    return "".join(out)


def _edit_dir(plan: dict) -> str | None:
    return plan.get("_edit_dir")


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
        # Anything see-through needs an alpha channel, so it can't be H.264.
        alpha = bool(b.get("mask") or b.get("chroma") or (b.get("opacity") is not None and not b.get("blend")))
        key = hashlib.sha1(json.dumps([str(p), b, W, H, fps, draft], sort_keys=True).encode()).hexdigest()[:10]
        clip = out_dir / f"broll_{key}.{'mov' if alpha else 'mp4'}"
        pip = b.get("mode") == "pip" or b.get("keyframes") or b.get("mask")
        if pip:
            pw = int(W * float(b.get("size", 0.46))) // 2 * 2
            pad = max(4, W // 120)
            vf = f"scale={pw}:-2"
            if not b.get("mask") and not b.get("chroma") and not b.get("no_border"):
                vf += f",pad=iw+{pad * 2}:ih+{pad * 2}:{pad}:{pad}:white"
        else:
            vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
        if b.get("flip"):
            vf += ",hflip"
        if b.get("chroma"):  # green screen: {"chroma": "#00FF00", "similarity": 0.14}
            vf += (f",format=yuva420p,chromakey={str(b['chroma']).replace('#', '0x')}:"
                   f"{float(b.get('similarity', 0.14)):.2f}:{float(b.get('smooth', 0.08)):.2f}")
        if not clip.exists():
            is_img = p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
            source_in = ["-loop", "1", "-framerate", fps] if is_img else ["-ss", f"{float(b.get('src_start', 0)):.3f}"]
            codec = (["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le"] if alpha else
                     ["-c:v", "libx264", "-preset", "ultrafast" if draft else "fast", "-crf", "20", "-pix_fmt", "yuv420p"])
            if b.get("mask"):
                mask = shape_mask(b["mask"], out_dir)
                graph = (f"[0:v]{vf},fps={fps},format=yuva444p[c];[1:v]scale2ref[m][c2];"
                         f"[c2][m]alphamerge,colorchannelmixer=aa={float(b.get('opacity', 1)):.2f}[o]")
                inputs = [*source_in, "-t", f"{dur:.3f}", "-i", str(p), "-loop", "1", "-i", str(mask)]
                cmd = ["-filter_complex", graph.replace("[1:v]scale2ref[m][c2];[c2][m]alphamerge",
                                                        "[1:v]format=gray[mg];[mg][c]scale2ref[m][c2];[c2][m]alphamerge"),
                       "-map", "[o]", "-t", f"{dur:.3f}"]
            else:
                if alpha and b.get("opacity") is not None:
                    vf += f",format=yuva444p,colorchannelmixer=aa={float(b['opacity']):.2f}"
                inputs = [*source_in, "-t", f"{dur:.3f}", "-i", str(p)]
                cmd = ["-an", "-vf", f"{vf},fps={fps}"]
            subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, *cmd, *codec, str(clip)], check=True)
        t0 = float(b["start_in_output"])
        ov = {"file": str(clip), "start_in_output": t0, "duration": dur}
        if b.get("blend"):  # screen, multiply, overlay, softlight, darken, lighten, difference
            ov["blend"], ov["opacity"] = b["blend"], b.get("opacity", 1)
        if b.get("keyframes"):  # [{"t": 0, "x": 0.8, "y": 0.2, "scale": 1}, ...] x/y = centre, 0-1
            from framing import pw_expr
            kf = b["keyframes"]
            rel = f"(t-{t0:.3f})"
            x = pw_expr(rel, [(float(k["t"]), float(k.get("x", 0.5))) for k in kf])
            y = pw_expr(rel, [(float(k["t"]), float(k.get("y", 0.5))) for k in kf])
            ov["x"], ov["y"] = f"({x})*W-w/2", f"({y})*H-h/2"
            if any("scale" in k for k in kf):
                sc = pw_expr(rel, [(float(k["t"]), float(k.get("scale", 1))) for k in kf])
                ov["vf"] = f"scale=w='iw*({sc})':h='ih*({sc})':eval=frame"
        elif pip:
            ov["x"], ov["y"] = b.get("x", "W-w-W*0.05"), b.get("y", "H*0.08")
            if isinstance(ov["x"], (int, float)):
                ov["x"] = f"{ov['x']}*W-w/2"
            if isinstance(ov["y"], (int, float)):
                ov["y"] = f"{ov['y']}*H-h/2"
        overlays.append(ov)
    return overlays


def shape_mask(shape: str, out_dir: Path, size: int = 1024) -> Path:
    """A white-on-black mask image: circle, rounded, heart, star, diamond, hexagon."""
    import math
    from PIL import Image, ImageDraw
    path = out_dir / f"mask_{shape}.png"
    if path.exists():
        return path
    im = Image.new("L", (size * 2, size * 2), 0)
    d = ImageDraw.Draw(im)
    S, c = size * 2, size
    if shape == "rounded":
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=S // 7, fill=255)
    elif shape == "diamond":
        d.polygon([(c, 0), (S, c), (c, S), (0, c)], fill=255)
    elif shape == "hexagon":
        d.polygon([(c + c * math.cos(math.pi / 3 * i), c + c * math.sin(math.pi / 3 * i)) for i in range(6)], fill=255)
    elif shape == "star":
        d.polygon([(c + (c if i % 2 == 0 else c * 0.42) * math.sin(math.pi * i / 5),
                    c - (c if i % 2 == 0 else c * 0.42) * math.cos(math.pi * i / 5)) for i in range(10)], fill=255)
    elif shape == "heart":
        pts = []
        for i in range(360):
            t = math.radians(i)
            pts.append((c + c * 0.058 * 16 * math.sin(t) ** 3,
                        c * 0.92 - c * 0.058 * (13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))))
        d.polygon(pts, fill=255)
    else:
        d.ellipse([0, 0, S - 1, S - 1], fill=255)
    im.resize((size, size), Image.LANCZOS).save(path)
    return path
