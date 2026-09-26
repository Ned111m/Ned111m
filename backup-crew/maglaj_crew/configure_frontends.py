"""Point Claude Code Router and Open Interpreter at the crew's measured local models (crew/crew_models.json).

CCR (desktop app v3, config in %APPDATA%/claude-code-router/config.sqlite): every Claude model slot of the
Claude Code profile (opus/sonnet/fable/haiku/small-fast) maps to the local orchestrator, so `claude` launched
through CCR runs the FULL first-team harness (~/.claude/agents, skills, hooks, MCP) on local models. One model for
all slots on purpose: two different 20+ GB models on a 16 GB GPU would swap on every subagent call.

Open Interpreter (Codex-based, ~/.openinterpreter/config.toml): a `maglaj` profile on the Ollama provider with the
maglaj-crew MCP server (hands-on seat, all 20 tools).

Run with the CCR app CLOSED (it owns the sqlite file while running). Backups are written next to each file.
"""
from __future__ import annotations

import json, re, shutil, sqlite3, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOME = Path.home()
MODELS = json.loads((REPO / "crew" / "crew_models.json").read_text(encoding="utf-8"))
CCR_DB = HOME / "AppData" / "Roaming" / "claude-code-router" / "config.sqlite"
OI_TOML = HOME / ".openinterpreter" / "config.toml"
STAMP = time.strftime("%Y%m%d-%H%M%S")


def configure_ccr() -> dict:
    shutil.copy2(CCR_DB, CCR_DB.with_name(f"config.sqlite.bak-{STAMP}"))
    con = sqlite3.connect(CCR_DB)
    cfg = json.loads(con.execute("select value_json from app_config where key='default'").fetchone()[0])
    prov = next(p for p in cfg["Providers"] if p["api_base_url"].startswith("http://localhost:11434"))
    local = [MODELS["orchestrator"], MODELS["vision_second"], MODELS["vision_fast_sampler"]]
    prov["models"] = list(dict.fromkeys(local + [m for m in prov["models"] if m not in local]))
    slot = f"{prov['name']}/{MODELS['orchestrator']}"
    slots = {k: slot for k in ("model", "opusModel", "sonnetModel", "fableModel", "haikuModel", "smallFastModel")}
    cfg["profile"]["claudeCode"].update(slots)
    for p in cfg["profile"]["profiles"]:
        if p.get("agent") == "claude-code":
            p.update(slots)
        if p.get("agent") == "opencode":
            p["model"] = slot
    cfg["preferredProvider"] = prov["name"]
    cfg["toolHub"]["llm"]["model"] = MODELS["orchestrator"]
    for v in cfg.get("virtualModelProfiles", []):
        if v.get("baseModel", {}).get("mode") == "fixed":
            v["baseModel"]["fixedModel"] = slot
            v.setdefault("metadata", {}).setdefault("fusionVision", {})["modelSelector"] = slot
    # Local models think longer than Claude; 10 min was the old cap. Long renders are tool time, not model time.
    cfg["API_TIMEOUT_MS"] = max(cfg.get("API_TIMEOUT_MS", 0), 1_800_000)
    con.execute("update app_config set value_json=?, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') where key='default'",
                (json.dumps(cfg, ensure_ascii=False),))
    con.commit(); con.close()
    return {"provider": prov["name"], "slot": slot, "models": prov["models"][:4]}


OI_BLOCK_START, OI_BLOCK_END = "# >>> maglaj-crew (managed) >>>", "# <<< maglaj-crew (managed) <<<"


def configure_oi() -> dict:
    txt = OI_TOML.read_text(encoding="utf-8") if OI_TOML.exists() else ""
    shutil.copy2(OI_TOML, OI_TOML.with_name(f"config.toml.bak-{STAMP}")) if OI_TOML.exists() else None
    txt = re.sub(re.escape(OI_BLOCK_START) + r".*?" + re.escape(OI_BLOCK_END) + r"\n?", "", txt, flags=re.S)
    py = str(REPO / ".venv" / "Scripts" / "python.exe").replace("\\", "\\\\")
    srv = str(REPO / "maglaj_crew" / "mcp_server.py").replace("\\", "\\\\")
    block = f"""{OI_BLOCK_START}
[model_providers.ollama-maglaj]
name = "Ollama (local)"
base_url = "http://localhost:11434/v1"
wire_api = "chat"

[mcp_servers.maglaj-crew]
command = "{py}"
args = ["{srv}"]
startup_timeout_sec = 60
tool_timeout_sec = 7200

[mcp_servers.maglaj-crew.env]
PYTHONUTF8 = "1"
{OI_BLOCK_END}
"""
    OI_TOML.write_text(txt.rstrip() + "\n\n" + block, encoding="utf-8")
    # OI 0.0.45 profiles are separate files (<name>.config.toml); a [profiles.x] table in config.toml is rejected.
    prof = OI_TOML.with_name("maglaj.config.toml")
    prof.write_text(f'# managed by maglaj-crew configure_frontends.py\nmodel_provider = "ollama-maglaj"\nmodel = "{MODELS["orchestrator"]}"\n', encoding="utf-8")
    return {"profile": "maglaj", "model": MODELS["orchestrator"], "files": [str(OI_TOML), str(prof)]}


if __name__ == "__main__":
    which = set(sys.argv[1:]) or {"ccr", "oi"}
    if "ccr" in which:
        print("CCR", json.dumps(configure_ccr(), ensure_ascii=False))
    if "oi" in which:
        print("OI ", json.dumps(configure_oi(), ensure_ascii=False))
