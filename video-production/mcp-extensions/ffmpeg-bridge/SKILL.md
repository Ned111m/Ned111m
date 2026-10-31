---
name: vp-ffmpeg-bridge
description: Hardware-accelerated FFmpeg for the Dron Maglaj pipeline: probe specs and gamma (HLG/D-Log M), make edit proxies, HLG->Rec.709 previews, two-pass EBU R128 loudness (-14 LUFS YouTube), and preset delivery encodes (ProRes 422 HQ, HEVC NVENC 4K, H.264 web). NVENC is probed with a real test encode, and on failure the command is rewritten to x265/x264 and retried up to 3 times. Use for any transcode, proxy, loudness or spec-check task.
---

# vp-ffmpeg-bridge

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Commands
```bash
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/mcp-extensions/ffmpeg-bridge/ffkit.py probe CLIP.MP4 --sidecar
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/mcp-extensions/ffmpeg-bridge/ffkit.py proxy *.MP4 --out-dir proxies --preset prores-proxy-edit --to709
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/mcp-extensions/ffmpeg-bridge/ffkit.py proxy *.MP4 --out-dir proxies --lut DJI_DLogM_to_Rec709.cube
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/mcp-extensions/ffmpeg-bridge/ffkit.py encode master.mov upload.mp4 --preset hevc-nvenc-youtube-4k
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/mcp-extensions/ffmpeg-bridge/ffkit.py loudnorm mix.wav mix_-14.wav -I -14 -TP -1
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/mcp-extensions/ffmpeg-bridge/ffkit.py presets
```
MCP: `ffmpeg-bridge` server exposes probe, encode, make_proxies, loudness_normalize, measure_loudness, voiceover_chain, ambience_loop, render_sfx_stem, tag_sfx_library.

## Rules
- Always `probe` before claiming anything about a file (fps, gamma, frame count).
- `--to709` / `--lut` are for proxies and quick deliverables only. Masters are graded through the Resolve CST graphs (vp-davinci-resolve).
- D-Log M has no FFmpeg transfer function. It requires the official DJI LUT (`--lut`).
- Presets live in `shared-config/render-presets/*.json` (schema: render-preset). Add presets there, never inline flags.
- Errors: NVENC/CUDA, zscale colourspace and pix_fmt failures are corrected automatically. Anything else surfaces after 3 attempts with the stderr tail.
