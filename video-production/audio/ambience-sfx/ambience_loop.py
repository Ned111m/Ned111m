#!/usr/bin/env python3
"""ambience_loop — seamless loop + bed builder for ambience / room tone.

Loop construction (no click at the wrap point):
  head = [0, X)      tail = [L-X, L)      mid = [X, L-X)
  loop = equal-power-crossfade(tail -> head) + mid          length = L - X
At the wrap, `mid` ends at source time L-X and the loop restarts with `tail`, which begins at L-X:
continuous on both sides of the seam. Equal-power (qsin) fades keep uncorrelated noise beds at constant
loudness through the crossfade.

  ambience_loop.py forest.wav forest_loop.wav --xfade 2.0
  ambience_loop.py forest.wav forest_bed.wav --xfade 2.0 --duration 600 --lufs -32
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_lib"))
sys.path.insert(0, str(ROOT / "mcp-extensions" / "ffmpeg-bridge"))
import vpcore  # noqa: E402
import ffkit  # noqa: E402


def make_loop(src: str, dst: str, xfade: float, duration: float | None = None, lufs: float | None = None,
              sr: int = 48000) -> dict:
    info = vpcore.probe(src)
    a = vpcore.audio_stream(info)
    if not a:
        raise SystemExit(f"{src}: no audio")
    L = float(a.get("duration") or info["format"]["duration"])
    if xfade <= 0 or 2 * xfade >= L:
        raise SystemExit(f"xfade must be > 0 and < half the source length ({L / 2:.3f}s)")
    # Sample-exact boundaries.
    Ls = int(round(L * sr))
    Xs = int(round(xfade * sr))
    fg = (
        f"[0:a]aresample={sr},aformat=sample_fmts=fltp:channel_layouts=stereo,asplit=3[a][b][c];"
        f"[a]atrim=start_sample={Ls - Xs}:end_sample={Ls},asetpts=PTS-STARTPTS,afade=t=out:ns={Xs}:curve=qsin[tail];"
        f"[b]atrim=start_sample=0:end_sample={Xs},asetpts=PTS-STARTPTS,afade=t=in:ns={Xs}:curve=qsin[head];"
        f"[c]atrim=start_sample={Xs}:end_sample={Ls - Xs},asetpts=PTS-STARTPTS[mid];"
        f"[tail][head]amix=inputs=2:normalize=0:duration=longest[x];"
        f"[x][mid]concat=n=2:v=0:a=1[loop]"
    )
    loop_len = (Ls - Xs) / sr
    out_label = "[loop]"
    if duration:
        reps = int(duration // loop_len) + 1
        total = int(round(duration * sr))
        fg += f";[loop]aloop=loop={reps}:size={Ls - Xs},atrim=end_sample={total}[bed]"
        out_label = "[bed]"
    tmp = Path(dst).with_suffix(".tmp.wav")
    vpcore.run_ffmpeg(["-y", "-i", src, "-filter_complex", fg, "-map", out_label, "-c:a", "pcm_s24le", str(tmp)])
    if lufs is not None:
        ffkit.loudnorm(str(tmp), dst, I=lufs, TP=-3.0, LRA=20.0)
        tmp.unlink(missing_ok=True)
    else:
        tmp.replace(dst)
    return {"output": dst, "sourceSec": L, "loopSec": loop_len, "xfadeSec": Xs / sr,
            "durationSec": duration or loop_len, "loudness": ffkit.measure_loudness(dst)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--xfade", type=float, default=2.0, help="crossfade seconds (1-3 s for noise beds)")
    ap.add_argument("--duration", type=float, help="render a bed of this length from the loop")
    ap.add_argument("--lufs", type=float, help="normalise bed, e.g. -32 (under VO) / -26 (no VO)")
    a = ap.parse_args()
    print(json.dumps(make_loop(a.src, a.dst, a.xfade, a.duration, a.lufs), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
