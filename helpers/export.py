"""Export an approved plan for every platform, plus the files that go with the upload.

    python helpers/export.py <edit>/plan.json                       # 9:16 only
    python helpers/export.py <edit>/plan.json --aspects 9:16,4:5,1:1,16:9
    python helpers/export.py <edit>/plan.json --draft               # fast test exports

Writes to <videos_dir>/exports/:
  <name>_9x16.mp4 ...   one render per aspect (captions, graphics and zooms re-laid out
                        for each shape; vertical -> 16:9 uses a blurred-background fill)
  <name>.srt            sentence-level captions for platform caption upload
  <name>_chapters.txt   YouTube chapters from the plan's beats (if long enough)
  <name>_thumb_N.jpg    3 thumbnail candidates (sharp, face visible, title text)
and runs qc.py on each video (report in <edit>/qc/).

Needs the rough cut approved (render.py enforces it); --draft skips that for testing.
"""

from __future__ import annotations

import tools_path  # noqa: F401  (the fastest FFmpeg on this computer goes first on PATH)

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

import framing
import plan as planlib

HELPERS = Path(__file__).resolve().parent


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "video"


def render_aspect(plan_path: Path, aspect: str, out: Path, draft: bool) -> Path:
    plan = planlib.load(plan_path)
    plan["target"] = dict(plan.get("target") or {}, aspect=aspect)
    tmp = plan_path.with_name(f"plan.export_{aspect.replace(':', 'x')}.json")
    planlib.save(tmp, plan)
    cmd = [sys.executable, str(HELPERS / "render.py"), str(tmp), "-o", str(out)]
    cmd += ["--draft"] if draft else []
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         env={**os.environ, "PYTHONUTF8": "1"})
    tmp.unlink(missing_ok=True)
    out.with_suffix(".json").unlink(missing_ok=True)  # render.py's rough-cut stamp; not needed here
    if res.returncode != 0:
        raise RuntimeError(f"render {aspect} failed:\n{res.stdout[-800:]}\n{res.stderr[-1500:]}")
    return out


# ---- captions file ---------------------------------------------------------

def write_srt(plan: dict, edit_dir: Path, out: Path) -> int:
    """Sentence-level cues (≤ 2 lines × 42 chars), the format platforms expect for uploads."""
    import captions
    s = captions.settings(plan, captions.load_brand(edit_dir))
    words = captions.output_words(plan, edit_dir, s.get("corrections") or [])
    cues, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        text = " ".join(x["text"] for x in cur)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or len(text) > 70 or w["text"][-1:] in ".?!" or nxt["start"] - w["end"] > 0.6:
            cues.append(cur)
            cur = []

    def stamp(t: float) -> str:
        ms = int(round(t * 1000))
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"

    lines = []
    for n, c in enumerate(cues, 1):
        # Hold each cue a beat after the last word, but never into the next cue.
        end = c[-1]["end"] + 0.15
        if n < len(cues):
            end = min(end, cues[n][0]["start"] - 0.01)
        text = " ".join(w["text"] for w in c)
        if len(text) > 42:  # break into two balanced lines
            words_ = text.split()
            best = min(range(1, len(words_)), key=lambda k: abs(len(" ".join(words_[:k])) - len(text) / 2))
            text = " ".join(words_[:best]) + "\n" + " ".join(words_[best:])
        lines += [str(n), f"{stamp(c[0]['start'])} --> {stamp(end)}", text, ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    return len(cues)


# ---- chapters --------------------------------------------------------------

def write_chapters(plan: dict, out: Path) -> str:
    chapters, t = [], 0.0
    for r in plan["ranges"]:
        beat = (r.get("beat") or "").strip().title()
        if beat and (not chapters or chapters[-1][1] != beat):
            chapters.append((t, beat))
        t += planlib.range_len(r)
    if chapters:
        chapters[0] = (0.0, chapters[0][1])
    ok = len(chapters) >= 3 and all(b[0] - a[0] >= 10 for a, b in zip(chapters, chapters[1:])) and t - chapters[-1][0] >= 10
    body = "\n".join(f"{int(s // 60)}:{int(s % 60):02d} {name}" for s, name in chapters)
    note = "" if ok else ("# Too short for YouTube chapters (needs 3+ chapters, each 10s+); "
                          "kept for reference / long-form use.\n")
    out.write_text(note + body + "\n", encoding="utf-8")
    return "ok" if ok else "too short for YouTube"


# ---- thumbnails ------------------------------------------------------------

def sharpness(rgb: np.ndarray) -> float:
    g = rgb.mean(axis=2)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def thumbnails(plan: dict, edit_dir: Path, out_dir: Path, name: str, n: int = 3) -> list[Path]:
    """Pick sharp frames with a big, centred face, spread across the video; add title text."""
    from captions import load_brand
    from title import title_image
    cands = []
    # About 40 moments spread over the whole video is plenty to find 3 good frames (each one is a
    # separate seek into the source, the slow part).
    moments = [(r, float(t)) for r in plan["ranges"]
               for t in np.arange(float(r["start"]) + 0.3, float(r["end"]) - 0.2, 0.5)]
    step = max(1, len(moments) // 40)
    for r, t in moments[::step]:
        src = planlib.resolve(plan["sources"][r["source"]], edit_dir)
        img = framing.grab(src, float(t), width=540)
        if img is None:
            continue
        f = framing._face_detector()
        det = f[1].detect(f[0].Image(image_format=f[0].ImageFormat.SRGB, data=np.ascontiguousarray(img))).detections
        if not det:
            continue
        bb = max(det, key=lambda d: d.bounding_box.width).bounding_box
        score = sharpness(img) * (bb.height / img.shape[0]) * det[0].categories[0].score
        cands.append((score, str(src), float(t)))
    cands.sort(reverse=True)
    picked: list[tuple[float, str, float]] = []
    for c in cands:
        if all(abs(c[2] - p[2]) > 3 or c[1] != p[1] for p in picked):
            picked.append(c)
        if len(picked) == n:
            break
    text = ((plan.get("title") or {}).get("text")
            or " ".join((plan["ranges"][0].get("quote") or "").split()[:5]))
    brand = load_brand(edit_dir)
    outs = []
    for i, (_, src, t) in enumerate(picked, 1):
        frame = Image.fromarray(framing.grab(Path(src), t, width=1080))
        W, H = 1280, 720
        # 16:9 thumbnail: blurred fill, speaker on the right, big title on the left.
        bg = frame.resize((W, int(frame.height * W / frame.width))).crop((0, 0, W, H)).filter(ImageFilter.GaussianBlur(28))
        fg = frame.resize((int(frame.width * H / frame.height), H))
        canvas = bg.copy()
        canvas.paste(fg, (W - fg.width - 40, 0))
        txt = title_image({"text": text, "size": 120, "position": "middle", "font": "sans"}, brand, int(W * 0.62), H)
        canvas = canvas.convert("RGBA")
        canvas.alpha_composite(txt, (20, 0))
        p = out_dir / f"{name}_thumb_{i}.jpg"
        canvas.convert("RGB").save(p, quality=90)
        outs.append(p)
    return outs


def main() -> None:
    ap = argparse.ArgumentParser(description="Export an approved plan for every platform")
    ap.add_argument("plan", type=Path)
    ap.add_argument("--aspects", default="9:16", help="comma list: 9:16,4:5,1:1,16:9 (default 9:16)")
    ap.add_argument("--name", default=None, help="file name stem (default: project folder name)")
    ap.add_argument("--draft", action="store_true", help="fast low-res exports for testing")
    ap.add_argument("--no-qc", action="store_true")
    ap.add_argument("--audio", action="store_true", help="also save the finished sound as an .mp3")
    args = ap.parse_args()

    plan_path = args.plan.resolve()
    edit_dir = plan_path.parent
    plan = planlib.load(plan_path)
    name = slug(args.name or edit_dir.parent.name)
    out_dir = edit_dir.parent / "exports"
    out_dir.mkdir(exist_ok=True)

    import qc
    from concurrent.futures import ThreadPoolExecutor
    # Thumbnails don't depend on the render, and each quality check can run while the next shape
    # renders: side jobs, so the export only waits for whatever finishes last.
    side = ThreadPoolExecutor(max_workers=1)  # one at a time: they share the face detector
    thumbs_job = side.submit(thumbnails, plan, edit_dir, out_dir, name)
    qc_jobs = []
    for aspect in [a.strip() for a in args.aspects.split(",") if a.strip()]:
        if aspect not in framing.OUTPUT_SIZES:
            print(f"skip {aspect}: use one of {', '.join(framing.OUTPUT_SIZES)}")
            continue
        out = out_dir / f"{name}_{aspect.replace(':', 'x')}{'_draft' if args.draft else ''}.mp4"
        print(f"rendering {aspect} → {out.name}", flush=True)
        render_aspect(plan_path, aspect, out, args.draft)
        if args.audio:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-vn", "-q:a", "2",
                            str(out_dir / f"{name}.mp3")], check=True)
        if not args.no_qc:
            tmp = plan_path.with_name(f"plan.qc_{aspect.replace(':', 'x')}.json")
            planlib.save(tmp, dict(plan, target=dict(plan.get("target") or {}, aspect=aspect)))

            # The shape the rough cut was reviewed and checked in only needs the technical checks again.
            same = aspect == (plan.get("target") or {}).get("aspect") and not args.draft

            def check(tmp=tmp, out=out, same=same):
                try:
                    return qc.run(tmp, out, quick=same)
                finally:
                    tmp.unlink(missing_ok=True)
            qc_jobs.append((aspect, side.submit(check)))
    for aspect, job in qc_jobs:
        r = job.result()
        print(f"  QC {aspect} {r['verdict'].upper()} ({len(r['findings'])} finding(s))")
        for x in r["findings"]:
            print(f"    {x['level']} {'' if x['at'] is None else f'@{x['at']:.1f}s '}{x['message']}")

    n = write_srt(plan, edit_dir, out_dir / f"{name}.srt")
    print(f"captions → {name}.srt ({n} cues)")
    print(f"chapters → {name}_chapters.txt ({write_chapters(plan, out_dir / f'{name}_chapters.txt')})")
    thumbs = thumbs_job.result()
    side.shutdown()
    print(f"thumbnails → {', '.join(p.name for p in thumbs)}")
    print(f"done → {out_dir}")


if __name__ == "__main__":
    main()
