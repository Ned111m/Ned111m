"""Machine-specific paths. Real values live in crew/local/paths.json (gitignored); the public repo ships
crew/paths.example.json with fictional placeholders."""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_f = REPO / "crew" / "local" / "paths.json"
PATHS = json.loads((_f if _f.exists() else REPO / "crew" / "paths.example.json").read_text(encoding="utf-8"))
