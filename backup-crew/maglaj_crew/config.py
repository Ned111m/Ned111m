"""Paths and runtime settings. Every value can be overridden via environment variables."""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
REPO_DIR = PKG_DIR.parent
SKILLS_DIR = REPO_DIR / "skills"
ROSTER_FILE = REPO_DIR / "crew" / "roster.yaml"

IS_WINDOWS = platform.system() == "Windows"
HOME = Path.home()


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


@dataclass(frozen=True)
class Settings:
    ollama_url: str = os.environ.get("OLLAMA_HOST_URL", os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
    qwenpaw_url: str = os.environ.get("QWENPAW_URL", "http://127.0.0.1:8088").rstrip("/")
    qwenpaw_token: str = os.environ.get("QWENPAW_TOKEN", "")
    ccr_gateway_url: str = os.environ.get("CCR_GATEWAY_URL", "http://127.0.0.1:3456").rstrip("/")
    comfyui_url: str = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
    state_dir: Path = field(default_factory=lambda: _env_path("MAGLAJ_CREW_STATE", HOME / ".maglaj-crew"))
    # Optional checkout of github.com/Ned111m/dron-maglaj-video-tools (beat_grid.py, beat_ramp_plan.py, Remotion comps).
    video_tools_dir: Path | None = field(
        default_factory=lambda: _env_path("MAGLAJ_TOOLS_DIR", Path()) if os.environ.get("MAGLAJ_TOOLS_DIR") else _guess_tools_dir()
    )
    num_ctx: int = int(os.environ.get("MAGLAJ_NUM_CTX", "32768"))
    vram_gb: float = float(os.environ.get("MAGLAJ_VRAM_GB", "0"))  # 0 = autodetect via nvidia-smi
    allow_cloud_models: bool = os.environ.get("MAGLAJ_ALLOW_CLOUD", "0") == "1"

    @property
    def roster_lock(self) -> Path:
        return self.state_dir / "roster.lock.json"

    @property
    def inventory_file(self) -> Path:
        return self.state_dir / "inventory.json"

    @property
    def library_file(self) -> Path:
        return self.state_dir / "library.json"


def _guess_tools_dir() -> Path | None:
    for cand in (
        REPO_DIR.parent.parent / "dron-maglaj-video-tools",
        REPO_DIR.parent / "dron-maglaj-video-tools",
        HOME / "dron-maglaj-video-tools",
        HOME / "Documents" / "GitHub" / "dron-maglaj-video-tools",
        HOME / "source" / "repos" / "dron-maglaj-video-tools",
    ):
        if (cand / "scripts" / "beat_grid.py").exists():
            return cand
    return None


def settings() -> Settings:
    s = Settings()
    s.state_dir.mkdir(parents=True, exist_ok=True)
    return s
