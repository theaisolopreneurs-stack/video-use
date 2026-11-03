---
name: edit-video
description: Edit raw video footage into a finished short (Reel/TikTok/Short), YouTube video, podcast clip or tutorial - cuts, captions, looks, transitions, effects, graphics, music, QC and exports - fully local and free. Use when the user wants to edit, improve, cut, caption, stitch, grade, or export a video, mentions footage, clips, reels or shorts, refers to a video by name ("my Texans video"), or says "check my notes".
---

# AI video editor

This plugin is a complete editor driven by Python helpers. **Its root is two folders up from
this file** (`<this skill's base directory>/../..`). Call that `ROOT` below and always use it
as an absolute path. Users may be non-technical: talk in plain words, never ask them to type
paths or commands, and do the file work yourself.

## Where videos live

Every video is a folder in the **video library**: `~/Videos/AI Video Editor/<video name>/`
(Windows: `C:\Users\<them>\Videos\AI Video Editor\`; overridable with `AI_VIDEO_LIBRARY`).
The clips sit in that folder; the editor's work is in `<video>/edit/`, finished files in
`<video>/exports/`. Most users add clips through the **Home screen** (drag and drop), so:

- "improve my Texans video" / "check my notes" → find the folder by name in the library (most
  recently changed if unclear) and work on its `edit/plan.json`.
- If they give you files elsewhere, copy them into a new library folder.
- If it isn't set up yet (`ROOT/.venv` missing), run `video-editor-setup` first.

## How to work

1. **Read `ROOT/SKILL.md` in full** before editing anything. It is the editor's manual: the
   process, the review gates, every helper and its options, and the craft rules.
2. Run every helper through the plugin's own environment:

   ```bash
   uv run --project "ROOT" python "ROOT/helpers/<helper>.py" ...
   ```

3. **Fast path** for a new video:
   `uv run --project "ROOT" python "ROOT/helpers/make.py" "<video folder>" --preset reel`
   (presets: reel, justyn, youtube, podcast, tutorial). Then do the editor's judgement work on
   the plan (best takes, order, hook, graphics) and re-render the rough cut.
4. **Reviewing**: the user reviews in the editor (Home screen → Open). If it isn't running,
   start it in the background: `uv run --project "ROOT" python "ROOT/helpers/review_server.py"`
   (it opens their browser). After you change a plan, tell them to click **Open** on that video
   (or refresh the page) to see it.
5. **Exporting**: they can press **Export** in the editor once the rough cut is approved, or ask
   you - then use `ROOT/helpers/export.py` and tell them which folder the files are in (offer to
   open it).

## Per-video or personal customisation

- `brand.json` in a video folder (that video) or `ROOT/brand/brand.json` (all videos):
  colours, fonts, caption defaults, words to always pop, call to action.
- Music: a `music/` folder inside the video folder, or `ROOT/music/`. LUTs: `*.cube` files.
