#!/usr/bin/env python3
"""sfx_library — field-recording / SFX library tooling for outdoor & aerial footage.

  tag   <dir> [--location ... --device ...]   write <file>.asset.json sidecars (+ library.jsonl for vector-rag)
  wind  <in> <out> [--strength 1-3]           wind/handling reduction for Osmo Action / drone-adjacent audio
  ir    <in> <ir.wav> <out> [--wet 0.35]      convolve a dry SFX with an impulse response (environment match)

Tags = filename keywords + measured properties (loudness, spectral brightness, loopability, transient peak).
The sidecar schema is shared-config/workflow-schemas/asset-sidecar.schema.json.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_lib"))
sys.path.insert(0, str(ROOT / "mcp-extensions" / "ffmpeg-bridge"))
import vpcore  # noqa: E402
import ffkit  # noqa: E402

AUDIO_EXT = {".wav", ".flac", ".aif", ".aiff", ".mp3", ".ogg", ".m4a", ".bwf"}

# keyword -> (tags, kind)
KEYWORDS: dict[str, tuple[tuple[str, ...], str]] = {
    r"wind|gust|breeze": (("nature", "wind"), "ambience"),
    r"river|stream|creek|brook|rijeka|potok": (("nature", "water", "river"), "ambience"),
    r"waterfall|vodopad": (("nature", "water", "waterfall"), "ambience"),
    r"rain|kisa|kiša|drizzle": (("nature", "water", "rain"), "ambience"),
    r"lake|jezero|shore|wave": (("nature", "water", "lake"), "ambience"),
    r"thunder|grom": (("nature", "weather", "thunder"), "sfx"),
    r"bird|ptic|chirp|songbird|owl|eagle|hawk": (("nature", "wildlife", "birds"), "ambience"),
    r"insect|cricket|cicada|bee": (("nature", "wildlife", "insects"), "ambience"),
    r"dog|cow|sheep|goat|horse|rooster": (("rural", "wildlife", "animals"), "sfx"),
    r"forest|wood|suma|šuma|leaves|foliage|rustl": (("nature", "forest", "foliage"), "ambience"),
    r"mountain|planina|valley|dolina": (("nature", "mountain"), "ambience"),
    r"city|town|traffic|street|grad|crowd|market": (("urban",), "ambience"),
    r"church|bell|zvono|mosque|ezan|adhan": (("urban", "bells", "culture"), "sfx"),
    r"room ?tone|roomtone|interior": (("roomtone",), "ambience"),
    r"whoosh|swoosh|swish|pass ?by|fly ?by": (("gfx", "whoosh", "transition"), "sfx"),
    r"click|tick|ui|blip|beep|select": (("gfx", "ui", "click"), "sfx"),
    r"hit|impact|boom|thud|slam": (("gfx", "impact"), "sfx"),
    r"riser|rise|build|uplift": (("gfx", "riser"), "sfx"),
    r"downer|drop|fall": (("gfx", "downer"), "sfx"),
    r"glitch|digital|data|hum|buzz|scan": (("gfx", "digital", "telemetry"), "sfx"),
    r"drone|propeller|rotor|motor": (("drone", "mechanical"), "sfx"),
    r"music|track|song|beat": (("music",), "music"),
    r"\bvo\b|voice|narrat": (("voiceover",), "voiceover"),
}


def centroid_hz(path: str) -> float | None:
    proc = subprocess.run(
        [vpcore.ffmpeg_bin(), "-v", "error", "-i", path, "-t", "60", "-af",
         "aformat=channel_layouts=mono,aspectralstats=win_size=4096:measure=centroid,ametadata=mode=print:file=-",
         "-f", "null", "-"], capture_output=True, text=True)
    vals = [float(m) for m in re.findall(r"centroid=([\d.]+)", proc.stdout) if float(m) > 0]
    return round(statistics.median(vals), 1) if vals else None


def peak_offset(path: str, max_sec: float = 30.0) -> float | None:
    info = vpcore.probe(path)
    if float(info["format"].get("duration", 0)) > max_sec:
        return None
    sr = 48000
    x = vpcore.decode_pcm_mono(path, sr)
    if not x:
        return None
    i = max(range(len(x)), key=lambda k: abs(x[k]))
    return round(i / sr, 5)


def tag_file(path: Path, location: str | None, device: str | None, lat: float | None, lon: float | None) -> dict:
    info = vpcore.probe(str(path))
    a = vpcore.audio_stream(info) or {}
    dur = float(info["format"].get("duration", 0))
    name = path.stem.lower().replace("_", " ").replace("-", " ")
    tags: set[str] = set()
    kind = None
    for pat, (t, k) in KEYWORDS.items():
        if re.search(pat, name):
            tags.update(t)
            kind = kind or k
    if kind is None:
        kind = "ambience" if dur >= 10 else "sfx"
    loud = ffkit.measure_loudness(str(path))
    c = centroid_hz(str(path))
    bright = None if c is None else ("dark" if c < 1500 else "bright" if c > 4000 else "neutral")
    if bright:
        tags.add(bright)
    tags.add("long" if dur >= 30 else "short" if dur < 3 else "medium")
    side = {
        "version": 1, "path": str(path.resolve()), "kind": kind, "durationSec": round(dur, 4),
        "sampleRate": int(a["sample_rate"]) if a.get("sample_rate") else None, "channels": a.get("channels"),
        "loudness": loud, "spectral": {"centroidHz": c, "brightness": bright},
        "peakOffsetSec": peak_offset(str(path)) if kind == "sfx" else None,
        "loopable": kind == "ambience" and dur >= 10,
        "tags": sorted(tags),
        "recording": {"location": location, "lat": lat, "lon": lon, "device": device,
                      "recordedAt": (info["format"].get("tags") or {}).get("creation_time")},
    }
    vpcore.validate(side, "asset-sidecar")
    return side


def tag_dir(root: str, location=None, device=None, lat=None, lon=None, force=False) -> dict:
    files = sorted(p for p in Path(root).rglob("*") if p.suffix.lower() in AUDIO_EXT)
    lib = Path(root) / "library.jsonl"
    done = 0
    with open(lib, "w", encoding="utf-8") as fh:
        for p in files:
            sc = p.with_suffix(p.suffix + ".asset.json")
            if sc.exists() and not force and sc.stat().st_mtime >= p.stat().st_mtime:
                side = vpcore.read_json(sc)
            else:
                try:
                    side = tag_file(p, location, device, lat, lon)
                except Exception as e:  # keep scanning the library
                    vpcore.log(f"[sfx_library] skip {p}: {e}")
                    continue
                vpcore.write_json(sc, side)
                done += 1
            fh.write(json.dumps(side, ensure_ascii=False) + "\n")
    return {"files": len(files), "tagged": done, "library": str(lib)}


WIND = {
    1: "highpass=f=90:poles=2,afftdn=nr=10:nf=-45:tn=1",
    2: "highpass=f=130:poles=2,afftdn=nr=16:nf=-38:tn=1,equalizer=f=200:t=q:w=1:g=-3",
    3: "highpass=f=180:poles=2,afftdn=nr=24:nf=-32:tn=1,equalizer=f=250:t=q:w=0.8:g=-4,"
       "acompressor=threshold=-18dB:ratio=6:attack=1:release=60:knee=2",
}


def wind(src: str, dst: str, strength: int) -> dict:
    vpcore.run_ffmpeg(["-y", "-i", src, "-map", "0:a:0", "-vn", "-af", WIND[strength], "-c:a", "pcm_s24le", "-ar", "48000", dst])
    return {"output": dst, "filter": WIND[strength], "loudness": ffkit.measure_loudness(dst)}


def convolve(src: str, ir: str, dst: str, wet: float, predelay_ms: float) -> dict:
    """Dry/wet convolution. afir's automatic IR gain differs across FFmpeg 6/7, so the wet render is
    loudness-matched to the dry signal (ebur128) before the equal-sum mix — `wet` is then a true ratio."""
    if not 0 <= wet <= 1:
        raise SystemExit("--wet must be 0..1")
    ir_len = float(vpcore.probe(ir)["format"]["duration"])
    pad = ir_len + predelay_ms / 1000 + 0.05
    fmt = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
    wet_tmp = Path(dst).with_suffix(".wet.wav")
    vpcore.run_ffmpeg(["-y", "-i", src, "-i", ir, "-filter_complex",
                       f"[0:a]{fmt},adelay=delays={predelay_ms}:all=1,apad=pad_dur={pad}[pre];[1:a]{fmt}[ir];"
                       f"[pre][ir]afir=dry=1:wet=1[w]", "-map", "[w]", "-c:a", "pcm_f32le", str(wet_tmp)])
    try:
        dry_i = ffkit.measure_loudness(src)["integratedLufs"]
        wet_i = ffkit.measure_loudness(str(wet_tmp))["integratedLufs"]
        match_db = (dry_i - wet_i) if dry_i is not None and wet_i is not None and wet_i > -120 else 0.0
        fg = (f"[0:a]{fmt},apad=pad_dur={pad},volume={1 - wet:.4f}[d];"
              f"[1:a]volume={match_db:.2f}dB,volume={wet:.4f}[w];"
              f"[d][w]amix=inputs=2:normalize=0:duration=longest,"
              f"areverse,silenceremove=start_periods=1:start_threshold=-80dB,areverse,"
              f"alimiter=limit=0.891:level=false[out]")
        vpcore.run_ffmpeg(["-y", "-i", src, "-i", str(wet_tmp), "-filter_complex", fg, "-map", "[out]",
                           "-c:a", "pcm_s24le", dst])
    finally:
        wet_tmp.unlink(missing_ok=True)
    return {"output": dst, "wet": wet, "wetMatchDb": round(match_db, 2), "loudness": ffkit.measure_loudness(dst)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("tag"); p.add_argument("dir"); p.add_argument("--location"); p.add_argument("--device")
    p.add_argument("--lat", type=float); p.add_argument("--lon", type=float); p.add_argument("--force", action="store_true")
    p = sub.add_parser("wind"); p.add_argument("src"); p.add_argument("dst"); p.add_argument("--strength", type=int, choices=[1, 2, 3], default=2)
    p = sub.add_parser("ir"); p.add_argument("src"); p.add_argument("ir"); p.add_argument("dst")
    p.add_argument("--wet", type=float, default=0.35); p.add_argument("--predelay-ms", type=float, default=0.0)
    a = ap.parse_args()
    if a.cmd == "tag":
        res = tag_dir(a.dir, a.location, a.device, a.lat, a.lon, a.force)
    elif a.cmd == "wind":
        res = wind(a.src, a.dst, a.strength)
    else:
        res = convolve(a.src, a.ir, a.dst, a.wet, a.predelay_ms)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
