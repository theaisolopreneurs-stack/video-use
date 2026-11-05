---
name: start
description: Open the AI video editor for the user (the Home screen where they drag in clips and pick a style). Use when the user says "open the video editor", "start the editor", "I want to make a video", or seems unsure how to begin - especially non-technical users.
---

# Open the video editor

The plugin root is two folders up from this file (`<this skill's base directory>/../..`); call
it `ROOT`. The user may be completely new to computers: keep every message short, friendly and
free of jargon (no "localhost", "terminal", "path", "JSON").

1. **Set up if needed.** If `ROOT/.venv` doesn't exist, run the `video-editor-setup` skill first
   (it asks them before adding any Desktop icon).
2. **Open it.** Start the Home screen in the background - it opens in their browser by itself
   (if it's already running, this just reopens the page):

   ```bash
   uv run --project "ROOT" python "ROOT/helpers/review_server.py"
   ```

   Run it in the background so the chat stays free.
3. **Tell them, in about three lines:**
   - "Your video editor just opened in your browser."
   - "Drag your video clips into the box, pick a style, and click **Make my video**."
   - If they have the icon: "Next time, just double-click **AI Video Editor** on your Desktop."
     Otherwise: "Next time, just ask me to open the video editor."
   Mention they can come back here any time and say things like *"improve my <name> video"*
   or *"make the captions bigger"*.

Videos are saved in `~/Videos/AI Video Editor/<video name>/` (Windows: `Videos\AI Video Editor`).
