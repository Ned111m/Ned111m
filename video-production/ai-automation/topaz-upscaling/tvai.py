#!/usr/bin/env python3
"""tvai — Topaz Video AI batch wrapper (bundled ffmpeg + tvai_up / tvai_fi filters).

  tvai.py upscale in.mp4 out.mov --model prob-4 --scale 2
  tvai.py slowmo  in.mp4 out.mov --model chr-2 --factor 4            # 4x frames, played back at source fps
  tvai.py upscale clips/*.mp4 --out-dir up/ --model prob-4 --scale 2  # batch
  tvai.py models                                                      # list installed model short names

Requirements (official CLI docs): Topaz's *bundled* ffmpeg must be used, and TVAI_MODEL_DIR /
TVAI_MODEL_DATA_DIR must be set when running without the GUI. Set TVAI_FFMPEG to the bundled binary.
Model short names change between Topaz releases. Run `tvai.py models`, or press Ctrl+Shift+E in the GUI
to see the exact command it would use, then copy the model name from there.

Output is ProRes 422 HQ by default (lossless enough for regrade); --nvenc writes HEVC 10-bit instead.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_lib"))
import vpcore  # noqa: E402

WIN_DEFAULT = r"C:\Program Files\Topaz Labs LLC\Topaz Video AI\ffmpeg.exe"
MAC_DEFAULT = "/Applications/Topaz Video AI.app/Contents/MacOS/ffmpeg"


def tvai_ffmpeg(dry: bool = False) -> str:
    for c in (os.environ.get("TVAI_FFMPEG"), WIN_DEFAULT, MAC_DEFAULT):
        if c and Path(c).exists():
            return c
    if dry:
        return "<TVAI_FFMPEG>"
    raise SystemExit("Topaz ffmpeg not found — set TVAI_FFMPEG to the ffmpeg bundled with Topaz Video AI")


def check_env() -> None:
    missing = [v for v in ("TVAI_MODEL_DIR", "TVAI_MODEL_DATA_DIR") if not os.environ.get(v)]
    if missing:
        raise SystemExit(f"set {', '.join(missing)} (Topaz CLI requirement when the GUI is not running)")


def list_models() -> list[str]:
    check_env()
    names = {Path(p).stem for p in glob.glob(os.path.join(os.environ["TVAI_MODEL_DIR"], "*.json"))}
    return sorted(names)


def codec_args(nvenc: bool) -> list[str]:
    if nvenc:
        return ["-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "16", "-b:v", "0",
                "-pix_fmt", "p010le", "-profile:v", "main10", "-tag:v", "hvc1"]
    return ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"]


def build_cmd(src: str, dst: str, vf: str, nvenc: bool, device: int, vram: float, instances: int,
              dry: bool = False) -> list[str]:
    vf = vf.replace("{hw}", f"device={device}:vram={vram}:instances={instances}")
    return [tvai_ffmpeg(dry), "-hide_banner", "-nostdin", "-y", "-i", src, "-map", "0:v:0", "-map", "0:a?",
            "-map_metadata", "0", "-vf", vf, *codec_args(nvenc), "-c:a", "copy", dst]


def run(cmd: list[str], dry: bool) -> None:
    print(" ".join(f'"{c}"' if " " in c else c for c in cmd), file=sys.stderr)
    if dry:
        return
    check_env()
    p = subprocess.run(cmd)
    if p.returncode != 0:
        raise SystemExit(f"Topaz ffmpeg failed ({p.returncode}). Verify the model name with `tvai.py models`.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("upscale", "slowmo"):
        p = sub.add_parser(name)
        p.add_argument("inputs", nargs="+")
        p.add_argument("--out", help="single output file")
        p.add_argument("--out-dir", help="batch output dir")
        p.add_argument("--model", required=True)
        p.add_argument("--nvenc", action="store_true")
        p.add_argument("--device", type=int, default=0, help="0 = auto")
        p.add_argument("--vram", type=float, default=1.0, help="fraction of VRAM Topaz may use (lower if sharing the GPU)")
        p.add_argument("--instances", type=int, default=1)
        p.add_argument("--dry-run", action="store_true")
        if name == "upscale":
            p.add_argument("--scale", type=int, choices=[1, 2, 4], default=2)
            p.add_argument("--width", type=int, help="final width (forces scale=0 + lanczos fit)")
            p.add_argument("--height", type=int)
        else:
            p.add_argument("--factor", type=int, default=4, help="frame multiplication (2/4/8)")
    sub.add_parser("models")
    a = ap.parse_args()
    if a.cmd == "models":
        print(json.dumps(list_models(), indent=2))
        return 0
    if len(a.inputs) > 1 and not a.out_dir:
        ap.error("multiple inputs need --out-dir")
    for src in a.inputs:
        info = vpcore.probe(src)
        v = vpcore.video_stream(info) or {}
        fps = vpcore.parse_fps(v.get("avg_frame_rate", "25"))
        if a.cmd == "upscale":
            if a.width and a.height:
                vf = f"tvai_up=model={a.model}:scale=0:w={a.width}:h={a.height}:{{hw}},scale={a.width}:{a.height}:flags=lanczos"
            else:
                vf = f"tvai_up=model={a.model}:scale={a.scale}:{{hw}}"
            suffix = f"_x{a.scale}"
        else:
            # Interpolate to fps*factor, then re-time back to source fps -> factor x slow motion.
            new = fps * a.factor
            vf = f"tvai_fi=model={a.model}:fps={new.numerator}/{new.denominator}:{{hw}},setpts={a.factor}*PTS"
            vf += f",fps={fps.numerator}/{fps.denominator}"
            suffix = f"_slow{a.factor}x"
        ext = ".mp4" if a.nvenc else ".mov"
        dst = a.out or str(Path(a.out_dir) / f"{Path(src).stem}{suffix}{ext}")
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        cmd = build_cmd(src, dst, vf, a.nvenc, a.device, a.vram, a.instances, a.dry_run)
        if a.cmd == "slowmo":
            cmd[cmd.index("-c:a") : cmd.index("-c:a") + 2] = ["-an"]  # audio no longer in sync after re-time
        run(cmd, a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
