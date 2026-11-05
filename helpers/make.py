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


# Words that tend to make a line a stronger opener. Only used to rank a video's own sentences.
HOOK_WORDS = {"secret", "mistake", "mistakes", "never", "stop", "why", "how", "nobody", "best", "worst", "wrong",
              "truth", "fail", "failed", "money", "free", "easy", "fast", "only", "actually", "biggest", "problem",
              "fix", "hack", "changed", "quit", "broke", "lost", "made", "instead", "without", "forget"}


def best_hook_line(plan: dict, edit_dir: Path) -> dict | None:
    """The kept sentence most likely to stop a scroll: a number, "you", a strong word, a question,
    and short enough to say in a few seconds. Returns source, start, end, text, score."""
    best = None
    for r in plan["ranges"]:
        tp = edit_dir / "transcripts" / f"{r['source']}.json"
        if not tp.exists():
            continue
        words = [w for w in json.loads(tp.read_text(encoding="utf-8")).get("words", []) if w.get("type") == "word"
                 and w["end"] > float(r["start"]) and w["start"] < float(r["end"])]
        sent: list[dict] = []
        for i, w in enumerate(words):
            sent.append(w)
            nxt = words[i + 1] if i + 1 < len(words) else None
            if nxt is not None and w["text"].strip()[-1:] not in ".?!" and nxt["start"] - w["end"] < 0.7:
                continue
            text = " ".join(x["text"].strip() for x in sent)
            toks = [re.sub(r"[^\w%$]", "", x["text"].lower()) for x in sent]
            n, length = len(toks), sent[-1]["end"] - sent[0]["start"]
            score = (3 if any(re.search(r"\d", t) or t in NUMBER_WORDS for t in toks) else 0) \
                + (1 if {"you", "your", "youre"} & set(toks) else 0) + 2 * min(2, len(HOOK_WORDS & set(toks))) \
                + (1.5 if text.rstrip().endswith("?") else 0) + (1 if 4 <= n <= 14 else -2) - (2 if length > 5 else 0)
            if float(sent[0]["start"]) >= float(r["start"]) and float(sent[-1]["end"]) <= float(r["end"]) + 0.05 \
                    and (best is None or score > best["score"]):
                best = {"source": r["source"], "start": max(float(r["start"]), sent[0]["start"] - 0.03),
                        "end": min(float(r["end"]), sent[-1]["end"] + 0.06), "text": text, "score": score}
            sent = []
    return best


def apply_hook(plan: dict, edit_dir: Path, hook: dict, title: str | None) -> list[str]:
    """Open on the strongest line: play it once as a teaser, before the video starts properly."""
    notes: list[str] = []
    plan["ranges"] = [r for r in plan["ranges"] if r.get("beat") != "HOOK TEASER"]
    line = best_hook_line(plan, edit_dir)
    if not line or line["score"] < 3:
        return ["hook: no line stood out, so the opening stays as it is"]
    first = plan["ranges"][0]
    already_first = line["source"] == first["source"] and line["start"] - float(first["start"]) < 2.0
    if hook.get("teaser") and not already_first:
        plan["ranges"].insert(0, {"source": line["source"], "start": round(line["start"], 3), "end": round(line["end"], 3),
                                  "beat": "HOOK TEASER", "quote": line["text"],
                                  "reason": "strongest line, played first to stop the scroll; you hear it again in context"})
        notes.append(f"opens on your strongest line: \"{line['text']}\"")
    else:
        notes.append(f"your opening line is already the strongest: \"{line['text']}\"")
    if hook.get("auto_title") and not title and not (plan.get("title") or {}).get("text"):
        words = line["text"].rstrip(".,!?").split()
        if len(words) <= 6:
            plan["_auto_title"] = " ".join(words)
    return notes


def auto_zooms(plan: dict, edit_dir: Path, z: dict, skip_until: float) -> list[dict]:
    """Punch-ins on popped phrases (else on section starts), at most one per every_s seconds.
    With "fill", gaps are topped up at sentence starts, so there's a steady change on screen.
    "scale" can be a list, alternated, so the framing keeps changing rather than just pulsing."""
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
    every = float(z.get("every_s", 8))
    if z.get("fill"):
        for i, w in enumerate(words):
            if i == 0 or words[i - 1]["text"].strip()[-1:] in ".?!," or w["start"] - words[i - 1]["end"] > 0.25:
                cands.append(w["start"])
    out, last_t = [], -99.0
    scales = z.get("scale", 1.15)
    scales = scales if isinstance(scales, list) else [scales]
    for t in sorted(set(cands)):
        t = max(0.0, t - 0.1)
        if t < skip_until or t - last_t < every or t + dur > total - 0.3:
            continue
        out.append({"start_in_output": round(t, 2), "duration": dur, "scale": scales[len(out) % len(scales)],
                    "style": z.get("style", "punch")})
        last_t = t
    if z.get("open_push") and total > 3:  # a slow push through the hook, so the opening is never static
        out.insert(0, {"start_in_output": 0.0, "duration": round(min(2.0, skip_until or 2.0), 2),
                       "scale": 1.1, "style": "drift"})
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
    if preset.get("hook"):
        notes += apply_hook(plan, edit_dir, preset["hook"], title)
        title = title or plan.pop("_auto_title", None)
    if preset.get("pace"):  # a touch faster than life; pitch stays natural
        for r in plan["ranges"]:
            r.setdefault("speed", preset["pace"])
        notes.append(f"pace: {preset['pace']}× (voice pitch unchanged)")
    plan["adjust"] = preset.get("adjust") or {}
    plan["beat_sync"] = preset.get("beat_sync") or {}
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
                                                                       if k not in ("music_volume_db", "music")}).items()}
    if not music and a.get("music") == "auto" and not (plan["audio"].get("music") or {}).get("file"):
        import audio  # the user's own tracks only: the editor doesn't ship any music
        tracks = [p for p in audio.list_music(edit_dir) if not p.name.startswith("_")]
        music = str(tracks[0]) if tracks else None
        if not music:
            notes.append("no music yet: add a track to get a music bed and beat pulses")
    if music:
        plan["audio"]["music"] = {"file": music, "volume_db": a.get("music_volume_db", -22), "duck": True}
        notes.append(f"music: {Path(music).name}, ducked under your voice")
    if a.get("sfx_on_emphasis"):
        notes.append(f"a '{a['sfx_on_emphasis']}' sound on each popped phrase")
    if a.get("sfx_on_zoom"):
        notes.append(f"a quiet '{a['sfx_on_zoom']}' on each punch-in")

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
