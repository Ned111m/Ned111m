---
name: vp-comfyui-queue
description: Headless ComfyUI runner for API-format workflows (Wan 2.x image-to-video, LTX-Video, FLUX, upscalers): patch inputs by node id or title, upload images, batch prompts from JSONL, wait for an idle queue and enough free VRAM so other GPU agents aren't preempted, then download outputs. Use for generative B-roll, background extension, inpainting or style-transfer batches.
---

# vp-comfyui-queue

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Commands
```bash
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/ai-automation/comfyui-nodes/comfy_queue.py wan22_i2v_api.json --wait-idle --min-vram-gb 14 \
     --set "Positive Prompt.text=slow aerial push over the Bosna river at golden hour" \
     --set 52.image=@frames/DJI_0042_f1200.png --set 3.seed=random --out broll/
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/ai-automation/comfyui-nodes/comfy_queue.py flux_api.json --batch prompts.jsonl --out thumbs/
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/ai-automation/comfyui-nodes/comfy_queue.py wf.json --set 3.steps=30 --dry-run
```
## Rules
- Export workflows with **Save (API Format)**. UI-format JSON is rejected.
- Highest-quality defaults: keep the workflow's native resolution and steps, fp16/bf16 weights when VRAM allows, and no fp8 unless VRAM forces it.
- Always use `--wait-idle` while another agent may be using the GPU.
- Disclose AI B-roll on upload (`containsSyntheticMedia: true` in publish metadata).
