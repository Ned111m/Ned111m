#!/usr/bin/env python3
"""vo_chain — broadcast voiceover chain in FFmpeg, mirrored 1:1 by the Fairlight preset in SKILL.md.

Order (same as the Fairlight channel strip): HPF -> Denoise -> EQ (mud cut, presence, air) -> De-esser
-> Compressor -> two-pass loudnorm (stem target, default -16 LUFS / -1.5 dBTP).

  vo_chain.py in.wav out.wav --profile narration
  vo_chain.py in.wav out.wav --profile outdoor-action --noise-sample 0.2:0.9   # learn noise from a silent gap
  vo_chain.py in.wav --print-filter --profile narration                        # print the -af chain only
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

# Each stage: (ffmpeg filter, functional reason). Values are the Fairlight values in SKILL.md.
PROFILES: dict[str, list[tuple[str, str]]] = {
    "narration": [
        ("highpass=f=80:poles=2", "remove rumble/handling below the voice fundamental"),
        ("afftdn@dn=nr=10:nf=-50:tn=1", "broadband room/hiss reduction, gentle (10 dB) to avoid artifacts"),
        ("equalizer=f=220:t=q:w=1.0:g=-2.5", "reduce boxiness/mud from small rooms"),
        ("equalizer=f=3800:t=q:w=1.2:g=1.5", "presence for intelligibility over music"),
        ("highshelf=f=10000:g=1.5", "air"),
        ("deesser=i=0.35:m=0.5:f=0.5:s=o", "tame sibilance boosted by presence/air EQ"),
        ("acompressor=threshold=-20dB:ratio=3:attack=8:release=120:knee=3:makeup=3", "level consistency, 3-6 dB GR"),
    ],
    "podcast": [
        ("highpass=f=70:poles=2", "rumble"),
        ("afftdn@dn=nr=8:nf=-55:tn=1", "light hiss reduction"),
        ("equalizer=f=300:t=q:w=1.0:g=-2", "mud"),
        ("equalizer=f=5000:t=q:w=1.0:g=1", "presence"),
        ("deesser=i=0.3:m=0.5:f=0.5:s=o", "sibilance"),
        ("acompressor=threshold=-22dB:ratio=2.5:attack=15:release=200:knee=4:makeup=3", "gentle leveling"),
    ],
    "outdoor-action": [
        ("highpass=f=120:poles=2", "wind buffet lives below ~150 Hz on action-cam mics"),
        ("afftdn@dn=nr=18:nf=-35:tn=1", "aggressive adaptive denoise for wind/traffic"),
        ("equalizer=f=250:t=q:w=1.0:g=-3", "remove proximity/body boom"),
        ("equalizer=f=3000:t=q:w=1.0:g=2.5", "recover intelligibility lost to denoise"),
        ("deesser=i=0.4:m=0.5:f=0.5:s=o", "sibilance"),
        ("acompressor=threshold=-24dB:ratio=4:attack=5:release=100:knee=3:makeup=4", "tame level swings"),
    ],
}


def build_filter(profile: str, noise_sample: str | None) -> str:
    stages = [f for f, _ in PROFILES[profile]]
    if noise_sample:
        start, end = (float(x) for x in noise_sample.split(":"))
        if end <= start:
            raise SystemExit("--noise-sample must be START:END with END > START")
        stages.insert(0, f"asendcmd=c='{start} afftdn@dn sn start; {end} afftdn@dn sn stop'")
    return ",".join(stages)


def process(src: str, dst: str, profile: str, noise_sample: str | None, I: float, TP: float, LRA: float) -> dict:
    chain = build_filter(profile, noise_sample)
    tmp = Path(dst).with_suffix(".chain.wav")
    vpcore.run_ffmpeg(["-y", "-i", src, "-vn", "-af", chain, "-ar", "48000", "-c:a", "pcm_s24le", str(tmp)])
    try:
        result = ffkit.loudnorm(str(tmp), dst, I, TP, LRA)
    finally:
        tmp.unlink(missing_ok=True)
    return {"profile": profile, "filter": chain, **result}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--profile", choices=sorted(PROFILES), default="narration")
    ap.add_argument("--noise-sample", metavar="START:END", help="seconds of room tone used as the noise print")
    ap.add_argument("-I", type=float, default=-16.0, help="integrated LUFS for the VO stem (final mix: -14)")
    ap.add_argument("-TP", type=float, default=-1.5)
    ap.add_argument("-LRA", type=float, default=7.0)
    ap.add_argument("--print-filter", action="store_true")
    ap.add_argument("--explain", action="store_true", help="print each stage with its reason")
    a = ap.parse_args()
    if a.explain:
        for f, why in PROFILES[a.profile]:
            print(f"{f:<80} # {why}")
        return 0
    if a.print_filter:
        print(build_filter(a.profile, a.noise_sample))
        return 0
    if not a.dst:
        ap.error("dst required")
    print(json.dumps(process(a.src, a.dst, a.profile, a.noise_sample, a.I, a.TP, a.LRA), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
