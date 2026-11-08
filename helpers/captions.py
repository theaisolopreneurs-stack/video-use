"""Styled captions: plan.json + transcripts + brand.json -> <edit>/captions.ass.

Caption settings come from the brand kit and can be overridden per project in
plan.json "captions":

    "captions": {
      "style": "pill",              # see PRESETS: clean, karaoke, pill, hormozi, beast, bold,
                                    # highlight, classic, minimal, neon, typewriter, one_word, stacked, none
      "position": "best",           # best (picked from where the face is) | top | middle | bottom,
                                    # or an exact spot {"x": 0.5, "y": 0.3}
      "emoji": true,                # matching emoji above the words (stickers.py)
      "max_words": 3,               # optional: overrides the style's own words-per-caption
      "uppercase": false,
      "size": 72,                   # px at 1080-wide portrait / 1080-tall landscape
      "emphasis": ["0 and 4"],      # phrases to make pop
      "emphasis_look": "accent",    # accent = bigger + accent colour | serif = italic serif
      "corrections": [{"from": "drive", "to": "derive", "source": "clip", "at": 24.6}],
      "text_edits": [{"source": "clip", "start": 12.3, "end": 15.1, "text": "and now, the Texans are 0-4!"}]
                                    # a line retyped in the Studio; times are seconds in the source clip
    }

Styles: PRESETS below, modelled on the caption looks short-form tools ship most: word-by-word
highlight, boxes and markers, clean and editorial, neon glow, retro 3D and glitch, handwritten,
and animated entrances. Free text anywhere on screen goes in plan "texts" (see add_texts).

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
from difflib import SequenceMatcher
from pathlib import Path

from PIL import ImageFont

REPO = Path(__file__).resolve().parent.parent
FONTS_DIR = "C:/Windows/Fonts"
POSITIONS = {"lower": 0.70, "bottom": 0.70, "middle": 0.50, "center": 0.50, "upper": 0.25, "top": 0.25}


def caption_xy(position, W: int, H: int) -> tuple[float, float]:
    """Caption centre as fractions of the frame.

    position: "top" | "middle" | "bottom" (aliases upper/center/lower), or an exact spot as
    {"x": 0.5, "y": 0.3} / [0.5, 0.3] / "0.5,0.3" (0-1 across and down). Named spots respect
    the platform UI: in 9:16 "bottom" sits at 70% (clear of the app's caption area); square
    and wide frames have no app UI, so it drops to 86%.
    """
    if isinstance(position, dict):
        return float(position.get("x", 0.5)), float(position.get("y", 0.7))
    if isinstance(position, (list, tuple)) and len(position) == 2:
        return float(position[0]), float(position[1])
    if isinstance(position, str) and "," in position:
        x, y = position.split(",", 1)
        return float(x), float(y)
    fy = POSITIONS.get(str(position), 0.70)
    if W / H > 0.9 and str(position) in ("lower", "bottom"):
        fy = 0.86
    return 0.5, fy
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
    """Output canvas: the plan's target aspect if set, else render.py's default
    (portrait -> 1080x1920, landscape -> 1920x1080)."""
    import framing
    size = framing.output_size((plan.get("target") or {}).get("aspect"))
    if size:
        return size
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


def _display(w: dict) -> str:
    """A word as captions show it before styling: trailing , ; : . are dropped unless typed in."""
    return w["text"].strip() if w.get("typed") else w["text"].strip().rstrip(",;:.")


def apply_text_edits(words: list[dict], edits: list[dict], source: str) -> list[dict]:
    """Retyped caption lines, onto a source's timed words. Words the person didn't change keep their
    exact timing; changed words share the time of the ones they replace, so highlighting stays in
    step with the voice. Each changed word is marked "typed", so its punctuation shows as typed."""
    for e in edits or []:
        if e.get("source") != source:
            continue
        a, b = float(e["start"]), float(e["end"])
        inside = [i for i, w in enumerate(words) if a <= (w["start"] + w["end"]) / 2 <= b]
        if not inside:
            continue
        old = [words[i] for i in inside]
        new_tokens = str(e.get("text") or "").split()
        rebuilt: list[dict] = []
        ops = SequenceMatcher(None, [_display(w) for w in old], new_tokens, autojunk=False).get_opcodes()
        for tag, i0, i1, j0, j1 in ops:
            if tag == "equal":
                rebuilt += old[i0:i1]
            elif j1 > j0:  # replaced or added
                toks = new_tokens[j0:j1]
                if i1 > i0:
                    t0, t1 = old[i0]["start"], old[i1 - 1]["end"]
                else:  # added: no time of its own, so it borrows from a neighbouring word below
                    t0, t1 = None, None
                total = sum(len(x) for x in toks) or 1
                run = 0
                for tok in toks:
                    w = {"text": tok, "typed": True, "type": "word", "start": None, "end": None}
                    if t0 is not None:
                        w["start"] = t0 + (t1 - t0) * run / total
                        run += len(tok)
                        w["end"] = t0 + (t1 - t0) * run / total
                    rebuilt.append(w)
        for i, w in enumerate(rebuilt):  # give added words time from the word before (or after)
            if w["start"] is not None:
                continue
            j = i
            while j < len(rebuilt) and rebuilt[j]["start"] is None:
                j += 1
            n = j - i
            if i > 0:
                prev = rebuilt[i - 1] = dict(rebuilt[i - 1])
                mid = (prev["start"] + prev["end"]) / 2
                span = (mid, prev["end"])
                prev["end"] = mid
            elif j < len(rebuilt):
                nxt = rebuilt[j] = dict(rebuilt[j])
                mid = (nxt["start"] + nxt["end"]) / 2
                span = (nxt["start"], mid)
                nxt["start"] = mid
            else:
                span = (a, b)
            for k in range(n):
                rebuilt[i + k]["start"] = span[0] + (span[1] - span[0]) * k / n
                rebuilt[i + k]["end"] = span[0] + (span[1] - span[0]) * (k + 1) / n
        words = words[:inside[0]] + rebuilt + words[inside[-1] + 1:]
    return words


def _source_words(plan: dict, edit_dir: Path, source: str, edited: bool = True) -> list[dict]:
    tp = edit_dir / "transcripts" / f"{source}.json"
    words = [w for w in json.loads(tp.read_text(encoding="utf-8")).get("words", [])
             if w.get("type") == "word"] if tp.exists() else []
    return apply_text_edits(words, (plan.get("captions") or {}).get("text_edits"), source) if edited else words


def _corrected(w: dict, source: str, corrections: list[dict]) -> str:
    text = w["text"].strip()
    for c in corrections:
        if norm(text) != norm(c["from"]):
            continue
        if c.get("source") not in (None, source):
            continue
        if c.get("at") is not None and abs(float(c["at"]) - w["start"]) > 1.0:
            continue
        trail = re.search(r"[^\w']+$", text)
        text = c["to"] + (trail.group(0) if trail else "")
    return text


def caption_lines(plan: dict, edit_dir: Path, max_words: int = 12) -> list[dict]:
    """The captions as lines of text for the Studio to edit. Lines come from the original transcript,
    so a line stays the same stretch of time whatever is typed into it."""
    from plan import play_pos, range_len
    corrections = (plan.get("captions") or {}).get("corrections") or []
    edits = (plan.get("captions") or {}).get("text_edits") or []
    raw_cache: dict[str, list[dict]] = {}
    new_cache: dict[str, list[dict]] = {}
    lines: list[dict] = []
    offset = 0.0
    for r in plan.get("ranges") or []:
        src, s, e = r["source"], float(r["start"]), float(r["end"])
        if not r.get("reverse"):
            if src not in raw_cache:
                raw_cache[src] = _source_words(plan, edit_dir, src, edited=False)
                new_cache[src] = _source_words(plan, edit_dir, src)
            kept = [w for w in raw_cache[src] if w["end"] > s and w["start"] < e]
            cur: list[dict] = []
            for i, w in enumerate(kept):
                cur.append(w)
                nxt = kept[i + 1] if i + 1 < len(kept) else None
                end_mark = w["text"].strip()[-1:]
                if (nxt is None or end_mark in ".?!" or nxt["start"] - w["end"] >= 0.6 or len(cur) >= max_words
                        or (end_mark in ",;:" and len(cur) >= 4)):
                    a, b = cur[0]["start"], cur[-1]["end"]
                    shown = [dict(x, text=_corrected(x, src, corrections)) for x in new_cache[src]
                             if a <= (x["start"] + x["end"]) / 2 <= b]
                    lines.append({"source": src, "start": round(a, 3), "end": round(b, 3),
                                  "at": round(play_pos(r, max(a, s)) + offset, 2),
                                  "text": " ".join(_display(x) for x in shown),
                                  "edited": any(x.get("source") == src and float(x["start"]) == round(a, 3)
                                                and float(x["end"]) == round(b, 3) for x in edits)})
                    cur = []
        offset += range_len(r)
    return lines


def output_words(plan: dict, edit_dir: Path, corrections: list[dict]) -> list[dict]:
    """Every kept word, re-timed onto the output timeline, with corrections applied.
    Speed-changed sections scale word times; reversed sections get no captions."""
    from plan import play_pos, range_len
    words: list[dict] = []
    offset = 0.0
    cache: dict[str, list[dict]] = {}
    for r in plan["ranges"]:
        src, s, e = r["source"], float(r["start"]), float(r["end"])
        speed = float(r.get("speed") or 1.0)
        if r.get("reverse"):
            offset += range_len(r)
            continue
        if src not in cache:
            cache[src] = _source_words(plan, edit_dir, src)
        for w in cache[src]:
            if w["end"] <= s or w["start"] >= e:
                continue
            text = _corrected(w, src, corrections)
            words.append({"text": text, "typed": w.get("typed"),
                          "start": play_pos(r, max(w["start"], s)) + offset,
                          "end": play_pos(r, min(w["end"], e)) + offset})
        offset += range_len(r)
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


def clean_text(w: dict, upper: bool, lower: bool = False) -> str:
    t = w["text"] if w.get("typed") else w["text"].rstrip(",;:.")  # typed punctuation is deliberate
    t = t.upper() if upper else (t.lower() if lower else t)
    t = t.replace("\\", "/")
    return t.replace("{", "(").replace("}", ")")


# System fonts the presets can use (all ship with Windows). "sans"/"serif" come from the brand kit.
SYSTEM_FONTS = {
    "heavy": {"family": "Arial Black", "bold": False, "file": f"{FONTS_DIR}/ariblk.ttf"},
    "black": {"family": "Segoe UI Black", "bold": False, "file": f"{FONTS_DIR}/seguibl.ttf"},
    "impact": {"family": "Impact", "bold": False, "file": f"{FONTS_DIR}/impact.ttf"},
    "semibold": {"family": "Segoe UI Semibold", "bold": False, "file": f"{FONTS_DIR}/seguisb.ttf"},
    "mono": {"family": "Consolas", "bold": True, "file": f"{FONTS_DIR}/consolab.ttf"},
    "franklin": {"family": "Franklin Gothic Medium", "bold": False, "file": f"{FONTS_DIR}/framd.ttf"},
    "bahn": {"family": "Bahnschrift", "bold": True, "file": f"{FONTS_DIR}/bahnschrift.ttf"},
    "narrow": {"family": "Arial Narrow", "bold": True, "file": f"{FONTS_DIR}/ARIALNB.TTF"},
    "tahoma": {"family": "Tahoma", "bold": True, "file": f"{FONTS_DIR}/tahomabd.ttf"},
    "georgia_i": {"family": "Georgia", "bold": True, "italic": True, "file": f"{FONTS_DIR}/georgiaz.ttf"},
    "times": {"family": "Times New Roman", "bold": True, "file": f"{FONTS_DIR}/timesbd.ttf"},
    "constantia": {"family": "Constantia", "bold": True, "file": f"{FONTS_DIR}/constanb.ttf"},
    "palatino": {"family": "Palatino Linotype", "bold": True, "file": f"{FONTS_DIR}/palab.ttf"},
    "courier": {"family": "Courier New", "bold": True, "file": f"{FONTS_DIR}/courbd.ttf"},
    "script": {"family": "Segoe Script", "bold": True, "file": f"{FONTS_DIR}/segoescb.ttf"},
    "print": {"family": "Segoe Print", "bold": True, "file": f"{FONTS_DIR}/segoeprb.ttf"},
    "ink": {"family": "Ink Free", "bold": False, "file": f"{FONTS_DIR}/Inkfree.ttf"},
    "comic": {"family": "Comic Sans MS", "bold": True, "file": f"{FONTS_DIR}/comicbd.ttf"},
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

# ---- the wider library -------------------------------------------------------------------
# Extra keys used below:
#   group: section in the Studio's picker         hollow: outline only, no fill
#   outline2: (colour, width) second outer ring   extrude: (colour, steps) stacked 3D edge
#   ghost: [(colour, dx, dy)...] offset copies    reveal: words appear only as they're spoken
#   progressive: spoken words stay lit            word_cycle: a colour per word
#   last_big: scale of the cue's last word        spacing: letter spacing (fraction of size)
#   shadow_xy: (x, y) hard offset shadow          shadow_color: its colour
#   box_color / box_radius / box_alpha / box_cycle: the box behind the cue
#   active: {color, scale, bounce, box, radius, underline, cycle, solid}
RAINBOW = ["#FF4D4D", "#FFB02E", "#FFE135", "#3DDC5A", "#4DB8FF", "#B57BFF"]
PRESETS.update({
    # --- viral / word-by-word ---
    "viral": dict(label="Viral", group="Viral", blurb="Heavy caps, 2-3 words, spoken word pops yellow, key words green.",
                  font="black", upper=True, size=1.22, max_words=3, fill="#FFFFFF", outline=("#000000", 0.13),
                  shadow=0.05, active=dict(color="#FFE135", scale=1.1, bounce=True), emph="#3DDC5A", anim="pop"),
    "viral_green": dict(label="Green pop", group="Viral", blurb="Spoken word in bright green. A short-form staple.",
                        font="heavy", upper=True, size=1.1, max_words=3, fill="#FFFFFF", outline=("#000000", 0.12),
                        active=dict(color="#39FF6A", scale=1.12), emph="#FFE135", anim="pop"),
    "viral_red": dict(label="Red alert", group="Viral", blurb="Spoken word in red. Urgent, news-like energy.",
                      font="heavy", upper=True, size=1.1, max_words=3, fill="#FFFFFF", outline=("#000000", 0.12),
                      active=dict(color="#FF3B3B", scale=1.12), emph="#FFE135", anim="pop"),
    "viral_cyan": dict(label="Electric blue", group="Viral", blurb="Spoken word in electric blue.",
                       font="heavy", upper=True, size=1.1, max_words=3, fill="#FFFFFF", outline=("#000000", 0.12),
                       active=dict(color="#22D3FF", scale=1.12), emph="#FFE135", anim="pop"),
    "viral_purple": dict(label="Purple pop", group="Viral", blurb="Spoken word in vivid purple.",
                         font="heavy", upper=True, size=1.1, max_words=3, fill="#FFFFFF", outline=("#000000", 0.12),
                         active=dict(color="#C77DFF", scale=1.12), emph="#FFE135", anim="pop"),
    "impact_yellow": dict(label="Yellow Impact", group="Viral", blurb="Solid yellow Impact caps with a black edge.",
                          font="impact", upper=True, size=1.35, max_words=3, fill="#FFE135", outline=("#000000", 0.12),
                          active=dict(color="#FFFFFF", scale=1.08), emph="#FF4D4D", anim="pop"),
    "stamp": dict(label="Stamp", group="Viral", blurb="Each caption slams in from huge.",
                  font="impact", upper=True, size=1.35, max_words=2, fill="#FFFFFF", outline=("#000000", 0.12),
                  emph="#FFE135", anim="stamp"),
    "reveal": dict(label="Word reveal", group="Viral", blurb="Words appear only as they're spoken.",
                   font="black", upper=True, size=1.12, max_words=4, fill="#FFFFFF", outline=("#000000", 0.11),
                   reveal=True, active=dict(color="#FFFFFF"), emph="#FFE135", anim="none"),
    "reveal_soft": dict(label="Soft reveal", group="Viral", blurb="Sentence-case words appear as they're spoken.",
                        font="sans", size=0.95, max_words=6, lines=2, fill="#FFFFFF", shadow=0.06, shadow_alpha=0x50,
                        reveal=True, active=dict(color="#FFFFFF"), anim="none"),
    "spotlight": dict(label="Spotlight", group="Viral", blurb="Grey text; only the spoken word lights up white.",
                      font="black", upper=True, size=1.08, max_words=4, fill="#9A9A9A", outline=("#000000", 0.08),
                      active=dict(color="#FFFFFF", scale=1.08), anim="pop"),
    "progress_fill": dict(label="Fill as spoken", group="Viral", blurb="Grey words fill with colour as you say them.",
                          font="black", upper=True, size=1.05, max_words=4, fill="#A8A8A8", outline=("#000000", 0.08),
                          progressive=True, active=dict(color="accent"), anim="pop"),
    "big_finish": dict(label="Big finish", group="Viral", blurb="The last word of each caption lands bigger.",
                       font="black", upper=True, size=1.05, max_words=3, fill="#FFFFFF", outline=("#000000", 0.11),
                       last_big=1.3, emph="#FFE135", anim="pop"),

    # --- boxes ---
    "box_white": dict(label="White box", group="Boxes", blurb="Black text on a white rounded box.",
                      font="sans", size=0.95, max_words=4, fill="#111111", box="pill", box_color="#FFFFFF", anim="pop"),
    "box_black": dict(label="Black box", group="Boxes", blurb="White text on a solid black box.",
                      font="sans", size=0.95, max_words=4, fill="#FFFFFF", box="pill", box_color="#000000", anim="pop"),
    "box_yellow": dict(label="Yellow box", group="Boxes", blurb="Black on bright yellow. Impossible to miss.",
                       font="heavy", upper=True, size=0.9, max_words=3, fill="#111111", box="pill",
                       box_color="#FFE135", box_radius=0.12, anim="pop"),
    "box_red": dict(label="Red box", group="Boxes", blurb="White on red, breaking-news style.",
                    font="heavy", upper=True, size=0.9, max_words=3, fill="#FFFFFF", box="pill",
                    box_color="#E5242A", box_radius=0.12, anim="pop"),
    "box_glass": dict(label="Glass", group="Boxes", blurb="White text on a see-through dark panel.",
                      font="semibold", size=0.92, max_words=5, fill="#FFFFFF", box="pill", box_color="#000000",
                      box_alpha=0x70, anim="fade"),
    "box_cycle": dict(label="Colour boxes", group="Boxes", blurb="The box changes colour with every caption.",
                      font="heavy", upper=True, size=0.9, max_words=3, fill="#FFFFFF", box="pill",
                      box_cycle=["#E5242A", "#2A7BE5", "#1FA85A", "#8B3FD9", "#E58A2A"], box_radius=0.2, anim="pop"),
    "tape": dict(label="Tape", group="Boxes", blurb="A tilted strip of tape behind the words.",
                 font="heavy", upper=True, size=0.88, max_words=3, fill="#111111", box="pill", box_color="#FFE9A8",
                 box_radius=0.02, tilt=True, anim="stamp"),
    "label_maker": dict(label="Label maker", group="Boxes", blurb="Sharp black strip, typewriter letters.",
                        font="courier", upper=True, size=0.82, max_words=4, fill="#FFFFFF", box="pill",
                        box_color="#111111", box_radius=0.0, anim="none"),
    "highlighter": dict(label="Highlighter", group="Boxes", blurb="A yellow marker swipes each spoken word.",
                        font="sans", size=1.05, max_words=3, fill="#FFFFFF", outline=("#000000", 0.05),
                        active=dict(box="#FFE135", color="#111111", radius=0.06), anim="none"),
    "highlight_red": dict(label="Red marker", group="Boxes", blurb="A red box jumps to each spoken word.",
                          font="heavy", upper=True, size=0.98, max_words=3, fill="#FFFFFF", outline=("#000000", 0.08),
                          active=dict(box="#E5242A", color="#FFFFFF"), anim="pop"),
    "highlight_green": dict(label="Green marker", group="Boxes", blurb="A green box jumps to each spoken word.",
                            font="heavy", upper=True, size=0.98, max_words=3, fill="#FFFFFF", outline=("#000000", 0.08),
                            active=dict(box="#1FBF5F", color="#FFFFFF"), anim="pop"),
    "highlight_pill": dict(label="Round marker", group="Boxes", blurb="A soft round pill follows the spoken word.",
                           font="sans", size=1.02, max_words=3, fill="#FFFFFF", outline=("#000000", 0.05),
                           active=dict(box="#7C5CFF", color="#FFFFFF", radius=0.5), anim="none"),
    "underline": dict(label="Underline", group="Boxes", blurb="A bar slides under each spoken word.",
                      font="sans", size=1.02, max_words=3, fill="#FFFFFF", shadow=0.05, shadow_alpha=0x50,
                      active=dict(underline="accent", color="#FFFFFF"), anim="none"),

    # --- clean / premium ---
    "clean_shadow": dict(label="Soft shadow", group="Clean", blurb="Plain white text with a soft shadow.",
                         font="semibold", size=0.92, max_words=4, fill="#FFFFFF", shadow=0.05, shadow_alpha=0x50,
                         blur=True, anim="fade"),
    "lowercase": dict(label="all lowercase", group="Clean", blurb="Casual, quiet, all lowercase.",
                      font="semibold", lower=True, size=0.92, max_words=4, fill="#FFFFFF", shadow=0.05,
                      shadow_alpha=0x50, anim="fade"),
    "wide": dict(label="Wide spaced", group="Clean", blurb="Thin capitals with airy letter spacing.",
                 font="semibold", upper=True, size=0.68, max_words=4, spacing=0.14, fill="#FFFFFF", shadow=0.05,
                 shadow_alpha=0x50, anim="fade"),
    "tech": dict(label="Tech", group="Clean", blurb="Squared-off modern letters.",
                 font="bahn", size=1.0, max_words=4, fill="#FFFFFF", outline=("#000000", 0.07), anim="slide_up"),
    "narrow": dict(label="Tall narrow", group="Clean", blurb="Tall condensed letters; fits more on a line.",
                   font="narrow", upper=True, size=1.25, max_words=5, fill="#FFFFFF", outline=("#000000", 0.07), anim="fade"),
    "news": dict(label="News", group="Clean", blurb="Sober, like a TV news subtitle.",
                 font="franklin", size=0.9, max_words=6, fill="#FFFFFF", plate=("#10243F", 0x18), anim="none"),
    "subtitle": dict(label="Movie subtitle", group="Clean", blurb="Yellow-white cinema subtitles.",
                     font="tahoma", size=0.72, max_words=8, lines=2, fill="#FFF7B0", outline=("#000000", 0.08), anim="none"),

    # --- editorial ---
    "editorial": dict(label="Editorial", group="Editorial", blurb="Elegant italic serif, magazine style.",
                      font="georgia_i", size=1.05, max_words=4, fill="#FFFFFF", shadow=0.05, shadow_alpha=0x50, anim="fade"),
    "editorial_mix": dict(label="Serif pop", group="Editorial", blurb="Clean sans with key words in italic serif.",
                          font="sans", size=1.0, max_words=4, fill="#FFFFFF", shadow=0.05, shadow_alpha=0x50,
                          emph_serif=True, anim="fade"),
    "classic_serif": dict(label="Classic serif", group="Editorial", blurb="Timeless bold serif.",
                          font="times", size=1.1, max_words=4, fill="#FFFFFF", outline=("#000000", 0.06), anim="fade"),
    "luxury": dict(label="Luxury", group="Editorial", blurb="Gold spaced capitals on dark footage.",
                   font="constantia", upper=True, size=0.72, max_words=4, spacing=0.16, fill="#E9CF8E", shadow=0.05,
                   shadow_alpha=0x40, anim="fade"),
    "storybook": dict(label="Storybook", group="Editorial", blurb="Warm serif for narrated stories.",
                      font="palatino", size=0.95, max_words=6, lines=2, fill="#FFF3DC", shadow=0.05, shadow_alpha=0x50, anim="fade"),

    # --- glow ---
    "neon_pink": dict(label="Neon pink", group="Glow", blurb="Hot pink glowing tube.",
                      font="sans", size=1.05, max_words=3, fill="#FFFFFF", glow="#FF2BD6", emph="#FFF6D6", anim="pop"),
    "neon_blue": dict(label="Neon blue", group="Glow", blurb="Cold blue glowing tube.",
                      font="sans", size=1.05, max_words=3, fill="#FFFFFF", glow="#1EA7FF", emph="#FFF6D6", anim="pop"),
    "neon_green": dict(label="Neon green", group="Glow", blurb="Toxic green glow.",
                       font="sans", size=1.05, max_words=3, fill="#FFFFFF", glow="#2BFF88", emph="#FFF6D6", anim="pop"),
    "neon_script": dict(label="Neon sign", group="Glow", blurb="Handwritten neon sign.",
                        font="script", size=1.25, max_words=3, fill="#FFFFFF", glow="#FF2F6E", emph="#FFF6D6", anim="blur_in"),
    "fire": dict(label="Fire", group="Glow", blurb="Orange-hot glow behind heavy caps.",
                 font="heavy", upper=True, size=1.05, max_words=3, fill="#FFF2B0", glow="#FF5A00", anim="pop"),
    "ice": dict(label="Ice", group="Glow", blurb="Frosty blue-white glow.",
                font="heavy", upper=True, size=1.05, max_words=3, fill="#F0FBFF", glow="#1E90FF", emph="#FFFFFF", anim="fade"),

    # --- retro ---
    "retro_3d": dict(label="Retro 3D", group="Retro", blurb="Chunky stacked 3D letters.",
                     font="heavy", upper=True, size=1.08, max_words=3, fill="#FFE135", outline=("#000000", 0.03),
                     extrude=("#E5242A", 7), anim="pop"),
    "synthwave": dict(label="Synthwave", group="Retro", blurb="80s pink and cyan offset.",
                      font="heavy", upper=True, size=1.05, max_words=3, fill="#FFFFFF",
                      ghost=[("#FF2BD6", -0.07, 0.045), ("#22D3FF", 0.07, -0.045)], emph="#FFFFFF", anim="pop"),
    "glitch": dict(label="Glitch", group="Retro", blurb="Red and cyan broken-signal text.",
                   font="heavy", upper=True, size=1.05, max_words=3, fill="#FFFFFF",
                   ghost=[("#FF0044", -0.055, 0.0), ("#00F0FF", 0.055, 0.0)], emph="#FFFFFF", tilt=True, anim="stamp"),
    "vhs": dict(label="VHS", group="Retro", blurb="Camcorder on-screen-display letters.",
                font="courier", upper=True, size=0.95, max_words=4, fill="#FFFFFF", shadow=0.04, shadow_alpha=0x20,
                ghost=[("#00F0FF", 0.018, 0.0)], anim="none"),
    "arcade": dict(label="Arcade", group="Retro", blurb="Hard black shadow, video-game colours.",
                   font="mono", upper=True, size=0.98, max_words=3, fill="#39FF6A", shadow=0.1, shadow_xy=(0.09, 0.09),
                   shadow_alpha=0x00, emph="#FFE135", anim="none"),
    "pop_art": dict(label="Pop art", group="Retro", blurb="Comic-book colours and a hard offset shadow.",
                    font="impact", upper=True, size=1.25, max_words=2, fill="#FFE135", outline=("#000000", 0.07),
                    shadow=0.1, shadow_xy=(0.1, 0.1), shadow_color="#E5242A", shadow_alpha=0x00, tilt=True, anim="stamp"),
    "double_outline": dict(label="Sticker", group="Retro", blurb="White ring and dark ring, like a die-cut sticker.",
                           font="heavy", upper=True, size=0.98, max_words=3, fill="#111111", outline=("#FFFFFF", 0.14),
                           outline2=("#000000", 0.2), emph="#E5242A", anim="pop"),
    "outline_only": dict(label="Outline", group="Retro", blurb="Hollow letters; the footage shows through.",
                         font="heavy", upper=True, size=1.22, max_words=2, fill="#FFFFFF", outline=("#FFFFFF", 0.035),
                         hollow=True, active=dict(color="#FFFFFF", solid=True), anim="none"),

    # --- fun / handwritten ---
    "handwritten": dict(label="Handwritten", group="Fun", blurb="Neat pen handwriting.",
                        font="print", size=1.05, max_words=4, fill="#FFFFFF", outline=("#000000", 0.06), emph="#FFE135", anim="fade"),
    "marker": dict(label="Marker", group="Fun", blurb="Loose felt-tip letters.",
                   font="ink", size=1.35, max_words=4, fill="#FFFFFF", outline=("#000000", 0.04), shadow=0.05, emph="#FFE135", anim="pop"),
    "script": dict(label="Script", group="Fun", blurb="Flowing cursive.",
                   font="script", size=1.15, max_words=4, fill="#FFFFFF", outline=("#000000", 0.04), shadow=0.05, emph="#FFE135", anim="fade"),
    "comic": dict(label="Comic", group="Fun", blurb="Friendly comic letters.",
                  font="comic", size=1.0, max_words=3, fill="#FFFFFF", outline=("#000000", 0.1), emph="#FFE135", anim="pop"),
    "rainbow": dict(label="Rainbow", group="Fun", blurb="Every word a different colour.",
                    font="heavy", upper=True, size=1.05, max_words=3, fill="#FFFFFF", outline=("#000000", 0.11),
                    word_cycle=RAINBOW, anim="pop"),
    "party": dict(label="Party", group="Fun", blurb="Bouncy, tilted, a new colour each spoken word.",
                  font="impact", upper=True, size=1.3, max_words=2, fill="#FFFFFF", outline=("#000000", 0.13),
                  active=dict(cycle=RAINBOW, bounce=True), tilt=True, anim="pop"),
    "wobble": dict(label="Wobble", group="Fun", blurb="Captions land with a little wobble.",
                   font="heavy", upper=True, size=1.05, max_words=3, fill="#FFFFFF", outline=("#000000", 0.11), anim="wobble"),
    "drop_in": dict(label="Drop in", group="Fun", blurb="Each caption drops in from above.",
                    font="black", size=1.08, max_words=3, fill="#FFFFFF", outline=("#000000", 0.09), anim="drop"),
    "slide_up": dict(label="Slide up", group="Fun", blurb="Each caption glides up into place.",
                     font="sans", size=1.0, max_words=4, fill="#FFFFFF", outline=("#000000", 0.08), anim="slide_up"),
    "zoom_in": dict(label="Zoom in", group="Fun", blurb="Each caption rushes in from big to normal.",
                    font="black", upper=True, size=1.08, max_words=3, fill="#FFFFFF", outline=("#000000", 0.09), anim="zoom_in"),
    "flip_in": dict(label="Flip in", group="Fun", blurb="Each caption flips round to face you.",
                    font="heavy", upper=True, size=1.02, max_words=3, fill="#FFFFFF", outline=("#000000", 0.09), anim="flip"),
    "spin_in": dict(label="Spin in", group="Fun", blurb="Each caption twists into place.",
                    font="heavy", upper=True, size=1.02, max_words=3, fill="#FFFFFF", outline=("#000000", 0.09), anim="spin_in"),
})
for _k, _g in {"clean": "Clean", "karaoke": "Viral", "pill": "Boxes", "hormozi": "Viral", "beast": "Viral", "bold": "Clean",
               "highlight": "Boxes", "classic": "Boxes", "minimal": "Clean", "neon": "Glow", "typewriter": "Retro",
               "one_word": "Viral", "stacked": "Clean"}.items():
    PRESETS[_k]["group"] = _g
STYLE_GROUPS = ["Viral", "Clean", "Boxes", "Editorial", "Glow", "Retro", "Fun"]
STYLES = tuple(PRESETS) + ("none",)


def strip_colors(text: str) -> str:
    """Remove colour tags, so a copy of the line can be drawn entirely in one colour."""
    return re.sub(r"\\1?c&H[0-9A-Fa-f]+&", "", text)


def style_list() -> list[dict]:
    """For UIs: id, label, group, one-line description of every style."""
    return [{"id": k, "label": p["label"], "blurb": p["blurb"], "group": p.get("group", "Clean")}
            for k, p in PRESETS.items()]


FONT_LABELS = {"sans": "Clean sans (your brand)", "serif": "Elegant serif (your brand)", "heavy": "Arial Black",
               "black": "Segoe Black", "impact": "Impact", "semibold": "Segoe Semibold", "franklin": "Franklin Gothic",
               "bahn": "Bahnschrift", "narrow": "Tall narrow", "tahoma": "Tahoma", "georgia_i": "Georgia italic",
               "times": "Times", "constantia": "Constantia", "palatino": "Palatino", "mono": "Consolas (code)",
               "courier": "Typewriter", "script": "Script", "print": "Handwriting", "ink": "Ink marker",
               "comic": "Comic"}


def font_list() -> list[dict]:
    """Fonts a person can pick for captions in the Studio (captions "font")."""
    return [{"id": k, "label": v} for k, v in FONT_LABELS.items()]


class Writer:
    def __init__(self, brand: dict, s: dict, W: int, H: int):
        self.b, self.s, self.W, self.H = brand, s, W, H
        # A copy, so a person's own font / colour (captions "font", "color") can override the style.
        self.p = dict(PRESETS.get(s["style"], PRESETS["clean"]))
        if s.get("color"):
            self.p["fill"] = s["color"]
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
        if s.get("font") in self.fonts:
            self.p["font"] = s["font"]
        self.font = self.fonts[self.p["font"]]
        unit = W / 1080 if H > W else H / 1080
        self.size = int(s["size"] * self.p.get("size", 1.0) * unit)
        self.upper = self.p["upper"] if "upper" in self.p else bool(s["uppercase"])
        if s.get("case") in ("upper", "normal"):  # the person's choice beats the style's
            self.upper = s["case"] == "upper"
        self.lower = bool(self.p.get("lower"))
        fx, fy = caption_xy(s["position"], W, H)
        self.x, self.y = int(W * fx), int(H * fy)
        self.events: list[str] = []
        self.n_cue = 0
        self.spots: list[tuple[float, float, float]] = []  # position "best": (from, to, y)
        self.fs_tag = ""

    def prepare_spots(self, plan: dict, edit_dir: Path) -> None:
        if self.s["position"] == "best":
            self.spots = best_spots(plan, edit_dir, self.W, self.H, self.size * 1.35 * self.p.get("lines", 1))
            # In 9:16 the apps' like/comment buttons run down the right edge, so sit a little left.
            self.x = int(self.W * (0.47 if self.H / self.W > 1.5 else 0.5))

    def y_at(self, t: float) -> int:
        for a, b, fy in self.spots:
            if a <= t < b:
                return int(self.H * fy)
        return int(self.H * self.spots[-1][2]) if self.spots else self.y

    def txt(self, w: dict) -> str:
        return clean_text(w, self.upper, self.lower)

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
        spacing = round(self.size * p.get("spacing", 0), 1)
        if p.get("shadow_color"):
            back = self.c(p["shadow_color"], p.get("shadow_alpha", 0))
        return "\n".join([
            "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {self.W}", f"PlayResY: {self.H}",
            "WrapStyle: 2", "ScaledBorderAndShadow: yes", "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Cap,{font['family']},{self.size},{fill},{fill},{outline_c},{back},{bold},{italic},0,0,"
            f"100,100,{spacing},0,{border},{outline_w},{shadow_w},5,40,40,0,1",
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
        if self.s["emphasis_look"] == "serif" or self.p.get("emph_serif"):
            sf = self.fonts["serif"]
            return (f"{{\\fn{sf['family']}\\i1\\fscx115\\fscy115}}",
                    f"{{\\fn{self.font['family']}\\i0\\fscx100\\fscy100}}", 1.15, sf)
        cycle = self.p.get("emph_cycle")
        default = self.p["fill"] if self.p.get("box_color") or self.p.get("box_cycle") else "accent"
        colour = cycle[(int(w["emph"]) - 1) % len(cycle)] if cycle else self.p.get("emph", default)
        return (f"{{\\c{self.c(colour)}\\fscx118\\fscy118}}",
                f"{{\\c{base}\\fscx100\\fscy100}}", 1.18, self.font)

    def word_text(self, w: dict, active: bool, since_ms: int = 0) -> str:
        pre, post, _, _ = self.emph_look(w)
        a = self.p.get("active") or {}
        base = self.c(self.p["fill"])
        wc = self.p.get("word_cycle")
        if wc and not w.get("emph"):
            pre = f"{{\\c{self.c(wc[int(w.get('idx', 0)) % len(wc)])}}}"
            post = f"{{\\c{base}}}"
        if active and a and not a.get("box"):
            cyc = a.get("cycle")
            colour = self.c(cyc[int(w.get("idx", 0)) % len(cyc)] if cyc else a.get("color", "accent"))
            scale = int(100 * a.get("scale", 1.0))
            if a.get("solid"):  # a hollow style fills in the spoken word
                pre = f"{{\\1a&H00&\\c{colour}}}"
                post = "{\\1a&HFF&}"
            elif a.get("bounce"):
                pre = f"{{\\c{colour}\\fscx128\\fscy128\\t(0,140,\\fscx108\\fscy108)}}"
                post = f"{{\\c{base}\\fscx100\\fscy100}}"
            elif not w.get("emph"):
                pre = f"{{\\c{colour}\\fscx{scale}\\fscy{scale}}}"
                post = f"{{\\c{base}\\fscx100\\fscy100}}"
            elif w.get("emph") and a.get("color") and not self.p.get("emph"):
                pass
        elif active and a.get("box"):
            # Box text colour goes last so it beats an emphasis colour (else the word vanishes).
            pre = pre + f"{{\\c{self.c(a.get('color', '#111111'))}\\bord0\\shad0}}"
            post = post + f"{{\\c{self.c(self.p['fill'])}\\bord{round(self.size * (self.p.get('outline') or ('', 0))[1], 1)}}}"
        return pre + self.txt(w) + post

    def line(self, cue: list[dict], active: int | None = None) -> str:
        p = self.p
        parts = []
        hide = "{\\1a&HFF&\\3a&HFF&\\4a&HFF&}"
        show = f"{{\\1a&H00&\\3a&H00&\\4a&H{p.get('shadow_alpha', 0x40):02X}&}}"
        for i, w in enumerate(cue):
            lit = i == active or bool(p.get("progressive") and active is not None and i < active)
            t = self.word_text(w, lit)
            if p.get("last_big") and i == len(cue) - 1 and len(cue) > 1:
                k = int(100 * p["last_big"])
                t = f"{{\\fscx{k}\\fscy{k}}}{t}{{\\fscx100\\fscy100}}"
            if p.get("reveal") and active is not None and i > active:
                t = hide + t + show  # keeps its space on the line, so the words don't shift
            parts.append(t)
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
            t = self.txt(w)
            sp = self.size * self.p.get("spacing", 0) * len(t)
            out.append(self._measure(font, self.size * scale, t) + sp)
        return out, self._measure(self.font, self.size, " ")

    def add(self, layer: int, a: float, b: float, style: str, text: str) -> None:
        self.events.append(f"Dialogue: {layer},{ts(a)},{ts(b)},{style},,0,0,0,,{text}")

    # ---- events ----

    def cue(self, cue: list[dict], end: float) -> None:
        self.n_cue += 1
        # A long line shrinks to fit: inside the frame always, and in 9:16 clear of the apps' side
        # buttons (they start at 86% across: centred text can be 72% wide, "best" sits left at 47%).
        full, self.fs_tag = self.size, ""
        ws, sp = self.widths(cue)
        lines = 2 if self.p.get("lines", 1) == 2 and len(cue) > 3 else 1
        width = (sum(ws) + sp * (len(ws) - 1)) / lines + self.size * 0.9
        limit = self.W * ((0.76 if self.spots else 0.72) if self.H / self.W > 1.5 else 0.94)
        if width > limit:
            self.size = max(12, int(self.size * limit / width))
            self.fs_tag = f"\\fs{self.size}"
        if self.p.get("active"):
            for i, w in enumerate(cue):
                a = cue[0]["start"] if i == 0 else w["start"]
                b = cue[i + 1]["start"] if i + 1 < len(cue) else end
                self.emit(cue, a, b, active=i, first=(i == 0))
        else:
            self.emit(cue, cue[0]["start"], end, active=None, first=True)
        self.size = full

    def emit(self, cue: list[dict], a: float, b: float, active: int | None, first: bool) -> None:
        p = self.p
        # Keep the whole line inside the frame, wherever the user put the captions.
        ws_all, sp_all = self.widths(cue)
        half = (sum(ws_all) + sp_all * (len(ws_all) - 1)) / (2 if p.get("lines", 1) == 1 or len(cue) <= 3 else 4)
        half += self.size * 0.6
        cx_line = int(min(max(self.x, half), self.W - half)) if half * 2 < self.W else self.W // 2
        base_y = self.y_at(a) if self.spots else self.y
        cy_line = int(min(max(base_y, self.size * 1.2), self.H - self.size * 1.2))
        pos = f"\\an5\\pos({cx_line},{cy_line}){self.fs_tag}"
        anim = ""
        kind = p["anim"] if first else ""
        if kind == "pop":
            anim = "\\fscx90\\fscy90\\t(0,90,\\fscx100\\fscy100)"
        elif kind == "fade":
            anim = "\\fad(120,0)"
        elif kind == "stamp":
            anim = "\\fscx210\\fscy210\\t(0,80,\\fscx100\\fscy100)"
        elif kind == "zoom_in":
            anim = "\\fscx160\\fscy160\\t(0,140,\\fscx100\\fscy100)"
        elif kind == "blur_in":
            anim = "\\blur14\\t(0,180,\\blur0)"
        elif kind == "spin_in":
            anim = "\\frz-14\\fscx85\\fscy85\\t(0,140,\\frz0\\fscx100\\fscy100)"
        elif kind == "flip":
            anim = "\\fry85\\t(0,170,\\fry0)"
        elif kind == "wobble":
            anim = "\\frz-5\\t(0,90,\\frz4)\\t(90,190,\\frz-2)\\t(190,280,\\frz0)"
        elif kind in ("slide_up", "drop"):
            off = int(self.size * (0.7 if kind == "slide_up" else -1.3))
            pos = f"\\an5\\move({cx_line},{cy_line + off},{cx_line},{cy_line},0,{130 if kind == 'slide_up' else 170}){self.fs_tag}"
        if p.get("tilt"):
            anim += f"\\frz{(-2.5, 2.0, -1.5, 2.5)[self.n_cue % 4]}"
        if p.get("blur"):
            anim += "\\be1"

        if p.get("box") == "pill":
            ws, sp = self.widths(cue)
            bw = sum(ws) + sp * (len(ws) - 1) + self.size * 0.9
            bh = self.size * 1.25 + self.size * 0.34
            r = min(bh / 2, self.size * p.get("box_radius", 0.35))
            cyc = p.get("box_cycle")
            bc = cyc[self.n_cue % len(cyc)] if cyc else p.get("box_color")
            tag = (f"\\1c{ass_color(self.col.get(bc, bc))}" if bc else "") + \
                  (f"\\1a&H{p['box_alpha']:02X}&" if p.get("box_alpha") else "")
            self.add(0, a, b, "Box", f"{{{pos}{anim}{tag}\\p1}}{rounded_rect(bw, bh, r)}{{\\p0}}")

        act = p.get("active") or {}
        if active is not None and (act.get("box") or act.get("underline")):
            # Place every word ourselves so the box and its word share exact coordinates
            # (libass word spacing differs slightly from PIL's, which would misalign the box).
            ws, sp = self.widths(cue)
            sp += self.size * 0.12  # room for the box padding between words
            x = cx_line - (sum(ws) + sp * (len(ws) - 1)) / 2
            key = act.get("box") or act.get("underline")
            colour = self.col.get(key, key)
            for j, w in enumerate(cue):
                cx = x + ws[j] / 2
                if j == active and act.get("box"):
                    bw, bh = ws[j] + self.size * 0.36, self.size * 1.22
                    r = min(bh / 2, self.size * act.get("radius", 0.22))
                    self.add(0, a, b, "Box", f"{{\\an5\\pos({cx:.0f},{cy_line}){anim}\\1c{ass_color(colour)}"
                                             f"\\shad0\\p1}}{rounded_rect(bw, bh, r)}{{\\p0}}")
                elif j == active:
                    bw, bh = ws[j], max(4, self.size * 0.11)
                    self.add(0, a, b, "Box", f"{{\\an5\\pos({cx:.0f},{cy_line + self.size * 0.62:.0f})\\1c{ass_color(colour)}"
                                             f"\\shad0\\p1}}{rounded_rect(bw, bh, bh / 2)}{{\\p0}}")
                self.add(1, a, b, "Cap", f"{{\\an5\\pos({cx:.0f},{cy_line}){self.fs_tag}{anim}}}" + self.word_text(w, j == active))
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
            line = self.line(cue, active)
            self.under_layers(line, a, b, cx_line, cy_line, anim)
            extra = "\\1a&HFF&" if p.get("hollow") else ""
            if p.get("shadow_xy"):
                extra += f"\\xshad{self.size * p['shadow_xy'][0]:.0f}\\yshad{self.size * p['shadow_xy'][1]:.0f}"
            text = f"{{{pos}{anim}{extra}}}" + line
        self.add(1, a, b, "Cap", text)

    def under_layers(self, line: str, a: float, b: float, x: int, y: int, anim: str) -> None:
        """Copies of the line under the real one: a second outline, a 3D edge, offset ghosts."""
        p = self.p
        plain = strip_colors(line)
        at = lambda dx, dy: f"\\an5\\pos({x + dx:.0f},{y + dy:.0f}){self.fs_tag}{anim}"  # noqa: E731
        if p.get("outline2"):
            col, w = p["outline2"]
            self.add(0, a, b, "Cap", f"{{{at(0, 0)}\\3c{self.c(col)}\\bord{self.size * w:.1f}\\shad0}}" + plain)
        if p.get("extrude"):
            col, steps = p["extrude"]
            step = max(1.0, self.size * 0.013)
            for k in range(steps, 0, -1):
                self.add(0, a, b, "Cap", f"{{{at(k * step, k * step)}\\c{self.c(col)}\\3c{self.c(col)}\\shad0}}" + plain)
        for col, dx, dy in p.get("ghost") or []:
            self.add(0, a, b, "Cap", f"{{{at(self.size * dx, self.size * dy)}\\c{self.c(col)}\\bord0\\shad0}}" + plain)

    def typed(self, cue: list[dict], a: float) -> str:
        """Each letter starts invisible and appears as its word is spoken."""
        out = []
        for i, w in enumerate(cue):
            t0 = max(0.0, w["start"] - a)
            t1 = max(t0 + 0.05, w["end"] - a)
            chars = self.txt(w)
            for k, ch in enumerate(chars):
                ms = int(1000 * (t0 + (t1 - t0) * k / max(1, len(chars))))
                out.append(f"{{\\alpha&HFF&\\t({ms},{ms + 1},\\alpha&H00&)}}{ch}")
            if i < len(cue) - 1:
                out.append(" ")
        return "".join(out)

    def add_texts(self, items: list[dict]) -> None:
        """Free text, CapCut's Text tool. plan "texts": [{"text": "SALE", "start_in_output": 2, "duration": 3,
        "x": 0.5, "y": 0.2, "size": 90, "font": "heavy", "color": "#FFFFFF", "outline": "#000000",
        "box": "#E5242A", "anim": "pop", "anim_out": "fade", "loop": "pulse"}]
        anim: fade pop stamp zoom_in blur_in spin_in flip wobble slide_up slide_down slide_left slide_right
        drop type; anim_out: fade slide_up slide_down zoom_out; loop: pulse float wiggle blink."""
        unit = self.W / 1080 if self.H > self.W else self.H / 1080
        for t in items:
            if not t.get("text"):
                continue
            a = float(t.get("start_in_output", 0))
            d = float(t.get("duration", 3))
            ms = int(d * 1000)
            x, y = int(self.W * float(t.get("x", 0.5))), int(self.H * float(t.get("y", 0.2)))
            size = int(float(t.get("size", 90)) * unit)
            font = self.fonts.get(t.get("font", "heavy"), self.fonts["heavy"])
            kind, out_kind, loop = t.get("anim", "pop"), t.get("anim_out"), t.get("loop")
            off = int(size * 0.9)
            start = {"slide_up": (x, y + off), "slide_down": (x, y - off), "slide_left": (x + off * 2, y),
                     "slide_right": (x - off * 2, y), "drop": (x, y - off * 2)}.get(kind)
            end = {"slide_up": (x, y - off), "slide_down": (x, y + off)}.get(out_kind)
            if start:
                place = f"\\move({start[0]},{start[1]},{x},{y},0,180)"
            elif end:
                place = f"\\move({x},{y},{end[0]},{end[1]},{ms - 180},{ms})"
            else:
                place = f"\\pos({x},{y})"
            tags = {"pop": "\\fscx70\\fscy70\\t(0,110,\\fscx100\\fscy100)",
                    "stamp": "\\fscx210\\fscy210\\t(0,80,\\fscx100\\fscy100)",
                    "zoom_in": "\\fscx160\\fscy160\\t(0,160,\\fscx100\\fscy100)",
                    "blur_in": "\\blur14\\t(0,200,\\blur0)",
                    "spin_in": "\\frz-20\\t(0,170,\\frz0)",
                    "flip": "\\fry85\\t(0,190,\\fry0)",
                    "wobble": "\\frz-6\\t(0,100,\\frz5)\\t(100,210,\\frz-2)\\t(210,300,\\frz0)"}.get(kind, "")
            fade_in = 150 if kind == "fade" else 0
            fade_out = 180 if out_kind == "fade" else 0
            if fade_in or fade_out:
                tags += f"\\fad({fade_in},{fade_out})"
            if out_kind == "zoom_out":
                tags += f"\\t({ms - 160},{ms},\\fscx10\\fscy10\\alpha&HFF&)"
            if loop in ("pulse", "wiggle", "float", "blink"):
                step, i = 500, 0
                while (i + 1) * step < ms:
                    u = i * step
                    v = (i + 1) * step
                    up = i % 2 == 0
                    tags += {"pulse": f"\\t({u},{v},\\fscx{108 if up else 100}\\fscy{108 if up else 100})",
                             "wiggle": f"\\t({u},{v},\\frz{4 if up else -4})",
                             "float": f"\\t({u},{v},\\fry{10 if up else -10})",
                             "blink": f"\\t({u},{v},\\alpha&H{'70' if up else '00'}&)"}[loop]
                    i += 1
            col = self.c(t.get("color", "#FFFFFF"))
            oc = self.c(t.get("outline", "#000000"))
            bord = 0 if t.get("box") else max(2, int(size * float(t.get("outline_width", 0.09))))
            base = (f"\\an5{place}\\fn{font['family']}\\fs{size}\\b{1 if font.get('bold') else 0}"
                    f"\\i{1 if font.get('italic') else 0}\\c{col}\\3c{oc}\\bord{bord}\\shad0{tags}")
            words = str(t["text"]).replace("{", "(").replace("}", ")")
            shown = words.upper() if t.get("uppercase") else words
            if t.get("box"):
                lines = shown.split("\\N")
                bw = max(self._measure(font, size, ln) for ln in lines) + size * 0.9
                bh = size * (1.3 * len(lines) + 0.3)
                self.add(2, a, a + d, "Box", f"{{\\an5{place}{tags}\\1c{self.c(t['box'])}\\shad0\\p1}}"
                                             f"{rounded_rect(bw, bh, size * 0.2)}{{\\p0}}")
            if kind == "type":
                n = max(1, len(shown))
                body = "".join(f"{{\\alpha&HFF&\\t({int(i * min(ms * 0.6, n * 45) / n)},{int(i * min(ms * 0.6, n * 45) / n) + 1},\\alpha&H00&)}}{ch}"
                               for i, ch in enumerate(shown))
                self.add(3, a, a + d, "Cap", f"{{{base}}}{body}")
            else:
                self.add(3, a, a + d, "Cap", f"{{{base}}}{shown}")

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
    wr.prepare_spots(plan, edit_dir)
    if plan.get("texts") and words is None:
        wr.add_texts(plan["texts"])
    if s["style"] != "none":
        if words is None:
            words = output_words(plan, edit_dir, s.get("corrections") or [])
        for i, w in enumerate(words):
            w.setdefault("idx", i)
        mark_emphasis(words, s["emphasis"])
        cues = chunk(words, words_per_cue(plan, s))
        for i, c in enumerate(cues):
            nxt = cues[i + 1][0]["start"] if i + 1 < len(cues) else c[-1]["end"] + 0.3
            # Hold a cue until the next one unless there's a real pause.
            end = nxt if nxt - c[-1]["end"] < 0.6 else c[-1]["end"] + 0.25
            wr.cue(c, end)
    out_path.write_text(wr.text(), encoding="utf-8")
    return len(wr.events)


# ---- best position ----------------------------------------------------------

def _face_cached(edit_dir: Path, src: Path, start: float, end: float) -> dict | None:
    """Face position for a section, remembered on disk: every render and preview needs it."""
    import framing
    path = edit_dir / "storyboard" / "face_cache.json"
    key = f"{src.name}|{start:.2f}|{end:.2f}"
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if key not in cache:
        cache[key] = framing.face(str(src), start, end, 3)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache), encoding="utf-8")
    return cache[key]


def best_spots(plan: dict, edit_dir: Path, W: int, H: int, cap_h: float) -> list[tuple[float, float, float]]:
    """Where captions should sit: [(from, to, y)] on the output timeline, y as a fraction of the height.

    The rules, in order:
    1. Stay inside the apps' safe area. In 9:16, aim for 62% down and never go below 72% or above
       14%: TikTok, Reels and Shorts all stack their own text in the bottom of the frame.
    2. Never cover the face, including when a punch-in makes it bigger.
    3. Close to the mouth is easiest to follow, so just under the chin beats anywhere further away.
    4. Hold one spot for the whole video if it works everywhere, because captions that jump around
       are harder to read. Only move for the sections that need it.
    """
    import framing
    from plan import range_len
    if H > W * 1.2:      # 9:16, 4:5
        lo, hi, pref = (0.14, 0.72, 0.62) if H / W > 1.5 else (0.10, 0.86, 0.80)
    else:                # 1:1, 16:9
        lo, hi, pref = 0.10, 0.90, 0.86
    h = cap_h / H
    margin = 0.012
    aspect = (plan.get("target") or {}).get("aspect")
    zooms = plan.get("zooms") or []
    rows = []  # (from, to, lowest y under the face, highest y over the face)
    t = 0.0
    for r in plan.get("ranges") or []:
        d = range_len(r)
        src = Path(plan["sources"][r["source"]])
        src = src if src.is_absolute() else edit_dir / src
        below, above = None, None
        f = _face_cached(edit_dir, src, float(r["start"]), float(r["end"]))
        if f:
            cy, fh = f["y"], f["h"]
            crop = framing.reframe_crop(src, float(r["start"]), float(r["end"]), aspect) if aspect else None
            m = re.match(r"crop=(\d+):(\d+):.*:(\d+)$", crop or "")
            if m and not r.get("fit"):  # a crop top and bottom moves the face within the frame
                _, sh = framing.probe_size(src)
                ch, y0 = int(m.group(2)), int(m.group(3))
                cy, fh = (cy * sh - y0) / ch, fh * sh / ch
            s = max([float(z.get("scale", 1.15)) for z in zooms
                     if float(z["start_in_output"]) < t + d and float(z["start_in_output"]) + float(z.get("duration", 0)) > t]
                    or [1.0])
            chin = cy + fh * 0.62 * s    # the detector's box stops at the mouth; leave room for the chin
            brow = cy - fh * 0.85 * s  # hair and forehead sit above the detector's box
            below, above = chin + margin + h / 2, brow - margin - h / 2
        rows.append((t, t + d, below, above))
        t += d
    if not rows:
        return []
    need = max((b for _, _, b, _ in rows if b is not None), default=pref)
    if need <= hi:
        return [(0.0, t + 60, round(max(pref, need), 4))]
    out = []
    for a, b, below, above in rows:
        if below is None or below <= hi:
            y = pref if below is None else max(pref, below)
        elif above is not None and above >= lo:
            y = above
        else:
            y = hi  # a huge face: the bottom of the safe area covers it least
        out.append((a, b, round(y, 4)))
    out[-1] = (out[-1][0], out[-1][1] + 60, out[-1][2])
    return out


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
    # Show a multi-word caption, ideally one with a popped word so the whole look shows. A style
    # built around the spoken word is shown on plain words instead, mid-caption, so that's visible.
    moving = bool(PRESETS.get(style, {}).get("active"))
    first = next((c for c in cues if len(c) >= 2 and (not any(w.get("emph") for w in c) if moving
                                                       else any(w.get("emph") for w in c))),
                 next((c for c in cues if len(c) >= 2), cues[0] if cues else []))
    t0 = first[0]["start"] if first else 0.0
    cue = [dict(w, start=w["start"] - t0 + 0.05, end=w["end"] - t0 + 0.05) for w in first]
    if s["position"] == "best":  # show the spot "best" picks for this moment
        probe = Writer(load_brand(edit_dir), s, *canvas(plan, edit_dir))
        probe.prepare_spots(plan, edit_dir)
        plan = dict(plan, captions=dict(plan.get("captions") or {}, position={"x": 0.5, "y": probe.y_at(t0) / probe.H}))
    # Map the cue's output time back to its source frame.
    offset, src, src_t = 0.0, plan["ranges"][0]["source"], float(plan["ranges"][0]["start"])
    sample_range = plan["ranges"][0]
    from plan import range_len
    for r in plan["ranges"]:
        d = range_len(r)
        if offset <= t0 < offset + d:
            src, src_t = r["source"], float(r["start"]) + (t0 - offset) * float(r.get("speed") or 1.0)
            sample_range = r
            break
        offset += d
    ass = out_jpg.with_suffix(".ass")
    build_ass(plan, edit_dir, ass, style, words=[dict(w, emph=w.get("emph")) for w in cue])
    W, H = canvas(plan, edit_dir)
    src_path = Path(plan["sources"][src])
    src_path = src_path if src_path.is_absolute() else edit_dir / src_path
    # Seek to just after the cue's pop-in finishes so the still shows the settled look.
    hold = min(0.4, (cue[-1]["end"] - 0.05) if cue else 0.4)
    if moving and len(cue) >= 2:
        hold = cue[1]["start"] + 0.12
    import framing
    crop = framing.reframe_crop(src_path, float(sample_range["start"]), float(sample_range["end"]),
                                (plan.get("target") or {}).get("aspect"))
    # A following crop's position changes over time; a still only needs where it starts.
    crop = re.sub(r":'.*':", ":0:", crop) if crop and "'" in crop else crop
    crop = f"{crop}," if crop and crop.startswith("crop=") else ""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{src_t:.3f}", "-i", str(src_path),
                    "-vf", f"{crop}scale={W}:{H},setpts=PTS-STARTPTS,{subtitles_filter(ass)},scale=-2:{height}",
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
