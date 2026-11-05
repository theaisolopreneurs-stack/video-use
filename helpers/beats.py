"""Beat sync: find the beats in the music and line the edit up with them.

plan.json:
    "audio": {"music": {"file": "music/track.mp3"}},
    "beat_sync": {"pulse": 1.04, "every": 2}    # a small zoom kick on every 2nd beat

Moving the cuts themselves changes the edit, so it's a separate, deliberate step:
    python helpers/beats.py snap <edit>/plan.json          # move each cut to the nearest beat
    python helpers/beats.py list <edit>/plan.json          # print the beat times
Best for music-driven montages; don't snap a talking-head edit, or sentences get clipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def beat_times(music: Path, cache_dir: Path | None = None) -> list[float]:
    """Beat times in seconds from the start of the track (cached, since analysis takes a moment)."""
    cache = None
    if cache_dir:
        key = hashlib.sha1(f"{music}|{music.stat().st_size}".encode()).hexdigest()[:12]
        cache = cache_dir / f"beats_{key}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
    import librosa
    y, sr = librosa.load(str(music), mono=True, sr=22050)
    _, frames = librosa.beat.beat_track(y=y, sr=sr)
    times = [round(float(t), 3) for t in librosa.frames_to_time(frames, sr=sr)]
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(times), encoding="utf-8")
    return times


def music_beats(plan: dict, edit_dir: Path) -> list[float]:
    """Beats on the output timeline, up to the video's length (the music loops if it's shorter)."""
    import audio
    from plan import total_duration
    m = (plan.get("audio") or {}).get("music") or {}
    path = audio.resolve_music(m["file"], edit_dir) if m.get("file") else None
    if not path:
        return []
    beats = beat_times(path, edit_dir / "animations")
    if not beats:
        return []
    total, length = total_duration(plan), audio.duration(path)
    out, offset = [], 0.0
    while offset < total and length > 0:
        out += [b + offset for b in beats if b + offset < total]
        offset += length
    return out


def snap(plan: dict, edit_dir: Path, max_move: float = 0.5) -> int:
    """Trim or extend each section so its cut lands on the nearest beat. Returns cuts moved."""
    from plan import range_len
    beats = music_beats(plan, edit_dir)
    if not beats:
        return 0
    moved, t = 0, 0.0
    for r in plan["ranges"][:-1]:
        t_end = t + range_len(r)
        near = min(beats, key=lambda b: abs(b - t_end))
        delta = near - t_end
        speed = float(r.get("speed") or 1.0)
        new_end = float(r["end"]) + delta * speed
        if abs(delta) <= max_move and new_end - float(r["start"]) >= 0.3 and not r.get("speed_curve"):
            r["end"] = round(new_end, 3)
            moved += 1
        t += range_len(r)
    return moved


def main() -> None:
    ap = argparse.ArgumentParser(description="Line an edit up with the music's beats")
    ap.add_argument("cmd", choices=["snap", "list"])
    ap.add_argument("plan", type=Path)
    args = ap.parse_args()
    import plan as planlib
    path = args.plan.resolve()
    p = planlib.load(path)
    if args.cmd == "list":
        print(" ".join(f"{b:.2f}" for b in music_beats(p, path.parent)) or "no music set on this plan")
        return
    n = snap(p, path.parent)
    planlib.save(path, p)
    print(f"moved {n} cut(s) onto the beat")


if __name__ == "__main__":
    main()
