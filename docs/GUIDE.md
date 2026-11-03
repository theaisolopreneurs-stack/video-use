# AI Video Editor — user guide

A free, local video editor that you drive by talking to Claude Code. You drop raw clips in a
folder; Claude cuts, captions, styles and exports them; you review and approve every step in
the **Review Studio** before anything final is rendered. Nothing is uploaded anywhere — the
only paid piece is your Claude plan.

---

## 1. Everyday use

### Start an edit

1. Make a folder for the video and put every clip in it (phone videos, screen recordings,
   B-roll — any mix of shapes and frame rates):

   ```
   footage/texans-part-2/
     main-take.MOV
     second-take.MOV
     screen-recording.mp4
   ```

2. Tell Claude what you want, in plain English:

   > Edit the clips in footage/texans-part-2 into a 45-second reel. Use the Justyn style,
   > title "Claude fixed the Texans", and cut to the screen recording when I talk about the results.

   Or run the one-liner yourself:

   ```bash
   uv run python helpers/make.py footage/texans-part-2 --preset justyn --title "Claude fixed the Texans" --studio
   ```

3. Claude transcribes everything (on your machine), drafts the cuts, applies the preset, and
   does the editor's judgement work: best takes, order, hook, graphics. You get a rough cut in
   a couple of minutes and the Studio opens.

### Review in the Studio (http://127.0.0.1:8765)

| Area | What you do there |
|---|---|
| **Top bar** | The four steps: Edit & save → Approve plan → Rough cut → Approve rough cut. The orange button is always the next step. |
| **Player + timeline** | Watch the rough cut. Coloured blocks are sections; click one to jump. **+ Note at current time** pins a note to a moment. |
| **Storyboard** | Every kept section with frames, the line, and why it's there. Crossed-out lines between cards are cuts — **Restore** brings one back. Each card's **Edit** drawer: nudge start/end, transition in, speed (slow-mo / fast), freeze at the end, stabilize, a note. Untick a card to drop it; arrows reorder. |
| **Captions** | 13 styles, each previewed on your footage. **Top / Middle / Bottom / Custom** — Custom lets you click exactly where captions go on the video. Words to pop. |
| **Look** | 40+ colour looks (natural, warm, cinematic, film, trend, black & white) previewed on your footage, plus any `.cube` LUTs you add. |
| **Title** | Hook title text, font, length, and "behind me". |
| **Effects** | Shake, glitch, VHS, flash, grain, blur, cinema bars… added at the player's current time for a set length, or across the whole video. |
| **Audio** | Voice clean-up, music (with ▶ Listen), volume, ducking, a sound on popped words. |
| **Notes** | Anything you'd rather tell Claude than click. |

New to the Studio? Click **? How to** in the top bar for a full walkthrough (it also opens by itself the first time). The **🎨** menu switches the Studio's colour theme — Warm (default, follows your system), Classic, GitHub, Solarized, Catppuccin, Rosé Pine, Gruvbox, Nord, Dracula, One Dark, Tokyo Night, Monokai, Everforest, High contrast — it only changes the page, never your video.

Click **Save**, then tell Claude **"check my notes"**. Claude answers each note, and the
**Latest changes** box says what changed. When the plan's right: **Approve plan → Make rough
cut → watch → Approve rough cut**. Only then can the full-quality render happen.

### Export

> Export it for Reels, Instagram feed and YouTube.

or

```bash
uv run python helpers/export.py footage/texans-part-2/edit/plan.json --aspects 9:16,4:5,16:9
```

You get, in `footage/texans-part-2/exports/`: one video per shape (each re-laid out and
quality-checked), an `.srt` caption file for upload, YouTube chapters, and 3 thumbnail options.
Ask Claude for titles, a description and hashtags.

### Make it yours

- **Brand kit**: `brand/brand.json` (defaults) or `brand.json` in a project folder (that video
  only): colours, fonts, caption style, words to always pop, your call-to-action line.
- **Music**: drop tracks in `music/` (see `music/README.md` for free sources).
- **Looks**: drop `.cube` LUTs in `luts/`.
- **Presets**: copy a file in `presets/` and tweak it to make your own recipe.

---

## 2. What it can do

| Area | Features |
|---|---|
| **Cutting** | Local transcription (faster-whisper), silences / fillers / retakes / restarts removed automatically, best take across clips, any number of clips stitched in any order, B-roll cutaways and picture-in-picture |
| **Timing** | Speed up / slow-mo per section (audio pitch kept), freeze frames, reverse, stabilization |
| **Transitions** | Crossfade, dissolve, fade to black/white, flash, whip pans, slides, wipes, circle open/close, radial, zoom, pixelate, blur, squeeze, diagonal, glitch cut, shake cut |
| **Effects** | Shake, glitch, RGB split, flash, VHS, film grain, vignette, blur, B&W, sepia, pixelate, invert, light leak, mirror, cinema bars |
| **Captions** | 13 styles (Hormozi, MrBeast, highlight box, karaoke, pill, neon, typewriter, one word, stacked…), popped words, spelling fixes, top/middle/bottom or any spot |
| **Look** | 35 recipe looks + basics + your own LUTs |
| **Framing** | Face-centred punch-in and push-in zooms, automatic vertical reframing, hook title behind you |
| **Graphics** | 14 animated templates (stat counter, checklist, prompt box, chat bubbles, app window, timer, bar chart, collage, CTA card…), progress bar |
| **Audio** | Voice clean-up, music with automatic ducking, sound effects, -14 LUFS loudness |
| **Quality** | Automatic QC (loudness, clicks, black frames, text over your face, app safe zones, clutter) and frames from every cut |
| **Export** | 9:16, 4:5, 1:1, 16:9; SRT; chapters; thumbnails |

---

## 3. Share it with other people (Claude Code plugin)

This repository is both a **Claude Code plugin** and its own **plugin marketplace**, so anyone
with Claude Code can install it with two commands.

### For the people you share it with

In a terminal:

```bash
claude plugin marketplace add theaisolopreneurs-stack/video-use
claude plugin install ai-video-editor@aisolopreneurs-video
```

(or inside a Claude Code session: `/plugin marketplace add theaisolopreneurs-stack/video-use`,
then `/plugin install ai-video-editor@aisolopreneurs-video`.) In the desktop app, the same
commands work in a terminal, or use the plugin manager.

Then, in any Claude Code session:

1. `/ai-video-editor:video-editor-setup` — once per machine. Claude installs FFmpeg, uv and
   Node if they're missing, sets up the editor, and runs a test.
2. Put clips in a folder and say *"edit the clips in footage/my-video as a reel"* — or run
   `/ai-video-editor:edit-video`.

Updates: when you push changes and bump `version` in `.claude-plugin/plugin.json`, they get them
with `claude plugin marketplace update aisolopreneurs-video` (or automatically, depending on
their settings). After an update they should re-run the setup skill.

### What's in the package

| File | Role |
|---|---|
| `.claude-plugin/plugin.json` | Plugin name, version, description |
| `.claude-plugin/marketplace.json` | Makes this repo a marketplace that lists the plugin |
| `skills/edit-video/SKILL.md` | Loads when someone wants to edit video; points Claude at the manual |
| `skills/video-editor-setup/SKILL.md` | One-time install and smoke test |
| `SKILL.md` | The full editor manual Claude follows |
| `helpers/`, `graphics/`, `presets/`, `brand/`, `luts/`, `music/` | The editor itself |

Check the package after changes with `claude plugin validate .`

### Things to know before sharing

- **Licence**: this is a fork of browser-use/video-use (MIT). Keep `LICENSE` and the credit.
  Fonts are the ones that ship with Windows; on macOS/Linux, set fonts in `brand.json` to ones
  that exist there (e.g. Helvetica/Arial Black, or any Google font file).
- **It's public**: anything you commit (brand kit, presets) is visible. Footage, renders, music
  and models are git-ignored and never uploaded.
- **Cost for users**: free apart from their own Claude plan. Editing uses a fair amount of
  Claude usage on long footage.
- **Tested on**: Windows 11 on ARM (Surface Pro 11). macOS/Linux should work (all tools are
  cross-platform) but haven't been tested yet — worth a test run before announcing it.
