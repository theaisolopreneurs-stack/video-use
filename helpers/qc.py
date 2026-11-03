"""Quality check a rendered plan before anyone watches it.

    python helpers/qc.py <edit>/plan.json <edit>/rough.mp4
    python helpers/qc.py <edit>/plan.json <videos_dir>/exports/final_9x16.mp4

Measures, never guesses (Claude can't listen or watch in real time):
- length matches the plan
- loudness: integrated LUFS (target -14 ±1.5) and true peak (≤ -1 dBTP)
- clicks at cuts: a peak at a cut boundary far above the audio around it
- black frames
- text/graphics over the face: pixels that differ between the render and the
  plain (zoomed) base, inside the speaker's face box
- platform safe zones (9:16): overlays under the TikTok/Reels top bar, right-hand
  buttons, or bottom caption area
- caption flashes (cues under 0.3 s) and reading speed
- clutter: zooms + graphics + sound effects + popped words per 10 s

Writes <edit>/qc/report.md, report.json and cuts.jpg (frames either side of every cut,
for Claude to look at). Prints a one-line summary per finding.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import framing
import plan as planlib

LUFS_TARGET, LUFS_TOL, TP_MAX = -14.0, 1.5, -1.0
# 9:16 platform UI (fractions of width/height) that covers part of the frame.
SAFE_ZONES = {
    "top bar": (0.0, 0.0, 1.0, 0.08),
    "right-hand buttons": (0.86, 0.42, 1.0, 0.86),
    "bottom caption area": (0.0, 0.86, 1.0, 0.985),  # below 98.5% = progress bar, allowed
}
CLUTTER_PER_10S = 5


def duration(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True)
    return float(out.stdout.strip() or 0)


def frame(video: Path, t: float, vf: str | None = None, width: int = 360) -> np.ndarray | None:
    w, h = framing.probe_size(video)
    height = int(round(h * width / w / 2) * 2)
    chain = (f"{vf}," if vf else "") + f"trim=start={t:.3f},setpts=PTS-STARTPTS,scale={width}:{height}"
    # With a zoom chain the time variable must be the original one, so seek inside the graph.
    cmd = (["ffmpeg", "-v", "error", "-i", str(video), "-vf", chain] if vf else
           ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-vf", f"scale={width}:{height}"])
    raw = subprocess.run(cmd + ["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(height, width, 3) if len(raw) == width * height * 3 else None


def frames_every(video: Path, every_n: int, vf: str | None = None, width: int = 360) -> list[np.ndarray]:
    """Every n-th frame from a single decode. Picked by frame number, not time, so a render
    and its base line up exactly (zoompan re-times frames, which drifts time-based picks)."""
    w, h = framing.probe_size(video)
    height = int(round(h * width / w / 2) * 2)
    chain = (f"{vf}," if vf else "") + f"select='not(mod(n\\,{every_n}))',scale={width}:{height}"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", chain, "-fps_mode", "passthrough",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    n = width * height * 3
    return [np.frombuffer(raw[i:i + n], np.uint8).reshape(height, width, 3) for i in range(0, len(raw) - n + 1, n)]


def cut_times(plan: dict) -> list[float]:
    t, out = 0.0, []
    for r in plan["ranges"][:-1]:
        t += float(r["end"]) - float(r["start"])
        out.append(round(t, 3))
    return out


# ---- checks ---------------------------------------------------------------

def check_length(plan, video, f):
    want, got = planlib.total_duration(plan), duration(video)
    if abs(want - got) > 0.25:
        f.append(("fail", None, f"length is {got:.2f}s but the plan adds up to {want:.2f}s"))
    return {"planned_s": want, "rendered_s": round(got, 2)}


def check_loudness(video, f):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    summary = err[err.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", summary)
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)
    lufs = float(i.group(1)) if i else None
    peak = float(tp.group(1)) if tp else None
    if lufs is not None and abs(lufs - LUFS_TARGET) > LUFS_TOL:
        f.append(("warn", None, f"loudness {lufs:.1f} LUFS (target {LUFS_TARGET:.0f}; draft renders skip the precise pass)"))
    if peak is not None and peak > TP_MAX + 0.15:  # 0.15 dB of meter rounding / draft's 1-pass loudnorm
        f.append(("warn", None, f"true peak {peak:.1f} dBTP (keep ≤ {TP_MAX:.0f} to avoid distortion on phones)"))
    return {"lufs": lufs, "true_peak_dbtp": peak}


def check_clicks(plan, video, f):
    sr = 16000
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True).stdout
    a = np.abs(np.frombuffer(pcm, np.float32))
    found = []
    for t in cut_times(plan):
        c = int(t * sr)
        edge = a[max(0, c - int(0.012 * sr)): c + int(0.012 * sr)]
        around = a[max(0, c - int(0.3 * sr)): c + int(0.3 * sr)]
        if len(edge) == 0 or len(around) == 0:
            continue
        ratio = float(edge.max() / (np.sqrt(np.mean(around ** 2)) + 1e-6))
        if ratio > 8 and edge.max() > 0.25:
            found.append(t)
            f.append(("warn", t, f"possible click at the cut (peak {ratio:.0f}× the audio around it)"))
    return {"cuts": len(cut_times(plan)), "clicks": found}


def check_black(video, f):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(video), "-vf", "blackdetect=d=0.1:pix_th=0.06",
                          "-an", "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stderr
    spans = [(float(s), float(e)) for s, e in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]
    for s, e in spans:
        f.append(("fail", s, f"black frames for {e - s:.2f}s"))
    return {"black_spans": spans}


def check_overlays(plan, edit_dir, video, f):
    """Face coverage + safe zones, from the difference between render and plain base."""
    W, H = framing.probe_size(video)
    base = next((b for b in (edit_dir / n for n in ("base_draft_mixed.mp4", "base_draft.mp4", "base_preview_mixed.mp4",
                                                    "base_preview.mp4", "base_mixed.mp4", "base.mp4"))
                 if b.exists() and framing.probe_size(b) == (W, H)), None)
    if base is None:
        f.append(("info", None, "no matching base render found, skipped face/safe-zone checks"))
        return {}
    from render import probe_source_fps
    fps_s = probe_source_fps(base) or "30"
    num, _, den = fps_s.partition("/")
    fps = float(num) / float(den or 1)
    zoom = framing.zoom_filter(plan, edit_dir, W, H, fps_s)
    vertical = W / H < 0.65  # app UI zones are a 9:16 (Reels/TikTok/Shorts) thing, not 4:5 feed posts
    face_hits, zone_hits = [], {}
    # One decode per video, every ~0.5 s (seeking each sample separately took minutes).
    step = max(1, round(fps / 2))
    rendered, plain = frames_every(video, step), frames_every(base, step, zoom)
    title = plan.get("title") or {}
    behind = (float(title.get("start_in_output", 0)), float(title.get("start_in_output", 0)) + float(title.get("duration", 0))) \
        if title.get("text") and title.get("behind_subject", True) else None
    for k, (a, b) in enumerate(zip(rendered, plain)):
        t = k * step / fps
        if behind and behind[0] <= t <= behind[1]:
            continue  # a title set behind the speaker wraps around the head on purpose
        mask = np.abs(a.astype(int) - b.astype(int)).sum(axis=2) > 60
        h, w = mask.shape
        fc = _face_box(b)
        if fc is not None:
            x0, y0, x1, y1 = fc
            box = mask[max(0, y0):y1, max(0, x0):x1]
            if box.size and box.mean() > 0.08:
                face_hits.append(round(float(t), 1))
        if vertical:
            for name, (zx0, zy0, zx1, zy1) in SAFE_ZONES.items():
                z = mask[int(zy0 * h):int(zy1 * h), int(zx0 * w):int(zx1 * w)]
                if z.size and z.mean() > 0.01:
                    zone_hits.setdefault(name, []).append(round(float(t), 1))
    for span in _spans(face_hits):
        f.append(("warn", span[0], f"text or graphic over the face {span[0]:.1f}–{span[1]:.1f}s"))
    for name, ts in zone_hits.items():
        for span in _spans(ts):
            f.append(("warn", span[0], f"overlay under the app's {name} {span[0]:.1f}–{span[1]:.1f}s"))
    return {"face_covered_at": face_hits, "safe_zone_hits": zone_hits}


def _face_box(rgb: np.ndarray):
    try:
        mp, det = framing._face_detector()
        found = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))).detections
    except Exception:
        return None
    if not found:
        return None
    bb = max(found, key=lambda d: d.bounding_box.width * d.bounding_box.height).bounding_box
    return bb.origin_x, bb.origin_y, bb.origin_x + bb.width, bb.origin_y + bb.height


def _spans(ts: list[float], gap: float = 0.6) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for t in sorted(ts):
        if out and t - out[-1][1] <= gap:
            out[-1][1] = t
        else:
            out.append([t, t])
    return [(a, b) for a, b in out]


def check_captions(plan, edit_dir, f):
    import captions
    s = captions.settings(plan, captions.load_brand(edit_dir))
    if s["style"] == "none":
        return {"style": "none"}
    words = captions.output_words(plan, edit_dir, s.get("corrections") or [])
    captions.mark_emphasis(words, s["emphasis"])
    cues = captions.chunk(words, captions.words_per_cue(plan, s))
    flashes = [round(c[0]["start"], 2) for c in cues if c[-1]["end"] - c[0]["start"] < 0.3 and len(c) > 1]
    for t in flashes[:5]:
        f.append(("info", t, "caption on screen under 0.3s (reads as a flash)"))
    total = planlib.total_duration(plan) or 1
    wps = len(words) / total
    if wps > 4.2:
        f.append(("info", None, f"fast speech ({wps:.1f} words/s): consider fewer words per caption"))
    return {"style": s["style"], "cues": len(cues), "words_per_s": round(wps, 2), "flashes": flashes}


def check_clutter(plan, edit_dir, f):
    events: list[float] = [float(z["start_in_output"]) for z in plan.get("zooms") or []]
    events += [float(g.get("start_in_output", 0)) for g in plan.get("graphics") or [] if g.get("type") != "progress"]
    a = plan.get("audio") or {}
    events += [float(x["at"]) for x in a.get("sfx") or []]
    if a.get("sfx_on_emphasis"):
        import audio
        events += audio.emphasis_times(plan, edit_dir)
    total = planlib.total_duration(plan)
    worst = (0, 0.0)
    for start in np.arange(0, max(0.1, total - 10) + 0.01, 1.0):
        n = sum(1 for e in events if start <= e < start + 10)
        worst = max(worst, (n, float(start)))
    if worst[0] > CLUTTER_PER_10S:
        f.append(("warn", worst[1], f"{worst[0]} effects in the 10s from {worst[1]:.0f}s (cap {CLUTTER_PER_10S}): feels busy"))
    return {"effects": len(events), "busiest_10s": worst[0]}


def cut_sheet(plan, video, out: Path) -> None:
    """Frames 0.08s before and 0.04s after every cut, in pairs, for a visual check."""
    pairs = []
    for t in cut_times(plan):
        a, b = frame(video, max(0, t - 0.08), width=200), frame(video, t + 0.04, width=200)
        if a is not None and b is not None:
            pairs.append((t, np.concatenate([a, np.full((a.shape[0], 4, 3), 255, np.uint8), b], axis=1)))
    if not pairs:
        return
    ph, pw = pairs[0][1].shape[:2]
    cols = 3
    sheet = Image.new("RGB", (cols * (pw + 12), ((len(pairs) + cols - 1) // cols) * (ph + 30)), "black")
    d = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=16)
    for i, (t, img) in enumerate(pairs):
        x, y = (i % cols) * (pw + 12), (i // cols) * (ph + 30)
        sheet.paste(Image.fromarray(img), (x, y + 24))
        d.text((x + 4, y + 4), f"cut {i + 1} @ {t:.2f}s   before | after", fill="yellow", font=font)
    sheet.save(out, quality=85)


def run(plan_path: Path, video: Path) -> dict:
    plan = planlib.load(plan_path)
    edit_dir = plan_path.parent
    findings: list[tuple[str, float | None, str]] = []
    report = {"video": str(video), "plan_version": plan.get("version")}
    report["length"] = check_length(plan, video, findings)
    report["loudness"] = check_loudness(video, findings)
    report["clicks"] = check_clicks(plan, video, findings)
    report["black"] = check_black(video, findings)
    report["overlays"] = check_overlays(plan, edit_dir, video, findings)
    report["captions"] = check_captions(plan, edit_dir, findings)
    report["clutter"] = check_clutter(plan, edit_dir, findings)
    qc_dir = edit_dir / "qc"
    qc_dir.mkdir(exist_ok=True)
    cut_sheet(plan, video, qc_dir / "cuts.jpg")
    order = {"fail": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda x: (order[x[0]], x[1] or 0))
    report["findings"] = [{"level": l, "at": t, "message": m} for l, t, m in findings]
    report["verdict"] = "fail" if any(l == "fail" for l, _, _ in findings) else \
        "check" if any(l == "warn" for l, _, _ in findings) else "pass"
    (qc_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    md = [f"# QC — {video.name} (plan v{plan.get('version')}): **{report['verdict'].upper()}**", "",
          f"- Length: {report['length']['rendered_s']}s (plan {report['length']['planned_s']}s)",
          f"- Loudness: {report['loudness']['lufs']} LUFS, true peak {report['loudness']['true_peak_dbtp']} dBTP",
          f"- Cuts: {report['clicks']['cuts']} (frames either side in cuts.jpg)",
          f"- Captions: {report['captions'].get('style')}, {report['captions'].get('words_per_s', '-')} words/s",
          f"- Effects: {report['clutter']['effects']} total, busiest 10s has {report['clutter']['busiest_10s']}", "",
          "## Findings", ""]
    md += [f"- **{x['level']}**{f' @ {x['at']:.1f}s' if x['at'] is not None else ''}: {x['message']}"
           for x in report["findings"]] or ["- none"]
    (qc_dir / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="Quality-check a rendered plan")
    ap.add_argument("plan", type=Path)
    ap.add_argument("video", type=Path)
    args = ap.parse_args()
    r = run(args.plan.resolve(), args.video.resolve())
    print(f"QC {r['verdict'].upper()}: {len(r['findings'])} finding(s) → {args.plan.resolve().parent / 'qc' / 'report.md'}")
    for x in r["findings"]:
        print(f"  {x['level']:4} {'' if x['at'] is None else f'@{x['at']:5.1f}s '}{x['message']}")
    sys.exit(0)


if __name__ == "__main__":
    main()
