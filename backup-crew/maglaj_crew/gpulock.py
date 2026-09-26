"""One GPU, one heavy workload at a time.

The crew shares one RTX 5060 Ti (16 GB) between the agent model (qwen3.6:35b, already spills to CPU), the vision
models (qwen3.8:27b, qwen3-vl), TransNetV2 and renders. Measured 2026-09-25: when several jobs ran at once, Ollama had
to swap models while requests were still in flight, sat in "Stopping..." and every job starved for over an hour.
This machine-wide lock makes GPU work queue instead of collide. Reentrant inside a process (a job that holds it can
call _ask without deadlocking itself). Released automatically by the OS if the holder dies."""
from __future__ import annotations

import json, os, threading, time
from contextlib import contextmanager
from pathlib import Path

LOCK = Path.home() / ".maglaj-crew" / "gpu.lock"
HOLDER = LOCK.with_suffix(".holder.json")
_local = threading.local()
_fh = None


def holder() -> dict:
    try:
        return json.loads(HOLDER.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _try_lock(fh) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


@contextmanager
def gpu_lock(label: str, on_wait=None, poll_s: float = 2.0):
    """Block until this process owns the GPU. on_wait(holder_dict) is called while queued."""
    global _fh
    depth = getattr(_local, "depth", 0)
    # A child process started by a lock holder (e.g. delivery_gate -> qc_render -> DINOv2) inherits the hold. Without
    # this the child waits for its own ancestor: a real deadlock hit 2026-09-25 (gate stuck 19 min, GPU idle).
    if not depth and os.environ.get("MAGLAJ_GPU_LOCK_HELD") == "1":
        yield
        return
    if depth:
        _local.depth = depth + 1
        try:
            yield
        finally:
            _local.depth -= 1
        return
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK, "a+b")
    if fh.seek(0, 2) == 0:
        fh.write(b"\0"); fh.flush()
    last = 0.0
    while not _try_lock(fh):
        if on_wait and time.time() - last > 10:
            on_wait(holder()); last = time.time()
        time.sleep(poll_s)
    _fh = fh; _local.depth = 1
    os.environ["MAGLAJ_GPU_LOCK_HELD"] = "1"  # inherited by every subprocess this holder starts
    HOLDER.write_text(json.dumps({"label": label, "pid": os.getpid(), "since": time.time()}), encoding="utf-8")
    try:
        yield
    finally:
        _local.depth = 0
        os.environ.pop("MAGLAJ_GPU_LOCK_HELD", None)
        try:
            HOLDER.unlink(missing_ok=True)
            if os.name == "nt":
                import msvcrt
                fh.seek(0); msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            fh.close(); _fh = None
