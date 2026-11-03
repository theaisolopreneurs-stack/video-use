"""One command from raw footage to a reviewable rough cut, using a preset recipe.

    python helpers/make.py footage/my-video --preset reel
    python helpers/make.py footage/my-video --preset justyn --title "I didn't edit this"
    python helpers/make.py footage/my-video --preset youtube --music music/lofi.mp3
    python helpers/make.py footage/my-video --preset reel --studio      # open the Review Studio after
    python helpers/make.py --list

Steps: ingest (cached) -> draft cuts (suggest_cuts) -> apply the preset (shape, captions,
look, audio, automatic highlighted words, punch-ins and end card) -> draft rough cut -> QC.
On an existing plan it keeps your cuts and edits and only re-applies the preset settings,
as a new plan version (use --fresh to start the cuts over).

Presets live in <repo>/presets/*.json; copy one to make your own. A preset sets the
defaults; Claude still does the editing judgement on top (see each preset's editor_notes).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import plan as planlib

HELPERS = Path(__file__).resolve().parent
PRESETS = HELPERS.parent / "presets"
NUMBER_WORDS = {"zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                "hundred", "thousand", "million", "billion", "half", "double", "triple"}


def load_preset(name: str) -> dict:
    p = PRESETS / f"{name}.json"
    if not p.exists():
        sys.exit(f"no preset '{name}'. Available: {', '.join(sorted(x.stem for x in PRESETS.glob('*.json')))}")
    return json.loads(p.read_text(encoding="utf-8"))


def step(cmd: list[str]) -> None:
    res = subprocess.run([sys.executable, *cmd], env={**os.environ, "PYTHONUTF8": "1", "GLOG_minloglevel": "2"},
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        sys.exit(f"step failed: {' '.join(cmd)}\n{res.stdout[-1000:]}\n{res.stderr[-1500:]}")
    for line in res.stdout.splitlines():
        if line.strip() and not line.startswith(("INFO", "W0000", "WARNING", "I0000")):
            print(f"   {line}")


# ---- automatic choices ------------------------------------------------------

def auto_emphasis(plan: dict, edit_dir: Path, min_gap: float = 4.0) -> list[str]:
    """Numbers (and "N and M" / "N to M" runs) are what viewers remember; pop those, spaced out."""
    import captions
    s = captions.settings(plan, captions.load_brand(edit_dir))
    words = captions.output_words(plan, edit_dir, s.get("corrections") or [])
    norm = [re.sub(r"[^\w%$]", "", w["text"].lower()) for w in words]
    is_num = [bool(re.search(r"\d", n)) or n in NUMBER_WORDS for n in norm]
    phrases, last_t, i = [], -99.0, 0
    while i < len(words):
        if is_num[i] and words[i]["start"] - last_t >= min_gap:
            j = i + 1
            while j + 1 < len(words) and norm[j] in ("and", "to", "of", "out") and is_num[j + 1]:
                j += 2
            phrase = " ".join(w["text"].strip(".,!?") for w in words[i:j])
            if phrase.lower() not in (p.lower() for p in phrases):
                phrases.append(phrase)
            last_t = words[i]["start"]
            i = j
        else:
            i += 1
    return phrases


def auto_zooms(plan: dict, edit_dir: Path, z: dict, skip_until: float) -> list[dict]:
    """Punch-ins on popped phrases (else on section starts), at most one per every_s seconds."""
    import captions
    s = captions.settings(plan, captions.load_brand(edit_dir))
    words = captions.output_words(plan, edit_dir, s.get("corrections") or [])
    captions.mark_emphasis(words, s["emphasis"])
    cands, last = [], None
    for w in words:
        if w.get("emph") and w["emph"] != last:
            cands.append(w["start"])
        last = w.get("emph")
    if not cands:  # no popped words: use the start of each section after the first
        t = 0.0
        for r in plan["ranges"]:
            cands.append(t + 0.1)
            t += planlib.range_len(r)
    total = planlib.total_duration(plan)
    dur = 1.5 if z.get("style", "punch") == "punch" else 2.6
    out, last_t = [], -99.0
    for t in sorted(cands):
        t = max(0.0, t - 0.1)
        if t < skip_until or t - last_t < float(z.get("every_s", 8)) or t + dur > total - 0.3:
            continue
        out.append({"start_in_output": round(t, 2), "duration": dur, "scale": z.get("scale", 1.15),
                    "style": z.get("style", "punch")})
        last_t = t
    return out


def cta_card(plan: dict, brand: dict) -> dict | None:
    text = brand.get("cta")
    if not text:
        return None
    total = planlib.total_duration(plan)
    m = re.search(r"[\"“]([^\"”]+)[\"”]", text)  # 'Comment "EDIT" ...' -> mock comment "EDIT"
    return {"type": "cta", "text": text, "comment": m.group(1) if m else None, "handle": "viewer",
            "start_in_output": round(max(0.0, total - 2.2), 2), "duration": 2.2}


def apply_preset(plan: dict, preset: dict, edit_dir: Path, title: str | None, music: str | None) -> list[str]:
    """Set the preset's defaults on the plan; returns plain-English change notes."""
    import captions
    brand = captions.load_brand(edit_dir)
    notes = []
    plan["target"] = dict(plan.get("target") or {}, **(preset.get("target") or {}))
    notes.append(f"shape {plan['target'].get('aspect')}"
                 + (f", aim for ≤{plan['target']['max_duration_s']}s" if plan["target"].get("max_duration_s") else ""))

    cap = {k: v for k, v in (preset.get("captions") or {}).items() if k != "auto_emphasis"}
    plan["captions"] = dict(plan.get("captions") or {}, **cap)
    if (preset.get("captions") or {}).get("auto_emphasis"):
        found = auto_emphasis(plan, edit_dir)
        keep = [e for e in plan["captions"].get("emphasis") or []]
        plan["captions"]["emphasis"] = keep + [f for f in found if f.lower() not in (k.lower() for k in keep)]
        notes.append(f"captions: {cap.get('style')} style; popping {', '.join(plan['captions']['emphasis']) or 'nothing yet'}")
    else:
        notes.append(f"captions: {cap.get('style')}")

    a = preset.get("audio") or {}
    plan["audio"] = {k: v for k, v in dict(plan.get("audio") or {}, **{k: v for k, v in a.items()
                                                                       if k != "music_volume_db"}).items()}
    if music:
        plan["audio"]["music"] = {"file": music, "volume_db": a.get("music_volume_db", -22), "duck": True}
        notes.append(f"music: {Path(music).name}, ducked under your voice")
    if a.get("sfx_on_emphasis"):
        notes.append(f"a '{a['sfx_on_emphasis']}' sound on each popped phrase")

    if "grade" in preset:
        plan["grade"] = preset["grade"]
        notes.append(f"look: {preset['grade'] or 'none'}")

    t = preset.get("title")
    if t and title:
        plan["title"] = {"text": title, "start_in_output": 0.0, **t}
        notes.append(f"hook title '{title}'" + (" behind you" if t.get("behind_subject") else ""))
    elif t and not (plan.get("title") or {}).get("text"):
        notes.append("no hook title yet: pass --title or let Claude write one")

    z = preset.get("zooms") or {}
    if z.get("auto"):
        skip = float((plan.get("title") or {}).get("duration", 0)) if (plan.get("title") or {}).get("text") else 0.5
        plan["zooms"] = auto_zooms(plan, edit_dir, z, skip)
        notes.append(f"{len(plan['zooms'])} {z.get('style', 'punch')} zoom(s) on key moments")
    elif "auto" in z:
        plan["zooms"] = []

    g = preset.get("graphics") or {}
    kept = [x for x in plan.get("graphics") or [] if x.get("type") not in ("progress", "cta") or x.get("_manual")]
    if g.get("progress"):
        kept.append({"type": "progress"})
        notes.append("progress bar")
    if g.get("cta"):
        card = cta_card(plan, brand)
        if card:
            kept.append(card)
            notes.append(f"end card: {card['text']}")
    plan["graphics"] = kept
    plan["preset"] = preset.get("_name")
    return notes


def main() -> None:
    ap = argparse.ArgumentParser(description="Raw footage -> reviewable rough cut, with a preset")
    ap.add_argument("videos_dir", type=Path, nargs="?")
    ap.add_argument("--preset", default="reel")
    ap.add_argument("--title", default=None, help="hook title text (presets with a title)")
    ap.add_argument("--music", default=None, help="music file (music/ folder or project folder)")
    ap.add_argument("--target", type=float, default=None, help="override max length in seconds")
    ap.add_argument("--fresh", action="store_true", help="redo the cuts from scratch")
    ap.add_argument("--studio", action="store_true", help="open the Review Studio at the end")
    ap.add_argument("--list", action="store_true", help="list presets")
    args = ap.parse_args()

    if args.list or not args.videos_dir:
        for p in sorted(PRESETS.glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            print(f"{p.stem:10} {d['label']}: {d['about']}")
        return

    preset = load_preset(args.preset)
    preset["_name"] = args.preset
    if args.target:
        preset.setdefault("target", {})["max_duration_s"] = args.target
    videos = args.videos_dir.resolve()
    edit = videos / "edit"
    plan_path = edit / "plan.json"
    tgt = preset.get("target") or {}

    print(f"1/5 ingest {videos.name}")
    step([str(HELPERS / "ingest.py"), str(videos)])

    if args.fresh or not plan_path.exists():
        print("2/5 draft cuts")
        cmd = [str(HELPERS / "suggest_cuts.py"), "--edit-dir", str(edit), "--force",
               "--max-gap", str((preset.get("cuts") or {}).get("max_gap", 0.35))]
        cmd += ["--aspect", tgt["aspect"]] if tgt.get("aspect") else []
        cmd += ["--target", str(tgt["max_duration_s"])] if tgt.get("max_duration_s") else []
        if plan_path.exists():
            step([str(HELPERS / "plan.py"), "snapshot", str(plan_path), "--change", "starting the cuts over (--fresh)"])
        step(cmd)
    else:
        print("2/5 keeping your existing cuts and edits")

    print(f"3/5 apply preset: {preset['label']}")
    step([str(HELPERS / "plan.py"), "snapshot", str(plan_path), "--change", f"applied the '{args.preset}' preset"])
    plan = planlib.load(plan_path)
    notes = apply_preset(plan, preset, edit, args.title, args.music)
    plan["changes"] = (plan.get("changes") or [])[:1] + [f"v{plan['version']}: {n}" for n in notes]
    planlib.save(plan_path, plan)
    for n in notes:
        print(f"   - {n}")
    problems = planlib.validate(plan, edit)
    if problems:
        sys.exit("plan has problems:\n  - " + "\n  - ".join(problems))

    print("4/5 rough cut (draft)")
    step([str(HELPERS / "render.py"), str(plan_path), "-o", str(edit / "rough.mp4"), "--draft"])

    print("5/5 quality check")
    step([str(HELPERS / "qc.py"), str(plan_path), str(edit / "rough.mp4")])

    total = planlib.total_duration(plan)
    over = tgt.get("max_duration_s") and total > tgt["max_duration_s"]
    print(f"\nready: {total:.1f}s{' (over the ' + str(tgt['max_duration_s']) + 's target: Claude should tighten it)' if over else ''}"
          f" → {edit / 'rough.mp4'}")
    print(f"editor notes for this preset: {preset.get('editor_notes', '')}")
    print(f"review: uv run python helpers/review_server.py \"{edit}\"")
    if args.studio:
        os.execv(sys.executable, [sys.executable, str(HELPERS / "review_server.py"), str(edit)])


if __name__ == "__main__":
    main()
