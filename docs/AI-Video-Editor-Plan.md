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

**Phase 1 — Ingest and transcribe** ✅ done 2026-10-29
`helpers/transcribe.py` now defaults to local faster-whisper (`small.en`), writing the same Scribe-shaped JSON so every existing helper reads it unchanged; `--engine scribe` keeps the paid path. A disfluent prompt makes Whisper keep "um / uh / like" for filler cuts.
`helpers/ingest.py <videos_dir>` = inventory (ffprobe) → transcripts → `takes_packed.md` → timestamped contact sheet per source (`edit/storyboard/`) → `edit/inventory.md`.
Tested on two synthetic takes (37 s): fillers, pauses, and the retake all show up in `takes_packed.md`; word timing lines up with the waveform in `timeline_view`.
Speed: ~1 s of processing per 1 s of footage with `small.en` on this laptop (~1 hour for 1 hour of raw footage; `base.en` is ~3× faster). Limits: no speaker labels locally; numbers may come out as digits ("54 -second").

**Phase 2 — Edit plan + cuts** ✅ done 2026-10-29
`plan.json` = video-use EDL + `version`, `status`, `target`, `cuts`, `captions`, `graphics`, `zooms`, `audio`, `changes`; `render.py` reads it directly.
`helpers/suggest_cuts.py`: mechanical draft — silences > 0.35 s, fillers (um/uh/erm…), retakes ("let me start that again" + the flubbed sentence), restarts; every cut stores its text + reason for the Studio. "like"/"so" left for Claude to judge. Multi-take selection, beat labels and ordering are Claude's job on top of this draft (per SKILL.md).
`helpers/plan.py`: `validate`, `snapshot` (plan_vN.json + change notes), `diff` (plain-English "what changed"), `approve plan|rough|final`.
`render.py` gate: refuses `--preview`/final renders of a plan until `rough_approved` (`--force` overrides). `--draft` rough cut: 720p in ~4 s for a 14 s cut.
Tested on the two synthetic takes: 37 s → 13.7 s; fillers, the retake and pauses removed; re-transcribing the rough cut confirms the right lines remain.
Watch on real footage: one cut start may clip a word onset (Whisper heard "Comment edit" as "I meant edit" in the rough cut) — if it recurs, widen `PAD_BEFORE` from 50 ms toward 100 ms.

**Phase 3 — Review Studio v1 (minimum useful version = Phases 1–3)** ✅ done 2026-10-29
`uv run python helpers/review_server.py footage/<project>/edit` → http://127.0.0.1:8765 (stdlib only, local only).
Built: summary bar (version, status, length vs target, aspect), "What changed" (Claude's notes + diff vs previous version), timeline strip coloured by beat, storyboard cards (3 thumbnails, quote, reason, planned graphics/zooms, ±0.1 s nudges, keep toggle, beat label, reorder, ▶ jump to rough cut, card note), struck-through cuts with reasons and **Restore**, overall notes, rough-cut player with **Make rough cut** and timestamped notes, two-click **Approve plan / Approve rough cut**.
Direct edits auto-snapshot to a new version; switched-off sections become restorable cuts. Notes go to `review_notes.json` for Claude ("check my notes"). Rough cut is "stale" only when the cut itself changed (`rough.json` stamp from `render.py`).
Tested end to end in the browser on the synthetic takes: edit → save (v2) → approve plan → make rough cut → timestamped note → approve rough. Works at phone width.
Later phases add their previews here: caption samples (4), music audition (5), grade stills / crop box (6), graphic mockups (7).

**Phase 4 — Brand kit + captions** ✅ done 2026-10-29
`brand/brand.json` (repo default, override per project with `<videos_dir>/brand.json`): colours, sans + serif fonts (Windows fonts by default), caption defaults, `always_emphasize`, CTA text. **Placeholder colours — swap in your real brand.**
`helpers/captions.py` writes `captions.ass` (libass, burned by FFmpeg): styles **clean**, **karaoke** (spoken word in accent colour), **pill** (rounded brand-colour box); emphasis phrases pop inline as **accent** (bigger, gold) or **serif** (italic serif — the mixed-type look); per-word `corrections` fix misheard words; popped phrases never split; position at 70% height (clear of app UI).
`render.py` burns captions into every `plan.json` render automatically, including the rough cut. Captions are part of the rough-cut "stale" check.
Studio: Captions panel with live previews of each style on your footage, position, ALL CAPS, words-to-pop; changes save as a new plan version.
Tested on the Texans clip: pill captions, "0 and 4" / "Super Bowl" / "Claude" popped, "drive" → "derive" fixed.
Not yet: custom brand font files (any installed Windows font works; dropping .ttf files into a fonts folder is a small follow-up), logo.

**Phase 5 — Audio polish** ✅ done 2026-10-29
`helpers/audio.py`, run by `render.py` on every plan render: voice cleanup (on by default; high-pass, gentle denoise, de-esser, light compression), music bed (loops, fades, sidechain-ducked under the voice), SFX at set times or automatically on popped caption words. Loudness normalisation to -14 LUFS (from upstream) runs last.
SFX kit (pop, click, ding, whoosh, riser) is synthesised by FFmpeg — free, no licensing. Music is yours: `music/` folder (git-ignored audio) with a README of free sources.
Studio: Audio panel — voice cleanup toggle, music picker with ▶ Listen, volume, duck toggle, sound-on-popped-words with preview. Audio changes save as a new version and mark the rough cut stale.
Measured on the Texans clip: final mix -14.2 LUFS; music ducks 8.2 dB under speech; voice noise floor -46.7 → -48.0 dB (recording was already clean, so the cleanup stays gentle).
Not yet: per-section music changes, beat-synced cuts, AI music generation.

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

**What it is:** a local web page (`uv run python helpers/review_server.py footage/<project>/edit` → opens in your browser at `http://127.0.0.1:8765`). Free, offline, footage never leaves your laptop. Built with Python's standard library + plain HTML/JS — no new installs.

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

- 720p draft with real cuts, real captions in the planned style, and **labeled placeholder boxes** where graphics will go ("CHART: 3 tools · 2.0 s").
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

## Limitations and risks

### HDR vs SDR (checked 2026-10-29 on this Surface Pro 11)

- **Today the pipeline is SDR-only — by design, not hardware.** Upstream `render.py` detects HDR sources (iPhone HLG, PQ cameras) and tone-maps them to 8-bit SDR H.264 on purpose, because a half-converted HDR file looks washed out on most phones/platforms.
- **The hardware can do HDR.** Our FFmpeg build has libx265 (10-bit HEVC) plus zscale/tonemap. Tested a 10-bit HLG (BT.2020) encode with correct HDR metadata: 1080×1920 at ~0.66× real time (60 s reel ≈ 1.5 min), 4K at ~0.27× (60 s ≈ 3.7 min). Software encoding, so it runs on any CPU.
- **No hardware HDR encoder.** Windows' Media Foundation HEVC encoder (`hevc_mf`) failed a quick 10-bit test, so HDR renders use the CPU (slower, but fine for short-form).
- **HDR output needs work in Phase 2/6 — "HDR mode":**
  - `render.py --hdr`: skip tone-mapping, encode libx265 10-bit, keep HLG (or PQ) + BT.2020 tags.
  - Graphics (HyperFrames renders in Chrome as SDR) and burned-in captions must be converted into the HDR color space before compositing, or they'll look dim/grey. Set a "graphics white" brightness so text doesn't blind on HDR screens.
  - Color grading: SDR LUTs/`eq` presets don't apply to HDR; need HDR-aware grades or grade in linear light.
  - **Dolby Vision** (iPhone's default) metadata is not preserved — output would be HLG or HDR10, which all major platforms accept.
- **Default stays SDR.** Most Reels/TikTok viewers see SDR anyway, and SDR is the safe choice for consistent graphics. HDR is opt-in per project (mainly YouTube/iPhone-shot content).
- **Previewing HDR:** the Review Studio plays in Chrome. Surface Pro 11's OLED model shows HDR; the LCD model shows a tone-mapped version — judge HDR on a phone too.

### Other limits

- **Speed:** transcription ~1× real time (`small.en`); x64 Python/FFmpeg run under Windows emulation on Snapdragon.
- **No speaker labels** with free local transcription.
- **Claude usage:** long footage uses a lot of your plan's allowance; transcripts and contact sheets keep it down.

## Open items

- Confirm faster-whisper word timestamps on real footage are tight enough for word-by-word captions (try `small.en` if `base.en` drifts).
- Check Herk Edit Kit's license before borrowing from it.
- Confirm which Python packages (rembg, MediaPipe, OpenCV) have Windows-ARM builds.
