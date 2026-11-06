# AI Video Editor — user guide

A free video editor that runs on your own computer. Drag in your clips, pick a style, and it
cuts, captions and styles them for you. Claude is there when you want it smarter. Nothing is
uploaded anywhere — the only paid piece is your Claude plan.

---

## 0. Never used anything like this? Start here

**You need:** a computer and the Claude app (Claude Code). That's it.

**Step 1 — install (once).** Open Claude Code and paste this sentence:

> Please install the AI Video Editor plugin from theaisolopreneurs-stack/video-use and set it up for me.

Claude does the rest (it may ask you to click "Allow" a few times). When it's finished, the
editor opens in your web browser. Claude asks before adding an **AI Video Editor** icon to your Desktop; say no
if you'd rather not have one, and you can add it later from the Home screen.

**Step 2 — make a video (every time).**

1. Double-click **AI Video Editor** on your Desktop. It opens in your browser.
2. **Drag your video clips** into the big box (or click it to choose them).
3. **Pick a style** — Reel/TikTok, Viral talking-head, YouTube, How-to, or Podcast clip.
4. Click **✨ Make my video** and wait a couple of minutes.
5. Watch it. Like it? Click **Approve plan → Approve rough cut → Export**, tick where it's
   going (TikTok, Instagram, YouTube…), and click **📁 Open the folder** to get your file.

**Step 3 (optional) — make it better with Claude.** In Claude, say things like
*"improve my Texans video"*, *"make the captions bigger"*, *"cut the part about the weather"*.
Then click **Open** on that video again to see the changes.

You never need to make folders, type file names or remember any web address. Your videos are
kept in **Videos → AI Video Editor** (one folder per video) — the **📁 Open my videos folder**
button takes you there.

---

## 1. Everyday use (more detail)

### Start an edit

- **Easiest:** the Home screen (Desktop icon, or ask Claude to open it) — drag clips, pick a style, **Make my video**.
- **By chat:** tell Claude what you want, in plain English. Claude finds or creates the
  video's folder for you:

  > Make a 45-second reel from the clips I just added to "Texans part 2". Justyn style, title
  > "Claude fixed the Texans", and cut to the screen recording when I talk about the results.

- **By command** (for the technical): `uv run python helpers/make.py "<video folder>" --preset justyn --title "…" --studio`

Either way: everything is transcribed on your machine, pauses and mistakes are cut, the style
is applied, and you get a preview in a couple of minutes. Claude adds the judgement work (best
takes, order, hook, graphics) when you ask it to.

### Review in the editor

| Area | What you do there |
|---|---|
| **Top bar** | The four steps: Edit & save → Approve plan → Rough cut → Approve rough cut. The orange button is always the next step. |
| **Player + timeline** | Watch the rough cut. Coloured blocks are sections; click one to jump. **+ Note at current time** pins a note to a moment. |
| **Storyboard** | Every kept section with frames, the line, and why it's there. Crossed-out lines between cards are cuts — **Restore** brings one back. Each card's **Edit** drawer: nudge start/end, transition in, speed (slow-mo / fast), freeze at the end, stabilize, a note. Untick a card to drop it; arrows reorder. |
| **Captions** | 76 styles, grouped by feel and previewed on your footage. **✨ Best** picks a spot that stays off your face and clear of the apps' buttons; or **Top / Middle / Bottom / Custom** — Custom lets you click exactly where captions go on the video. Matching emoji. **✏️ Fix the caption words** lets you retype any line. Words to pop. |
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
- **Style samples**: on the Home screen, open **Want it to feel like another video?** and add one or more videos you admire. Or tell Claude: *"make my clip feel like this one"* and share the file or link. The editor measures how the sample is edited and applies that to your footage; none of the sample ends up in your video. Links often get blocked by Instagram and TikTok, so saving or screen-recording the video is the dependable route. For the closest match, ask Claude, who can also look at the sample's frames and read its words.
- **Talk instead of type**: click the 🎤 in any text box in the editor, speak, and click it again. It uses the same free speech model as your captions, on your computer, in English. To talk to Claude in the chat, press Windows + H for Windows' own dictation, which may use Microsoft's online speech service.
- **Presets**: copy a file in `presets/` and tweak it to make your own recipe.

---

## 2. What it can do

| Area | Features |
|---|---|
| **Cutting** | Local transcription (faster-whisper), silences / fillers / retakes / restarts removed automatically, best take across clips, any number of clips stitched in any order, B-roll cutaways and picture-in-picture |
| **Timing** | Speed up / slow-mo per section (audio pitch kept), freeze frames, reverse, stabilization |
| **Transitions** | Crossfade, dissolve, fade to black/white, flash, whip pans, slides, wipes, circle open/close, radial, zoom, pixelate, blur, squeeze, diagonal, glitch cut, shake cut |
| **Effects** | Shake, glitch, RGB split, flash, VHS, film grain, vignette, blur, B&W, sepia, pixelate, invert, light leak, mirror, cinema bars |
| **Captions** | 76 styles (word-by-word, boxes and markers, editorial, neon, retro 3D, glitch, handwritten, animated…), matching emoji, popped words, spelling fixes, best/top/middle/bottom or any spot |
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
