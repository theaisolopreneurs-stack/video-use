"""Short looping previews for the Studio's pickers: what a transition, effect or camera move looks like,
on this video's own footage, before you choose it. Cached in <edit>/storyboard/previews/.

    preview(plan, edit_dir, "transition", "whip_left") -> Path to a ~2 s silent mp4
    kinds: transition (render.TRANSITIONS + cut effects), effect (effects.EFFECTS + freeze_frame,
    hand_wipe), zoom (punch, push_in, push_out, crash, crash_out, drift)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import plan as planlib

SIZES = {"9:16": (180, 320), "4:5": (216, 270), "1:1": (240, 240), "16:9": (320, 180)}
ZOOMS = {  # scale over time t (seconds), the clip is 1.8 s long
    "punch": "if(gte(t,0.6),1.22,1)",
    "push_in": "1+0.22*pow(min(1,max(0,(t-0.3)/1.1)),2)*(3-2*min(1,max(0,(t-0.3)/1.1)))",
    "push_out": "1.22-0.22*pow(min(1,max(0,(t-0.3)/1.1)),2)*(3-2*min(1,max(0,(t-0.3)/1.1)))",
    "crash": "1+0.3*min(1,max(0,(t-0.6)/0.25))",
    "crash_out": "1.3-0.3*min(1,max(0,(t-0.6)/0.25))",
    "drift": "1+0.12*t/1.8",
}


def _shots(plan: dict, edit_dir: Path):
    on = plan.get("ranges") or []
    if not on:
        raise ValueError("no scenes")
    # The two longest scenes: steadier than a quick opener, and usually two different shots.
    by_len = sorted(on, key=lambda r: float(r["end"]) - float(r["start"]), reverse=True)
    a, b = by_len[0], (by_len[1] if len(by_len) > 1 else by_len[0])
    mid = lambda r, f: float(r["start"]) + (float(r["end"]) - float(r["start"])) * f  # noqa: E731
    src = lambda r: planlib.resolve(plan["sources"][r["source"]], edit_dir)  # noqa: E731
    return (src(a), mid(a, 0.3)), (src(b), mid(b, 0.6) if b is not a else mid(a, 0.8))


def preview(plan: dict, edit_dir: Path, kind: str, name: str) -> Path | None:
    out_dir = edit_dir / "storyboard" / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{kind}_{name}.mp4"
    if out.exists():
        return out
    W, H = SIZES.get((plan.get("target") or {}).get("aspect"), (180, 320))
    fit = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=24,format=yuv420p"
    (pa, ta), (pb, tb) = _shots(plan, edit_dir)
    A = ["-ss", f"{ta:.2f}", "-t", "2", "-i", str(pa)]
    B = ["-ss", f"{tb:.2f}", "-t", "2", "-i", str(pb)]
    graph, inputs = None, A
    if kind == "transition":
        from effects import CUT_EFFECTS, EFFECTS
        from render import TRANSITIONS
        inputs = A + B
        if name in TRANSITIONS:
            x, d = TRANSITIONS[name]
            d = max(0.35, d)
            if x == "custom":  # hand swipe: a line runs across and reveals the next shot
                expr = "if(lt(abs(X/W-(P)),0.006),if(eq(PLANE,0),255,128),if(lt(X/W,P),B,A))"
                graph = f"[0:v]{fit}[a];[1:v]{fit}[b];[a][b]xfade=transition=custom:duration=0.6:offset=0.8:expr='{expr}'[v]"
            else:
                graph = f"[0:v]{fit}[a];[1:v]{fit}[b];[a][b]xfade=transition={x}:duration={d}:offset=0.8[v]"
        elif name in CUT_EFFECTS:
            fx, d = CUT_EFFECTS[name]
            chain = EFFECTS[fx][1](W, H, 1.0).replace("@", "p")
            graph = (f"[0:v]{fit},trim=0:0.9,setpts=PTS-STARTPTS[a];[1:v]{fit},trim=0:0.9,setpts=PTS-STARTPTS[b];"
                     f"[a][b]concat=n=2:v=1:a=0,split[o][e];[e]{chain}[c];"
                     f"[o][c]overlay=enable='between(t,{0.9 - d / 2:.2f},{0.9 + d / 2:.2f})'[v]")
    elif kind == "effect":
        from effects import EFFECTS
        if name in EFFECTS:
            chain = EFFECTS[name][1](W, H, 1.0).replace("@", "p")
            a, b = (0.6, 0.9) if name in ("blackout", "flash") else (0.45, 2)
            graph = f"[0:v]{fit},split[o][e];[e]{chain}[c];[o][c]overlay=enable='between(t,{a},{b})'[v]"
        elif name == "freeze_frame":
            graph = (f"[0:v]{fit},split[m][s];[m][s]freezeframes=first=12:last=40:replace=12,split[o][g];"
                     f"[g]hue=s=0,scale={int(W * 1.2) // 2 * 2}:-2,crop={W}:{H}[z];[o][z]overlay=enable='between(t,0.5,1.7)'[v]")
        elif name == "hand_wipe":
            expr = "if(lt(abs(X/W-(T-0.3)/0.7),0.006),255,if(lt(X/W,(T-0.3)/0.7),B,A))"
            graph = (f"[0:v]{fit},split[o][d];[d]eq=brightness=-0.2:saturation=0.75[dd];[o]format=gbrp[g1];[dd]format=gbrp[g2];"
                     f"[g1][g2]blend=all_expr='{expr}',format=yuv420p[v]")
    elif kind == "zoom" and name in ZOOMS:
        z = ZOOMS[name]
        graph = (f"[0:v]{fit},scale=w='trunc(iw*({z})/2)*2':h=-2:eval=frame,crop={W}:{H},setsar=1[v]")
    if graph is None:
        return None
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-t", "1.8", "-an",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(out)], check=False, capture_output=True)
    return out if out.exists() and out.stat().st_size > 0 else None
