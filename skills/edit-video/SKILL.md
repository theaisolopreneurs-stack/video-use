---
name: edit-video
description: Edit raw video footage into a finished short (Reel/TikTok/Short), YouTube video, podcast clip or tutorial — cuts, captions, looks, transitions, effects, graphics, music, QC and exports — fully local and free. Use when the user wants to edit, cut, caption, stitch, grade, or export videos, or mentions footage, clips, reels, shorts, or the Review Studio.
---

# AI video editor

This plugin is a complete editor driven by Python helpers. **Its root is two folders up from
this file** (`<this skill's base directory>/../..`). Call that `ROOT` below and always use it
as an absolute path.

## Before the first edit on a machine

If `ROOT/.venv` is missing, or `ffmpeg` / `uv` / `node` aren't on PATH, run the
`video-editor-setup` skill first.

## How to work

1. **Read `ROOT/SKILL.md` in full** before editing anything. It is the editor's manual: the
   process, the review gates, every helper and its options, and the craft rules.
2. Run every helper through the plugin's own environment, from the user's project folder:

   ```bash
   uv run --project "ROOT" python "ROOT/helpers/<helper>.py" ...
   ```

3. Footage lives in the **user's** folder (e.g. `./footage/<project>/`); all output goes to
   `<that folder>/edit/` and `<that folder>/exports/`. Never write into `ROOT` except the
   brand kit if the user asks to change their defaults.

## Fast path

```bash
uv run --project "ROOT" python "ROOT/helpers/make.py" "<footage folder>" --preset reel --studio
```

Presets: `reel`, `justyn`, `youtube`, `podcast`, `tutorial` (`--list` to see them). Then do the
editor's judgement work on the plan (takes, order, hook, graphics), let the user review in the
Studio (http://127.0.0.1:8765), revise on "check my notes", and export with
`ROOT/helpers/export.py` after the rough cut is approved.

## Per-project customisation (in the user's footage folder)

- `brand.json` — their colours, fonts, caption defaults, CTA (copy `ROOT/brand/brand.json`).
- `music/` — their music tracks. `*.cube` — their LUTs.
