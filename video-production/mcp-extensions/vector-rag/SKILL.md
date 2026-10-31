---
name: vp-vector-rag
description: Qdrant semantic index over asset sidecars (SFX, ambience, footage specs) and pipeline code/docs. Compatible with the official mcp-server-qdrant (same payload and FastEmbed vector naming), so Claude and local agents can query the same collection. Use to find assets by description or locate pipeline code.
---

# vp-vector-rag

Part of the global video-production skill repo (`~/.config/global-agent-skills/video-production`). Python tools run in the repo venv (`install.sh --venv`). Shared contracts are in `shared-config/workflow-schemas/`.

## Commands
```bash
docker run -d --name qdrant -p 6333:6333 -v ~/qdrant:/qdrant/storage qdrant/qdrant
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/mcp-extensions/vector-rag/asset_index.py index ~/SFX ~/Footage ~/.config/global-agent-skills/video-production
~/.config/global-agent-skills/video-production/.venv/bin/python ~/.config/global-agent-skills/video-production/mcp-extensions/vector-rag/asset_index.py search "dark wind ambience bed, loopable" --kind ambience
```
MCP (read/write from Claude): `claude mcp add --scope user qdrant -e QDRANT_URL=http://127.0.0.1:6333 -e COLLECTION_NAME=dron-maglaj -- uvx mcp-server-qdrant`

## Rules
- Generate sidecars first (`sfx_library.py tag`, `ffkit.py probe --sidecar`). The index reads sidecars, not raw media.
- Re-indexing is idempotent (deterministic point ids).
