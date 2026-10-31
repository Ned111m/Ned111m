---
name: vp-sfx-for-gfx
description: Frame-accurate sound design for motion graphics: renders a sample-exact SFX stem from a frame-based cue sheet shared with Remotion. Whooshes align on start, impacts and clicks on their peak transient, risers on their end. Use when syncing whooshes, clicks, impacts or risers to lower thirds, telemetry overlays, transitions or kinetic type.
---

# vp-sfx-for-gfx

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Cue sheet (schema: `shared-config/workflow-schemas/sfx-cue-sheet.schema.json`)
```json
{"version": 1, "fps": "25", "durationInFrames": 750, "masterGainDb": -6,
 "cues": [{"frame": 48, "file": "sfx/whoosh_02.wav", "align": "start", "gainDb": -3},
          {"frame": 60, "file": "sfx/ui_click.wav", "align": "peak"},
          {"frame": 125, "file": "sfx/riser_4s.wav", "align": "end", "pan": -0.3, "fadeOutMs": 40}]}
```
```bash
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/audio/sfx-for-gfx/sfx_cue_mix.py cues.json sfx_stem.wav --report
```
Frames are the Remotion frame numbers (`Math.round(sec * fps)`). In Remotion, preview the rendered `sfx_stem.wav` with `<Html5Audio src={staticFile('sfx_stem.wav')} />` instead of per-cue audio.

## Rules
- Every cue needs a functional reason: a GFX event at that frame. No ambient "sweeteners".
- The stem warns above -1 dBTP. Lower `masterGainDb` rather than limiting.
- Import the stem into Fairlight at timeline start (01:00:00:00) on its own SFX track.
