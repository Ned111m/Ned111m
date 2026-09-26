"""Long tool as a detached job: must survive the MCP session that started it, and respect the seat allowlist."""
import asyncio, json, os, sys, time
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = Path(__file__).resolve().parents[1]
PY = str(REPO / ".venv" / "Scripts" / "python.exe")
VIDEO = sys.argv[1]


async def call(role, tool, args):
    p = StdioServerParameters(command=PY, args=["-m", "maglaj_crew.mcp_server"], env={**os.environ, "MAGLAJ_CREW_ROLE": role}, cwd=str(REPO))
    async with stdio_client(p) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(tool, args)
            return json.loads(res.content[0].text)


async def main():
    denied = await call("colorist", "start_job", {"tool": "repeat_shot_audit", "args": {"video": VIDEO}})
    print("colorist start repeat_shot_audit ->", denied)
    j = await call("continuity-auditor", "start_job", {"tool": "repeat_shot_audit", "args": {"video": VIDEO}})
    print("started", j)                        # MCP session is closed here
    for _ in range(60):
        st = await call("continuity-auditor", "job_status", {"job_id": j["job_id"]})
        if st["status"] in ("done", "error"): break
        time.sleep(5)
    print("final status", st["status"], st.get("error", ""))
    if st["status"] == "done":
        print("repeats", [(h["t_a"], h["t_b"], h["hamming"]) for h in st["result"]["candidate_repeats"][:5]], "segments", st["result"]["segments"])
    ok = "error" in denied and st["status"] == "done" and st["result"]["candidate_repeats"]
    sys.exit(0 if ok else 1)

asyncio.run(main())
