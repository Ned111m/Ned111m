#!/usr/bin/env python3
"""ffkit — hardware-accelerated FFmpeg bridge (CLI + library, used by the MCP server).

Subcommands
  probe     <file>                       spec summary + gamma detection (+ --sidecar writes *.asset.json)
  proxy     <files...> --out-dir DIR     edit/review proxies (ProRes Proxy or H.264 720p)
  loudnorm  <in> <out>                   two-pass EBU R128 (video stream-copied)
  encode    <in> <out> --preset NAME     preset delivery encode (+ two-pass loudnorm when the preset defines it)
  presets                                list render presets

Colour: --to709 applies an HLG (ARIB STD-B67 / BT.2020) -> Rec.709 tone-map for proxies/previews.
D-Log M has no FFmpeg transfer function: pass the official DJI D-Log M->Rec.709 LUT with --lut.
Masters are graded in Resolve (CST nodes) — these conversions are for proxies and quick deliverables.

NVENC is probed with a real test encode; on missing GPU/driver every command is rewritten to
libx265/libx264 with equivalent quality targets and retried (max 3 attempts, see vpcore.run_ffmpeg).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
import vpcore  # noqa: E402


# ---------------------------------------------------------------- colour
def detect_gamma(info: dict) -> str:
    v = vpcore.video_stream(info) or {}
    trc = (v.get("color_transfer") or "").lower()
    if trc in ("arib-std-b67", "hlg"):
        return "hlg"
    tags = json.dumps(info.get("format", {}).get("tags", {})).lower() + json.dumps(v.get("tags", {})).lower()
    if "dlog" in tags or "d-log" in tags:
        return "dlog-m"
    if trc in ("bt709", "bt1361e"):
        return "rec709"
    return "unknown"


def ff_escape(path: str) -> str:
    """Escape a path for use as a filtergraph option value."""
    return "'" + path.replace("\\", "/").replace("'", r"'\''").replace(":", r"\:") + "'"


def hlg_to_709_chain(pix_fmt: str = "yuv420p10le") -> str:
    return ("zscale=tin=arib-std-b67:pin=bt2020:min=bt2020nc:t=linear:npl=100,format=gbrpf32le,"
            "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            f"zscale=t=bt709:m=bt709:r=tv,format={pix_fmt}")


def lut_chain(lut: str) -> str:
    return f"lut3d=file={ff_escape(str(Path(lut).resolve()))}:interp=tetrahedral"


def output_pix_fmt(preset: dict) -> str:
    v = preset["video"]
    if "-pix_fmt" in v:
        pf = v[v.index("-pix_fmt") + 1]
        return {"p010le": "yuv420p10le", "nv12": "yuv420p"}.get(pf, pf)
    return "yuv420p"


def build_vf(info: dict, preset: dict, to709: bool, lut: str | None, gamma: str | None, extra_vf: str | None) -> str | None:
    chain: list[str] = []
    max_h = preset.get("maxHeight")
    v = vpcore.video_stream(info) or {}
    if max_h and int(v.get("height", 0)) > max_h:
        chain.append(f"scale=-2:{max_h}:flags=lanczos")
    g = gamma or detect_gamma(info)
    if lut:
        chain.append(lut_chain(lut))
    elif to709:
        if g == "hlg":
            chain.append(hlg_to_709_chain(output_pix_fmt(preset)))
        elif g == "dlog-m":
            raise SystemExit("D-Log M source: pass the DJI D-Log M -> Rec.709 LUT with --lut")
        elif g == "unknown":
            vpcore.log("[ffkit] transfer untagged; assuming HLG for --to709 (override with --gamma)")
            chain.append(hlg_to_709_chain(output_pix_fmt(preset)))
    if extra_vf:
        chain.append(extra_vf)
    return ",".join(chain) if chain else None


# ---------------------------------------------------------------- loudness
_JSON_BLOCK = re.compile(r"\{\s*\"input_i\"[^{}]*\}", re.S)


def loudnorm_measure(src: str, I: float, TP: float, LRA: float) -> dict | None:
    proc = vpcore.run_ffmpeg(["-i", src, "-map", "0:a:0", "-vn",
                              "-af", f"loudnorm=I={I}:TP={TP}:LRA={LRA}:print_format=json", "-f", "null", "-"])
    m = _JSON_BLOCK.findall(proc.stderr)
    if not m:
        raise RuntimeError("loudnorm pass 1 produced no JSON")
    data = json.loads(m[-1])
    if data["input_i"] in ("-inf", "inf") or float(data["input_i"]) < -70:
        vpcore.log("[ffkit] input is silent; loudnorm skipped")
        return None
    return data


def loudnorm_filter(measured: dict, I: float, TP: float, LRA: float) -> str:
    return (f"loudnorm=I={I}:TP={TP}:LRA={LRA}:measured_I={measured['input_i']}:"
            f"measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}:"
            f"measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:"
            "linear=true:print_format=summary,aresample=48000")


def measure_loudness(src: str) -> dict:
    """Integrated / true-peak / LRA via ebur128 (read-only)."""
    proc = vpcore.run_ffmpeg(["-i", src, "-map", "0:a:0", "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"])
    tail = proc.stderr[proc.stderr.rfind("Summary:"):]

    def grab(pat: str) -> float | None:
        m = re.search(pat, tail)
        return float(m.group(1)) if m else None
    return {"integratedLufs": grab(r"I:\s+(-?[\d.]+) LUFS"), "lra": grab(r"LRA:\s+(-?[\d.]+) LU"),
            "truePeakDbtp": grab(r"Peak:\s+(-?[\d.]+) dBFS")}


def loudnorm(src: str, dst: str, I: float = -14.0, TP: float = -1.0, LRA: float = 11.0) -> dict:
    info = vpcore.probe(src)
    if not vpcore.audio_stream(info):
        raise SystemExit(f"{src}: no audio stream")
    m = loudnorm_measure(src, I, TP, LRA)
    args = ["-y", "-i", src, "-map", "0", "-c", "copy"]
    if m:
        args += ["-af", loudnorm_filter(m, I, TP, LRA)]
        ext = Path(dst).suffix.lower()
        args += ["-c:a", "pcm_s24le"] if ext in (".wav", ".mov") else ["-c:a", "aac", "-b:a", "320k"]
    vpcore.run_ffmpeg(args + [dst])
    return {"input": m, "output": measure_loudness(dst)}


# ---------------------------------------------------------------- encode
def encode(src: str, dst: str, preset_name: str, to709: bool = False, lut: str | None = None,
           gamma: str | None = None, extra_vf: str | None = None, loudness: bool = True) -> dict:
    preset = vpcore.load_preset(preset_name)
    info = vpcore.probe(src)
    vf = build_vf(info, preset, to709, lut, gamma, extra_vf)
    args = ["-y"] + preset.get("hwaccel", []) + ["-i", src, "-map", "0:v:0", "-map", "0:a:0?"]
    if vf:
        args += ["-vf", vf]
    args += preset["video"]
    has_audio = vpcore.audio_stream(info) is not None
    if has_audio:
        ln = preset.get("loudnorm")
        if ln and loudness:
            m = loudnorm_measure(src, ln["I"], ln["TP"], ln["LRA"])
            if m:
                args += ["-af", loudnorm_filter(m, ln["I"], ln["TP"], ln["LRA"])]
        args += preset["audio"]
    args += preset.get("extra", [])
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    vpcore.run_ffmpeg(args + [dst])
    out = vpcore.probe(dst)
    v = vpcore.video_stream(out) or {}
    return {"output": dst, "codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height"),
            "pix_fmt": v.get("pix_fmt"), "duration": float(out["format"].get("duration", 0))}


def proxy(files: list[str], out_dir: str, preset_name: str = "prores-proxy-edit", to709: bool = False,
          lut: str | None = None, gamma: str | None = None) -> list[dict]:
    preset = vpcore.load_preset(preset_name)
    results = []
    for f in files:
        dst = Path(out_dir) / f"{Path(f).stem}_proxy.{preset['container']}"
        if dst.exists() and dst.stat().st_mtime >= Path(f).stat().st_mtime:
            vpcore.log(f"[ffkit] up-to-date: {dst}")
            results.append({"output": str(dst), "skipped": True})
            continue
        results.append(encode(f, str(dst), preset_name, to709, lut, gamma, loudness=False))
    return results


def summarize(path: str) -> dict:
    info = vpcore.probe(path)
    v = vpcore.video_stream(info)
    a = vpcore.audio_stream(info)
    out: dict = {"path": str(path), "durationSec": float(info["format"].get("duration", 0)),
                 "sizeBytes": int(info["format"].get("size", 0)), "bitRate": int(info["format"].get("bit_rate", 0) or 0)}
    if v:
        fps = vpcore.parse_fps(v.get("avg_frame_rate") if v.get("avg_frame_rate") not in (None, "0/0") else v.get("r_frame_rate", "25"))
        out["video"] = {"codec": v.get("codec_name"), "profile": v.get("profile"), "width": v.get("width"),
                        "height": v.get("height"), "fps": f"{fps.numerator}/{fps.denominator}" if fps.denominator != 1 else str(fps.numerator),
                        "pixFmt": v.get("pix_fmt"), "colorTransfer": v.get("color_transfer"),
                        "colorPrimaries": v.get("color_primaries"), "colorSpace": v.get("color_space"),
                        "gamma": detect_gamma(info), "frames": int(v["nb_frames"]) if v.get("nb_frames", "").isdigit() else None,
                        "timecode": (v.get("tags") or {}).get("timecode") or info["format"].get("tags", {}).get("timecode")}
    if a:
        out["audio"] = {"codec": a.get("codec_name"), "sampleRate": int(a.get("sample_rate", 0)),
                        "channels": a.get("channels"), "layout": a.get("channel_layout")}
    out["nvenc"] = {"hevc": vpcore.encoder_works("hevc_nvenc"), "h264": vpcore.encoder_works("h264_nvenc")}
    return out


def write_video_sidecar(path: str) -> Path:
    s = summarize(path)
    v = s.get("video", {})
    a = s.get("audio", {})
    side = {"version": 1, "path": str(Path(path).resolve()), "kind": "video" if v else "sfx",
            "durationSec": s["durationSec"], "sampleRate": a.get("sampleRate"), "channels": a.get("channels"),
            "tags": sorted({v.get("gamma", "unknown"), f"{v.get('height', 0)}p"} if v else set()),
            "video": {k: v[k] for k in ("width", "height", "fps", "codec", "pixFmt", "colorTransfer", "colorPrimaries", "gamma") if k in v}}
    side["video"] = {k: val for k, val in side["video"].items() if val is not None}
    vpcore.validate(side, "asset-sidecar")
    return vpcore.write_json(Path(path).with_suffix(Path(path).suffix + ".asset.json"), side)


# ---------------------------------------------------------------- cli
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ffkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("probe"); p.add_argument("file"); p.add_argument("--sidecar", action="store_true")

    def colour(sp):
        sp.add_argument("--to709", action="store_true", help="HLG -> Rec.709 tone-map")
        sp.add_argument("--lut", help=".cube LUT (D-Log M -> Rec.709)")
        sp.add_argument("--gamma", choices=["hlg", "dlog-m", "rec709"], help="override source gamma detection")

    p = sub.add_parser("proxy"); p.add_argument("files", nargs="+"); p.add_argument("--out-dir", required=True)
    p.add_argument("--preset", default="prores-proxy-edit"); colour(p)

    p = sub.add_parser("loudnorm"); p.add_argument("src"); p.add_argument("dst")
    p.add_argument("-I", type=float, default=-14.0); p.add_argument("-TP", type=float, default=-1.0)
    p.add_argument("-LRA", type=float, default=11.0)

    p = sub.add_parser("encode"); p.add_argument("src"); p.add_argument("dst"); p.add_argument("--preset", required=True)
    p.add_argument("--vf", help="extra filter appended after colour conversion"); colour(p)
    p.add_argument("--no-loudnorm", action="store_true")

    sub.add_parser("presets")
    a = ap.parse_args(argv)

    if a.cmd == "probe":
        res = summarize(a.file)
        if a.sidecar:
            res["sidecar"] = str(write_video_sidecar(a.file))
    elif a.cmd == "proxy":
        res = proxy(a.files, a.out_dir, a.preset, a.to709, a.lut, a.gamma)
    elif a.cmd == "loudnorm":
        res = loudnorm(a.src, a.dst, a.I, a.TP, a.LRA)
    elif a.cmd == "encode":
        res = encode(a.src, a.dst, a.preset, a.to709, a.lut, a.gamma, a.vf, not a.no_loudnorm)
    else:
        res = [{"name": p.stem, "description": vpcore.read_json(p)["description"]} for p in sorted(vpcore.PRESET_DIR.glob("*.json"))]
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except vpcore.FFmpegError as e:
        vpcore.log(str(e))
        sys.exit(2)
