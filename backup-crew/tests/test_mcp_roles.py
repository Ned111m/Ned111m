"""Spawns the real MCP server over stdio per seat and checks the exposed tool set == roster allowlist."""
import asyncio, json, os, sys
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
roster = json.loads((REPO / "crew" / "roster.json").read_text(encoding="utf-8"))


async def tools_for(role):
    env = {**os.environ, "MAGLAJ_CREW_ROLE": role} if role else dict(os.environ)
    params = StdioServerParameters(command=PY, args=["-m", "maglaj_crew.mcp_server"], env=env, cwd=str(REPO))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            return sorted(t.name for t in (await s.list_tools()).tools)


async def main():
    all_tools = await tools_for(None)
    print("ALL", len(all_tools), all_tools)
    bad = 0
    for seat in roster["seats"]:
        want = sorted(set(roster["common_tools"]) | set(seat["tools"]))
        missing = [t for t in want if t not in all_tools]
        got = await tools_for(seat["id"])
        ok = got == want and not missing
        bad += not ok
        print(("OK " if ok else "BAD"), seat["id"], len(got), "missing_in_server=" + str(missing) if missing else "", "" if ok else f"got={got}")
    sys.exit(1 if bad else 0)

asyncio.run(main())
