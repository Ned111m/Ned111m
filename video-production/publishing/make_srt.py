#!/usr/bin/env python3
"""make_srt — local Whisper (faster-whisper, CUDA) transcription -> broadcast-style SRT.

  make_srt.py vo_final.wav --lang bs --out captions/bs.srt
  make_srt.py master.mp4 --lang en --model large-v3 --max-chars 42 --max-lines 2

Line rules: <= 42 chars/line, <= 2 lines, 1-7 s per cue, split on word boundaries using word timestamps.
pip install faster-whisper   (GPU: CUDA 12 + cuDNN 9; falls back to CPU int8 automatically)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def wrap(words: list[str], max_chars: int) -> list[str]:
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def cues_from_words(words: list[tuple[float, float, str]], max_chars: int, max_lines: int,
                    min_dur: float = 1.0, max_dur: float = 7.0) -> list[tuple[float, float, str]]:
    cues, buf = [], []
    for w in words:
        trial = buf + [w]
        too_long = len(wrap([x[2] for x in trial], max_chars)) > max_lines
        too_slow = trial[-1][1] - trial[0][0] > max_dur
        sentence_end = buf and buf[-1][2].endswith((".", "?", "!")) and w[0] - buf[-1][1] > 0.3
        if buf and (too_long or too_slow or sentence_end):
            cues.append(buf)
            buf = [w]
        else:
            buf = trial
    if buf:
        cues.append(buf)
    out = []
    for i, c in enumerate(cues):
        start, end = c[0][0], max(c[-1][1], c[0][0] + min_dur)
        if i + 1 < len(cues):
            end = min(end, cues[i + 1][0][0] - 0.04)  # 1-frame gap @ 25 fps
        out.append((start, end, "\n".join(wrap([x[2] for x in c], max_chars))))
    return out


def transcribe(path: str, lang: str | None, model_name: str) -> list[tuple[float, float, str]]:
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel(model_name, device="cuda", compute_type="float16")
    except Exception:  # noqa: BLE001 — no CUDA/cuDNN
        print("[make_srt] CUDA unavailable -> CPU int8", file=sys.stderr)
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, info = model.transcribe(path, language=lang, word_timestamps=True, vad_filter=True,
                                      vad_parameters={"min_silence_duration_ms": 400}, beam_size=5)
    print(f"[make_srt] language {info.language} p={info.language_probability:.2f}", file=sys.stderr)
    return [(w.start, w.end, w.word.strip()) for s in segments for w in (s.words or []) if w.word.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("media")
    ap.add_argument("--lang", help="ISO 639-1 (bs, hr, sr, en); auto-detect if omitted")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--max-chars", type=int, default=42)
    ap.add_argument("--max-lines", type=int, default=2)
    ap.add_argument("--out")
    a = ap.parse_args()
    cues = cues_from_words(transcribe(a.media, a.lang, a.model), a.max_chars, a.max_lines)
    srt = "\n".join(f"{i}\n{ts(s)} --> {ts(e)}\n{t}\n" for i, (s, e, t) in enumerate(cues, 1))
    out = Path(a.out or Path(a.media).with_suffix(f".{a.lang or 'auto'}.srt"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(srt, encoding="utf-8")
    print(f"{len(cues)} cues -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
