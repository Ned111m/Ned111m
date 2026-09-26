"""Push crew/RULES.md (the single source of the crew's operating rules) into every frontend that is not QwenPaw.
QwenPaw reads it directly in provision_qwenpaw.py. Idempotent: replaces the text between the markers.
  - deepagents: AGENTS.md (path in crew/local/paths.json)
  - Cline: .clinerules file (path in crew/local/paths.json)
Run after every edit to crew/RULES.md, then re-run provision_qwenpaw.py."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BEGIN, END = "<!-- crew-rules:begin (generated from backup-crew/crew/RULES.md by sync_rules.py; edit that file) -->", "<!-- crew-rules:end -->"
from maglaj_crew.localpaths import PATHS
TARGETS = {
    Path(PATHS["deepagents_agents_md"]): "## MANDATORY (every task, before anything else)",
    Path(PATHS["cline_rules"]): None,
}


def render() -> str:
    body = (REPO / "crew" / "RULES.md").read_text(encoding="utf-8").strip()
    return f"{BEGIN}\n{body}\n{END}"


def sync() -> list[str]:
    block, done = render(), []
    for path, legacy_heading in TARGETS.items():
        if not path.exists():
            continue
        s = path.read_text(encoding="utf-8")
        if BEGIN in s:
            s = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: block, s, flags=re.S)
        elif legacy_heading and legacy_heading in s:
            # replace the hand-written MANDATORY section (up to the next "## " heading) with the generated block
            a = s.index(legacy_heading); b = s.find("\n## ", a + len(legacy_heading))
            s = s[:a] + block + "\n\n" + (s[b + 1:] if b >= 0 else "")
        else:
            # put it right after the first heading line, at the top where it gets read
            first_nl = s.find("\n") + 1 if s.startswith("#") else 0
            s = s[:first_nl] + "\n" + block + "\n\n" + s[first_nl:]
        path.write_text(s, encoding="utf-8"); done.append(str(path))
    return done


if __name__ == "__main__":
    print("synced:", sync())
