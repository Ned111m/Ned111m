---
name: vp-local-llm
description: Local LLM task router for llama-server and Ollama (OpenAI-compatible): YouTube SEO metadata, chapters, narration outlines and thumbnail concepts from versioned prompt templates. Validates JSON against YouTube rules with automatic re-asks. Read-only load probes (/slots, /api/ps) make it wait instead of preempting or evicting another agent's model (e.g. Strata). Use for local scriptwriting/SEO generation.
---

# vp-local-llm

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Commands
```bash
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/ai-automation/local-llm-agents/llm_route.py --status
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/ai-automation/local-llm-agents/llm_route.py seo-metadata --var brief=@brief.txt --var location="Maglaj, BiH" \
     --var footage="DJI Mini 4 Pro 4K HLG" --var keyword="Maglaj dron" --var language=bosanski --out seo.json
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/ai-automation/local-llm-agents/llm_route.py chapters --var durationSec=642 --var scenes=@scenes.txt --var language=bosanski
```
Routes and models: `routes.json`. Prompts: `prompts/*.md` (`SYSTEM:` / `USER:` sections, `{{var}}` placeholders).

## Rules
- Never restart, unload or reconfigure llama-server/Ollama/Strata from this tool. It only reads load and waits (`--max-wait`).
- Model output is a draft. Facts in titles and descriptions must come from the brief.
