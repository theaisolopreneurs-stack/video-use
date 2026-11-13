"""Fonts that exist on THIS computer.

The presets and brand kit name Windows fonts (Segoe UI, Georgia, Impact...). On a Mac or Linux
those files aren't there, so every font goes through here: the same file if it exists, else the
same name in the system font folders, else the closest look-alike that ships with that system
(Segoe UI -> Helvetica Neue on a Mac, DejaVu Sans on Linux), else a generic sans/serif/mono.

    fonts.path("segoeuib.ttf")            -> a usable font file
    fonts.resolve({"family": "Segoe UI", "file": "C:/Windows/Fonts/segoeuib.ttf"})
                                          -> the same dict, with file and family that exist here
    fonts.folder()                        -> the system font folder (for the captions renderer)
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

if os.name == "nt":
    DIRS = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts"]
elif sys.platform == "darwin":
    DIRS = [Path("/System/Library/Fonts/Supplemental"), Path("/System/Library/Fonts"), Path("/Library/Fonts"),
            Path.home() / "Library" / "Fonts"]
else:
    DIRS = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".local" / "share" / "fonts",
            Path.home() / ".fonts"]

# Windows font file -> look-alikes on Mac and Linux, best first.
SAME_LOOK = {
    "segoeui.ttf": ["HelveticaNeue.ttc", "Helvetica.ttc", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
    "segoeuib.ttf": ["HelveticaNeue.ttc", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"],
    "seguibl.ttf": ["Arial Black.ttf", "HelveticaNeue.ttc", "DejaVuSans-Bold.ttf"],
    "seguisb.ttf": ["HelveticaNeue.ttc", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"],
    "arial.ttf": ["Arial.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
    "arialbd.ttf": ["Arial Bold.ttf", "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "ariblk.ttf": ["Arial Black.ttf", "DejaVuSans-Bold.ttf"],
    "arialnb.ttf": ["Arial Narrow Bold.ttf", "LiberationSansNarrow-Bold.ttf", "DejaVuSansCondensed-Bold.ttf"],
    "impact.ttf": ["Impact.ttf", "DejaVuSans-Bold.ttf"],
    "georgia.ttf": ["Georgia.ttf", "DejaVuSerif.ttf", "LiberationSerif-Regular.ttf"],
    "georgiab.ttf": ["Georgia Bold.ttf", "DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf"],
    "georgiai.ttf": ["Georgia Italic.ttf", "DejaVuSerif-Italic.ttf", "LiberationSerif-Italic.ttf"],
    "georgiaz.ttf": ["Georgia Bold Italic.ttf", "DejaVuSerif-BoldItalic.ttf", "LiberationSerif-BoldItalic.ttf"],
    "times.ttf": ["Times New Roman.ttf", "LiberationSerif-Regular.ttf", "DejaVuSerif.ttf"],
    "timesbd.ttf": ["Times New Roman Bold.ttf", "LiberationSerif-Bold.ttf", "DejaVuSerif-Bold.ttf"],
    "consola.ttf": ["Menlo.ttc", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf"],
    "consolab.ttf": ["Menlo.ttc", "DejaVuSansMono-Bold.ttf", "LiberationMono-Bold.ttf"],
    "courbd.ttf": ["Courier New Bold.ttf", "LiberationMono-Bold.ttf", "DejaVuSansMono-Bold.ttf"],
    "cour.ttf": ["Courier New.ttf", "LiberationMono-Regular.ttf", "DejaVuSansMono.ttf"],
    "tahomabd.ttf": ["Tahoma Bold.ttf", "Verdana Bold.ttf", "DejaVuSans-Bold.ttf"],
    "framd.ttf": ["Futura.ttc", "Avenir Next.ttc", "DejaVuSans-Bold.ttf"],
    "bahnschrift.ttf": ["DIN Alternate Bold.ttf", "Avenir Next Condensed.ttc", "DejaVuSansCondensed-Bold.ttf"],
    "constanb.ttf": ["Hoefler Text.ttc", "Georgia Bold.ttf", "DejaVuSerif-Bold.ttf"],
    "palab.ttf": ["Palatino.ttc", "Book Antiqua Bold.ttf", "DejaVuSerif-Bold.ttf"],
    "segoescb.ttf": ["SnellRoundhand.ttc", "Apple Chancery.ttf", "DejaVuSerif-Italic.ttf"],
    "segoeprb.ttf": ["Noteworthy.ttc", "Chalkboard.ttc", "DejaVuSans.ttf"],
    "inkfree.ttf": ["Noteworthy.ttc", "Bradley Hand Bold.ttf", "Chalkboard.ttc", "DejaVuSans.ttf"],
    "comicbd.ttf": ["Comic Sans MS Bold.ttf", "Chalkboard.ttc", "DejaVuSans-Bold.ttf"],
}
GENERIC = {"serif": ["Georgia.ttf", "Times New Roman.ttf", "DejaVuSerif.ttf", "LiberationSerif-Regular.ttf"],
           "mono": ["Menlo.ttc", "consola.ttf", "DejaVuSansMono.ttf"],
           "sans": ["segoeui.ttf", "HelveticaNeue.ttc", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]}


@lru_cache(maxsize=1)
def _index() -> dict[str, str]:
    """Every font file in the system folders, by lower-case file name."""
    found: dict[str, str] = {}
    for d in DIRS:
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() in (".ttf", ".ttc", ".otf"):
                found.setdefault(p.name.lower(), str(p))
    return found


@lru_cache(maxsize=256)
def path(name: str) -> str:
    """A font file that exists here for `name` (a file name or path); always returns something."""
    p = Path(str(name))
    if p.is_file():
        return str(p)
    idx = _index()
    base = p.name.lower()
    for cand in [base] + [c.lower() for c in SAME_LOOK.get(base, [])]:
        if cand in idx:
            return idx[cand]
    kind = "serif" if any(k in base for k in ("georgia", "times", "pala", "constan", "serif")) else \
        "mono" if any(k in base for k in ("consol", "cour", "mono", "menlo")) else "sans"
    for cand in GENERIC[kind] + GENERIC["sans"]:
        if cand.lower() in idx:
            return idx[cand.lower()]
    return next(iter(idx.values()), str(p))


def resolve(spec: dict) -> dict:
    """A brand/preset font {family, file, ...} made to match a font that exists on this computer."""
    if not spec:
        return spec
    f = spec.get("file")
    if f and Path(f).is_file():
        return spec
    real = path(f or spec.get("family", "segoeui.ttf"))
    out = dict(spec, file=real)
    try:
        from PIL import ImageFont
        out["family"] = ImageFont.truetype(real, 20).getname()[0] or spec.get("family")
    except OSError:
        pass
    return out


def folder() -> str:
    """The main system font folder, for the captions renderer (libass) to look in."""
    return next((str(d) for d in DIRS if d.is_dir()), "")
