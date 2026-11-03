"""Colour looks as recipes: a few numbers each, built into a .cube LUT on first use.

Modelled on the looks creators reach for most (teal & orange, moody, film emulations like
Kodak Portra / Fuji 400H / Ektachrome / Polaroid, golden hour, Y2K, "clean girl", noir...).
They're our own approximations from simple colour maths, so there's nothing to license.

Use in plan.json:  "grade": "look:teal_orange"     (or the Studio's Look tab)
List them:         python helpers/looks.py
Built LUTs are cached in <repo>/luts/generated/ (git-ignored).

Recipe fields (all optional):
  temp     + warmer / - cooler          tint   + magenta / - green
  exposure brightness multiplier         contrast  S-curve strength (-0.3 .. 0.5)
  lift     raise blacks (faded/matte)    rolloff   soften highlights (0 .. 0.3)
  sat      saturation multiplier         vibrance  boost muted colours only
  shadows / mids / highlights:  [r, g, b] colour pushed into that tonal range
  split    strength of shadows/highlights tints (default 1)
  mono     black & white;  mono_tint [r, g, b] for sepia/cyanotype
  grain    film grain amount (adds FFmpeg noise)   vignette  darken corners (0..1)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

GEN_DIR = Path(__file__).resolve().parent.parent / "luts" / "generated"
N = 25

LOOKS: dict[str, dict] = {
    # --- natural / clean ---
    "clean_bright": dict(label="Clean bright", group="Natural", exposure=1.06, contrast=0.08, sat=1.05, lift=0.01),
    "soft_skin": dict(label="Soft skin", group="Natural", temp=0.03, contrast=-0.05, rolloff=0.08, sat=0.95,
                      mids=[0.02, 0.0, -0.01]),
    "clean_girl": dict(label="Clean girl", group="Natural", exposure=1.08, temp=0.025, contrast=-0.08, lift=0.04,
                       sat=0.88, highlights=[0.03, 0.01, -0.01], rolloff=0.1),
    "vibrant_pop": dict(label="Vibrant pop", group="Natural", contrast=0.18, sat=1.28, vibrance=0.25),
    "crisp_cool": dict(label="Crisp cool", group="Natural", temp=-0.04, contrast=0.12, sat=1.02),

    # --- warm ---
    "golden_hour": dict(label="Golden hour", group="Warm", temp=0.1, contrast=0.06, sat=1.08,
                        highlights=[0.08, 0.04, -0.06], shadows=[0.02, 0.0, -0.02]),
    "sunset_glow": dict(label="Sunset glow", group="Warm", temp=0.12, tint=0.03, contrast=0.1, sat=1.12,
                        highlights=[0.1, 0.02, -0.04], shadows=[0.02, -0.01, 0.04]),
    "cozy_autumn": dict(label="Cozy autumn", group="Warm", temp=0.09, contrast=0.08, sat=0.92, lift=0.03,
                        mids=[0.04, 0.01, -0.04], shadows=[0.03, 0.0, -0.03]),
    "desert_orange": dict(label="Desert", group="Warm", temp=0.14, contrast=0.14, sat=1.0,
                          mids=[0.06, 0.02, -0.06]),

    # --- cinematic ---
    "teal_orange": dict(label="Teal & orange", group="Cinematic", contrast=0.16, sat=1.1,
                        shadows=[-0.07, 0.02, 0.08], highlights=[0.08, 0.03, -0.06], split=1.2),
    "blockbuster": dict(label="Blockbuster", group="Cinematic", contrast=0.24, sat=1.0, rolloff=0.06,
                        shadows=[-0.08, 0.02, 0.1], highlights=[0.07, 0.03, -0.05]),
    "moody_teal": dict(label="Moody teal", group="Cinematic", exposure=0.92, contrast=0.2, sat=0.8, lift=0.02,
                       shadows=[-0.06, 0.03, 0.06], mids=[-0.02, 0.01, 0.02]),
    "moody_dark": dict(label="Moody dark", group="Cinematic", exposure=0.88, contrast=0.22, sat=0.72,
                       shadows=[0.0, 0.0, 0.03], rolloff=0.05),
    "bleach_bypass": dict(label="Bleach bypass", group="Cinematic", contrast=0.32, sat=0.45, rolloff=0.04),
    "nordic": dict(label="Nordic", group="Cinematic", temp=-0.06, contrast=0.1, sat=0.75, lift=0.03,
                   highlights=[-0.01, 0.01, 0.03]),
    "matrix": dict(label="Matrix", group="Cinematic", contrast=0.2, sat=0.7, tint=-0.06,
                   shadows=[-0.02, 0.05, 0.0], highlights=[-0.02, 0.04, -0.02]),

    # --- film emulation ---
    "kodak_portra": dict(label="Portra-style", group="Film", temp=0.04, contrast=-0.04, lift=0.025, rolloff=0.1,
                         sat=0.92, mids=[0.025, 0.01, -0.015], grain=6),
    "kodak_gold": dict(label="Gold-style", group="Film", temp=0.08, contrast=0.08, sat=1.12, lift=0.02,
                       highlights=[0.06, 0.04, -0.05], grain=7),
    "fuji_400h": dict(label="Fuji 400H-style", group="Film", contrast=-0.02, lift=0.03, sat=0.9, rolloff=0.08,
                      shadows=[-0.03, 0.02, 0.04], highlights=[0.03, 0.02, -0.01], mids=[-0.01, 0.02, 0.0], grain=6),
    "ektachrome": dict(label="Slide film", group="Film", contrast=0.22, sat=1.22, temp=-0.01,
                       shadows=[-0.02, 0.0, 0.04], grain=5),
    "cinestill": dict(label="Tungsten night", group="Film", temp=-0.08, tint=0.02, contrast=0.14, sat=1.05,
                      highlights=[0.08, 0.02, -0.02], grain=9),
    "polaroid": dict(label="Instant photo", group="Film", lift=0.07, contrast=-0.06, sat=0.8, rolloff=0.12,
                     shadows=[-0.04, 0.02, 0.05], highlights=[0.05, 0.03, -0.03], vignette=0.35),
    "super8": dict(label="Super 8", group="Film", temp=0.1, lift=0.06, contrast=0.04, sat=0.82,
                   mids=[0.03, 0.01, -0.03], grain=16, vignette=0.55),

    # --- trend / stylised ---
    "y2k": dict(label="Y2K", group="Trend", temp=-0.05, tint=0.04, contrast=0.12, sat=1.15, lift=0.03,
                shadows=[0.03, -0.01, 0.08], highlights=[-0.01, 0.02, 0.04]),
    "pastel_dream": dict(label="Pastel dream", group="Trend", exposure=1.06, lift=0.08, contrast=-0.12, sat=0.75,
                         shadows=[0.04, 0.0, 0.05], highlights=[0.03, 0.02, 0.02]),
    "wes_pastel": dict(label="Storybook pastel", group="Trend", temp=0.05, lift=0.05, contrast=0.02, sat=0.95,
                       mids=[0.04, 0.02, -0.02], highlights=[0.04, 0.03, 0.0]),
    "cyberpunk": dict(label="Cyberpunk", group="Trend", contrast=0.2, sat=1.2, tint=0.06,
                      shadows=[0.0, 0.03, 0.1], highlights=[0.1, -0.02, 0.08]),
    "faded_matte": dict(label="Faded matte", group="Trend", lift=0.09, contrast=-0.05, sat=0.85, rolloff=0.1),
    "retro_70s": dict(label="Retro 70s", group="Trend", temp=0.11, lift=0.05, contrast=0.05, sat=0.85,
                      mids=[0.04, 0.02, -0.05], highlights=[0.04, 0.03, -0.04], grain=10),
    "cross_process": dict(label="Cross process", group="Trend", contrast=0.2, sat=1.15,
                          shadows=[-0.02, 0.04, 0.08], highlights=[0.06, 0.06, -0.08]),

    # --- black & white / tinted ---
    "bw_classic": dict(label="B&W classic", group="Mono", mono=True, contrast=0.1),
    "noir": dict(label="Noir", group="Mono", mono=True, contrast=0.38, exposure=0.95, vignette=0.6, grain=8),
    "bw_soft": dict(label="B&W soft", group="Mono", mono=True, contrast=-0.08, lift=0.05, grain=5),
    "sepia": dict(label="Sepia", group="Mono", mono=True, mono_tint=[1.08, 0.97, 0.78], contrast=0.06, lift=0.03),
    "cyanotype": dict(label="Cyanotype", group="Mono", mono=True, mono_tint=[0.75, 0.92, 1.12], contrast=0.1),
}


def _apply(rgb: np.ndarray, r: dict) -> np.ndarray:
    """Apply a recipe to an (..., 3) array of 0-1 values."""
    x = rgb * r.get("exposure", 1.0)
    lum = lambda a: 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    if r.get("mono"):
        l = lum(x)[..., None]
        x = np.repeat(l, 3, axis=-1) * np.array(r.get("mono_tint", [1.0, 1.0, 1.0]))
    t, tint = r.get("temp", 0.0), r.get("tint", 0.0)
    x = x + np.array([t, -tint * 0.5, -t]) * 0.6 + np.array([tint * 0.3, 0, tint * 0.3])
    c = r.get("contrast", 0.0)
    if c:
        x = x + c * (x - 0.5) * (1 - np.abs(2 * np.clip(x, 0, 1) - 1)) * 2  # S-curve, gentle at the ends
    l = np.clip(lum(np.clip(x, 0, 1)), 0, 1)[..., None]
    k = r.get("split", 1.0)
    for key, w in (("shadows", (1 - l) ** 2), ("mids", 4 * l * (1 - l)), ("highlights", l ** 2)):
        if key in r:
            x = x + np.array(r[key]) * w * k
    sat = r.get("sat", 1.0)
    l = lum(x)[..., None]
    x = l + (x - l) * sat
    v = r.get("vibrance", 0.0)
    if v:
        chroma = x.max(axis=-1, keepdims=True) - x.min(axis=-1, keepdims=True)
        x = l + (x - l) * (1 + v * (1 - np.clip(chroma * 2, 0, 1)))
    lift = r.get("lift", 0.0)
    x = lift + x * (1 - lift)
    ro = r.get("rolloff", 0.0)
    if ro:
        x = np.where(x > 1 - 2 * ro, (1 - 2 * ro) + (x - (1 - 2 * ro)) / (1 + (x - (1 - 2 * ro)) / ro), x)
    return np.clip(x, 0, 1)


def cube_for(name: str) -> Path:
    """Build (once) and return the .cube file for a look."""
    recipe = LOOKS[name]
    key = hashlib.sha1(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:8]
    path = GEN_DIR / f"{name}_{key}.cube"
    if path.exists():
        return path
    GEN_DIR.mkdir(parents=True, exist_ok=True)
    g = np.linspace(0, 1, N)
    b, gg, rr = np.meshgrid(g, g, g, indexing="ij")  # .cube order: red fastest
    rgb = np.stack([rr, gg, b], axis=-1).reshape(-1, 3)
    out = _apply(rgb, recipe)
    lines = [f'TITLE "{recipe["label"]}"', f"LUT_3D_SIZE {N}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    lines += [f"{a:.6f} {b_:.6f} {c:.6f}" for a, b_, c in out]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def look_filter(name: str) -> str:
    """FFmpeg filter for 'look:<name>': the LUT plus any grain / vignette the recipe asks for."""
    name = name[5:] if name.startswith("look:") else name
    if name not in LOOKS:
        raise KeyError(f"unknown look '{name}'. Available: {', '.join(LOOKS)}")
    r = LOOKS[name]
    esc = str(cube_for(name)).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    parts = [f"lut3d=file='{esc}':interp=tetrahedral"]
    if r.get("vignette"):
        parts.append(f"vignette=angle={0.35 + 0.35 * r['vignette']:.2f}")
    if r.get("grain"):
        parts.append(f"noise=alls={int(r['grain'])}:allf=t+u")
    return ",".join(parts)


def look_list() -> list[dict]:
    return [{"id": f"look:{k}", "label": v["label"], "group": v["group"]} for k, v in LOOKS.items()]


if __name__ == "__main__":
    for group in dict.fromkeys(v["group"] for v in LOOKS.values()):
        print(f"{group}: " + ", ".join(f"{k} ({v['label']})" for k, v in LOOKS.items() if v["group"] == group))
    if len(sys.argv) > 1 and sys.argv[1] == "--build":
        for k in LOOKS:
            print(cube_for(k))
