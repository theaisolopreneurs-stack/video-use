"""Styled captions: plan.json + transcripts + brand.json -> <edit>/captions.ass.

Caption settings come from the brand kit and can be overridden per project in
plan.json "captions":

    "captions": {
      "style": "pill",              # see PRESETS: clean, karaoke, pill, hormozi, beast, bold,
                                    # highlight, classic, minimal, neon, typewriter, one_word, stacked, none
      "position": "lower",          # lower (clear of Reels/TikTok UI) | middle | upper
      "max_words": 3,               # optional: overrides the style's own words-per-caption
      "uppercase": false,
      "size": 72,                   # px at 1080-wide portrait / 1080-tall landscape
      "emphasis": ["0 and 4"],      # phrases to make pop
      "emphasis_look": "accent",    # accent = bigger + accent colour | serif = italic serif
      "corrections": [{"from": "drive", "to": "derive", "source": "clip", "at": 24.6}]
    }

Styles: 13 presets (PRESETS below) modelled on the caption looks short-form tools ship
most - word-by-word highlight (karaoke, hormozi, beast, highlight box), clean/bold/minimal,
boxed (pill, classic bar), neon glow, typewriter, one word at a time, stacked two-liners.

Brand kit lookup: <videos_dir>/brand.json, else <repo>/brand/brand.json.

Usage:
    python helpers/captions.py <edit>/plan.json            # writes <edit>/captions.ass
    python helpers/captions.py <edit>/plan.json --style karaoke -o /tmp/test.ass
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from PIL import ImageFont

REPO = Path(__file__).resolve().parent.parent
FONTS_DIR = "C:/Windows/Fonts"
POSITIONS = {"lower": 0.70, "middle": 0.50, "upper": 0.25}
PUNCT_BREAK = set(".,!?;:")


# ---- inputs ---------------------------------------------------------------

def load_brand(edit_dir: Path) -> dict:
    for p in (edit_dir.parent / "brand.json", REPO / "brand" / "brand.json"):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return {}


def settings(plan: dict, brand: dict, style_override: str | None = None) -> dict:
    s = {"style": "pill", "position": "lower", "max_words": 3, "uppercase": False,
         "size": 72, "emphasis": [], "emphasis_look": "accent", "corrections": []}
    s.update(brand.get("captions") or {})
    s.update({k: v for k, v in (plan.get("captions") or {}).items() if v is not None})
    s["emphasis"] = list(s.get("emphasis") or []) + list((brand.get("captions") or {}).get("always_emphasize") or [])
    if style_override:
        s["style"] = style_override
    return s


def canvas(plan: dict, edit_dir: Path) -> tuple[int, int]:
    """Output canvas: render.py scales portrait to 1080x1920 and landscape to 1920x1080."""
    src = next(iter(plan["sources"].values()))
    p = Path(src) if Path(src).is_absolute() else edit_dir / src
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
                          "-of", "json", str(p)], capture_output=True, text=True, encoding="utf-8").stdout
    st = json.loads(out)["streams"][0]
    w, h = st["width"], st["height"]
    if any(abs(int(sd.get("rotation", 0))) in (90, 270) for sd in st.get("side_data_list", [])):
        w, h = h, w
    return (1080, 1920) if h > w else (1920, 1080)


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9']", "", t.lower())


def output_words(plan: dict, edit_dir: Path, corrections: list[dict]) -> list[dict]:
    """Every kept word, re-timed onto the output timeline, with corrections applied."""
    words: list[dict] = []
    offset = 0.0
    cache: dict[str, list[dict]] = {}
    for r in plan["ranges"]:
        src, s, e = r["source"], float(r["start"]), float(r["end"])
        if src not in cache:
            tp = edit_dir / "transcripts" / f"{src}.json"
            cache[src] = [w for w in json.loads(tp.read_text(encoding="utf-8")).get("words", [])
                          if w.get("type") == "word"] if tp.exists() else []
        for w in cache[src]:
            if w["end"] <= s or w["start"] >= e:
                continue
            text = w["text"].strip()
            for c in corrections:
                if norm(text) != norm(c["from"]):
                    continue
                if c.get("source") not in (None, src):
                    continue
                if c.get("at") is not None and abs(float(c["at"]) - w["start"]) > 1.0:
                    continue
                trail = re.search(r"[^\w']+$", text)
                text = c["to"] + (trail.group(0) if trail else "")
            words.append({"text": text,
                          "start": max(w["start"], s) - s + offset,
                          "end": min(w["end"], e) - s + offset})
        offset += e - s
    return words


def mark_emphasis(words: list[dict], phrases: list[str]) -> None:
    """Tag words inside each phrase occurrence with emph=<occurrence id> (truthy)."""
    toks = [norm(w["text"]) for w in words]
    occ = 0
    for ph in phrases:
        target = [norm(x) for x in ph.split() if norm(x)]
        n = len(target)
        for i in range(len(toks) - n + 1):
            if n and toks[i:i + n] == target:
                occ += 1
                for j in range(i, i + n):
                    words[j]["emph"] = occ


def chunk(words: list[dict], max_words: int) -> list[list[dict]]:
    """1..max_words per cue; break on punctuation and pauses. Emphasised words stay
    inline, so a cue can mix looks ("that's one more *sentence*"), and a popped phrase
    is never split across two cues."""
    def phrase_len(i: int) -> int:
        e = words[i].get("emph")
        n = 0
        while i + n < len(words) and e and words[i + n].get("emph") == e:
            n += 1
        return n

    out: list[list[dict]] = []
    cur: list[dict] = []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        inside_phrase = nxt is not None and w.get("emph") and nxt.get("emph") == w.get("emph")
        phrase_wont_fit = (nxt is not None and nxt.get("emph") and nxt.get("emph") != w.get("emph")
                           and len(cur) + phrase_len(i + 1) > max_words)
        if (nxt is None or w["text"][-1:] in PUNCT_BREAK or nxt["start"] - w["end"] >= 0.3
                or phrase_wont_fit or (len(cur) >= max_words and not inside_phrase)):
            out.append(cur)
            cur = []
    return out


# ---- ASS writing ----------------------------------------------------------

def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(int(round(t * 100)), 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def clean_text(w: dict, upper: bool) -> str:
    t = w["text"].rstrip(",;:.")
    t = t.upper() if upper else t
    return t.replace("{", "(").replace("}", ")")


# System fonts the presets can use (all ship with Windows). "sans"/"serif" come from the brand kit.
SYSTEM_FONTS = {
    "heavy": {"family": "Arial Black", "bold": False, "file": f"{FONTS_DIR}/ariblk.ttf"},
    "black": {"family": "Segoe UI Black", "bold": False, "file": f"{FONTS_DIR}/seguibl.ttf"},
    "impact": {"family": "Impact", "bold": False, "file": f"{FONTS_DIR}/impact.ttf"},
    "semibold": {"family": "Segoe UI Semibold", "bold": False, "file": f"{FONTS_DIR}/seguisb.ttf"},
    "mono": {"family": "Consolas", "bold": True, "file": f"{FONTS_DIR}/consolab.ttf"},
}

# Caption presets, modelled on the styles short-form tools ship most (Submagic, Opus, CapCut).
# Colours: a brand colour name (text, accent, primary, pill_text, outline) or "#RRGGBB".
#   size: multiplier on the caption size     outline: (colour, width as fraction of size)
#   shadow: fraction of size                 plate: (colour, alpha) = box behind the line
#   box: "pill" behind the cue               active: how the word being spoken is shown
#   anim: pop | fade | none | type           emph: colour for popped phrases
PRESETS: dict[str, dict] = {
    "clean": dict(label="Clean", blurb="White bold text, dark outline. Safe everywhere.",
                  font="sans", size=1.0, max_words=3, fill="text", outline=("outline", 0.09),
                  shadow=0.03, anim="pop"),
    "karaoke": dict(label="Karaoke", blurb="Each word lights up as it's spoken.",
                    font="sans", size=1.0, max_words=3, fill="text", outline=("outline", 0.09),
                    shadow=0.03, active=dict(color="accent"), anim="pop"),
    "pill": dict(label="Pill", blurb="Words on a rounded brand-colour box.",
                 font="sans", size=1.0, max_words=3, fill="pill_text", box="pill", anim="pop"),
    "hormozi": dict(label="Hormozi", blurb="Heavy caps, thick outline, spoken word in yellow, key words green.",
                    font="heavy", upper=True, size=1.15, max_words=3, fill="#FFFFFF",
                    outline=("#000000", 0.13), active=dict(color="#FFE135", scale=1.12),
                    emph="#3DDC5A", anim="pop"),
    "beast": dict(label="MrBeast", blurb="Huge Impact caps, 1-2 words, bouncing coloured words.",
                  font="impact", upper=True, size=1.4, max_words=2, fill="#FFFFFF",
                  outline=("#000000", 0.14), shadow=0.07, active=dict(color="#FFE135", bounce=True),
                  emph_cycle=["#FFE135", "#3DDC5A", "#FF4D4D", "#4DB8FF"], tilt=True, anim="pop"),
    "bold": dict(label="Bold", blurb="Extra-black white text with a soft shadow. Business-clean.",
                 font="black", size=1.12, max_words=3, fill="#FFFFFF", shadow=0.06,
                 shadow_alpha=0x60, anim="fade"),
    "highlight": dict(label="Highlight box", blurb="A brand-colour box jumps to each spoken word.",
                      font="sans", size=1.08, max_words=3, fill="#FFFFFF", outline=("#000000", 0.05),
                      active=dict(box="accent", color="#111111"), anim="pop"),
    "classic": dict(label="Classic", blurb="White on a dark bar, like TikTok's auto-captions.",
                    font="semibold", size=0.82, max_words=6, fill="#FFFFFF", plate=("#000000", 0x55),
                    anim="none"),
    "minimal": dict(label="Minimal", blurb="Small, light, no outline. Lets your face lead.",
                    font="semibold", size=0.78, max_words=5, fill="#FFFFFF", shadow=0.04,
                    shadow_alpha=0x70, blur=True, anim="fade"),
    "neon": dict(label="Neon", blurb="Glowing text in your accent colour.",
                 font="sans", size=1.05, max_words=3, fill="#FFFFFF", glow="accent", emph="#FFF6D6",
                 anim="pop"),
    "typewriter": dict(label="Typewriter", blurb="Monospace letters typed out as you speak.",
                       font="mono", size=0.85, max_words=5, fill="#FFFFFF", plate=("#000000", 0x40),
                       anim="type"),
    "one_word": dict(label="One word", blurb="One big word at a time. Maximum punch.",
                     font="black", upper=True, size=1.55, max_words=1, fill="#FFFFFF",
                     outline=("#000000", 0.09), shadow=0.05, anim="pop"),
    "stacked": dict(label="Stacked", blurb="Two lines, more words. Good for teaching.",
                    font="sans", size=0.82, max_words=8, lines=2, fill="#FFFFFF",
                    outline=("#000000", 0.08), active=dict(color="accent"), anim="fade"),
}
STYLES = tuple(PRESETS) + ("none",)


def style_list() -> list[dict]:
    """For UIs: id, label, one-line description of every style."""
    return [{"id": k, "label": p["label"], "blurb": p["blurb"]} for k, p in PRESETS.items()]


class Writer:
    def __init__(self, brand: dict, s: dict, W: int, H: int):
        self.b, self.s, self.W, self.H = brand, s, W, H
        self.p = PRESETS.get(s["style"], PRESETS["clean"])
        c = brand.get("colors", {})
        self.col = {k: c.get(k, d) for k, d in
                    {"primary": "#1F5C4A", "accent": "#F2C14E", "text": "#FFFFFF",
                     "pill_text": "#F6F1E7", "outline": "#000000"}.items()}
        f = brand.get("fonts", {})
        self.fonts = {
            "sans": f.get("sans") or {"family": "Segoe UI", "bold": True, "file": f"{FONTS_DIR}/segoeuib.ttf"},
            "serif": f.get("serif") or {"family": "Georgia", "bold": True, "italic": True,
                                        "file": f"{FONTS_DIR}/georgiaz.ttf"},
            **SYSTEM_FONTS,
        }
        self.font = self.fonts[self.p["font"]]
        unit = W / 1080 if H > W else H / 1080
        self.size = int(s["size"] * self.p.get("size", 1.0) * unit)
        self.upper = self.p["upper"] if "upper" in self.p else bool(s["uppercase"])
        self.y = int(H * POSITIONS.get(s["position"], 0.70))
        self.events: list[str] = []
        self.n_cue = 0

    def c(self, name_or_hex: str, alpha: int = 0) -> str:
        return ass_color(self.col.get(name_or_hex, name_or_hex), alpha)

    def header(self) -> str:
        p, font = self.p, self.font
        bold = -1 if font.get("bold") else 0
        italic = -1 if font.get("italic") else 0
        if p.get("plate"):
            # BorderStyle 3 = opaque box; libass paints it in OutlineColour, Outline = padding.
            pc, pa = p["plate"]
            border, outline_c, outline_w = 3, self.c(pc, pa), max(6, int(self.size * 0.22))
        else:
            oc, ow = p.get("outline") or ("outline", 0)
            border, outline_c, outline_w = 1, self.c(oc), round(self.size * ow, 1)
        shadow_w = round(self.size * p.get("shadow", 0), 1)
        back = self.c("#000000", p.get("shadow_alpha", 0x40))
        fill = self.c(p["fill"])
        pc = self.col["primary"]
        return "\n".join([
            "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {self.W}", f"PlayResY: {self.H}",
            "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Cap,{font['family']},{self.size},{fill},{fill},{outline_c},{back},{bold},{italic},0,0,"
            f"100,100,0,0,{border},{outline_w},{shadow_w},5,40,40,0,1",
            f"Style: Box,{font['family']},{self.size},{ass_color(pc, 0x10)},{ass_color(pc)},{ass_color(pc)},"
            f"{ass_color('#000000', 0xB0)},0,0,0,0,100,100,0,0,1,0,{max(2, self.size // 20)},5,0,0,0,1",
            "", "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ])

    # ---- per-word looks ----

    def emph_look(self, w: dict) -> tuple[str, str, float, dict]:
        """(open tags, close tags, scale, font) for a popped word."""
        if not w.get("emph"):
            return "", "", 1.0, self.font
        base = self.c(self.p["fill"])
        if self.s["emphasis_look"] == "serif":
            sf = self.fonts["serif"]
            return (f"{{\\fn{sf['family']}\\i1\\fscx115\\fscy115}}",
                    f"{{\\fn{self.font['family']}\\i0\\fscx100\\fscy100}}", 1.15, sf)
        cycle = self.p.get("emph_cycle")
        colour = cycle[(int(w["emph"]) - 1) % len(cycle)] if cycle else self.p.get("emph", "accent")
        return (f"{{\\c{self.c(colour)}\\fscx118\\fscy118}}",
                f"{{\\c{base}\\fscx100\\fscy100}}", 1.18, self.font)

    def word_text(self, w: dict, active: bool, since_ms: int = 0) -> str:
        pre, post, _, _ = self.emph_look(w)
        a = self.p.get("active") or {}
        if active and a and not a.get("box"):
            colour = self.c(a.get("color", "accent"))
            scale = int(100 * a.get("scale", 1.0))
            base = self.c(self.p["fill"])
            if a.get("bounce"):
                pre = f"{{\\c{colour}\\fscx128\\fscy128\\t(0,140,\\fscx108\\fscy108)}}"
                post = f"{{\\c{base}\\fscx100\\fscy100}}"
            elif not w.get("emph"):
                pre = f"{{\\c{colour}\\fscx{scale}\\fscy{scale}}}"
                post = f"{{\\c{base}\\fscx100\\fscy100}}"
        elif active and a.get("box"):
            # Box text colour goes last so it beats an emphasis colour (else the word vanishes).
            pre = pre + f"{{\\c{self.c(a.get('color', '#111111'))}\\bord0\\shad0}}"
            post = post + f"{{\\c{self.c(self.p['fill'])}\\bord{round(self.size * (self.p.get('outline') or ('', 0))[1], 1)}}}"
        return pre + clean_text(w, self.upper) + post

    def line(self, cue: list[dict], active: int | None = None) -> str:
        parts = [self.word_text(w, i == active) for i, w in enumerate(cue)]
        if self.p.get("lines", 1) == 2 and len(parts) > 3:
            half = (len(parts) + 1) // 2
            return " ".join(parts[:half]) + "\\N" + " ".join(parts[half:])
        return " ".join(parts)

    @staticmethod
    def _measure(font: dict, size: float, text: str) -> float:
        """Width of `text` as libass draws it. libass sizes a font so ascent+descent equals the
        font size, while PIL sizes by the em square, so scale PIL's width by size/(asc+desc)."""
        try:
            f = ImageFont.truetype(font.get("file", ""), 200)
            asc, desc = f.getmetrics()
            return f.getlength(text) * size / (asc + desc)
        except OSError:
            return len(text) * size * 0.5

    def widths(self, cue: list[dict]) -> tuple[list[float], float]:
        """Width of each word and of one space, in canvas pixels."""
        out = []
        for w in cue:
            _, _, scale, font = self.emph_look(w)
            out.append(self._measure(font, self.size * scale, clean_text(w, self.upper)))
        return out, self._measure(self.font, self.size, " ")

    def add(self, layer: int, a: float, b: float, style: str, text: str) -> None:
        self.events.append(f"Dialogue: {layer},{ts(a)},{ts(b)},{style},,0,0,0,,{text}")

    # ---- events ----

    def cue(self, cue: list[dict], end: float) -> None:
        self.n_cue += 1
        if self.p.get("active"):
            for i, w in enumerate(cue):
                a = cue[0]["start"] if i == 0 else w["start"]
                b = cue[i + 1]["start"] if i + 1 < len(cue) else end
                self.emit(cue, a, b, active=i, first=(i == 0))
        else:
            self.emit(cue, cue[0]["start"], end, active=None, first=True)

    def emit(self, cue: list[dict], a: float, b: float, active: int | None, first: bool) -> None:
        p = self.p
        pos = f"\\an5\\pos({self.W // 2},{self.y})"
        anim = ""
        if first and p["anim"] == "pop":
            anim = "\\fscx90\\fscy90\\t(0,90,\\fscx100\\fscy100)"
        elif first and p["anim"] == "fade":
            anim = "\\fad(120,0)"
        if p.get("tilt"):
            anim += f"\\frz{(-2.5, 2.0, -1.5, 2.5)[self.n_cue % 4]}"
        if p.get("blur"):
            anim += "\\be1"

        if p.get("box") == "pill":
            ws, sp = self.widths(cue)
            bw = sum(ws) + sp * (len(ws) - 1) + self.size * 0.9
            bh = self.size * 1.25 + self.size * 0.34
            r = min(bh / 2, self.size * 0.35)
            self.add(0, a, b, "Box", f"{{{pos}{anim}\\p1}}{rounded_rect(bw, bh, r)}{{\\p0}}")

        if active is not None and (p.get("active") or {}).get("box"):
            # Place every word ourselves so the box and its word share exact coordinates
            # (libass word spacing differs slightly from PIL's, which would misalign the box).
            ws, sp = self.widths(cue)
            sp += self.size * 0.12  # room for the box padding between words
            x = self.W / 2 - (sum(ws) + sp * (len(ws) - 1)) / 2
            colour = self.col.get(p["active"]["box"], p["active"]["box"])
            for j, w in enumerate(cue):
                cx = x + ws[j] / 2
                if j == active:
                    bw, bh = ws[j] + self.size * 0.36, self.size * 1.22
                    self.add(0, a, b, "Box", f"{{\\an5\\pos({cx:.0f},{self.y}){anim}\\1c{ass_color(colour)}"
                                             f"\\shad0\\p1}}{rounded_rect(bw, bh, self.size * 0.22)}{{\\p0}}")
                self.add(1, a, b, "Cap", f"{{\\an5\\pos({cx:.0f},{self.y}){anim}}}" + self.word_text(w, j == active))
                x += ws[j] + sp
            return

        if p.get("glow"):
            glow = self.c(p["glow"])
            self.add(0, a, b, "Cap", f"{{{pos}{anim}\\1a&HFF&\\3c{glow}\\bord{self.size * 0.16:.0f}"
                                     f"\\blur{self.size * 0.2:.0f}\\shad0}}" + self.line(cue, active))
            text = f"{{{pos}{anim}\\3c{glow}\\bord{max(1, self.size // 28)}\\shad0}}" + self.line(cue, active)
        elif p["anim"] == "type":
            text = f"{{{pos}}}" + self.typed(cue, a)
        else:
            text = f"{{{pos}{anim}}}" + self.line(cue, active)
        self.add(1, a, b, "Cap", text)

    def typed(self, cue: list[dict], a: float) -> str:
        """Each letter starts invisible and appears as its word is spoken."""
        out = []
        for i, w in enumerate(cue):
            t0 = max(0.0, w["start"] - a)
            t1 = max(t0 + 0.05, w["end"] - a)
            chars = clean_text(w, self.upper)
            for k, ch in enumerate(chars):
                ms = int(1000 * (t0 + (t1 - t0) * k / max(1, len(chars))))
                out.append(f"{{\\alpha&HFF&\\t({ms},{ms + 1},\\alpha&H00&)}}{ch}")
            if i < len(cue) - 1:
                out.append(" ")
        return "".join(out)

    def text(self) -> str:
        return self.header() + "\n" + "\n".join(self.events) + "\n"


def rounded_rect(w: float, h: float, r: float) -> str:
    k = r * 0.4477  # bezier handle for a quarter circle
    return (f"m {r:.0f} 0 l {w - r:.0f} 0 b {w - k:.0f} 0 {w:.0f} {k:.0f} {w:.0f} {r:.0f} "
            f"l {w:.0f} {h - r:.0f} b {w:.0f} {h - k:.0f} {w - k:.0f} {h:.0f} {w - r:.0f} {h:.0f} "
            f"l {r:.0f} {h:.0f} b {k:.0f} {h:.0f} 0 {h - k:.0f} 0 {h - r:.0f} "
            f"l 0 {r:.0f} b 0 {k:.0f} {k:.0f} 0 {r:.0f} 0")


def words_per_cue(plan: dict, s: dict) -> int:
    """The style's own count, unless this plan sets max_words explicitly."""
    explicit = (plan.get("captions") or {}).get("max_words")
    return int(explicit or PRESETS.get(s["style"], PRESETS["clean"])["max_words"])


def build_ass(plan: dict, edit_dir: Path, out_path: Path, style: str | None = None,
              words: list[dict] | None = None) -> int:
    """Write captions for the plan (or for `words`, already on the output timeline). Returns cue count."""
    brand = load_brand(edit_dir)
    s = settings(plan, brand, style)
    W, H = canvas(plan, edit_dir)
    wr = Writer(brand, s, W, H)
    if s["style"] != "none":
        if words is None:
            words = output_words(plan, edit_dir, s.get("corrections") or [])
        mark_emphasis(words, s["emphasis"])
        cues = chunk(words, words_per_cue(plan, s))
        for i, c in enumerate(cues):
            nxt = cues[i + 1][0]["start"] if i + 1 < len(cues) else c[-1]["end"] + 0.3
            # Hold a cue until the next one unless there's a real pause.
            end = nxt if nxt - c[-1]["end"] < 0.6 else c[-1]["end"] + 0.25
            wr.cue(c, end)
    out_path.write_text(wr.text(), encoding="utf-8")
    return len(wr.events)


def subtitles_filter(ass_path: Path) -> str:
    """ffmpeg filter that burns an .ass file, escaped for Windows paths."""
    p = str(ass_path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
    fd = FONTS_DIR.replace(":", r"\:")
    return f"subtitles='{p}':fontsdir='{fd}'"


def sample_frame(plan: dict, edit_dir: Path, style: str, out_jpg: Path, height: int = 960) -> Path:
    """One real frame from the first section with its first caption cue burned in."""
    s = settings(plan, load_brand(edit_dir), style)
    words = output_words(plan, edit_dir, s.get("corrections") or [])
    mark_emphasis(words, s["emphasis"])
    cues = chunk(words, words_per_cue(plan, s))
    # Show a multi-word caption, ideally one with a popped word so the whole look shows.
    first = next((c for c in cues if len(c) >= 2 and any(w.get("emph") for w in c)),
                 next((c for c in cues if len(c) >= 2), cues[0] if cues else []))
    t0 = first[0]["start"] if first else 0.0
    cue = [dict(w, start=w["start"] - t0 + 0.05, end=w["end"] - t0 + 0.05) for w in first]
    # Map the cue's output time back to its source frame.
    offset, src, src_t = 0.0, plan["ranges"][0]["source"], float(plan["ranges"][0]["start"])
    for r in plan["ranges"]:
        d = float(r["end"]) - float(r["start"])
        if offset <= t0 < offset + d:
            src, src_t = r["source"], float(r["start"]) + (t0 - offset)
            break
        offset += d
    ass = out_jpg.with_suffix(".ass")
    build_ass(plan, edit_dir, ass, style, words=[dict(w, emph=w.get("emph")) for w in cue])
    W, H = canvas(plan, edit_dir)
    src_path = Path(plan["sources"][src])
    src_path = src_path if src_path.is_absolute() else edit_dir / src_path
    # Seek to just after the cue's pop-in finishes so the still shows the settled look.
    hold = min(0.4, (cue[-1]["end"] - 0.05) if cue else 0.4)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{src_t:.3f}", "-i", str(src_path),
                    "-vf", f"scale={W}:{H},setpts=PTS-STARTPTS,{subtitles_filter(ass)},scale=-2:{height}",
                    "-ss", f"{hold:.2f}", "-frames:v", "1", str(out_jpg)], check=True)
    ass.unlink(missing_ok=True)
    return out_jpg


def main() -> None:
    ap = argparse.ArgumentParser(description="Build styled captions (.ass) for a plan")
    ap.add_argument("plan", type=Path)
    ap.add_argument("--style", choices=STYLES, default=None)
    ap.add_argument("-o", "--output", type=Path, default=None)
    ap.add_argument("--sample", type=Path, default=None,
                    help="instead: write a still frame (.jpg) showing the style on real footage")
    args = ap.parse_args()
    plan_path = args.plan.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if args.sample:
        style = args.style or settings(plan, load_brand(plan_path.parent))["style"]
        print(f"sample -> {sample_frame(plan, plan_path.parent, style, args.sample.resolve())}")
        return
    out = args.output or plan_path.parent / "captions.ass"
    n = build_ass(plan, plan_path.parent, out, args.style)
    print(f"captions -> {out} ({n} events)")


if __name__ == "__main__":
    main()
