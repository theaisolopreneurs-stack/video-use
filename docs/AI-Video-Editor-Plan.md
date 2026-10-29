# AI Video Editor — Build Plan (v2)

Goal: drop raw footage in a folder, tell Claude Code what you want in plain English, and get a finished, on-brand video back. Free tools only (Claude Code is the one cost, on your existing plan).

Updated 2026-10-28 with features seen in @justyn.ai's viral reel (Oct 10, 2026).

---

## How it works

1. **Transcribe** the audio locally with word-level timestamps.
2. **Claude reads the transcript** and looks at still frames / contact sheets only when it needs to.
3. **Claude writes an edit list (EDL)**: in/out points, captions, overlays, effects.
4. **FFmpeg + HyperFrames render** the edit list into a video.
5. **Self-review**: pull frames at every cut and overlay, check for problems, re-render (max 3 rounds).
6. **You give notes in plain English** ("bigger captions", "cut the second story") and it revises.

Starting point: fork **video-use** (MIT), swap its paid ElevenLabs transcription for local **whisper.cpp**, then add our own skills.

---

## Free tool stack

| Job | Tool |
|---|---|
| Brain / orchestration | Claude Code (existing plan) |
| Cut, render, audio, color | FFmpeg |
| Transcription | whisper.cpp (local, native on Snapdragon/ARM) |
| Quick silence removal | auto-editor |
| Motion graphics, titles, UI mockups | HyperFrames (Apache 2.0) + GSAP — needs Node 22 + Chrome |
| Captions | Styled .ass subtitles burned in with FFmpeg |
| Person cut-out (text-behind-subject) | rembg or MediaPipe selfie segmentation |
| Face tracking for vertical crops | OpenCV / MediaPipe |
| Music and SFX | YouTube Audio Library, Pixabay, Freesound (CC0) |
| B-roll | Your own footage, Pexels / Pixabay free APIs |
| Optional final polish | DaVinci Resolve (free) via exported timeline |
| Comment-to-DM (distribution, not editing) | ManyChat free tier — check current limits |

**Machine notes:** Snapdragon X Elite, 16 GB RAM, no NVIDIA GPU. Python + Git installed; FFmpeg, Node, uv not yet. If a Python AI package has no Windows-ARM build, install x64 Python and run it under emulation.

---

## Capabilities (~55)

**Cuts (8):** silences, filler words, false starts, best-take picking, trim to target length, story restructure (hook → problem → solution → proof → CTA), teaser cut, blooper reel.

**Captions and text (10):**
- word-by-word captions, highlighted keywords, chapter titles, end screen, translated subtitles
- *new* — **pill captions**: 1–3 words in rounded brand-color boxes, lower third
- *new* — **single-word emphasis**: key words pop larger on their own
- *new* — **mixed-type emphasis**: bold sans + italic serif in one line ("BIGGER *captions.*")
- lower-third name/handle
- *new* — **hook title with person in front of the text** (segmentation)

**Motion graphics (13):**
- animated charts on numbers, checklists on lists, screenshot pop-outs, progress bar, icon pops, brand intro/outro
- *new* — **prompt-box typing animation**
- *new* — **chat-bubble animation** (shows revision notes)
- *new* — **code / app window mockup**
- *new* — **timer / stat card** (e.g. "0:54")
- *new* — **picture-in-picture proof insert** with label (e.g. "1hr of raw footage")
- *new* — **social-proof collage grid** of posts/screenshots
- *new* — **CTA end card** with mock comment ("Comment 'EDIT' and I'll send…")

**Camera and framing (8):** punch-in zooms, slow push-ins, 9:16 face-tracked reframe, 16:9 version, 1:1 version, split screen, stabilization, *new* — **multi-angle / multi-location jump-cut pacing** (pick the best angle per line across several clips).

**Audio (7):** noise removal, −14 LUFS loudness, music ducked under voice, SFX on cards/pops, 30 ms fades at cuts, de-essing, room tone.

**Color and look (4):** auto correction, LUT looks, cross-camera color match, grain/vignette.

**Delivery (6):** thumbnail candidates, YouTube chapters, title/description ideas, SRT files, multi-ratio export, self-review report.

---

## Build phases

Each phase works on its own. Phase 2 = first useful version.

**Phase 0 — Setup**
Install FFmpeg, Node 22, uv, whisper.cpp, auto-editor, Chrome. Smoke test on a sample clip.

**Phase 1 — Ingest and transcribe**
Word-level transcript, packed transcript file, contact sheets/filmstrips so Claude can "see" footage. Supports multiple clips/angles per project.

**Phase 2 — Cuts (minimum useful version)**
EDL format, FFmpeg renderer, silence/filler/retake removal, audio fades, multi-clip best-take selection.

**Phase 3 — Brand kit + captions**
*New:* `brand.json` — colors, fonts, caption style, logo, CTA wording. Every later phase reads it, so videos look consistent without re-asking.
Caption styles: word-by-word, pill, single-word emphasis, mixed-type emphasis.

**Phase 4 — Audio polish**
Noise removal, loudness, music ducking, SFX.

**Phase 5 — Framing and color**
Zooms, face-tracked reframes, LUTs.
*New:* person segmentation for text-behind-subject hook titles.

**Phase 6 — Motion-graphics template library (HyperFrames)**
Charts, checklists, pop-outs, progress bar, plus *new* templates: prompt box, chat bubbles, app window, timer/stat card, PiP proof insert, collage grid, CTA end card. Triggered by keywords in the transcript or by request.

**Phase 7 — Self-review loop and exports**
Frame checks at cuts/overlays, safe-zone and text-over-face checks, loudness check, an "anti-slop" cap on effects per 10 seconds. Multi-ratio exports, thumbnails, chapters.

**Phase 8 — Presets**
One-command recipes: "YouTube video", "Short/Reel", "Podcast clip", and *new* "Justyn-style reel" (hook title behind subject → pill captions → proof inserts → revision demo → CTA end card).

---

## Lessons from the justyn.ai reel

- The reach came largely from **comment-to-DM** ("Drop 'EDIT' below"): 152.9K comments vs 66K likes. That's distribution, outside the editor.
- The "one sentence" claim is generous — the reel itself shows several plain-English revision rounds. Plan for draft → notes → revise.
- A tight, consistent **brand look** (two colors, one serif) does more than lots of effects.

## Open items

- Confirm whisper.cpp word timestamps are accurate enough for word-by-word captions.
- Check Herk Edit Kit's license before borrowing from it.
- Confirm which Python packages (rembg, MediaPipe, OpenCV) have Windows-ARM builds.
