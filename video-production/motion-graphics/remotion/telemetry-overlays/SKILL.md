---
name: vp-remotion-telemetry
description: Data-driven drone telemetry overlays in Remotion: parses DJI SRT (Mini 4 Pro / Air 3 / Mavic 3 per-frame format and legacy GPS(...) format) into per-frame props with smoothed ground speed, vertical speed, heading and distance, then renders a transparent ProRes 4444 HUD (altitude, speed, climb, coordinates, route mini-map). Also a data-driven LowerThird. Use for any telemetry, GPS route or data lower-third graphic.
---

# vp-remotion-telemetry

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Pipeline
```bash
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/motion-graphics/remotion/telemetry-overlays/dji_srt_to_props.py DJI_0042.SRT --video DJI_0042.MP4 \
     --out ~/.config/global-agent-skills/video-production/public/telemetry/DJI_0042.json
cd ~/.config/global-agent-skills/video-production && npx remotion render motion-graphics/remotion/index.ts TelemetryOverlay out/DJI_0042_hud.mov \
     --codec=prores --prores-profile=4444 --pixel-format=yuva444p10le --image-format=png \
     --props='{"telemetrySrc":"telemetry/DJI_0042.json","units":"metric","accent":"#FFB000","showTrack":true,"showCamera":false,"locationLabel":"Maglaj, BiH"}'
```
Studio: `npm run studio`. Composition fps and duration come from the telemetry JSON (`calculateMetadata`).

## Rules
- `--video` gives the exact frame count, so overlay frame i equals clip frame i. Place the overlay at the clip's start in Resolve.
- DJI SRTs have no battery, gimbal or wind data. Don't display invented values (the flight log .txt is a separate source).
- Show relAlt (above takeoff). abs_alt is unreliable.
- Only transform and opacity are animated. Keep any new components compositor-only.
