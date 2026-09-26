"""Machine inventory: detect everything on this workstation the backup crew can use.

Writes ~/.maglaj-crew/inventory.json and prints a report with concrete tuning actions.
Nothing here mutates the system.
"""
from __future__ import annotations

import glob
import importlib.metadata as md
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx

from .config import HOME, IS_WINDOWS, Settings, settings

PY_PACKAGES = {
    # module/dist name : role in the crew
    "langchain-core": "LangChain core",
    "langchain-ollama": "LangChain <-> Ollama",
    "langchain": "LangChain",
    "langgraph": "LangGraph orchestration",
    "open-interpreter": "Open Interpreter (hands)",
    "qwenpaw": "QwenPaw desktop/runtime",
    "mcp": "MCP SDK (crew tool server)",
    "librosa": "beat grid / audio analysis",
    "soundfile": "audio IO",
    "opencv-python": "optical flow / frame analysis",
    "opencv-python-headless": "optical flow / frame analysis",
    "scenedetect": "PySceneDetect",
    "faster-whisper": "transcription (captions seat)",
    "openai-whisper": "transcription (captions seat)",
    "whisperx": "word-level alignment",
    "demucs": "stem separation",
    "torch": "PyTorch",
    "onnxruntime-gpu": "ONNX GPU inference",
    "ultralytics": "YOLO (privacy blur / reframe tracking)",
    "pyloudnorm": "loudness metering",
    "opentimelineio": "OTIO timeline interchange",
    "rembg": "background removal (thumbnails)",
    "pillow": "thumbnail compositing",
}

CLIS = [
    "ollama", "ffmpeg", "ffprobe", "node", "npm", "npx", "git", "python",
    "claude", "ccr", "ccr-app", "interpreter", "qwenpaw", "code",
    "exiftool", "rubberband", "sox", "demucs", "whisper", "whisper-ctranslate2",
    "nvidia-smi", "uv", "pipx",
]

NVENC_ENCODERS = ["h264_nvenc", "hevc_nvenc", "av1_nvenc"]
FFMPEG_FILTERS = ["libvmaf", "vidstabdetect", "vidstabtransform", "ebur128", "loudnorm",
                  "zscale", "libplacebo", "scale_cuda", "xfade", "minterpolate", "nlmeans",
                  "blackdetect", "freezedetect", "signalstats", "lut3d", "subtitles"]

OLLAMA_TUNING = {
    "OLLAMA_FLASH_ATTENTION": ("1", "halves KV-cache VRAM and speeds long contexts"),
    "OLLAMA_KV_CACHE_TYPE": ("q8_0", "8-bit KV cache: 32k context fits next to a 14-30B model on 16 GB"),
    "OLLAMA_CONTEXT_LENGTH": ("32768", "agents need >=32k; Ollama's default is too small for tool loops"),
    "OLLAMA_MAX_LOADED_MODELS": ("2", "orchestrator + vision resident together; more thrashes 16 GB VRAM"),
    "OLLAMA_NUM_PARALLEL": ("1", "one stream per model keeps the KV cache from multiplying"),
    "OLLAMA_KEEP_ALIVE": ("30m", "avoid reloading multi-GB weights between crew steps"),
}


def _run(cmd: list[str], timeout: float = 15) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                             encoding="utf-8", errors="replace")
        return (out.stdout or "") + (out.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _http_json(url: str, method: str = "GET", body: Any = None, timeout: float = 4) -> Any:
    try:
        r = httpx.request(method, url, json=body, timeout=timeout)
        if r.status_code >= 400:
            return {"_status": r.status_code}
        return r.json()
    except Exception:  # noqa: BLE001 - probing, any failure means "not reachable"
        return None


def system_info() -> dict:
    info: dict[str, Any] = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu": platform.processor() or platform.machine(),
        "cpu_threads": os.cpu_count(),
    }
    try:
        import psutil  # type: ignore

        info["ram_gb"] = round(psutil.virtual_memory().total / 2**30, 1)
    except ImportError:
        if not IS_WINDOWS and Path("/proc/meminfo").exists():
            kb = int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1))
            info["ram_gb"] = round(kb / 2**20, 1)
        elif IS_WINDOWS:
            out = _run(["powershell", "-NoProfile", "-Command",
                        "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"])
            if out.strip().isdigit():
                info["ram_gb"] = round(int(out.strip()) / 2**30, 1)
    return info


def gpu_info() -> list[dict]:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version,compute_cap",
                "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4 and parts[1].replace(".", "").isdigit():
            gpus.append({"name": parts[0], "vram_total_gb": round(float(parts[1]) / 1024, 1),
                         "vram_free_gb": round(float(parts[2]) / 1024, 1), "driver": parts[3],
                         "compute_cap": parts[4] if len(parts) > 4 else ""})
    return gpus


def python_packages() -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for dist in PY_PACKAGES:
        try:
            found[dist] = md.version(dist)
        except md.PackageNotFoundError:
            found[dist] = None
    try:
        import torch  # type: ignore

        found["torch.cuda"] = str(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        pass
    return found


def cli_tools() -> dict[str, dict]:
    res: dict[str, dict] = {}
    for name in CLIS:
        path = shutil.which(name)
        entry: dict[str, Any] = {"path": path}
        if path and name in {"ollama", "ffmpeg", "node", "git", "claude", "ccr", "qwenpaw", "interpreter"}:
            flag = "-version" if name == "ffmpeg" else "--version"
            first = _run([path, flag], timeout=20).strip().splitlines()
            entry["version"] = first[0][:160] if first else ""
        res[name] = entry
    return res


def ffmpeg_capabilities() -> dict:
    ff = shutil.which("ffmpeg")
    if not ff:
        return {"present": False}
    enc = _run([ff, "-hide_banner", "-encoders"])
    flt = _run([ff, "-hide_banner", "-filters"])
    hw = _run([ff, "-hide_banner", "-hwaccels"])
    return {
        "present": True,
        "nvenc": {e: bool(re.search(rf"\b{e}\b", enc)) for e in NVENC_ENCODERS},
        "filters": {f: bool(re.search(rf"\s{f}\s", flt)) for f in FFMPEG_FILTERS},
        "hwaccels": [h.strip() for h in hw.splitlines()[1:] if h.strip()],
    }


def ollama_state(s: Settings) -> dict:
    ver = _http_json(f"{s.ollama_url}/api/version")
    tags = _http_json(f"{s.ollama_url}/api/tags") or {}
    ps = _http_json(f"{s.ollama_url}/api/ps") or {}
    env = {k: os.environ.get(k) for k in OLLAMA_TUNING} | {"OLLAMA_MODELS": os.environ.get("OLLAMA_MODELS")}
    return {
        "reachable": ver is not None and "_status" not in (ver or {}),
        "version": (ver or {}).get("version"),
        "models": [m.get("name") for m in tags.get("models", [])],
        "loaded": [{"name": m.get("name"), "size_vram_gb": round(m.get("size_vram", 0) / 2**30, 1)}
                   for m in ps.get("models", [])],
        "env": env,
    }


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def harvest_mcp_servers() -> dict[str, dict]:
    """Collect MCP servers already configured for Claude Code / Claude Desktop so the backup
    crew (QwenPaw, Open Interpreter) gets the same tools as the first team - e.g. davinci-resolve."""
    servers: dict[str, dict] = {}
    sources = [HOME / ".claude.json", HOME / ".claude" / "settings.json", Path.cwd() / ".mcp.json"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        sources.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    sources.append(HOME / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    sources.append(HOME / ".config" / "Claude" / "claude_desktop_config.json")
    for src in sources:
        data = _read_json(src)
        if not isinstance(data, dict):
            continue
        blocks = [data.get("mcpServers") or {}]
        for proj in (data.get("projects") or {}).values():
            if isinstance(proj, dict):
                blocks.append(proj.get("mcpServers") or {})
        for block in blocks:
            for name, cfg in block.items():
                if isinstance(cfg, dict) and name not in servers:
                    servers[name] = cfg | {"_source": str(src)}
    return servers


def apps() -> dict[str, Any]:
    pf = [Path(os.environ.get(v, "")) for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA") if os.environ.get(v)]

    def any_glob(patterns: list[str]) -> str | None:
        for base in pf + [Path("/opt"), Path("/Applications"), HOME]:
            for pat in patterns:
                hits = glob.glob(str(base / pat))
                if hits:
                    return hits[0]
        return None

    resolve_api = os.environ.get("RESOLVE_SCRIPT_API") or next(
        (p for p in [
            r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
            "/opt/resolve/Developer/Scripting",
        ] if Path(p).exists()), None)
    vscode_ext = HOME / ".vscode" / "extensions"
    roo = sorted(glob.glob(str(vscode_ext / "rooveterinaryinc.roo-cline-*")))
    return {
        "davinci_resolve": any_glob(["Blackmagic Design/DaVinci Resolve/Resolve.exe", "resolve/bin/resolve",
                                     "DaVinci Resolve/DaVinci Resolve.app"]),
        "resolve_scripting_api": resolve_api,
        "premiere_pro": any_glob(["Adobe/Adobe Premiere Pro*/Adobe Premiere Pro.exe", "Adobe Premiere Pro*"]),
        "topaz_video_ai": any_glob(["Topaz Labs LLC/Topaz Video AI/ffmpeg.exe", "Topaz Labs LLC/Topaz Video*",
                                    "Topaz Video AI.app"]),
        "comfyui_dir": any_glob(["ComfyUI", "ComfyUI_windows_portable", "Documents/ComfyUI", "*/ComfyUI"]),
        "lm_studio": any_glob(["Programs/LM Studio/LM Studio.exe", "LM Studio.app", ".lmstudio"]),
        "roo_code": roo[-1] if roo else None,
        "qwenpaw_workdir": str(HOME / ".qwenpaw") if (HOME / ".qwenpaw").exists() else None,
        "qwenpaw_desktop": any_glob(["Programs/QwenPaw/QwenPaw.exe", "QwenPaw/QwenPaw.exe", "QwenPaw.app"]),
        "ccr_dir": next((str(p) for p in [HOME / ".claude-code-router",
                                           Path(os.environ.get("APPDATA", "~")) / "claude-code-router"] if p.exists()), None),
        "claude_code_dir": str(HOME / ".claude") if (HOME / ".claude").exists() else None,
        "open_interpreter_profiles": _oi_profiles_dir(),
    }


def _oi_profiles_dir() -> str | None:
    try:
        from platformdirs import user_config_dir  # type: ignore

        p = Path(user_config_dir("open-interpreter", appauthor=False)) / "profiles"
        return str(p) if p.exists() else None
    except ImportError:
        return None


def services(s: Settings) -> dict[str, bool]:
    return {
        "ollama": _http_json(f"{s.ollama_url}/api/version") is not None,
        "qwenpaw": _http_json(f"{s.qwenpaw_url}/api/agents") is not None,
        "ccr_gateway": _http_json(f"{s.ccr_gateway_url}/v1/models") is not None,
        "comfyui": _http_json(f"{s.comfyui_url}/system_stats") is not None,
    }


def recommendations(inv: dict) -> list[str]:
    recs: list[str] = []
    ol = inv["ollama"]
    if not ol["reachable"]:
        recs.append("Start Ollama (tray app or `ollama serve`) - every crew seat depends on it.")
    for k, (val, why) in OLLAMA_TUNING.items():
        if (ol["env"].get(k) or "") != val:
            recs.append(f"Set {k}={val} ({why}). Windows: setx {k} {val}  then restart Ollama.")
    ff = inv["ffmpeg"]
    if not ff.get("present"):
        recs.append("Install FFmpeg (gyan.dev full build on Windows) - the crew's media tools shell out to it.")
    else:
        if not ff["nvenc"].get("hevc_nvenc"):
            recs.append("FFmpeg lacks hevc_nvenc: install a full build so renders use the RTX NVENC block.")
        for f in ("libvmaf", "vidstabdetect", "zscale"):
            if not ff["filters"].get(f):
                recs.append(f"FFmpeg lacks `{f}` (QC/stabilization/colorspace): use the gyan.dev *full* build.")
    pk = inv["python_packages"]
    missing = [d for d in ("langgraph", "langchain-ollama", "mcp", "librosa") if not pk.get(d)]
    if missing:
        recs.append(f"pip install {' '.join(missing)}  (or `pip install -e backup-crew[all]`).")
    if not (pk.get("faster-whisper") or pk.get("openai-whisper")):
        recs.append("pip install faster-whisper  -> enables the Captions & Localization seat (bs/en/de SRT).")
    if not pk.get("demucs"):
        recs.append("pip install demucs  -> enables stem isolation for the Acoustic Stem Inspector seat.")
    if not pk.get("open-interpreter"):
        recs.append("pip install open-interpreter  (in its own venv if it pins old deps).")
    if not inv["apps"].get("resolve_scripting_api"):
        recs.append("DaVinci Resolve scripting API not found: set RESOLVE_SCRIPT_API so the Resolve bridge seat can build timelines.")
    if not inv["mcp_servers"]:
        recs.append("No Claude MCP servers found to share - run doctor from the same user account Claude Code uses.")
    gpus = inv["gpus"]
    if gpus and gpus[0]["vram_total_gb"] < 20:
        recs.append(f"{gpus[0]['name']} ({gpus[0]['vram_total_gb']} GB): prefer MoE models (e.g. *-a3b, gpt-oss) or <=14B dense "
                    "for the orchestrator; keep the vision model <=12B so both stay resident.")
    return recs


def collect(s: Settings | None = None) -> dict:
    s = s or settings()
    inv = {
        "system": system_info(),
        "gpus": gpu_info(),
        "cli": cli_tools(),
        "ffmpeg": ffmpeg_capabilities(),
        "python_packages": python_packages(),
        "ollama": ollama_state(s),
        "services": services(s),
        "apps": apps(),
        "mcp_servers": harvest_mcp_servers(),
        "video_tools_dir": str(s.video_tools_dir) if s.video_tools_dir else None,
    }
    inv["recommendations"] = recommendations(inv)
    s.inventory_file.write_text(json.dumps(inv, indent=2), encoding="utf-8")
    return inv


def render_report(inv: dict) -> str:
    ok, no = "[x]", "[ ]"
    lines = ["# Maglaj crew - workstation inventory", ""]
    sysi = inv["system"]
    lines.append(f"OS {sysi['os']} | CPU {sysi['cpu']} ({sysi['cpu_threads']} threads) | RAM {sysi.get('ram_gb', '?')} GB")
    for g in inv["gpus"] or [{"name": "no NVIDIA GPU detected", "vram_total_gb": 0, "vram_free_gb": 0, "driver": "-"}]:
        lines.append(f"GPU {g['name']} | VRAM {g['vram_total_gb']} GB (free {g['vram_free_gb']}) | driver {g['driver']}")
    lines += ["", "## Services"] + [f"{ok if v else no} {k}" for k, v in inv["services"].items()]
    ol = inv["ollama"]
    lines += ["", f"## Ollama {ol.get('version') or '(not reachable)'} - {len(ol['models'])} models"]
    lines += [f"- {m}" for m in ol["models"]]
    lines += ["", "## CLIs"] + [f"{ok if v['path'] else no} {k} {v.get('version', '')}".rstrip() for k, v in inv["cli"].items()]
    lines += ["", "## Python packages"] + [f"{ok if v else no} {k} {v or ''}".rstrip() for k, v in inv["python_packages"].items()]
    ff = inv["ffmpeg"]
    if ff.get("present"):
        lines += ["", "## FFmpeg", "NVENC: " + ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in ff["nvenc"].items()),
                  "Filters: " + ", ".join(f"{k}={'yes' if v else 'no'}" for k, v in ff["filters"].items())]
    lines += ["", "## Apps"] + [f"{ok if v else no} {k} {v or ''}".rstrip() for k, v in inv["apps"].items()]
    lines += ["", f"## MCP servers shared from Claude ({len(inv['mcp_servers'])})"] + [f"- {k}" for k in inv["mcp_servers"]]
    lines += ["", "## Actions to max out the setup"] + [f"{i}. {r}" for i, r in enumerate(inv["recommendations"], 1)]
    return "\n".join(lines)
