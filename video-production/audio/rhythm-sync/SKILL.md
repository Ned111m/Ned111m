---
name: vp-rhythm-sync
description: librosa beat, downbeat and transient analysis to frame-exact cut markers: tempo, beats snapped to the audible attack (about 1-2 ms error on clicks), bar downbeats, and strong onsets for SFX hits. Exports JSON (Remotion/scripts), a Resolve marker EDL and CSV. Use for music-synced editing, speed-ramp anchor points and cut placement.
---

# vp-rhythm-sync

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Commands
```bash
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/audio/rhythm-sync/beat_markers.py music.wav --fps 25 --onsets --out markers/music
# -> markers/music.json (beat-markers schema) + .edl (Resolve: Timeline > Import > Timeline Markers from EDL) + .csv
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/audio/rhythm-sync/beat_markers.py music.wav --fps 30000/1001 --start-bpm 90   # tempo prior if half/double-time
```
## Rules
- Run with the timeline fps. Frames are quantized with `Math.round`, so the maximum error is half a frame.
- Cut on downbeats (blue) for section changes and on beats (green) for rhythm cuts. Onsets (red) are for impacts.
- Check the reported BPM against the track's known tempo before cutting. If it reads half or double, rerun with `--start-bpm`.
- The markers assume the music starts at timeline frame 0. Offset them if it doesn't.
