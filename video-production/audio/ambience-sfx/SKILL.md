---
name: vp-ambience-sfx
description: Seamless ambience and room-tone loops and long background beds: sample-exact equal-power crossfade loops with no click at the wrap, beds rendered to any length, normalized to a target LUFS (-32 under VO, -26 without). Use for documentary/narrative background beds and room-tone fill.
---

# vp-ambience-sfx

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Commands
```bash
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/audio/ambience-sfx/ambience_loop.py forest.wav forest_loop.wav --xfade 2
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/audio/ambience-sfx/ambience_loop.py river.wav river_bed.wav --xfade 2 --duration 642 --lufs -32
```
## Rules
- xfade 1-3 s for noise beds. Shorter crossfades are audible on tonal content (birdsong).
- Source must be longer than 2x the xfade. Pick a source without distinct events (a single bird call repeats on every loop).
- Bed level: -32 LUFS under VO, -26 LUFS with music only. Then mix and normalize the full mix with ffkit.
