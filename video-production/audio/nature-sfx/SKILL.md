---
name: vp-nature-sfx
description: Field-recording and nature SFX library tooling for outdoor/aerial footage: auto-tag audio libraries into *.asset.json sidecars (keywords incl. Bosnian, loudness, spectral brightness, loopability, transient peak), wind/handling reduction in 3 strengths, and impulse-response convolution to match an SFX to an environment. Use to organize SFX libraries, find assets, or clean wind noise.
---

# vp-nature-sfx

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Commands
```bash
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/audio/nature-sfx/sfx_library.py tag ~/SFX --location "Maglaj, BiH" --device "Zoom H5"
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/audio/nature-sfx/sfx_library.py wind osmo_clip.wav osmo_clean.wav --strength 2
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/audio/nature-sfx/sfx_library.py ir dry_footsteps.wav forest_IR.wav footsteps_forest.wav --wet 0.3 --predelay-ms 12
```
Then run `vp-vector-rag` `asset_index.py index ~/SFX` for semantic search ("dark wind bed for night shot").

## Rules
- Wind strength 3 (highpass at 180 Hz) also thins voices. Use 1-2 when dialogue matters.
- Drone propeller noise is not removable. Replace it with ambience beds instead of denoising.
- The IR wet level is a loudness-matched ratio (0.2-0.4 is natural).
