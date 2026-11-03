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

```bash
uv sync --project "ROOT"
```

**Windows on ARM (Snapdragon):** several audio/AI packages have no ARM wheels. Create the
environment on x64 Python first (it runs under emulation), then sync:

```bash
uv python install cpython-3.12-windows-x86_64-none
uv venv --project "ROOT" --python cpython-3.12-windows-x86_64-none "ROOT/.venv"
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

## 5. Tell the user

Say it's ready, and that the fastest start is: put clips in a folder and say
"edit the clips in <folder> as a reel".
