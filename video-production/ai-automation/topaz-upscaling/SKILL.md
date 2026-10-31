---
name: vp-topaz-upscaling
description: Topaz Video AI batch CLI wrapper: AI upscale (tvai_up) and frame-interpolated slow motion (tvai_fi, re-timed to source fps) using Topaz's bundled ffmpeg. Outputs ProRes 422 HQ or HEVC NVENC 10-bit, with VRAM-fraction control for sharing the GPU. Use for 4K/8K upscales, noise reduction or slow-mo of drone/action clips.
---

# vp-topaz-upscaling

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Commands
```bash
export TVAI_FFMPEG="C:/Program Files/Topaz Labs LLC/Topaz Video AI/ffmpeg.exe" TVAI_MODEL_DIR=... TVAI_MODEL_DATA_DIR=...
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/ai-automation/topaz-upscaling/tvai.py models
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/ai-automation/topaz-upscaling/tvai.py upscale clips/*.mp4 --out-dir up --model prob-4 --scale 2 --vram 0.6
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/ai-automation/topaz-upscaling/tvai.py slowmo DJI_0042.MP4 --out slow.mov --model chr-2 --factor 4 --dry-run
```
## Rules
- Model short names change between Topaz releases. Confirm them with `models`, or with Ctrl+Shift+E in the GUI, before batch runs.
- Slow-mo drops audio (it is no longer in sync).
- Upscale before grading only if the grade happens on the upscaled file. Otherwise grade first, then upscale the ProRes master.
