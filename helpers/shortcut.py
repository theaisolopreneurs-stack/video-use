"""Put an "AI Video Editor" icon on the Desktop (and Start menu / Applications).

Double-clicking it opens the Home screen in the browser: drag in clips, pick a style, done.
No terminal, no typing, no "localhost". Clicking it again while it's running just reopens
the page. Also creates the video library folder (~/Videos/AI Video Editor).

    python helpers/shortcut.py            # create / refresh the shortcuts
    python helpers/shortcut.py --remove
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HELPERS = Path(__file__).resolve().parent
REPO = HELPERS.parent
NAME = "AI Video Editor"


def make_icon() -> Path:
    """A simple warm clapper/play icon, drawn here so there's nothing to license."""
    from PIL import Image, ImageDraw
    ico = REPO / "brand" / "app_icon.ico"
    if ico.exists():
        return ico
    s = 256
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([8, 8, s - 8, s - 8], radius=56, fill=(192, 83, 43, 255))
    d.rounded_rectangle([8, 8, s - 8, 82], radius=56, fill=(45, 33, 24, 255))
    d.rectangle([8, 56, s - 8, 82], fill=(45, 33, 24, 255))
    for x in range(30, s - 20, 46):  # clapper stripes
        d.polygon([(x, 20), (x + 22, 20), (x + 6, 70), (x - 16, 70)], fill=(246, 239, 228, 255))
    d.polygon([(100, 112), (100, 212), (186, 162)], fill=(255, 250, 242, 255))  # play
    im.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return ico


def windows_folder(kind: str) -> Path:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{kind}')"],
                         capture_output=True, text=True).stdout.strip()
    return Path(out)


def python_for_launch() -> Path:
    """The editor's own Python; on Windows the windowless one so no console pops up."""
    exe = Path(sys.executable)
    if os.name == "nt":
        w = exe.with_name("pythonw.exe")
        return w if w.exists() else exe
    return exe


def python_home_problem() -> str | None:
    """The Claude desktop app redirects AppData, so a Python installed there from inside the app
    doesn't exist for a shortcut started from the Desktop ("No Python at ..."). Catch it here,
    where it can be fixed, because a check run from inside the app can't see the failure."""
    if os.name != "nt":
        return None
    cfg = Path(sys.prefix) / "pyvenv.cfg"
    home = next((ln.split("=", 1)[1].strip() for ln in cfg.read_text(encoding="utf-8").splitlines()
                 if ln.startswith("home")), "") if cfg.exists() else sys.base_prefix
    for var in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base and home.lower().startswith(base.lower()):
            return (f"This environment's Python is under {var} ({home}). A Desktop shortcut may not be able "
                    "to see it. Rebuild it with Python in the editor's folder: see Windows Python in "
                    "skills/video-editor-setup/SKILL.md.")
    return None


def create() -> list[Path]:
    from library import default_library
    problem = python_home_problem()
    if problem:
        sys.exit(problem)
    lib = default_library()
    lib.mkdir(parents=True, exist_ok=True)
    server = HELPERS / "review_server.py"
    made = []
    if os.name == "nt":
        icon = make_icon()
        for folder in (windows_folder("Desktop"), windows_folder("Programs")):
            lnk = folder / f"{NAME}.lnk"
            ps = (
                "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
                "$s.TargetPath = '{exe}'; $s.Arguments = '\"{server}\"'; $s.WorkingDirectory = '{repo}'; "
                "$s.IconLocation = '{icon}'; $s.Description = 'Edit videos with AI - drag in clips, pick a style'; $s.Save()"
            ).format(lnk=lnk, exe=python_for_launch(), server=server, repo=REPO, icon=icon)
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
            made.append(lnk)
    elif sys.platform == "darwin":
        for folder in (Path.home() / "Desktop", Path.home() / "Applications"):
            folder.mkdir(exist_ok=True)
            cmd = folder / f"{NAME}.command"
            cmd.write_text(f'#!/bin/bash\n"{sys.executable}" "{server}" >/dev/null 2>&1 &\n', encoding="utf-8")
            cmd.chmod(0o755)
            made.append(cmd)
    else:
        for folder in (Path.home() / "Desktop", Path.home() / ".local" / "share" / "applications"):
            folder.mkdir(parents=True, exist_ok=True)
            entry = folder / "ai-video-editor.desktop"
            entry.write_text("[Desktop Entry]\nType=Application\nName=AI Video Editor\n"
                             f'Exec="{sys.executable}" "{server}"\nTerminal=false\nCategories=AudioVideo;\n',
                             encoding="utf-8")
            entry.chmod(0o755)
            made.append(entry)
    return made


def remove() -> None:
    if os.name == "nt":
        for folder in (windows_folder("Desktop"), windows_folder("Programs")):
            (folder / f"{NAME}.lnk").unlink(missing_ok=True)
    elif sys.platform == "darwin":
        for folder in (Path.home() / "Desktop", Path.home() / "Applications"):
            (folder / f"{NAME}.command").unlink(missing_ok=True)
    else:
        for folder in (Path.home() / "Desktop", Path.home() / ".local" / "share" / "applications"):
            (folder / "ai-video-editor.desktop").unlink(missing_ok=True)


if __name__ == "__main__":
    sys.path.insert(0, str(HELPERS))
    if "--remove" in sys.argv:
        remove()
        print("shortcuts removed")
    else:
        for p in create():
            print(f"created {p}")
