"""Runs INSIDE the voice-clone environment (voice_clone.py starts it): turns text into speech in a
cloned voice with Chatterbox. Kept separate because Chatterbox needs its own versions of PyTorch.

    python voice_worker.py <reference.wav> <text_file> <out.wav> [turbo|original]
"""

import re
import sys
import time

import torch
import torchaudio as ta

torch.set_num_threads(max(1, __import__("os").cpu_count() or 4))


def sentences(text: str, limit: int = 260) -> list[str]:
    """Short chunks (a sentence or two): long inputs make TTS models rush or lose the voice."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p.strip()]
    out, cur = [], ""
    for p in parts:
        if cur and len(cur) + len(p) + 1 > limit:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    return out


def main() -> None:
    ref, text_file, out = sys.argv[1], sys.argv[2], sys.argv[3]
    which = sys.argv[4] if len(sys.argv) > 4 else "turbo"
    text = open(text_file, encoding="utf-8").read()
    t0 = time.time()
    if which == "turbo":
        from chatterbox.tts_turbo import ChatterboxTurboTTS as Model
    else:
        from chatterbox.tts import ChatterboxTTS as Model
    model = Model.from_pretrained(device="cuda" if torch.cuda.is_available() else
                                  ("mps" if torch.backends.mps.is_available() else "cpu"))
    pieces = []
    gap = torch.zeros(1, int(model.sr * 0.18))
    chunks = sentences(text)
    for i, chunk in enumerate(chunks, 1):
        print(f"speaking {i}/{len(chunks)}", flush=True)
        pieces += [model.generate(chunk, audio_prompt_path=ref), gap]
    wav = torch.cat(pieces[:-1], dim=1)
    ta.save(out, wav, model.sr)
    print(f"done: {wav.shape[-1] / model.sr:.1f}s of speech in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
