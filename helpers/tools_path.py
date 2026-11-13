"""Use the fastest FFmpeg on this computer.

On Windows laptops with an ARM chip (Snapdragon), the usual FFmpeg is built for Intel/AMD and runs
through Windows' translation layer: every video step is slower than it needs to be. If an ARM build
is in ~/.cache/ai-video-editor/ffmpeg-arm64/ (setup puts it there: `python helpers/tools_path.py
install`), it goes first on PATH for this process and everything it starts. Measured on a Snapdragon
X: 25-30% faster, same picture.

    import tools_path  # noqa: F401  (first thing in an entry-point script)
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

ARM_DIR = Path.home() / ".cache" / "ai-video-editor" / "ffmpeg-arm64"
RELEASES = "https://api.github.com/repositories/292087234/releases/latest"  # FFmpeg-Builds (BtbN)


def is_windows_arm() -> bool:
    if os.name != "nt":
        return False
    arch = (os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").upper()
    return arch == "ARM64" or "ARM" in platform.processor().upper()


def use_fastest() -> str | None:
    """Put the ARM FFmpeg first on PATH when it's here and this is an ARM PC. Returns its folder."""
    if os.environ.get("AIVE_NO_ARM_FFMPEG") or not is_windows_arm():
        return None
    if not ((ARM_DIR / "ffmpeg.exe").exists() and (ARM_DIR / "ffprobe.exe").exists()):
        return None
    path = os.environ.get("PATH", "")
    if not path.lower().startswith(str(ARM_DIR).lower()):
        os.environ["PATH"] = str(ARM_DIR) + os.pathsep + path
    return str(ARM_DIR)


def install() -> None:
    """Download the ARM build of FFmpeg (stable 9.0, GPL, from the FFmpeg-Builds project on GitHub)."""
    import json
    import shutil
    import tempfile
    import urllib.request
    import zipfile
    if not is_windows_arm():
        print("not an ARM Windows PC: the usual FFmpeg is already the fast one")
        return
    rel = json.load(urllib.request.urlopen(urllib.request.Request(RELEASES, headers={"User-Agent": "ai-video-editor"})))
    asset = next(a for a in rel["assets"] if a["name"].startswith("ffmpeg-n9.0") and "winarm64-gpl-9.0.zip" in a["name"])
    print(f"downloading {asset['name']} ({asset['size'] / 1e6:.0f} MB)…", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / "ff.zip"
        urllib.request.urlretrieve(asset["browser_download_url"], z)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(tmp)
        ARM_DIR.mkdir(parents=True, exist_ok=True)
        for exe in ("ffmpeg.exe", "ffprobe.exe"):
            shutil.copyfile(next(Path(tmp).rglob(exe)), ARM_DIR / exe)
    print(f"installed → {ARM_DIR}")


use_fastest()

if __name__ == "__main__":
    if sys.argv[1:] == ["install"]:
        install()
    else:
        print(use_fastest() or "using the FFmpeg on PATH")
