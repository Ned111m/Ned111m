#!/usr/bin/env python3
"""ffmpeg-bridge MCP server (stdio) — exposes ffkit + audio tools to Claude Code / Claude Desktop / Strata.

Register (user scope, every project):
  claude mcp add --scope user ffmpeg-bridge -- python3 ~/.config/global-agent-skills/video-production/mcp-extensions/ffmpeg-bridge/server.py

Tools are thin wrappers over the CLIs in this repo, so CLI and MCP behaviour are identical (same NVENC probe,
same CPU fallback, same 3-attempt recovery). All paths must be absolute.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in ("_lib", "mcp-extensions/ffmpeg-bridge", "audio/voiceover-fairlight", "audio/ambience-sfx",
          "audio/sfx-for-gfx", "audio/nature-sfx"):
    sys.path.insert(0, str(ROOT / p))

import ffkit  # noqa: E402
import vpcore  # noqa: E402

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore

mcp = _Server("ffmpeg-bridge")


def _abs(p: str) -> str:
    path = Path(p).expanduser()
    if not path.is_absolute():
        raise ValueError(f"absolute path required: {p}")
    return str(path)


@mcp.tool()
def probe(path: str, write_sidecar: bool = False) -> dict:
    """Inspect a media file: codec, resolution, exact fps, pixel format, colour tags, detected gamma
    (hlg / dlog-m / rec709 / unknown), audio layout and whether NVENC works on this machine."""
    res = ffkit.summarize(_abs(path))
    if write_sidecar:
        res["sidecar"] = str(ffkit.write_video_sidecar(_abs(path)))
    return res


@mcp.tool()
def list_presets() -> list[dict]:
    """List render presets (ProRes 422 HQ, ProRes Proxy, HEVC NVENC YouTube 4K, H.264 web 1080, H.264 proxy 720)."""
    return [{"name": p.stem, "description": vpcore.read_json(p)["description"]} for p in sorted(vpcore.PRESET_DIR.glob("*.json"))]


@mcp.tool()
def encode(src: str, dst: str, preset: str, to709: bool = False, lut: str | None = None,
           gamma: str | None = None, extra_vf: str | None = None, loudnorm: bool = True) -> dict:
    """Encode with a render preset. to709 tone-maps HLG to Rec.709; lut applies a .cube (D-Log M).
    Presets with a loudnorm block get two-pass EBU R128 normalisation. NVENC falls back to CPU automatically."""
    return ffkit.encode(_abs(src), _abs(dst), preset, to709, _abs(lut) if lut else None, gamma, extra_vf, loudnorm)


@mcp.tool()
def make_proxies(files: list[str], out_dir: str, preset: str = "prores-proxy-edit", to709: bool = False,
                 lut: str | None = None) -> list[dict]:
    """Generate edit proxies (skips up-to-date ones)."""
    return ffkit.proxy([_abs(f) for f in files], _abs(out_dir), preset, to709, _abs(lut) if lut else None)


@mcp.tool()
def loudness_normalize(src: str, dst: str, integrated_lufs: float = -14.0, true_peak_db: float = -1.0,
                       lra: float = 11.0) -> dict:
    """Two-pass EBU R128 loudnorm (video stream-copied). YouTube: -14 LUFS / -1 dBTP."""
    return ffkit.loudnorm(_abs(src), _abs(dst), integrated_lufs, true_peak_db, lra)


@mcp.tool()
def measure_loudness(path: str) -> dict:
    """Integrated LUFS, true peak (dBTP) and LRA via ebur128 — read-only."""
    return ffkit.measure_loudness(_abs(path))


@mcp.tool()
def voiceover_chain(src: str, dst: str, profile: str = "narration", noise_sample: str | None = None,
                    integrated_lufs: float = -16.0) -> dict:
    """Broadcast VO chain (HPF, denoise, EQ, de-ess, compressor, loudnorm). Profiles: narration, podcast,
    outdoor-action. noise_sample='START:END' seconds of room tone to learn the noise print from."""
    import vo_chain
    return vo_chain.process(_abs(src), _abs(dst), profile, noise_sample, integrated_lufs, -1.5, 7.0)


@mcp.tool()
def ambience_loop(src: str, dst: str, xfade_sec: float = 2.0, duration_sec: float | None = None,
                  lufs: float | None = None) -> dict:
    """Seamless equal-power loop of an ambience/room tone; optionally render a bed of duration_sec at lufs."""
    import ambience_loop as al
    return al.make_loop(_abs(src), _abs(dst), xfade_sec, duration_sec, lufs)


@mcp.tool()
def render_sfx_stem(cue_sheet: str, dst: str) -> dict:
    """Render a sample-accurate SFX stem from a frame-based cue sheet (sfx-cue-sheet schema)."""
    import sfx_cue_mix
    return sfx_cue_mix.render(_abs(cue_sheet), _abs(dst), report=True)


@mcp.tool()
def tag_sfx_library(directory: str, location: str | None = None, device: str | None = None) -> dict:
    """Tag every audio file in a directory with an *.asset.json sidecar and write library.jsonl."""
    import sfx_library
    return sfx_library.tag_dir(_abs(directory), location, device)


if __name__ == "__main__":
    mcp.run()
