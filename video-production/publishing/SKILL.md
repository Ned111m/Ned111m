---
name: vp-publishing
description: YouTube publishing automation: validated metadata (title/tag limits, chapter rules, synthetic-media disclosure, scheduling), resumable YouTube Data API v3 upload with captions, thumbnail and playlist, plus local faster-whisper transcription to broadcast-style SRT (42 chars x 2 lines). Use when preparing or uploading a video, captions or chapters.
---

# vp-publishing

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Commands
```bash
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/publishing/make_srt.py master.mp4 --lang bs --out captions/bs.srt
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/publishing/youtube_upload.py publish.json --dry-run
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/publishing/youtube_upload.py publish.json
```
Metadata schema: `shared-config/workflow-schemas/publish-metadata.schema.json`. Chapters can come from `llm_route.py chapters`.

## Rules
- Dry-run first. Upload as `private` and switch to public in Studio after checking the processed HD/4K version.
- Quota: one upload costs 1600 of the 10 000 units/day.
- Instagram Reels publishing goes through the vidIQ MCP (`vidiq_instagram_publish_reel`). TikTok's Content Posting API needs an audited app, so it isn't automated here.
