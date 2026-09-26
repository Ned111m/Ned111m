"""Detached runner for long maglaj-crew tools (renders, QC, mastering, indexing).

Frontends cut tool calls off after 60-120 s (QwenPaw shell 60 s, call 120 s) while qc_render or a DNxHR master
takes minutes to hours. start_job() spawns this module as its own process, so the work survives the MCP call,
the MCP server and even a QwenPaw restart. State lives in ~/.maglaj-crew/jobs/<id>.json.

Reliability (2026-09-25, after jobs died silently and agents polled "running" for an hour):
- stdout/stderr + faulthandler go to jobs/<id>.log, so a crash always leaves a reason
- GPU work queues behind one machine-wide lock (status "waiting_gpu", with who holds it)
- a heartbeat every 15 s; job_status treats a stale heartbeat or a dead pid as an error
- a hard timeout per tool; on expiry the job is marked error and the process exits
"""
from __future__ import annotations

import faulthandler, json, os, sys, threading, time, traceback
from pathlib import Path

JOBS = Path.home() / ".maglaj-crew" / "jobs"
HEARTBEAT_S = 15
TIMEOUT_S = {"deliver_dnxhr": 4 * 3600, "master_audio": 3600, "index_footage": 6 * 3600}
DEFAULT_TIMEOUT_S = 2 * 3600


def _write(job_id: str, **kw) -> None:
    p = JOBS / f"{job_id}.json"
    d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    d.update(kw)
    tmp = p.with_suffix(f".{os.getpid()}.tmp"); tmp.write_text(json.dumps(d, ensure_ascii=False, default=str), encoding="utf-8"); tmp.replace(p)


def main(job_id: str) -> None:
    # A WMI-spawned process has no console: without this, a crash leaves no trace and job_status says "running".
    log = open(JOBS / f"{job_id}.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    faulthandler.enable(file=log)
    os.environ.pop("MAGLAJ_CREW_ROLE", None)  # the runner may call any tool; the allowlist was enforced at start_job
    spec = json.loads((JOBS / f"{job_id}.json").read_text(encoding="utf-8"))
    timeout = TIMEOUT_S.get(spec["tool"], DEFAULT_TIMEOUT_S)
    _write(job_id, pid=os.getpid(), heartbeat=time.time(), timeout_s=timeout)
    finished = threading.Event()

    def beat():
        while not finished.wait(HEARTBEAT_S):
            _write(job_id, heartbeat=time.time())
    threading.Thread(target=beat, daemon=True).start()

    from maglaj_crew.gpulock import gpu_lock
    try:
        with gpu_lock(f"job {job_id} {spec['tool']}",
                      on_wait=lambda h: _write(job_id, status="waiting_gpu", gpu_held_by=h.get("label"))):
            start = time.time()
            _write(job_id, status="running", started=start, gpu_held_by=None)

            def watchdog():
                if not finished.wait(timeout):
                    _write(job_id, status="error", finished=time.time(), error=f"timeout after {timeout} s")
                    log.flush(); os._exit(3)
            threading.Thread(target=watchdog, daemon=True).start()

            from maglaj_crew import mcp_server as M
            result = getattr(M, spec["tool"])(**spec["args"])
        finished.set()
        _write(job_id, status="done", finished=time.time(), result=result)
    except Exception as e:
        finished.set()
        traceback.print_exc()
        _write(job_id, status="error", finished=time.time(), error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-4000:])


if __name__ == "__main__":
    main(sys.argv[1])
