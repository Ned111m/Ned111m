# Global video-production skill repo (v2.5)

The Dron Maglaj production pipeline as executable tools, each one registered as a Claude Code skill. It installs to `~/.config/global-agent-skills/video-production/` and links into `~/.claude/skills/vp-*`.

```bash
./install.sh --venv --npm --mcp          # Linux / macOS
powershell -ExecutionPolicy Bypass -File install.ps1 -Venv -Npm -Mcp   # Windows
```
The installer only creates links, a venv and `node_modules`. It never starts, stops or restarts a process, so it is safe to run while other agents (Strata, ComfyUI, llama-server) are busy.

## Gap analysis: requested modules vs. what already existed

| Module | Already available before this repo | Gap | Implemented here |
|---|---|---|---|
| `nature-sfx` | `audio-spatial-architect`, `procedural-audio-synthesizer` (guidance), ElevenLabs MCP (generation) | No library tagging, wind cleanup or IR matching | `audio/nature-sfx/sfx_library.py`: tag → `*.asset.json`, wind 1–3, IR convolution (loudness-matched wet) |
| `ambience-sfx` | none | No loops or beds | `audio/ambience-sfx/ambience_loop.py`: sample-exact equal-power loop, bed at target LUFS |
| `sfx-for-gfx` | `frame-accurate-speed-ramp-sfx-aligner` (guidance only) | Nothing renders SFX to frames | `audio/sfx-for-gfx/sfx_cue_mix.py`: cue sheet → sample-exact stem (start/peak/end alignment) |
| `voiceover-fairlight` | none | No VO chain or presets | `audio/voiceover-fairlight/vo_chain.py` + Fairlight/Audition value table in its SKILL.md |
| `rhythm-sync` | `rhythm-cut-synchronizer`, `music-synced-speed-ramping` (guidance) | No executable analysis or Resolve markers | `audio/rhythm-sync/beat_markers.py`: librosa beats/downbeats/onsets → JSON + Resolve EDL + CSV |
| `remotion-automation` | `remotion:*` plugin, `remotion-video-developer`, `remotion-transitions` | No DJI telemetry | `motion-graphics/remotion/`: SRT → per-frame props, `TelemetryOverlay`, `LowerThird` |
| `davinci-resolve-scripting` | `dlog-m-color-specialist`, `color-grade-cinematic-matching` (guidance) | No API automation | `editing-color/davinci-resolve/`: manifest → timeline → per-gamma DRX grade → markers → render; CST node-graph spec |
| `ai-video-generation` / `comfyui-nodes` | Kling AI, ElevenLabs, vidIQ (cloud generation) | No local ComfyUI runner | `ai-automation/comfyui-nodes/comfy_queue.py`: API-format runner, waits for an idle queue and free VRAM |
| `topaz-upscaling` | none | No batch CLI | `ai-automation/topaz-upscaling/tvai.py`: `tvai_up` upscale, `tvai_fi` slow-mo |
| `mcp-ffmpeg-bridge` | `ffmpeg-llm` plugin (generates commands, doesn't run them) | No executor, presets, NVENC fallback or loudness | `mcp-extensions/ffmpeg-bridge/`: `ffkit.py` + MCP server (10 tools) |
| `vector-rag-codebase` | none | No index | `mcp-extensions/vector-rag/asset_index.py`: Qdrant, compatible with `mcp-server-qdrant` |
| `local-ai-orchestration` | Strata (yours), `local-coder-setup-assistant` | No task routing; no protection against preempting other agents | `ai-automation/local-llm-agents/llm_route.py` + prompts (SEO, chapters, script, thumbnails) |
| `publishing-automation` | vidIQ / TubeAlfred (analytics, packaging), vidIQ IG Reels publish | No YouTube upload; no local SRT | `publishing/youtube_upload.py`, `publishing/make_srt.py` (faster-whisper) |
| `shared-config` | none | No data contracts | 7 JSON Schemas (2020-12) + 5 render presets |
| `after-effects-scripting` | none | — | **Deferred.** Not in your stated stack (Resolve + Remotion). Remotion covers templated GFX. |
| `premiere-pro-automation` | `nle-plugin-template-expert` | — | **Deferred.** Resolve is the default NLE. The CSV marker export works for Premiere marker scripts. |
| TikTok publishing | — | Content Posting API requires an audited app | **Deferred.** IG Reels goes through vidIQ MCP. |

## Data contracts (`shared-config/workflow-schemas/`)

```
DJI .SRT ──dji_srt_to_props──▶ telemetry-props ──▶ Remotion TelemetryOverlay (ProRes 4444 α)
music.wav ──beat_markers──▶ beat-markers ──▶ resolve_assemble --markers / Remotion / sfx cues
Remotion frames ──▶ sfx-cue-sheet ──sfx_cue_mix──▶ SFX stem (Fairlight)
edit decisions ──▶ timeline-manifest ──resolve_assemble──▶ Resolve timeline (+DRX CST grade)
audio/video files ──sfx_library tag / ffkit probe──▶ asset-sidecar ──asset_index──▶ Qdrant
llm_route seo/chapters ──▶ publish-metadata ──youtube_upload──▶ YouTube
render-preset ──▶ ffkit encode / proxy
```
Every producer validates its output and every consumer validates its input (`vpcore.validate`). Frame numbers are always `Math.round(sec * fps)` on exact rational fps (`30000/1001`, never `29.97`).

## Hardware and error recovery
- NVENC is checked with a real 1-frame test encode, because the encoder list reports `hevc_nvenc` even when no GPU or driver is present. If it's unavailable, the argv is rewritten to `libx265`/`libx264` (`-cq`→`-crf`, `p010le`→`yuv420p10le`, `-hwaccel` dropped).
- `vpcore.run_ffmpeg` matches stderr against known failure classes (NVENC/CUDA, zscale colourspace path, pix_fmt), corrects the arguments and retries up to 3 times. After that it raises with the last 15 stderr lines.
- GPU sharing: `comfy_queue --wait-idle --min-vram-gb`, `tvai --vram 0.6` and `llm_route` (read-only `/slots` / `/api/ps` probes) wait instead of preempting.

## Verification status

| Component | Verified here | Not verifiable here (needs your machine) |
|---|---|---|
| ffkit (probe, proxy, encode, loudnorm, CPU fallback) | ✅ synthetic HLG 4K; output tagged BT.709, -14.0 LUFS | NVENC path on a real GPU |
| vo_chain, ambience_loop, sfx_cue_mix, sfx_library | ✅ -16.1 LUFS; seam jump below the normal sample step; impact peak sample-exact | — |
| beat_markers | ✅ 120 BPM click: tempo 120.2, beat error ≤ 1.6 ms, downbeat phase correct | Real music (tempo octave errors are possible; use `--start-bpm`) |
| dji_srt_to_props + TelemetryOverlay | ✅ synthetic Mini 4 Pro SRT; Remotion still rendered and inspected; `tsc` clean | **A real Mini 4 Pro / Osmo Action 6 SRT.** The format is based on community documentation; no file from your drones has been inspected yet. |
| resolve_assemble | ✅ logic against a mock of the Resolve API | Real Resolve Studio. API names follow the Resolve scripting README; the docs host was unreachable from this sandbox. |
| comfy_queue, llm_route | ✅ against mock HTTP servers | Your real workflows and models |
| asset_index | ✅ embedded Qdrant + stub embedder | FastEmbed model download (Hugging Face is blocked from this sandbox) |
| ffmpeg-bridge MCP server | ✅ stdio session; 10 tools listed; tool call OK | — |
| tvai, youtube_upload, make_srt | ✅ command build, timing chain, metadata rules | Topaz binary and model names, OAuth upload, Whisper inference |

```bash
.venv/bin/python -m pytest -q tests     # 33 tests, ~15 s
npx tsc --noEmit
```
