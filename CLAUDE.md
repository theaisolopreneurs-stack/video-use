# AI Video Editor

A free, local video editor driven by Claude: raw clips in, finished branded videos out.

**Before editing a video, read `SKILL.md`** (the editor manual: every helper, plan.json field and
workflow). For project history, open items and gotchas, read `docs/HANDOFF.md`. The user guide is
`docs/GUIDE.md`.

## Running things

- Use the project's own Python: `uv run python helpers/<tool>.py ...` or `.venv/Scripts/python.exe`.
- On Windows, set `UV_PYTHON_INSTALL_DIR=<this folder>/.python` before any `uv` command that
  installs Python or syncs packages. The Claude desktop app hides AppData from the Desktop icon.
- Set `PYTHONUTF8=1` for helpers run as subprocesses (they print "→").
- Open the editor: `uv run python helpers/review_server.py` (Home) or `... review_server.py <video>/edit` (Studio).
- Make a video: `uv run python helpers/make.py "<video folder>" --preset viral --studio`.
- Videos made from the Home screen live in `~/Videos/AI Video Editor/<name>/`.

## Working with the user

- They aren't a programmer. Explain results in plain language and keep jargon out.
- Commit, push or merge only when they ask.
- Never add a Desktop shortcut without asking (`shortcut.py` refuses without `--yes`).
- Keep their plan edits: they change things in the Studio between turns.
- Test launch problems from outside the app (see HANDOFF section 4).
