"""Create/update the backup crew as QwenPaw agents through QwenPaw's own REST API. Idempotent (matches by name).

Per seat: model (crew_models.json), maglaj-crew MCP filtered to the seat's tools (MAGLAJ_CREW_ROLE), extra MCP servers,
skills from ~/.claude/skills (pool via skill_paths), and workspace prompt files carrying Nedim's CLAUDE.md plus the
first-team agent brief(s) VERBATIM, so the backup seat knows everything the first-team seat knows.
"""
from __future__ import annotations

import json, os, re, sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
HOME = Path.home()
ROSTER = json.loads((REPO / "crew" / "roster.json").read_text(encoding="utf-8"))
MODELS = json.loads((REPO / "crew" / "crew_models.json").read_text(encoding="utf-8"))
_srv = REPO / "crew" / "local" / "mcp_servers.json"  # machine-specific paths live in the gitignored local copy
SERVERS = json.loads((_srv if _srv.exists() else REPO / "crew" / "mcp_servers.example.json").read_text(encoding="utf-8"))
PORT = (HOME / ".qwenpaw" / "desktop_port").read_text().strip() if (HOME / ".qwenpaw" / "desktop_port").exists() else "8088"
API = os.environ.get("QWENPAW_API", f"http://127.0.0.1:{PORT}/api")
AGENTS_DIR = HOME / ".claude" / "agents"
PY_VENV = str(REPO / ".venv" / "Scripts" / "python.exe")

TOOL_MAP = """## How your brief maps onto this runtime
Your first-team brief below was written for Claude Code. Here it maps like this:
- Bash -> execute_shell_command (PowerShell on Windows). Python is C:\\Users\\Admin\\AppData\\Local\\Programs\\Python\\Python311\\python.exe, never `python3`.
- Read / Write / Edit / Grep / Glob -> read_file / write_file / edit_file / grep_search / glob_search.
- Agent (delegate to a specialist) -> the multi_agent_collaboration skill: talk to the crew seat that owns the job (directory below).
- Skill -> your enabled skills. mcp__davinci-resolve__* -> davinci-resolve MCP. mcp__kinocut__* -> kinocut MCP.
- Measurement (loudness, probe, shots, repeats, vision cross-check, the real qc_render/master_audio/deliver_dnxhr scripts) -> maglaj-crew MCP.
If a tool your brief names does not exist here, say so in one sentence and use the closest REAL tool; never pretend a step ran.
"""

# Single source of the crew rules (2026-09-25): crew/RULES.md. sync_rules.py pushes the same text into
# deepagents AGENTS.md and Cline .clinerules, so every frontend runs identical rules.
RULES = (REPO / "crew" / "RULES.md").read_text(encoding="utf-8")


def s(method: str, path: str, **kw):
    r = requests.request(method, API + path, timeout=120, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code} {r.text[:400]}")
    return r.json() if r.text else {}


def strip_frontmatter(md: str) -> str:
    return re.sub(r"\A---\n.*?\n---\n", "", md, flags=re.S).strip()


def mirrored_mcp_tools(seat: dict) -> dict[str, list[str]]:
    """{server: [tools]} exactly as the first-team briefs this seat mirrors grant them (frontmatter `tools:` line).
    This is how Resolve write access follows the first team: resolve-conform-operator gets everything,
    dctl-colorist its 15 color tools, audio-mix-engineer its 13, sound-director its 4, nobody guesses."""
    out: dict[str, set[str]] = {}
    for m in seat["mirrors"]:
        head = (AGENTS_DIR / f"{m}.md").read_text(encoding="utf-8").split("\n---", 2)[0]
        line = next((l for l in head.splitlines() if l.startswith("tools:")), "")
        for srv, tool in re.findall(r"mcp__([A-Za-z0-9_-]+?)__([A-Za-z0-9_]+)", line):
            if srv in SERVERS:
                out.setdefault(srv, set()).add(tool)
    return {k: sorted(v) for k, v in out.items()}


def mcp_clients(seat: dict) -> dict:
    crew = {"name": "maglaj-crew", "description": f"Measured tool layer, filtered to seat '{seat['id']}'", "enabled": True,
            "transport": "stdio", "command": PY_VENV, "args": ["-m", "maglaj_crew.mcp_server"], "cwd": str(REPO),
            "env": {"MAGLAJ_CREW_ROLE": seat["id"], "PYTHONUTF8": "1"}, "url": "", "headers": {}}
    out = {"maglaj-crew": crew}
    mirrored = mirrored_mcp_tools(seat)
    for name in dict.fromkeys(list(ROSTER.get("common_mcp", [])) + list(seat.get("extra_mcp", [])) + list(mirrored)):
        d = SERVERS[name]
        out[name] = {"name": name, "description": d.get("description", name), "enabled": True,
                     "transport": "streamable_http" if d.get("url") else "stdio", "command": d.get("command", ""),
                     "args": d.get("args", []), "env": d.get("env", {}), "cwd": "", "url": d.get("url", ""), "headers": {}}
        # Docker's gateway always adds its management tools (mcp-add/mcp-remove/code-mode; only a GLOBAL switch turns them
        # off, which would change the first team too). Allowlist in QwenPaw so a no-approval local agent cannot install servers.
        if "--tools" in d.get("args", []):
            out[name]["tools"] = d["args"][d["args"].index("--tools") + 1].split(",")
        if d.get("allow_tools"):  # e.g. crew-memory: read/add only, destructive tools hidden from no-approval agents
            out[name]["tools"] = d["allow_tools"]
        if name in mirrored:  # first-team brief grants specific tools of this server: mirror exactly
            out[name]["tools"] = mirrored[name]
    return out


def directory() -> str:
    lines = ["## Crew directory (delegate by seat id)"]
    for x in ROSTER["seats"]:
        lines.append(f"- `{x['id']}` ({x['name']}): {x['role']}")
    return "\n".join(lines)


def agents_md(seat: dict) -> str:
    claude_md = (HOME / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    parts = [f"# {seat['name']} (backup crew seat `{seat['id']}`)", "",
             f"You are the local backup for the Dron Maglaj first team"
             + (f": you stand in for {', '.join('`'+m+'`' for m in seat['mirrors'])}." if seat["mirrors"] else ", in a NEW seat the first team does not have as a separate role."),
             f"Model: {MODELS[seat['model']]} via Ollama. Your job: {seat['role']}", "", RULES, TOOL_MAP, directory(), "",
             "## Nedim's standing instructions (~/.claude/CLAUDE.md, verbatim; these override everything below)", claude_md]
    for m in seat["mirrors"]:
        parts += ["", f"## First-team brief: {m} (verbatim from ~/.claude/agents/{m}.md)", strip_frontmatter((AGENTS_DIR / f"{m}.md").read_text(encoding="utf-8"))]
    return "\n".join(parts) + "\n"


SOUL = """# Soul
Senior post-production professional on a small, demanding crew. Standard: work DJI would point to; beat a named reference.
Rigorous and plain-spoken. Evidence before claims. When unsure, measure; when you cannot measure, say so.
Nedim's notes after watching a render are decisions, not suggestions.
"""

# Owner profile is private: crew/local/profile.md (gitignored). The public repo only has profile.example.md.
_prof = REPO / "crew" / "local" / "profile.md"
PROFILE = _prof.read_text(encoding="utf-8") if _prof.exists() else (REPO / "crew" / "profile.example.md").read_text(encoding="utf-8")


def skills_for(seat):
    return sorted(set(ROSTER["common_skills"]) | set(seat["skills"]) | {"multi_agent_collaboration"})


def provision(only: set[str] | None = None) -> list[dict]:
    existing = {a["name"]: a for a in s("GET", "/agents")["agents"]}
    report = []
    for seat in ROSTER["seats"]:
        if only and seat["id"] not in only:
            continue
        model = {"provider_id": "ollama", "model": MODELS[seat["model"]]}
        desc = f"[maglaj-crew:{seat['id']}] {seat['role']}"
        if seat["name"] in existing:
            aid = existing[seat["name"]]["id"]; created = False
        else:
            aid = s("POST", "/agents", json={"name": seat["name"], "description": desc, "language": "en", "active_model": model,
                                              "skill_names": skills_for(seat)})["id"]; created = True
        agent = s("GET", f"/agents/{aid}")
        # approval_level OFF + MCP policy allow: explicitly authorized by Nedim for all 22 crew seats (2026-09-24).
        # QwenPaw's global tool_guard rule SAFETY_CHECKS_DESTRUCTIVE_COMMAND stays on and still blocks rm -rf class commands.
        agent.update({"description": desc, "active_model": model, "language": "en", "approval_level": "OFF"})
        s("PUT", f"/agents/{aid}", json=agent)
        # MCP lives in per-workspace driver cards (drivers/mcp/*.yaml), created through QwenPaw's own /mcp API.
        # agent.json `mcp` is the legacy field and is ignored after creation. Tool-call policy is left at QwenPaw's default.
        hdr = {"X-Agent-Id": aid}
        for key, client in mcp_clients(seat).items():
            try:
                s("POST", "/mcp", json={"client_key": key, "client": client}, headers=hdr)
            except RuntimeError:
                s("PUT", f"/mcp/{key}", json=client, headers=hdr)
            card = Path(agent["workspace_dir"]) / "drivers" / "mcp" / f"{key}.yaml"
            card.write_text(re.sub(r"default_effect: \w+", "default_effect: allow", card.read_text(encoding="utf-8")), encoding="utf-8")
        have = set(next((w["skill_names"] for w in s("GET", "/skills/workspaces") if w["agent_id"] == aid), []))
        for sk in skills_for(seat):
            if sk not in have:
                s("POST", "/skills/pool/download", json={"skill_name": sk, "targets": [{"workspace_id": aid}]})
        ws = Path(agent["workspace_dir"])
        (ws / "AGENTS.md").write_text(agents_md(seat), encoding="utf-8")
        (ws / "SOUL.md").write_text(SOUL, encoding="utf-8")
        (ws / "PROFILE.md").write_text(PROFILE, encoding="utf-8")
        (ws / "BOOTSTRAP.md").unlink(missing_ok=True)
        report.append({"seat": seat["id"], "agent_id": aid, "created": created, "model": model["model"],
                       "mcp": list(mcp_clients(seat)), "skills": skills_for(seat)})
        print(json.dumps(report[-1], ensure_ascii=False), flush=True)
    return report


def settle_policies(report: list[dict], rounds: int = 5) -> int:
    """QwenPaw's DriverConfigWatcher (2 s poll) can rewrite a card after our edit and restore `ask`.
    Re-apply until two consecutive checks are clean. Returns the number of cards still not `allow`."""
    import time
    cards = [c for r in report for c in (Path(s("GET", f"/agents/{r['agent_id']}")["workspace_dir"]) / "drivers" / "mcp").glob("*.yaml")
             if c.stem != "anysearch"]
    clean = 0
    for _ in range(rounds):
        bad = [c for c in cards if "default_effect: allow" not in c.read_text(encoding="utf-8")]
        for c in bad:
            c.write_text(re.sub(r"default_effect: \w+", "default_effect: allow", c.read_text(encoding="utf-8")), encoding="utf-8")
        clean = clean + 1 if not bad else 0
        if clean >= 2:
            return 0
        time.sleep(6)
    return len([c for c in cards if "default_effect: allow" not in c.read_text(encoding="utf-8")])


if __name__ == "__main__":
    rep = provision(set(sys.argv[1:]) or None)
    left = settle_policies(rep)
    print(json.dumps({"cards_not_allow": left}))
    sys.exit(1 if left else 0)
