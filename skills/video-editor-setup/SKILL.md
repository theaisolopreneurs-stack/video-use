---
name: video-editor-setup
description: One-time setup for the AI video editor plugin - installs FFmpeg, uv, Node.js and the editor's Python and graphics dependencies, then runs a smoke test. Use when the editor has never been set up on this machine, after a plugin update, or when a helper fails with a missing tool or module.
---

# Set up the AI video editor

The plugin root is two folders up from this file (`<this skill's base directory>/../..`); call
it `ROOT` and use it as an absolute path. Do each step, checking before installing, and tell the
user what you installed. Ask before any step that needs admin rights.

## 1. System tools (skip any already on PATH)

| Tool | Windows (winget) | macOS (Homebrew) | Debian/Ubuntu |
|---|---|---|---|
| FFmpeg (with libass) | `winget install Gyan.FFmpeg` | `brew install ffmpeg` | `sudo apt install ffmpeg` |
| uv | `winget install astral-sh.uv` | `brew install uv` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js 22+ | `winget install OpenJS.NodeJS.LTS` | `brew install node` | use NodeSource or `nvm install 22` |
| Chrome or Chromium | usually present | usually present | `sudo apt install chromium` |

On Windows, open a new shell (or refresh PATH) after installing.

## 2. Python environment

**Windows: Python must be installed inside `ROOT`.** The Claude desktop app silently redirects
`AppData`, so a Python installed there from inside the app doesn't exist for the Desktop icon
("No Python at ..."). Checks run from Claude's own shell still pass, so don't skip this.

```bash
export UV_PYTHON_INSTALL_DIR="ROOT/.python"
uv python install cpython-3.12-windows-x86_64-none   # x64 on Intel/AMD and on ARM (Snapdragon)
uv venv --clear --project "ROOT" --python "ROOT/.python/cpython-3.12-windows-x86_64-none/python.exe" "ROOT/.venv"
uv sync --project "ROOT"
```

x64 is deliberate on ARM: several audio/AI packages have no ARM wheels, and it runs under emulation.

**macOS / Linux:**

```bash
uv sync --project "ROOT"
```

## 3. Motion-graphics engine

```bash
npm install --prefix "ROOT/graphics"
```

## 4. Smoke test

```bash
uv run --project "ROOT" python "ROOT/scripts/smoke_test.py"
```

It makes a spoken test clip, transcribes it locally, and cuts the silence. `PASS` means the
editor is ready. The first run downloads the small Whisper model (~250 MB) and two tiny MediaPipe
models; nothing else leaves the machine.

## 5. Desktop icon and video library

```bash
uv run --project "ROOT" python "ROOT/helpers/shortcut.py"
```

This puts an **AI Video Editor** icon on the Desktop (and Start menu / Applications) and creates
the video library folder `~/Videos/AI Video Editor/`.

## 6. Open it and tell the user

Start the editor in the background (it opens their browser):
`uv run --project "ROOT" python "ROOT/helpers/review_server.py"`. Then, in plain words:

- "All set! Your video editor is open in your browser."
- "Drag your clips in, pick a style, click **Make my video**."
- "Next time, double-click **AI Video Editor** on your Desktop. Come back here any time to ask
  me to improve a video."
