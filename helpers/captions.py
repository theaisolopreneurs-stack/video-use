"""Styled captions: plan.json + transcripts + brand.json -> <edit>/captions.ass.

Caption settings come from the brand kit and can be overridden per project in
plan.json "captions":

    "captions": {
      "style": "pill",              # clean | karaoke | pill | none
      "position": "lower",          # lower (clear of Reels/TikTok UI) | middle | upper
      "max_words": 3,
      "uppercase": false,
      "size": 72,                   # px at 1080-wide portrait / 1080-tall landscape
      "emphasis": ["0 and 4"],      # phrases to make pop
      "emphasis_look": "accent",    # accent = bigger + accent colour | serif = italic serif
      "corrections": [{"from": "drive", "to": "derive", "source": "clip", "at": 24.6}]
    }

Styles:
  clean    white bold text, dark outline
  karaoke  clean, with the word being spoken in the accent colour
  pill     1-3 words on a rounded box in the brand's primary colour

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
STYLES = ("clean", "karaoke", "pill", "none")
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


class Writer:
    def __init__(self, brand: dict, s: dict, W: int, H: int):
        self.b, self.s, self.W, self.H = brand, s, W, H
        c = brand.get("colors", {})
        self.col = {k: c.get(k, d) for k, d in
                    {"primary": "#1F5C4A", "accent": "#F2C14E", "text": "#FFFFFF",
                     "pill_text": "#F6F1E7", "outline": "#000000"}.items()}
        f = brand.get("fonts", {})
        self.sans = f.get("sans") or {"family": "Segoe UI", "bold": True, "file": f"{FONTS_DIR}/segoeuib.ttf"}
        self.serif = f.get("serif") or {"family": "Georgia", "bold": True, "italic": True, "file": f"{FONTS_DIR}/georgiaz.ttf"}
        self.size = int(s["size"] * (W / 1080 if H > W else H / 1080))
        self.y = int(H * POSITIONS.get(s["position"], 0.70))
        self.events: list[str] = []

    def header(self) -> str:
        sans, col = self.sans, self.col
        bold = -1 if sans.get("bold") else 0
        return "\n".join([
            "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {self.W}", f"PlayResY: {self.H}",
            "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Clean,{sans['family']},{self.size},{ass_color(col['text'])},{ass_color(col['accent'])},"
            f"{ass_color(col['outline'])},{ass_color(col['outline'], 0x80)},{bold},0,0,0,100,100,0,0,1,"
            f"{max(3, self.size // 12)},{max(1, self.size // 30)},5,40,40,0,1",
            f"Style: PillText,{sans['family']},{self.size},{ass_color(col['pill_text'])},{ass_color(col['accent'])},"
            f"{ass_color(col['primary'])},{ass_color(col['primary'])},{bold},0,0,0,100,100,0,0,1,0,0,5,40,40,0,1",
            f"Style: PillBox,{sans['family']},{self.size},{ass_color(col['primary'], 0x10)},{ass_color(col['primary'])},"
            f"{ass_color(col['primary'])},{ass_color('#000000', 0xB0)},0,0,0,0,100,100,0,0,1,0,"
            f"{max(2, self.size // 20)},5,0,0,0,1",
            "", "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ])

    # Emphasis is either bigger+accent colour, or a switch to italic serif.
    def run_tags(self, w: dict, base_color: str) -> tuple[str, str, float, dict]:
        if not w.get("emph"):
            return "", "", 1.0, self.sans
        if self.s["emphasis_look"] == "serif":
            return (f"{{\\fn{self.serif['family']}\\i1\\fscx115\\fscy115}}",
                    f"{{\\fn{self.sans['family']}\\i0\\fscx100\\fscy100}}", 1.15, self.serif)
        return (f"{{\\c{ass_color(self.col['accent'])}\\fscx120\\fscy120}}",
                f"{{\\c{base_color}\\fscx100\\fscy100}}", 1.2, self.sans)

    def line(self, cue: list[dict], base_color: str, active: int | None = None) -> str:
        parts = []
        for i, w in enumerate(cue):
            pre, post, _, _ = self.run_tags(w, base_color)
            if active is not None and i == active and not w.get("emph"):
                pre, post = f"{{\\c{ass_color(self.col['accent'])}}}", f"{{\\c{base_color}}}"
            parts.append(pre + clean_text(w, self.s["uppercase"]) + post)
        return " ".join(parts)

    def text_width(self, cue: list[dict]) -> float:
        total = 0.0
        for i, w in enumerate(cue):
            _, _, scale, font = self.run_tags(w, "")
            try:
                f = ImageFont.truetype(font.get("file", ""), max(8, int(self.size * scale)))
            except OSError:
                f = ImageFont.load_default(size=max(8, int(self.size * scale)))
            total += f.getlength(clean_text(w, self.s["uppercase"]) + (" " if i < len(cue) - 1 else ""))
        return total

    def add(self, layer: int, a: float, b: float, style: str, text: str) -> None:
        self.events.append(f"Dialogue: {layer},{ts(a)},{ts(b)},{style},,0,0,0,,{text}")

    def cue(self, cue: list[dict], end: float) -> None:
        a = cue[0]["start"]
        pop = "\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)"
        pos = f"\\an5\\pos({self.W // 2},{self.y})"
        style = self.s["style"]
        if style == "clean":
            self.add(1, a, end, "Clean", f"{{{pos}{pop}}}" + self.line(cue, ass_color(self.col["text"])))
        elif style == "karaoke":
            for i, w in enumerate(cue):
                b = cue[i + 1]["start"] if i + 1 < len(cue) else end
                self.add(1, w["start"], b, "Clean",
                         f"{{{pos}{pop if i == 0 else ''}}}" + self.line(cue, ass_color(self.col["text"]), active=i))
        elif style == "pill":
            tw = self.text_width(cue)
            pad_x, pad_y = self.size * 0.45, self.size * 0.28
            bw, bh = tw + 2 * pad_x, self.size * 1.25 + 2 * pad_y * 0.6
            r = min(bh / 2, self.size * 0.35)
            # \an5 centres the drawing on \pos, so the pop scales from the middle like the text.
            self.add(0, a, end, "PillBox", f"{{{pos}{pop}\\p1}}{rounded_rect(bw, bh, r)}{{\\p0}}")
            self.add(1, a, end, "PillText", f"{{{pos}{pop}}}" + self.line(cue, ass_color(self.col["pill_text"])))

    def text(self) -> str:
        return self.header() + "\n" + "\n".join(self.events) + "\n"


def rounded_rect(w: float, h: float, r: float) -> str:
    k = r * 0.4477  # bezier handle for a quarter circle
    return (f"m {r:.0f} 0 l {w - r:.0f} 0 b {w - k:.0f} 0 {w:.0f} {k:.0f} {w:.0f} {r:.0f} "
            f"l {w:.0f} {h - r:.0f} b {w:.0f} {h - k:.0f} {w - k:.0f} {h:.0f} {w - r:.0f} {h:.0f} "
            f"l {r:.0f} {h:.0f} b {k:.0f} {h:.0f} 0 {h - k:.0f} 0 {h - r:.0f} "
            f"l 0 {r:.0f} b 0 {k:.0f} {k:.0f} 0 {r:.0f} 0")


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
        cues = chunk(words, int(s["max_words"]))
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
    cues = chunk(words, int(s["max_words"]))
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
