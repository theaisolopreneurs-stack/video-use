# AI Video Editor — Build Plan (v3)

Goal: drop raw footage in a folder, tell Claude Code what you want in plain English, and get a finished, on-brand video back. Free tools only (Claude Code is the one cost, on your existing plan).

Updated 2026-10-28 with features seen in @justyn.ai's viral reel (Oct 10, 2026).
Updated 2026-10-29: added the **Review Studio** — you see and change Claude's edit plan before anything expensive renders.

---

## How it works

1. **Transcribe** the audio locally with word-level timestamps.
2. **Claude reads the transcript** and looks at still frames / contact sheets only when it needs to.
3. **Claude writes an edit plan** (`plan.json`): cuts, captions, graphics, zooms, music — described, not rendered yet.
4. **🛑 Gate 1 — Plan review.** The Review Studio shows the plan as a storyboard. You change things or leave notes; Claude revises. Repeat until you click **Approve plan**.
5. **🛑 Gate 2 — Rough-cut review.** A fast, low-res draft (cuts + captions + labeled placeholder boxes for graphics) renders in seconds. Same loop: notes → revise → **Approve rough cut**.
6. **Final render**: FFmpeg + HyperFrames build the real graphics, color, and audio at full quality.
7. **Self-review**: pull frames at every cut and overlay, check for problems, re-render (max 3 rounds).
8. **Final notes** in plain English for small polish; big changes go back to Gate 1.

Nothing slow or heavy (animations, color grading, full-res render) runs until both gates are approved.

Starting point: fork **video-use** (MIT), swap its paid ElevenLabs transcription for local **faster-whisper**, then add our own skills.

---

## Free tool stack

| Job | Tool |
|---|---|
| Brain / orchestration | Claude Code (existing plan) |
| Cut, render, audio, color | FFmpeg |
| Transcription | faster-whisper (local, `base.en` model to start) |
| Quick silence removal | auto-editor |
| Motion graphics, titles, UI mockups | HyperFrames (Apache 2.0) + GSAP — needs Node 22 + Chrome |
| Captions | Styled .ass subtitles burned in with FFmpeg |
| Person cut-out (text-behind-subject) | rembg or MediaPipe selfie segmentation |
| Face tracking for vertical crops | OpenCV / MediaPipe |
| Music and SFX | YouTube Audio Library, Pixabay, Freesound (CC0) |
| B-roll | Your own footage, Pexels / Pixabay free APIs |
| Optional final polish | DaVinci Resolve (free) via exported timeline |
| Comment-to-DM (distribution, not editing) | ManyChat free tier — check current limits |

**Machine notes:** Snapdragon X Elite, 16 GB RAM, no NVIDIA GPU. The project venv uses **x64 Python 3.12** (runs under Windows emulation) because several audio/AI packages have no Windows-ARM builds.

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

Each phase works on its own. Phases 1–3 = first useful version (cuts you can preview, change, and approve).

**Phase 0 — Setup** ✅ done 2026-10-28
Installed via winget: FFmpeg 9.0.2 (Gyan), uv, Node.js LTS (v26). Chrome already present.
`uv sync` builds `.venv` on x64 Python 3.12 with video-use deps + faster-whisper. auto-editor installed as a uv tool.
Test: `uv run python scripts/smoke_test.py` — TTS clip → FFmpeg video → local word-level transcript → silence cut. Passes.
Notes: whisper.cpp dropped (no prebuilt ARM binary, no C++ compiler here); faster-whisper is decoded via FFmpeg because its PyAV path breaks on current PyAV.

**Phase 1 — Ingest and transcribe**
Word-level transcript, packed transcript file, contact sheets/filmstrips so Claude can "see" footage. Supports multiple clips/angles per project.

**Phase 2 — Edit plan + cuts**
`plan.json` format (extends video-use's EDL — see Review Studio below), FFmpeg renderer, silence/filler/retake removal, audio fades, multi-clip best-take selection.

**Phase 3 — Review Studio v1 (minimum useful version = Phases 1–3)**
Local review page with Gate 1 (storyboard) and Gate 2 (rough-cut draft). Covers cuts first; each later phase adds its own preview to the Studio (caption samples, graphic mockups, zoom markers, music bed).

**Phase 4 — Brand kit + captions**
*New:* `brand.json` — colors, fonts, caption style, logo, CTA wording. Every later phase reads it, so videos look consistent without re-asking.
Caption styles: word-by-word, pill, single-word emphasis, mixed-type emphasis. Studio shows each style on a real frame.

**Phase 5 — Audio polish**
Noise removal, loudness, music ducking, SFX. Studio lets you audition the music bed before render.

**Phase 6 — Framing and color**
Zooms, face-tracked reframes, LUTs.
*New:* person segmentation for text-behind-subject hook titles. Studio shows before/after grade stills and the vertical crop box.

**Phase 7 — Motion-graphics template library (HyperFrames)**
Charts, checklists, pop-outs, progress bar, plus *new* templates: prompt box, chat bubbles, app window, timer/stat card, PiP proof insert, collage grid, CTA end card. Triggered by keywords in the transcript or by request. Studio shows a still mockup of each graphic in brand style before it's animated.

**Phase 8 — Self-review loop and exports**
Frame checks at cuts/overlays, safe-zone and text-over-face checks, loudness check, an "anti-slop" cap on effects per 10 seconds. Multi-ratio exports, thumbnails, chapters.

**Phase 9 — Presets**
One-command recipes: "YouTube video", "Short/Reel", "Podcast clip", and *new* "Justyn-style reel" (hook title behind subject → pill captions → proof inserts → revision demo → CTA end card).

---

## Review Studio (preview → iterate → approve → final edit)

Why: rendering animations and full-res video takes the longest. If the plan is wrong, that time is wasted. The Studio catches it while changes are still cheap.

**What it is:** a local web page (`uv run python helpers/review_server.py <project>` → opens in Chrome at `http://localhost:8765`). Free, offline, footage never leaves your laptop. Built with Python's standard library + plain HTML/JS — no new installs.

### Gate 1 — Plan review (storyboard, seconds to generate)

- **Summary bar:** target platform/ratio, planned length vs target, number of cuts, graphics, zooms.
- **Timeline strip:** colored blocks for each kept segment, labeled by beat (HOOK / PROBLEM / …), with markers for graphics, zooms, SFX, music.
- **Storyboard cards**, one per kept segment:
  - thumbnail frames from the real footage
  - the transcript, with **cut words shown struck through** in grey between cards (so you see exactly what's being removed and why)
  - Claude's reason ("cleanest take; stops before the stumble at 0:38")
  - planned captions, zooms, graphics, and SFX on that segment
- **Graphic mockups:** a still, brand-styled preview of each planned graphic (e.g. the "0:54" timer card) with its text and timing.
- **What you can do:**
  - restore a cut or remove a segment (checkbox)
  - nudge in/out points (±0.1 s)
  - edit caption or graphic text inline
  - delete, swap, or request a graphic ("add a checklist here")
  - leave a note on any card, plus overall notes
  - **Save notes** (Claude revises) or **Approve plan**
- **Version diff:** after each revision, a "What changed" panel lists exactly what Claude changed in response to your notes (v1 → v2).

### Gate 2 — Rough-cut review (fast draft, ~seconds to a minute)

- 540p draft with real cuts, real captions in the planned style, and **labeled placeholder boxes** where graphics will go ("CHART: 3 tools · 2.0 s").
- Plays in the Studio next to the storyboard; clicking a card jumps the player to that moment.
- Timestamped notes ("0:12 — cut is too abrupt"), then **Save notes** or **Approve rough cut**.

### Final edit

Only after both approvals: build animations, grade, mix audio, render full quality, run self-review. The final video opens in the Studio for sign-off; small notes are fixed directly, structural changes go back to Gate 1.

### Files (in each project's `edit/` folder)

| File | Purpose |
|---|---|
| `plan.json` | The edit plan: video-use EDL fields (`sources`, `ranges` with `beat`/`quote`/`reason`) plus `captions`, `graphics`, `zooms`, `audio`, `status` (`draft` / `plan_approved` / `rough_approved` / `final`), `version` |
| `plan_vN.json` | Saved copy of every version, for the diff and for undo |
| `review_notes.json` | Your edits and notes from the Studio, which Claude reads next |
| `storyboard/` | Thumbnails and graphic mockups |
| `rough.mp4` | Gate 2 draft |

### Rules added to SKILL.md

- Never build animations, grade, or render above draft quality before `status` is `rough_approved`.
- Every revision bumps `version` and writes a "What changed" summary that answers each note.
- Claude reads `review_notes.json` at the start of each turn, so you can review in the browser and just say "check my notes."

---

## Lessons from the justyn.ai reel

- The reach came largely from **comment-to-DM** ("Drop 'EDIT' below"): 152.9K comments vs 66K likes. That's distribution, outside the editor.
- The "one sentence" claim is generous — the reel itself shows several plain-English revision rounds. Plan for draft → notes → revise.
- A tight, consistent **brand look** (two colors, one serif) does more than lots of effects.

## Open items

- Confirm faster-whisper word timestamps on real footage are tight enough for word-by-word captions (try `small.en` if `base.en` drifts).
- Check Herk Edit Kit's license before borrowing from it.
- Confirm which Python packages (rembg, MediaPipe, OpenCV) have Windows-ARM builds.
