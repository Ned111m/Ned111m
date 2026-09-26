"""Reliability of detached jobs (2026-09-25, after jobs died silently and agents polled "running" for an hour).
1. Two GPU jobs started together run one after the other (the second reports waiting_gpu), both finish.
2. A job whose process is killed is reported as error by job_status, never as running."""
import json, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maglaj_crew import mcp_server as M  # noqa: E402

VIDEO = sys.argv[1] if len(sys.argv) > 1 else r"C:\AI\crew-eval\gate_controls\control.mp4"
ok = True

a = M.start_job("repeat_shot_audit", {"video": VIDEO})["job_id"]
b = M.start_job("repeat_shot_audit", {"video": VIDEO})["job_id"]
seen_wait, t0 = False, time.time()
while time.time() - t0 < 900:
    sa, sb = M.job_status(a, wait_s=0)["status"], M.job_status(b, wait_s=0)["status"]
    seen_wait |= "waiting_gpu" in (sa, sb)
    if sa in ("done", "error") and sb in ("done", "error"):
        break
    time.sleep(2)
print("queue:", sa, sb, "second job waited for GPU:", seen_wait, f"{round(time.time() - t0)} s")
ok &= sa == sb == "done" and seen_wait

k = M.start_job("repeat_shot_audit", {"video": VIDEO})["job_id"]
while not M.job_status(k, wait_s=0).get("pid"):
    time.sleep(1)
time.sleep(3)
pid = M.job_status(k, wait_s=0)["pid"]
subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
time.sleep(2)
sk = M.job_status(k, wait_s=0)
print("killed job:", sk["status"], sk.get("error"))
ok &= sk["status"] == "error"

print("PASS" if ok else "FAIL"); sys.exit(0 if ok else 1)
