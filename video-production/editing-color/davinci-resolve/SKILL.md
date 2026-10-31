---
name: vp-davinci-resolve
description: DaVinci Resolve Studio Python API automation: builds a timeline from a frame-exact manifest (or dumps a footage folder), sets DaVinci YRGB, applies the D-Log M or HLG to Rec.709 CST node graph per clip via DRX, adds beat/onset markers, and queues or starts renders. Includes the CST node-graph spec (DWG/Intermediate working space). Use for Resolve assembly, grading setup or render automation.
---

# vp-davinci-resolve

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Commands (Resolve Studio running, Preferences > System > General > External scripting = Local)
```bash
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/editing-color/davinci-resolve/python-api-scripts/resolve_assemble.py --manifest edit.json \
     --drx-hlg ~/.config/global-agent-skills/video-production/editing-color/davinci-resolve/cst-node-graphs/HLG_to_709.drx \
     --drx-dlogm ~/.config/global-agent-skills/video-production/editing-color/davinci-resolve/cst-node-graphs/DLogM_to_709.drx \
     --markers markers/music.json --render-preset "YouTube 2160p" --target-dir D:/Renders
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/editing-color/davinci-resolve/python-api-scripts/resolve_assemble.py --media-dir D:/DCIM/100MEDIA --fps 25
```
Manifest schema: `shared-config/workflow-schemas/timeline-manifest.schema.json` (outFrame exclusive; Resolve's inclusive end is handled).

## Colour pipeline
DaVinci YRGB, timeline DWG / DaVinci Intermediate. Node 1 is the input CST (DJI D-Gamut/D-Log M or Rec.2100 HLG). The last node is the output CST to Rec.709 Gamma 2.4 with DaVinci tone mapping and saturation compression. Full node list: `cst-node-graphs/node-graphs.json`. Nodes cannot be created through the API: build the graph once, export it as .drx, and the script applies it.

## Rules
- The script verifies the V1 length against the manifest and warns on mismatch (source fps differs from timeline fps).
- Marker fps must equal the timeline fps (checked).
- Without `--render`, the job is only queued, so it can be reviewed first.
