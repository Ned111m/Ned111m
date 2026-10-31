#!/usr/bin/env python3
"""sfx_cue_mix — render a sample-accurate SFX stem from a frame-based cue sheet.

The same cue sheet (shared-config/workflow-schemas/sfx-cue-sheet.schema.json) is consumed by Remotion
(<Audio> per cue, preview only) and here (final stem for Resolve/Fairlight), so GFX and SFX can't drift.

Alignment (`align` per cue) decides which point of the SFX lands on the cue frame:
  start -> first sample      (whooshes: lead-in starts on the cut)
  peak  -> loudest sample    (impacts, clicks: the transient hits the frame)
  end   -> last sample       (risers: resolve exactly on the cut)

  sfx_cue_mix.py cues.json sfx_stem.wav
  sfx_cue_mix.py cues.json sfx_stem.wav --report      # also print per-cue sample offsets
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_lib"))
sys.path.insert(0, str(ROOT / "mcp-extensions" / "ffmpeg-bridge"))
import vpcore  # noqa: E402
import ffkit  # noqa: E402

_peak_cache: dict[tuple[str, int], int] = {}
_len_cache: dict[tuple[str, int], int] = {}


def _analyse(path: str, sr: int) -> tuple[int, int]:
    key = (str(Path(path).resolve()), sr)
    if key not in _peak_cache:
        x = vpcore.decode_pcm_mono(path, sr)
        if not x:
            raise ValueError(f"{path}: empty audio")
        _peak_cache[key] = max(range(len(x)), key=lambda k: abs(x[k]))
        _len_cache[key] = len(x)
    return _peak_cache[key], _len_cache[key]


def plan(sheet: dict, base_dir: Path) -> list[dict]:
    fps = vpcore.parse_fps(sheet["fps"])
    sr = int(sheet.get("sampleRate", 48000))
    out = []
    for i, cue in enumerate(sheet["cues"]):
        f = Path(cue["file"])
        f = f if f.is_absolute() else (base_dir / f)
        if not f.exists():
            raise FileNotFoundError(f"cue {cue.get('id', i)}: {f}")
        align = cue.get("align", "start")
        # Exact rational frame -> sample position (no float drift at 29.97).
        hit = math.floor(cue["frame"] * sr / fps + 0.5)
        anchor = 0
        if align in ("peak", "end"):
            peak, n = _analyse(str(f), sr)
            anchor = peak if align == "peak" else n
        start = hit - anchor           # may be negative -> trim the head instead of delaying
        out.append({"index": i, "id": cue.get("id", f"cue{i}"), "file": str(f), "frame": cue["frame"], "align": align,
                    "hitSample": hit, "anchorSample": anchor, "startSample": start,
                    "gainDb": cue.get("gainDb", 0.0), "pan": cue.get("pan", 0.0),
                    "fadeOutMs": cue.get("fadeOutMs"), "maxDurationMs": cue.get("maxDurationMs")})
    return out


def render(sheet_path: str, dst: str, report: bool = False) -> dict:
    sheet = vpcore.read_json(sheet_path)
    vpcore.validate(sheet, "sfx-cue-sheet")
    fps = vpcore.parse_fps(sheet["fps"])
    sr = int(sheet.get("sampleRate", 48000))
    total = math.floor(sheet["durationInFrames"] * sr / fps + 0.5)
    cues = plan(sheet, Path(sheet_path).resolve().parent)
    args = ["-y"]
    chains = []
    labels = []
    for k, c in enumerate(cues):
        args += ["-i", c["file"]]
        st = [f"[{k}:a]aresample={sr},aformat=sample_fmts=fltp:channel_layouts=stereo"]
        if c["startSample"] < 0:
            st.append(f"atrim=start_sample={-c['startSample']},asetpts=PTS-STARTPTS")
        if c["maxDurationMs"]:
            st.append(f"atrim=end_sample={int(c['maxDurationMs'] * sr / 1000)}")
        if c["fadeOutMs"]:
            # fade the last fadeOutMs of the (possibly trimmed) clip; areverse keeps it length-agnostic
            st.append(f"areverse,afade=t=in:d={c['fadeOutMs'] / 1000:.4f}:curve=qsin,areverse")
        if c["gainDb"]:
            st.append(f"volume={c['gainDb']}dB")
        if c["pan"]:
            th = (c["pan"] + 1) * math.pi / 4  # constant-power, centre = unity
            l, r = math.cos(th) * math.sqrt(2), math.sin(th) * math.sqrt(2)
            st.append(f"pan=stereo|c0={l:.5f}*c0|c1={r:.5f}*c1")
        if c["startSample"] > 0:
            st.append(f"adelay=delays={c['startSample']}S:all=1")
        chains.append(",".join(st) + f"[c{k}]")
        labels.append(f"[c{k}]")
    master = sheet.get("masterGainDb", 0.0)
    if cues:
        mix = (f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest:dropout_transition=0,"
               f"volume={master}dB,apad=whole_len={total},atrim=end_sample={total}[out]")
        fg = ";".join(chains + [mix])
    else:
        args += ["-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=stereo"]
        fg = f"[0:a]atrim=end_sample={total}[out]"
    vpcore.run_ffmpeg(args + ["-filter_complex", fg, "-map", "[out]", "-c:a", "pcm_s24le", "-ar", str(sr), dst])
    res = {"output": dst, "cues": len(cues), "samples": total, "durationSec": total / sr,
           "truePeakDbtp": ffkit.measure_loudness(dst)["truePeakDbtp"] if cues else None}
    if res["truePeakDbtp"] is not None and res["truePeakDbtp"] > -1.0:
        vpcore.log(f"[sfx_cue_mix] WARNING: stem true peak {res['truePeakDbtp']} dBTP > -1; lower masterGainDb")
    if report:
        res["plan"] = cues
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheet")
    ap.add_argument("dst")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    print(json.dumps(render(a.sheet, a.dst, a.report), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
