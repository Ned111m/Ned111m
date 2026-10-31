---
name: vp-voiceover-fairlight
description: Broadcast voiceover chain for narration, podcast and outdoor action-cam dialogue: HPF, adaptive denoise (optionally learned from a room-tone gap), corrective/presence EQ, de-esser, compressor, and two-pass loudnorm to a -16 LUFS stem. Includes the matching DaVinci Fairlight and Adobe Audition values. Use when cleaning, leveling or delivering VO.
---

# vp-voiceover-fairlight

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Commands
```bash
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/audio/voiceover-fairlight/vo_chain.py vo_raw.wav vo_clean.wav --profile narration --noise-sample 0.2:0.9
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/audio/voiceover-fairlight/vo_chain.py vo_raw.wav --profile outdoor-action --explain   # each stage + reason
```
Profiles: `narration`, `podcast`, `outdoor-action` (Osmo Action 6 mic, wind).

## Fairlight channel strip (same values as `narration`)
| Order | Fairlight module | Setting |
|---|---|---|
| 1 | EQ band 1 (HPF) | 80 Hz, 12 dB/oct |
| 2 | Noise Reduction (FairlightFX) | Auto speech mode, Threshold ~-50 dB, Reduction 10 dB, Smoothing 25 % |
| 3 | EQ band 2 (bell) | 220 Hz, Q 1.0, -2.5 dB (mud) |
| 4 | EQ band 4 (bell) | 3.8 kHz, Q 1.2, +1.5 dB (presence) |
| 5 | EQ band 6 (hi-shelf) | 10 kHz, +1.5 dB (air) |
| 6 | De-Esser (FairlightFX) | Freq ~6.5 kHz, Range 6 dB, Amount 35 % |
| 7 | Dynamics > Compressor | Threshold -20 dB, Ratio 3:1, Attack 8 ms, Release 120 ms, Knee 3 dB, Make-up +3 dB |
| 8 | Bus: Loudness | Stem -16 LUFS integrated. Final mix bus -14 LUFS, TP -1 dBTP (YouTube) |

Audition: Effects Rack in the same order (Parametric EQ > Noise Reduction (process) > DeEsser > Dynamics > Match Loudness -16 LUFS).

## Rules
- Deliver the VO stem at -16 LUFS. Normalize the full mix to -14 LUFS / -1 dBTP with ffkit `loudnorm` or the preset encode.
- Use `--noise-sample` only on a section that is pure room tone (no breaths). Otherwise leave it off and rely on adaptive tracking.
- Check the result with `ffkit.py probe` and `measure_loudness`. Never report loudness without measuring it.
