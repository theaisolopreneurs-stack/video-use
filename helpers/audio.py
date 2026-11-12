"""Audio polish for a plan render: voice cleanup, music bed with ducking, SFX.

Runs on the concatenated base (one continuous voice track), before captions
and loudness normalisation. Settings live in plan.json "audio":

    "audio": {
      "voice": "clean",                  # clean | isolate (only the voice, background removed) | off
      "voice_gain_db": 0,                # voice louder (+) or quieter (-) against music and sound effects
      "loudness": "normal",              # whole video: quieter | quiet | normal | loud | louder, or LUFS
      "music": {"file": "music/lofi.mp3", "volume_db": -20, "duck": true,
                "fade_in": 1.0, "fade_out": 2.0},
      "sfx": [{"name": "whoosh", "at": 3.2, "volume_db": -8}],   # at = output seconds
      "sfx_on_emphasis": "pop"           # a pop on every popped caption phrase (or null)
    }

voice "clean": 80 Hz high-pass, gentle FFT denoise, de-esser, light compression.
Music loops to the video length, fades in/out, and (duck=true) drops under speech
via a sidechain compressor keyed on the voice.

SFX names are a built-in, synthesised kit (no licensing): pop, click, whoosh,
ding, riser. Or give "file" instead of "name". Music files are yours: put them in
<repo>/music/ or the project folder (free sources listed in music/README.md).

Usage:
    python helpers/audio.py --make-sfx            # (re)build the SFX kit in <repo>/sfx/
    python helpers/audio.py --list-music
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SFX_DIR = REPO / "sfx"
MUSIC_DIR = REPO / "music"
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}

VOICE_CLEAN = ("highpass=f=80,afftdn=nr=10:nf=-40:tn=1,deesser=i=0.4,"
               "acompressor=threshold=-21dB:ratio=2.5:attack=8:release=120:makeup=1.5")
# "isolate": keep only the speaking voice. Background noise, hum, room tone, music and chatter
# between words are pushed right down. Uses an RNNoise speech model (models/*.rnnn) when one is
# there, else strong spectral denoising; both are gated between words.
VOICE_ISOLATE_TAIL = ("agate=threshold=0.02:ratio=6:range=0.03:attack=4:release=180,deesser=i=0.4,"
                      "acompressor=threshold=-21dB:ratio=3:attack=6:release=120:makeup=2")


def voice_chain(mode: str | None) -> str:
    """The voice filter for audio "voice": clean (default) | isolate | off."""
    mode = mode or "clean"
    if mode == "off":
        return "anull"
    if mode != "isolate":
        return VOICE_CLEAN
    model = next(iter(sorted((REPO / "models").glob("*.rnnn"))), None)
    if model:
        m = str(model).replace("\\", "/").replace(":", "\\:")
        denoise = f"arnndn=m='{m}':mix=1,afftdn=nr=8:nf=-45:tn=1"
    else:
        denoise = "afftdn=nr=30:nf=-30:tn=1:ad=0.6,afftdn=nr=12:nf=-40:tn=1"  # (anlmdn crashes on joined clips)
    return f"highpass=f=90,lowpass=f=9000,{denoise},{VOICE_ISOLATE_TAIL}"


# Overall loudness of the finished video (the last step evens it to this level).
LOUDNESS = {"quieter": -18.0, "quiet": -16.0, "normal": -14.0, "loud": -12.0, "louder": -10.0}


def loudness_target(plan: dict) -> float:
    v = (plan.get("audio") or {}).get("loudness", "normal")
    return float(v) if isinstance(v, (int, float)) else LOUDNESS.get(str(v), -14.0)

# Voice changer, as in CapCut's Voice effects. Pitch moves use asetrate + atempo so length is kept.
def _pitch(k: float) -> str:
    return f"asetrate=48000*{k},aresample=48000,atempo={1 / k:.4f}"


VOICE_FX = {
    "chipmunk": ("Chipmunk", _pitch(1.45)),
    "high": ("High", _pitch(1.2)),
    "deep": ("Deep", _pitch(0.82)),
    "monster": ("Monster", _pitch(0.68) + ",aecho=0.6:0.5:40:0.3"),
    "robot": ("Robot", "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75,volume=1.4"),
    "megaphone": ("Megaphone", "highpass=f=500,lowpass=f=3200,acompressor=threshold=-20dB:ratio=6,volume=1.6"),
    "phone": ("Phone call", "highpass=f=350,lowpass=f=3000,volume=1.3"),
    "radio": ("Old radio", "highpass=f=250,lowpass=f=4200,acrusher=bits=10:mode=log:mix=0.4"),
    "echo": ("Echo", "aecho=0.8:0.6:260|520:0.35|0.2"),
    "hall": ("Big hall", "aecho=0.8:0.85:90|170|310:0.4|0.3|0.2"),
    "underwater": ("Underwater", "lowpass=f=500,tremolo=f=4:d=0.3"),
    "whisper": ("Whisper", "highpass=f=900,volume=1.5,aphaser=speed=0.3"),
}


def voice_fx_filter(name: str) -> str:
    return VOICE_FX[name][1] if name in VOICE_FX else ""


# Synthesised SFX: (ffmpeg lavfi source, duration). Short, soft, brand-neutral.
SFX_KIT = {
    "pop": "aevalsrc='0.7*sin(2*PI*(1100-700*t/0.09)*t)*exp(-t*38)':s=48000:d=0.14",
    "click": "aevalsrc='0.6*sin(2*PI*2400*t)*exp(-t*140)':s=48000:d=0.05",
    "ding": "aevalsrc='0.35*sin(2*PI*1320*t)*exp(-t*5)+0.2*sin(2*PI*1980*t)*exp(-t*7)':s=48000:d=1.2",
    "whoosh": "anoisesrc=d=0.5:c=pink:a=0.6:r=48000,highpass=f=400,lowpass=f=4000,"
              "afade=t=in:d=0.28:curve=exp,afade=t=out:st=0.28:d=0.22",
    "riser": "aevalsrc='0.25*sin(2*PI*(200+900*t*t)*t)':s=48000:d=1.2,afade=t=in:d=1.0,afade=t=out:st=1.1:d=0.1",
    # Record scratch (the freeze-frame meme), a deep cinematic boom (push-ins, reveals), a camera shutter.
    "scratch": "aevalsrc='(0.45*sin(2*PI*(180+1400*exp(-t*9))*t)+0.25*(random(0)*2-1))*exp(-t*4)':s=48000:d=0.45,"
               "highpass=f=200,lowpass=f=5000",
    "boom": "aevalsrc='0.9*sin(2*PI*(55+40*exp(-t*8))*t)*exp(-t*3.2)':s=48000:d=1.4",
    "shutter": "aevalsrc='0.6*(random(0)*2-1)*(exp(-t*90)+0.7*exp(-abs(t-0.07)*120))':s=48000:d=0.16,highpass=f=1500",
}


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def make_sfx(force: bool = False) -> dict[str, Path]:
    SFX_DIR.mkdir(exist_ok=True)
    out = {}
    for name, src in SFX_KIT.items():
        p = SFX_DIR / f"{name}.wav"
        if force or not p.exists():
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src, "-ac", "2", str(p)])
        out[name] = p
    return out


def list_music(edit_dir: Path | None = None) -> list[Path]:
    dirs = [MUSIC_DIR] + ([edit_dir.parent, edit_dir.parent / "music"] if edit_dir else [])
    found: list[Path] = []
    for d in dirs:
        if d.is_dir():
            found += sorted(p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXTS)
    return found


def resolve_music(name: str, edit_dir: Path) -> Path | None:
    p = Path(name)
    for cand in ([p] if p.is_absolute() else [REPO / p, edit_dir.parent / p, MUSIC_DIR / p]):
        if cand.exists():
            return cand
    return None


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip() or 0)


def emphasis_times(plan: dict, edit_dir: Path) -> list[float]:
    """Output-time start of each popped caption phrase."""
    import captions
    s = captions.settings(plan, captions.load_brand(edit_dir))
    words = captions.output_words(plan, edit_dir, s.get("corrections") or [])
    captions.mark_emphasis(words, s["emphasis"])
    times, last = [], None
    for w in words:
        e = w.get("emph")
        if e and e != last:
            times.append(round(w["start"], 3))
        last = e
    return times


def has_work(plan: dict) -> bool:
    a = plan.get("audio") or {}
    return (a.get("voice", "clean") != "off" or bool((a.get("music") or {}).get("file"))
            or bool(a.get("sfx")) or bool(a.get("sfx_on_emphasis")) or bool(a.get("sfx_on_zoom"))
            or bool(a.get("voiceover")) or bool(plan.get("fade")) or bool(freeze_sounds(plan)))


def freeze_sounds(plan: dict) -> list[dict]:
    """A record scratch on every meme-style freeze frame (unless its "sound" is false or another name)."""
    out = []
    for e in plan.get("effects") or []:
        if e.get("type") == "freeze_frame" and e.get("style", "meme") == "meme" and e.get("sound", "scratch"):
            out.append({"name": e.get("sound", "scratch"), "at": float(e.get("start_in_output", 0)), "volume_db": -2})
    return out


def mix(base: Path, plan: dict, edit_dir: Path, out: Path) -> Path:
    """Write `out` = base video (stream copy) + polished audio. Returns out."""
    a = plan.get("audio") or {}
    total = duration(base)
    inputs = ["-i", str(base)]
    parts: list[str] = []

    chain = voice_chain(a.get("voice"))
    # Voice louder or quieter against music and sound effects, in dB (+6 = about twice as loud).
    if a.get("voice_gain_db"):
        chain += f",volume={float(a['voice_gain_db']):.1f}dB"
    parts.append(f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,{chain}[voice]")
    voice_label = "[voice]"
    mix_inputs: list[str] = []
    n_inputs = 1

    music = a.get("music") or {}
    mpath = resolve_music(music["file"], edit_dir) if music.get("file") else None
    if music.get("file") and not mpath:
        print(f"  music not found: {music['file']} (skipping)")
    if mpath:
        idx, n_inputs = n_inputs, n_inputs + 1
        inputs += ["-stream_loop", "-1", "-i", str(mpath)]
        fi, fo = float(music.get("fade_in", 1.0)), float(music.get("fade_out", 2.0))
        # The music can start later or stop earlier than the video ("start_in_output", "duration").
        m0 = min(max(0.0, float(music.get("start_in_output") or 0)), max(0.0, total - 0.5))
        mlen = min(float(music.get("duration") or total), total - m0)
        fo = min(fo, mlen / 2)
        delay = f",adelay={int(m0 * 1000)}:all=1" if m0 > 0 else ""
        parts.append(
            f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,atrim=0:{mlen:.3f},"
            f"volume={float(music.get('volume_db', -20))}dB,"
            f"afade=t=in:d={min(fi, mlen / 2)},afade=t=out:st={max(0.0, mlen - fo):.3f}:d={fo}{delay},apad,atrim=0:{total:.3f}[mus]")
        if music.get("duck", True):
            parts.append("[voice]asplit=2[vmain][key]")
            parts.append("[mus][key]sidechaincompress=threshold=0.03:ratio=6:attack=15:release=350[musd]")
            voice_label = "[vmain]"
            mix_inputs.append("[musd]")
        else:
            mix_inputs.append("[mus]")

    freezes = freeze_sounds(plan)
    kit = make_sfx() if (a.get("sfx") or a.get("sfx_on_emphasis") or a.get("sfx_on_zoom") or freezes) else {}
    sfx = list(a.get("sfx") or []) + freezes
    if a.get("sfx_on_emphasis"):
        sfx += [{"name": a["sfx_on_emphasis"], "at": t, "volume_db": -10} for t in emphasis_times(plan, edit_dir)]
    if a.get("sfx_on_zoom"):
        sfx += [{"name": a["sfx_on_zoom"], "at": float(z["start_in_output"]), "volume_db": -20}
                for z in plan.get("zooms") or []]
    if a.get("voiceover"):  # text-to-speech lines: [{"text": "...", "at": 2.0, "voice": null}]
        import tts
        sfx += [{"file": tts.speak(v["text"], edit_dir, v.get("voice")), "at": float(v.get("at", 0)),
                 "volume_db": float(v.get("volume_db", 0))} for v in a["voiceover"]]
    for k, s in enumerate(sfx):
        p = Path(s["file"]) if s.get("file") else kit.get(s.get("name", ""))
        if not p or not Path(p).exists():
            print(f"  sfx not found: {s} (skipping)")
            continue
        idx, n_inputs = n_inputs, n_inputs + 1
        inputs += ["-i", str(p)]
        ms = int(max(0.0, float(s["at"])) * 1000)
        parts.append(f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"volume={float(s.get('volume_db', -8))}dB,adelay={ms}|{ms}[sfx{k}]")
        mix_inputs.append(f"[sfx{k}]")

    if mix_inputs:
        n = 1 + len(mix_inputs)
        # normalize=0 keeps the voice at full level; loudnorm runs after this anyway.
        parts.append(f"{voice_label}{''.join(mix_inputs)}amix=inputs={n}:duration=first:normalize=0[aout]")
    else:
        parts.append(f"{voice_label}anull[aout]")
    fd = plan.get("fade") or {}
    if fd.get("in") or fd.get("out"):  # sound fades with the picture
        bits = [f"afade=t=in:st=0:d={float(fd['in']):.2f}"] if fd.get("in") else []
        bits += [f"afade=t=out:st={max(0.0, total - float(fd['out'])):.2f}:d={float(fd['out']):.2f}"] if fd.get("out") else []
        parts[-1] = parts[-1].replace("[aout]", "[afd]")
        parts.append(f"[afd]{','.join(bits)}[aout]")

    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(parts),
         "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-t", f"{total:.3f}", str(out)])
    print(f"audio → {out.name}  (voice {a.get('voice', 'clean')}"
          f"{', music ' + mpath.name if mpath else ''}{f', {len(sfx)} sfx' if sfx else ''})")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Audio kit helpers")
    ap.add_argument("--make-sfx", action="store_true", help="rebuild the synthesised SFX kit")
    ap.add_argument("--list-music", action="store_true")
    args = ap.parse_args()
    if args.make_sfx:
        for n, p in make_sfx(force=True).items():
            print(f"{n}: {p}")
    if args.list_music:
        for p in list_music():
            print(p)


if __name__ == "__main__":
    main()
