# Project handoff: AI Video Editor

Written 2026-11-03 so work can resume in a new context. **Read this first, then `SKILL.md`
(editor manual), `docs/GUIDE.md` (user guide) and `docs/AI-Video-Editor-Plan.md` (build log).**

---

## 1. What this is and what the user wants

A free, local AI video editor: raw clips in, finished branded video out. Started from the trend
of Claude editing raw footage (video-use, justyn.ai's viral reel). Forked from
`browser-use/video-use` (MIT; keep `LICENSE` and credit).

**The user's priorities, in their words and choices:**
- All free software. The only cost is their Claude plan.
- See and change the plan **before** slow rendering (review gates, inspired by Justyn's workflow).
- "Make this the easiest tool ever to use. That is where we will win!" Design for a brand-new
  user who is bad with computers: no folders, paths, terminals or "localhost".
- Warm colours in the editor, no "v7" version noise in the UI.
- Package it so other people can install it in their Claude Code.
- HDR mode was tabled by the user: "a lot of the things you mentioned have already been solved in
  my other project repository that is public which I can have you reference later."
  (Earlier local project on this machine: `C:\Users\alang\OneDrive\AI Solopreneurs\Products\Digital ToDo with Priority`;
  the repo they meant was not named. Ask before assuming.)

**How the user works with me:**
- Explain results in plain language; they are not reading code.
- One branch per phase; **push or merge only when told**. They usually reply "Push, start Phase N".
- They try things in the Studio themselves between turns (edits to plans are theirs; keep them).
- Do not spawn subagents unasked.

---

## 2. Repo and git state

- Local: `C:\Users\alang\Documents\AI Video Editor`
- Remote: https://github.com/theaisolopreneurs-stack/video-use (public fork; can't be made private
  while it's a fork)
- `origin` = the fork, `upstream` = browser-use/video-use
- `main` holds all the work and is pushed to origin; run `git log -1` for the current commit. Phase branches (`phase-0/setup` ...
  `phase-10/creative-toolkit`, `studio-redesign`) are all contained in main and can be deleted.
- A leftover worktree may exist at `.claude/worktrees/studio-redesign` (git-ignored).
- Git-ignored: `footage/`, `renders/`, video/audio files, `.env`, `sfx/`, `models/`,
  `luts/generated/`, `.claude/launch.json`, `.claude/worktrees/`.
- This machine: Surface Pro 11, Snapdragon X Elite (Windows on ARM), 16 GB, Windows 11.
  `.venv` is **x64 Python 3.12 under emulation** (audio/AI packages lack ARM wheels), from `.python/`.

---

## 3. Everything built (all on main)

| Phase | What | Files |
|---|---|---|
| 0 | Toolchain: FFmpeg 9, uv, Node 26, faster-whisper, auto-editor; smoke test | `scripts/smoke_test.py` |
| 1 | Free local transcription, one-command ingest | `helpers/transcribe.py`, `transcribe_batch.py`, `ingest.py` |
| 2 | `plan.json`, auto cut suggestions, versions, approval gates | `helpers/suggest_cuts.py`, `plan.py`, gate in `render.py` |
| 3 | Review Studio (redesigned later: step bar, central player, tabs) | `helpers/review_server.py`, `helpers/studio/index.html` |
| 4 | Brand kit + 13 caption styles, pops, spelling corrections | `brand/brand.json`, `helpers/captions.py` |
| 5 | Voice cleanup, ducked music, synthesised SFX | `helpers/audio.py`, `music/` |
| 6 | Face zooms, reframe, hook title behind speaker, LUTs | `helpers/framing.py`, `title.py`, `grade.py`, `luts/` |
| 7 | 14 HyperFrames motion graphics | `graphics/templates.js/.css`, `helpers/graphics.py` |
| 8 | Measured QC; exports (9:16, 4:5, 1:1, 16:9, SRT, chapters, thumbnails) | `helpers/qc.py`, `export.py` |
| 9 | 5 presets + one-command pipeline | `presets/*.json`, `helpers/make.py` |
| 10 | 35 recipe looks; caption position (named or x/y); 21 transitions + 3 effect cuts; per-section speed/freeze/stabilize/reverse; 15 effects; multi-video + B-roll; warm UI; plugin | `helpers/looks.py`, `effects.py`, `.claude-plugin/`, `skills/` |
| 11 | CapCut comparison (`docs/CAPCUT-FEATURES.md`) and everything feasible from it; 76 caption styles with groups; **Best** caption position; **Viral** preset; ask before adding a shortcut; exact section lengths so sync can't drift | `helpers/stickers.py`, `background.py`, `beats.py`, `tts.py`, `presets/viral.json`, `captions.best_spots`, `plan.curve_slices` |
| 11b | Talk instead of type (🎤 on every text box, `/api/dictate`, local model); retype caption lines (`captions.text_edits`), kept in step with the voice | `helpers/dictate.py`, `helpers/studio/mic.js`, `captions.apply_text_edits` |
| 11c | Style samples: measure a sample video, apply as a recipe, files in `<video>/inspiration/`, Home box, links through yt-dlp | `helpers/inspire.py`, `make.apply_inspiration` |
| 11d | Hand swipe reveal and hand swipe transition (MediaPipe hand model, follows the hand, look picked from the words); CLAUDE.md; plugin installed from GitHub and set up in its own folder (smoke test PASS, ~1 GB) | `helpers/handwipe.py`, `CLAUDE.md` |
| Later | 21 Studio colour themes; in-app How-to; **Home screen** (drag clips, pick style, Make my video), library, Export dialog, desktop icon | `helpers/studio/theme.css`, `home.html`, `helpers/library.py`, `shortcut.py` |

**Pipeline:** ingest → `suggest_cuts` → preset/Claude refine `plan.json` → Gate 1 approve plan →
`render.py --draft` rough cut → Gate 2 approve rough → `export.py` / full render → QC.

**Render order:** per-section extract (crop, grade, speed, freeze) → concat or xfade join → audio
mix → zooms → effects → progress bar → overlays (B-roll, title, graphics) → captions last → loudnorm.

**`plan.json` keys:** `version`, `status` (draft → plan_approved → rough_approved → final),
`target` {aspect, max_duration_s}, `sources`, `ranges` [{source, start, end, beat, quote, reason,
speed, hold, stabilize, reverse, transition}], `cuts`, `captions`, `audio`, `grade`, `title`,
`zooms`, `graphics`, `effects`, `broll`, `changes`, `preset`.
Section on-screen length is always `plan.range_len(r)`; staleness is `plan.cut_signature(plan)`.

**Library (Home mode):** `~/Videos/AI Video Editor/<video>/` with clips, `edit/`, `exports/`.
Override with `AI_VIDEO_LIBRARY` or `review_server.py --library`.

**Plugin:** this repo is both plugin (`ai-video-editor`) and marketplace (`aisolopreneurs-video`). Installed on this
machine from GitHub (user scope) at `~/.claude/plugins/cache/aisolopreneurs-video/ai-video-editor/<version>/`,
with its own `.venv`. It's a snapshot: after pushing, run `claude plugin marketplace update aisolopreneurs-video`
then `claude plugin update ai-video-editor@aisolopreneurs-video`, and re-run setup if the folder changed.
Skills: `start`, `edit-video`, `video-editor-setup`, plus upstream `manim-video`. Validate with
`claude plugin validate .` (passes). **Never actually installed from GitHub yet.**

**Commands:**
```bash
uv run python helpers/review_server.py                       # Home screen
uv run python helpers/review_server.py footage/x/edit        # one video's Studio
uv run python helpers/make.py "<folder>" --preset reel --title "..." --studio
uv run python helpers/export.py "<folder>/edit/plan.json" --aspects 9:16,4:5,1:1,16:9
uv run python helpers/qc.py <plan.json> <video>
uv run python helpers/shortcut.py                            # (re)create desktop icon
```

---

## 4. RESOLVED: desktop shortcut "No Python at ..."

**Symptom (2026-11-03):** double-clicking the icon showed Python Launcher's "No Python at
C:\Users\alang\AppData\Roaming\uv\python\cpython-3.12.16-windows-x86_64-none\pythonw.exe".

**Cause (confirmed):** the Claude desktop app is a packaged MSIX app, so `AppData` writes from inside
it are redirected to `...\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\...`.
uv installed Python there. Inside the app the path looks real; from Explorer it doesn't exist.
A probe launched via Explorer confirmed only `uv\python` was missing. FFmpeg, ffprobe, uv, Node,
Chrome, the Hugging Face models and both shortcuts are all visible outside.

**Fix (committed and pushed in `2a4c838`):**
- Python now lives in `<repo>/.python` (git-ignored; `UV_PYTHON_INSTALL_DIR`), `.venv` rebuilt on it.
- `skills/video-editor-setup/SKILL.md` does the same on Windows for every user.
- `shortcut.py` refuses to create a shortcut if the venv's Python is under AppData.
- `review_server.py`: `run_windowless()` logs to `%TEMP%\ai_video_editor.log` under pythonw and hides
  console windows for FFmpeg calls; background jobs use `library.worker_python()` (python.exe, hidden).

**Tested from outside the app:** the real `.lnk` started Home on the real library; a server started
via Explorer made a full justyn-preset video (QC pass) and a 1080x1920 export.
**Not verifiable by me:** that no windows flash, and that the browser opens on double-click. Ask the user.

**Rule: test launch problems from outside.** Claude's shell can't see them. Use
`explorer.exe "<a .cmd or .lnk>"` and have the .cmd write to a file under Documents.

Leftover, harmless: `C:\Users\alang\.local\bin\python3.12.exe` from the first uv install; old Python
copy in the app's LocalCache. Neither is used.

---

## 5. Other open items

- Do a real dry run of the one-sentence install from GitHub (may create a second copy).
- macOS and Linux untested; brand fonts default to Windows paths (`C:/Windows/Fonts`, and
  `captions.FONTS_DIR` is hard-coded).
- Plugin updates change the install path, so setup must re-run to refresh the shortcut.
- The browser's "Open the folder" button wasn't clicked in testing.
- Brand colours in `brand/brand.json` are placeholders (forest green, gold). Ask for the real ones.
- Studio has no graphics editing or still graphic mockups (ask Claude; deferred until after redesign).
- Not built: beat sync, moving-face tracking within a section, speed ramps (speed is per section),
  thumbnails with cut-outs, 16:9 long-form testing on real landscape footage, HDR mode (tabled).
- Home has no delete button; a server started by the icon runs in the background with no quit button, and keeps running old code after an update until it's stopped or the computer restarts.
- CapCut gaps worth doing next: drag-and-drop timeline, photos on the Home screen, vocal isolation (needs a Demucs download), better text-to-speech voices, bundled open-licence fonts for Mac/Linux and a stronger viral look.
- Many effects at once are slow: every effect processes the whole video (24 hadn't finished after 15 min on a 20 s test; 5 took under a minute).
- `make.py` applies the preset mechanically; Claude's judgement pass only happens if asked.

---

## 6. The user's test project

`footage/my-first-video/post-73-how-to_fix_texans.MOV`: 33 s vertical iPhone talking head, SDR,
two audio tracks (AAC + Apple spatial; stereo gets used). Plan is at **v7, status draft**, 28.1 s.
The user chose the Punchy look (`neutral_punch`) and Clean captions at the lower position. Plan has a
hook title behind subject, 2 punch-ins, 5 graphics + progress bar, "drive" → "derive" correction.
Rough cut is current and passes QC. Draft exports are in `footage/my-first-video/exports/`.
It is not in the Home library (that folder is empty).

---

## 7. Gotchas learned

**Windows / tooling**
- Run FFmpeg-using commands with PATH refreshed, or use the Bash tool where it's already set:
  `$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')`
- Helpers print "→": subprocesses need `PYTHONUTF8=1` or they crash on Windows pipes.
- Long inline Python heredocs with quotes/backticks can break the Bash tool: write the script to the
  scratchpad and run it.
- Bash `cd` can persist and change the session's working directory; prefer absolute paths.
- `.python-version` is `3.12`; on Windows use `UV_PYTHON_INSTALL_DIR=<repo>/.python` (see section 4).
- `rm -rf "$(...)"` is blocked by a safety check: use literal paths.

**Editor internals**
- Restart `review_server.py` after Python changes (HTML is read fresh each request).
- The built-in browser reaches the server only via `preview_start` and `.claude/launch.json`
  (configs: `review-studio`, `review-studio-test`, `home-test`). Port 8765 is shared; stop one before
  starting another. A leftover server makes the desktop icon open the wrong library.
- Hidden browser pane delays lazy images and screenshots can be a frame behind; verify with JS.
- FFmpeg: crop width/height aren't per-frame (use zoompan), drawbox width ignores time (slide an
  overlay), xfade needs `fps` then `settb=AVTB` on all inputs, `-t` must be an input option for speed
  changes, and color tags should be normalised before joining.
- libass sizes fonts by ascent+descent, not em; `Writer._measure` corrects for that.
- MediaPipe short-range face detection needs tiling on wide frames; OpenCV 5 has no Haar files.
- faster-whisper's PyAV decode breaks: audio is passed as a NumPy array.
- Each section adds about one frame of length drift; QC tolerance scales with section count.
- Exports overwrite `edit/base_*`, so QC must run right after each render to get a matching base.
- A true-peak of -0.9 on drafts is normal (1-pass loudnorm); QC allows 0.15 dB.
- FFmpeg's blend filter has a wrong `T` (time) on some phone footage; `handwipe.py` uses `N / fps` instead.
- A real hand swipe is often only a blur for 2-4 frames, which MediaPipe's hand model misses. `handwipe.motion_track` (each frame compared with the median of the frames around it) is the fallback; tested on a real 0.1 s swipe (IMG_3565). The line trails the tracked hand by one frame so the hand drags it. QC skips face and safe-zone checks while a full-frame effect is on.
- `framing.person_mask` must copy: MediaPipe's view dangles once its result is freed (crashed Python when a mask was kept between frames).
- Windows lets two servers share port 8765 if address reuse is on; `review_server.py` turns it off there. Test servers should use `--port`, because the real icon may be running on 8765.
- Commit on the right branch: Phase 4 once landed on the Phase 3 branch by mistake.
