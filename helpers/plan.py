"""Edit-plan tools: validate, version, diff, and approve <edit>/plan.json.

plan.json is a superset of the EDL that render.py reads, so it renders
directly (`render.py edit/plan.json --draft`). Extra fields carry what the
Review Studio shows and what later phases fill in:

    {
      "version": 1,
      "status": "draft",            # draft -> plan_approved -> rough_approved -> final
      "target": {"aspect": "9:16", "max_duration_s": 60},
      "sources": {"take1": "C:/.../take1.mp4"},
      "ranges": [{"source": "take1", "start": 1.45, "end": 4.72,
                  "beat": "HOOK", "quote": "...", "reason": "..."}],
      "cuts":   [{"source": "take1", "start": 4.72, "end": 6.35,
                  "text": "Uh,", "kind": "filler", "reason": "..."}],
      "captions": {}, "graphics": [], "zooms": [], "audio": {},
      "grade": null,
      "changes": ["v2: restored the 'one hour' line per note on card 3"],
      "total_duration_s": 21.4
    }

Usage:
    python helpers/plan.py validate <plan.json>
    python helpers/plan.py snapshot <plan.json> --change "what changed" [--change ...]
    python helpers/plan.py diff <old.json> <new.json>
    python helpers/plan.py approve <plan.json> plan|rough|final
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

STATUSES = ["draft", "plan_approved", "rough_approved", "final"]
APPROVALS = {"plan": ("draft", "plan_approved"),
             "rough": ("plan_approved", "rough_approved"),
             "final": ("rough_approved", "final")}


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, plan: dict) -> None:
    path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def source_duration(path: Path) -> float | None:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return None


def resolve(maybe: str, edit_dir: Path) -> Path:
    p = Path(maybe)
    return p if p.is_absolute() else (edit_dir / p).resolve()


def validate(plan: dict, edit_dir: Path) -> list[str]:
    """Return a list of problems; empty means the plan is renderable."""
    problems: list[str] = []
    if plan.get("status", "draft") not in STATUSES:
        problems.append(f"status must be one of {STATUSES}")
    sources = plan.get("sources") or {}
    durations: dict[str, float | None] = {}
    for name, p in sources.items():
        path = resolve(p, edit_dir)
        if not path.exists():
            problems.append(f"source '{name}' not found: {path}")
        else:
            durations[name] = source_duration(path)
    ranges = plan.get("ranges") or []
    if not ranges:
        problems.append("no ranges - nothing would render")
    for i, r in enumerate(ranges):
        tag = f"range {i} ({r.get('beat') or r.get('source')})"
        if r.get("source") not in sources:
            problems.append(f"{tag}: unknown source '{r.get('source')}'")
            continue
        s, e = float(r.get("start", -1)), float(r.get("end", -1))
        if s < 0 or e <= s:
            problems.append(f"{tag}: bad times {s}-{e}")
        dur = durations.get(r["source"])
        if dur is not None and e > dur + 0.05:
            problems.append(f"{tag}: ends at {e:.2f}s but source is {dur:.2f}s")
        if e - s < 0.2:
            problems.append(f"{tag}: only {e - s:.2f}s long - reads as a flash")
    return problems


def total_duration(plan: dict) -> float:
    return round(sum(float(r["end"]) - float(r["start"]) for r in plan.get("ranges", [])), 2)


def summary(plan: dict) -> str:
    ranges = plan.get("ranges", [])
    cuts = plan.get("cuts", [])
    kinds: dict[str, int] = {}
    for c in cuts:
        kinds[c.get("kind", "other")] = kinds.get(c.get("kind", "other"), 0) + 1
    target = (plan.get("target") or {}).get("max_duration_s")
    total = total_duration(plan)
    lines = [f"plan v{plan.get('version', 1)}  status={plan.get('status', 'draft')}",
             f"  {len(ranges)} kept range(s), {total:.1f}s"
             + (f" (target <= {target}s{' - OVER' if total > target else ''})" if target else ""),
             f"  {len(cuts)} cut(s): " + (", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "none"),
             f"  {len(plan.get('graphics', []))} graphic(s), {len(plan.get('zooms', []))} zoom(s)"]
    return "\n".join(lines)


def range_key(r: dict) -> tuple:
    return (r.get("source"), round(float(r["start"]), 2), round(float(r["end"]), 2))


def diff(old: dict, new: dict) -> list[str]:
    """Plain-English list of what changed between two plan versions."""
    out: list[str] = []
    old_r, new_r = old.get("ranges", []), new.get("ranges", [])
    old_keys = {range_key(r) for r in old_r}
    new_keys = {range_key(r) for r in new_r}

    def overlaps(a: dict, b: dict) -> bool:
        return a["source"] == b["source"] and a["start"] < b["end"] and b["start"] < a["end"]

    for r in new_r:
        if range_key(r) in old_keys:
            continue
        match = next((o for o in old_r if range_key(o) not in new_keys and overlaps(o, r)), None)
        q = f"\"{(r.get('quote') or '')[:50]}\""
        if match:
            out.append(f"trimmed {r['source']} {match['start']:.2f}-{match['end']:.2f} -> "
                       f"{r['start']:.2f}-{r['end']:.2f} {q}")
        else:
            out.append(f"added {r['source']} {r['start']:.2f}-{r['end']:.2f} {q}")
    for o in old_r:
        if range_key(o) not in new_keys and not any(overlaps(o, r) for r in new_r):
            out.append(f"removed {o['source']} {o['start']:.2f}-{o['end']:.2f} "
                       f"\"{(o.get('quote') or '')[:50]}\"")
    if [range_key(r) for r in old_r if range_key(r) in new_keys] != \
       [range_key(r) for r in new_r if range_key(r) in old_keys]:
        out.append("reordered segments")
    for field in ("captions", "graphics", "zooms", "audio", "grade", "target"):
        if old.get(field) != new.get(field):
            out.append(f"changed {field}")
    t0, t1 = total_duration(old), total_duration(new)
    if abs(t1 - t0) >= 0.05:
        out.append(f"length {t0:.1f}s -> {t1:.1f}s")
    return out


def cmd_validate(args) -> None:
    plan = load(args.plan)
    problems = validate(plan, args.plan.parent)
    plan["total_duration_s"] = total_duration(plan)
    save(args.plan, plan)
    print(summary(plan))
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("OK")


def cmd_snapshot(args) -> None:
    """Save the current plan as plan_vN.json, then bump the live plan to N+1."""
    plan = load(args.plan)
    v = int(plan.get("version", 1))
    shutil.copyfile(args.plan, args.plan.with_name(f"plan_v{v}.json"))
    plan["version"] = v + 1
    plan["status"] = "draft"
    plan["changes"] = [f"v{v + 1}: {c}" for c in args.change]
    save(args.plan, plan)
    print(f"saved plan_v{v}.json; live plan is now v{v + 1} (status reset to draft)")


def cmd_diff(args) -> None:
    changes = diff(load(args.old), load(args.new))
    print("\n".join(f"- {c}" for c in changes) if changes else "no changes")


def cmd_approve(args) -> None:
    plan = load(args.plan)
    need, becomes = APPROVALS[args.gate]
    status = plan.get("status", "draft")
    if status != need:
        sys.exit(f"can't approve {args.gate}: status is '{status}', needs '{need}'")
    problems = validate(plan, args.plan.parent)
    if problems:
        sys.exit("can't approve, plan has problems:\n  - " + "\n  - ".join(problems))
    plan["status"] = becomes
    save(args.plan, plan)
    print(f"status: {status} -> {becomes}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Validate, version, diff, and approve plan.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate"); p.add_argument("plan", type=Path); p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("snapshot"); p.add_argument("plan", type=Path)
    p.add_argument("--change", action="append", default=[], help="one line per change; repeatable")
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("diff"); p.add_argument("old", type=Path); p.add_argument("new", type=Path)
    p.set_defaults(fn=cmd_diff)
    p = sub.add_parser("approve"); p.add_argument("plan", type=Path)
    p.add_argument("gate", choices=list(APPROVALS)); p.set_defaults(fn=cmd_approve)
    args = ap.parse_args()
    for attr in ("plan", "old", "new"):
        if hasattr(args, attr):
            setattr(args, attr, getattr(args, attr).resolve())
    args.fn(args)


if __name__ == "__main__":
    main()
