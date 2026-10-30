"""Mechanical first pass: build a draft plan.json from the transcripts.

Finds the obvious cuts so the editor starts from a clean baseline instead of
raw takes:

- silence: gaps between words longer than --max-gap
- filler:  um / uh / erm / hmm ... (not "like" or "you know" - those are often
           real words; they're flagged in the summary for the editor to judge)
- retake:  a sentence containing "let me start that again" / "scratch that" /
           ..., plus the flubbed sentence right before it
- restart: a sentence whose first words repeat at the start of one of the next
           few sentences (keeps the later, usually cleaner, take)

Every removed stretch is recorded in `cuts` with its text and reason, which is
what the Review Studio shows struck through. The editor (Claude) then refines
the plan: picks takes across sources, orders beats, trims to length.

Output: <edit>/plan.json (refuses to overwrite unless --force).

Usage:
    python helpers/suggest_cuts.py --edit-dir <edit>
    python helpers/suggest_cuts.py --edit-dir <edit> --max-gap 0.3 --target 60 --aspect 9:16
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from plan import save, summary, total_duration
from transcribe_batch import find_videos

FILLERS = {"um", "umm", "uh", "uhh", "uh-huh", "er", "erm", "hmm", "hm", "mm", "mhm", "ah"}
SOFT_FILLERS = {"like", "you know", "basically", "actually", "literally", "so"}
RETAKE_PHRASES = [
    "let me start that again", "let me start again", "let me start over", "start over",
    "let me try that again", "let me redo", "let me do that again", "scratch that",
    "take two", "let me rephrase",
]
PAD_BEFORE = 0.05   # SKILL.md: 50ms before the first kept word
PAD_AFTER = 0.08    # and 80ms after the last


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9' -]", "", text.lower()).strip()


def join_words(texts: list[str]) -> str:
    """Join tokens without a space before punctuation pieces like ".I." or "-second"."""
    out = ""
    for t in texts:
        out += t if (out == "" or t[:1] in ".,!?;:-'") else " " + t
    return out


def sentences(words: list[dict]) -> list[list[int]]:
    """Split word indices into sentences on terminal punctuation."""
    out: list[list[int]] = [[]]
    for i, w in enumerate(words):
        out[-1].append(i)
        if w["text"].rstrip().endswith((".", "?", "!")):
            out.append([])
    return [s for s in out if s]


def mark_removals(words: list[dict]) -> dict[int, tuple[str, str]]:
    """Map word index -> (kind, reason) for every word to drop."""
    drop: dict[int, tuple[str, str]] = {}
    for i, w in enumerate(words):
        if norm(w["text"]) in FILLERS:
            drop[i] = ("filler", f"filler word \"{w['text'].strip(',.')}\"")

    sents = sentences(words)
    texts = [" ".join(norm(words[i]["text"]) for i in s) for s in sents]
    for si, text in enumerate(texts):
        if any(p in text for p in RETAKE_PHRASES):
            for i in sents[si]:
                drop[i] = ("retake", "speaker restarts here")
            if si > 0:
                for i in sents[si - 1]:
                    drop.setdefault(i, ("retake", "flubbed take, redone right after"))
                    if drop[i][0] == "filler":
                        drop[i] = ("retake", "flubbed take, redone right after")

    def head(si: int) -> list[str]:
        return [t for t in (norm(words[i]["text"]) for i in sents[si]) if t and t not in FILLERS][:3]

    for si in range(len(sents)):
        h = head(si)
        if len(h) < 3 or all(i in drop for i in sents[si]):
            continue
        for sj in range(si + 1, min(si + 4, len(sents))):
            if head(sj) == h:
                for i in sents[si]:
                    drop[i] = ("restart", f"restarted later at {words[sents[sj][0]]['start']:.2f}s")
                break
    return drop


def build_source(name: str, words: list[dict], src_dur: float, max_gap: float):
    drop = mark_removals(words)
    kept = [i for i in range(len(words)) if i not in drop]

    # Group kept words into ranges; a long pause or a dropped word between them splits.
    groups: list[list[int]] = []
    for i in kept:
        if groups:
            prev = groups[-1][-1]
            gap = words[i]["start"] - words[prev]["end"]
            if gap <= max_gap and not any(j in drop for j in range(prev + 1, i)):
                groups[-1].append(i)
                continue
        groups.append([i])

    ranges: list[dict] = []
    for g in groups:
        start = max(0.0, words[g[0]]["start"] - PAD_BEFORE)
        end = min(src_dur, words[g[-1]]["end"] + PAD_AFTER)
        quote = join_words([words[i]["text"] for i in g])
        if ranges and start <= ranges[-1]["end"]:
            ranges[-1]["end"] = end
            ranges[-1]["quote"] += " " + quote
            continue
        ranges.append({"source": name, "start": round(start, 3), "end": round(end, 3),
                       "beat": "", "quote": quote, "reason": "kept speech"})

    # Everything between kept ranges is a cut; name it by the dropped words inside.
    cuts: list[dict] = []
    edges = [(0.0, ranges[0]["start"])] if ranges and ranges[0]["start"] > 0.25 else []
    edges += [(a["end"], b["start"]) for a, b in zip(ranges, ranges[1:])]
    if ranges and src_dur - ranges[-1]["end"] > 0.25:
        edges.append((ranges[-1]["end"], src_dur))
    for s, e in edges:
        inside = [i for i in range(len(words)) if words[i]["start"] >= s - 0.01 and words[i]["end"] <= e + 0.01]
        kinds = sorted({drop[i][0] for i in inside if i in drop})
        reasons = list(dict.fromkeys(drop[i][1] for i in inside if i in drop))
        dead_air = (e - s) - sum(words[i]["end"] - words[i]["start"] for i in inside)
        # Padding eats into a pause, so a gap that triggered the split can be shorter
        # than max_gap once cut; a cut with no dropped words is a pause either way.
        if (dead_air > max_gap and "retake" not in kinds) or not kinds:
            kinds.append("silence")
            reasons.append(f"{dead_air:.1f}s pause")
        cuts.append({
            "source": name, "start": round(s, 3), "end": round(e, 3),
            "text": join_words([words[i]["text"] for i in inside]),
            "kind": "+".join(kinds),
            "reason": "; ".join(reasons),
        })
    soft = [w["text"] for w in words if norm(w["text"]) in SOFT_FILLERS]
    return ranges, cuts, soft


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a draft plan.json from transcripts")
    ap.add_argument("--edit-dir", type=Path, required=True)
    ap.add_argument("--max-gap", type=float, default=0.35,
                    help="pauses longer than this (s) are cut. Default 0.35")
    ap.add_argument("--target", type=float, default=None, help="target max length in seconds")
    ap.add_argument("--aspect", default=None, help="e.g. 9:16, 16:9, 1:1")
    ap.add_argument("--force", action="store_true", help="overwrite an existing plan.json")
    args = ap.parse_args()

    edit_dir = args.edit_dir.resolve()
    out = edit_dir / "plan.json"
    if out.exists() and not args.force:
        sys.exit(f"{out} exists - the editor may have refined it. Use --force to start over.")

    inventory = json.loads((edit_dir / "inventory.json").read_text(encoding="utf-8")) \
        if (edit_dir / "inventory.json").exists() else []
    durations = {Path(i["name"]).stem: i["duration"] for i in inventory}
    videos = {v.stem: v for v in find_videos(edit_dir.parent)}

    plan: dict = {"version": 1, "status": "draft", "target": {}, "sources": {},
                  "ranges": [], "cuts": [], "captions": {}, "graphics": [], "zooms": [],
                  "audio": {}, "grade": None, "changes": ["v1: mechanical first pass (suggest_cuts.py)"]}
    if args.target:
        plan["target"]["max_duration_s"] = args.target
    if args.aspect:
        plan["target"]["aspect"] = args.aspect

    soft_all: list[str] = []
    for tpath in sorted((edit_dir / "transcripts").glob("*.json")):
        name = tpath.stem
        if name not in videos:
            print(f"  skip {name}: no matching source video")
            continue
        words = [w for w in json.loads(tpath.read_text(encoding="utf-8")).get("words", [])
                 if w.get("type") == "word"]
        if not words:
            continue
        plan["sources"][name] = str(videos[name])
        ranges, cuts, soft = build_source(name, words, durations.get(name, words[-1]["end"] + 1), args.max_gap)
        plan["ranges"] += ranges
        plan["cuts"] += cuts
        soft_all += soft

    plan["total_duration_s"] = total_duration(plan)
    save(out, plan)
    print(summary(plan))
    if soft_all:
        print(f"  judgment calls left in (editor decides): {', '.join(sorted(set(soft_all)))}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
