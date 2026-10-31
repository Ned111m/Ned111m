#!/usr/bin/env python3
"""comfy_queue — run ComfyUI API-format workflows headless (Wan 2.x / LTX-Video / FLUX / upscalers).

Export the workflow from ComfyUI with "Save (API Format)". Inputs are patched by node id or node title:
  --set 3.seed=random  --set "Positive Prompt.text=drone over Bosna river at dusk"  --set 52.image=@frame.png
  (@file uploads the file via /upload/image and sets the returned name)

  comfy_queue.py wan22_i2v_api.json --set 3.seed=random --set 6.text="..." --out renders/
  comfy_queue.py wan22_i2v_api.json --batch prompts.jsonl --out renders/   # one job per line {"6.text": "..."}

Before queueing, the free VRAM is checked (/system_stats) and the run waits while the queue is busy if
--wait-idle is set, so it never preempts another agent's job on the same GPU.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

DEFAULT = "http://127.0.0.1:8188"


class Comfy:
    def __init__(self, base: str = DEFAULT, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex

    def _req(self, path: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.read()

    def get(self, path: str) -> dict:
        return json.loads(self._req(path))

    def post_json(self, path: str, obj: dict) -> dict:
        return json.loads(self._req(path, json.dumps(obj).encode(), {"Content-Type": "application/json"}))

    def upload(self, file: Path, kind: str = "input") -> str:
        boundary = uuid.uuid4().hex
        ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{file.name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + file.read_bytes() + \
               (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\n{kind}\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue\r\n--{boundary}--\r\n").encode()
        res = json.loads(self._req("/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"}))
        return f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]

    def queue_busy(self) -> bool:
        q = self.get("/queue")
        return bool(q.get("queue_running") or q.get("queue_pending"))

    def free_vram_gb(self) -> float | None:
        try:
            devs = self.get("/system_stats").get("devices", [])
            return round(max(d.get("vram_free", 0) for d in devs) / 1024 ** 3, 2) if devs else None
        except (urllib.error.URLError, ValueError):
            return None

    def submit(self, workflow: dict) -> str:
        try:
            res = self.post_json("/prompt", {"prompt": workflow, "client_id": self.client_id})
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise SystemExit(f"ComfyUI rejected the workflow ({e.code}): {detail[:2000]}") from e
        if res.get("node_errors"):
            raise SystemExit(f"node errors: {json.dumps(res['node_errors'])[:2000]}")
        return res["prompt_id"]

    def wait(self, prompt_id: str, poll: float = 2.0, timeout: float = 6 * 3600) -> dict:
        t0 = time.time()
        while time.time() - t0 < timeout:
            hist = self.get(f"/history/{prompt_id}")
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
                    raise RuntimeError(f"execution error: {json.dumps(msgs)[:2000]}")
                if status.get("completed", True):
                    return entry
            time.sleep(poll)
        raise TimeoutError(prompt_id)

    def download_outputs(self, entry: dict, out_dir: Path) -> list[str]:
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for node_id, outputs in entry.get("outputs", {}).items():
            for key in ("images", "gifs", "videos", "audio"):
                for f in outputs.get(key, []):
                    if f.get("type") == "temp":
                        continue  # previews
                    q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")})
                    dst = out_dir / f["filename"]
                    dst.write_bytes(self._req(f"/view?{q}"))
                    saved.append(str(dst))
        return saved


def _coerce(v: str):
    if v == "random":
        return random.randint(0, 2 ** 53 - 1)  # ComfyUI seeds are 64-bit; JS-safe range for UI round-trips
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    if v in ("true", "false"):
        return v == "true"
    return v


def apply_overrides(wf: dict, overrides: dict[str, object], comfy: Comfy | None = None) -> dict:
    titles = {n.get("_meta", {}).get("title"): k for k, n in wf.items()}
    for key, raw in overrides.items():
        node_ref, _, field = key.rpartition(".")
        node_id = node_ref if node_ref in wf else titles.get(node_ref)
        if node_id is None:
            raise SystemExit(f"--set {key}: node '{node_ref}' not found (ids: {sorted(wf)[:20]}...)")
        inputs = wf[node_id].setdefault("inputs", {})
        if field not in inputs:
            raise SystemExit(f"--set {key}: node {node_id} ({wf[node_id].get('class_type')}) has no input '{field}'; "
                             f"available: {sorted(inputs)}")
        val = raw
        if isinstance(raw, str) and raw.startswith("@"):
            if comfy is None:
                raise SystemExit("@file uploads need a live ComfyUI")
            val = comfy.upload(Path(raw[1:]))
        elif isinstance(raw, str):
            val = _coerce(raw)
        inputs[field] = val
    return wf


def parse_sets(sets: list[str]) -> dict[str, str]:
    out = {}
    for s in sets:
        k, sep, v = s.partition("=")
        if not sep:
            raise SystemExit(f"--set expects node.field=value, got {s}")
        out[k] = v
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("workflow", help="API-format workflow JSON")
    ap.add_argument("--server", default=DEFAULT)
    ap.add_argument("--set", action="append", default=[], help="node.field=value (repeatable)")
    ap.add_argument("--batch", help="JSONL: each line is an override dict -> one job")
    ap.add_argument("--out", default="comfy_out")
    ap.add_argument("--min-vram-gb", type=float, default=0.0, help="refuse to queue below this free VRAM")
    ap.add_argument("--wait-idle", action="store_true", help="wait until the ComfyUI queue is empty before each job")
    ap.add_argument("--dry-run", action="store_true", help="print patched workflow, do not queue")
    a = ap.parse_args()

    base_wf = json.loads(Path(a.workflow).read_text(encoding="utf-8"))
    if "nodes" in base_wf and "links" in base_wf:
        raise SystemExit("This is a UI-format workflow. Re-export with 'Save (API Format)'.")
    jobs = [parse_sets(a.set)]
    if a.batch:
        jobs = [{**jobs[0], **json.loads(line)} for line in Path(a.batch).read_text().splitlines() if line.strip()]
    comfy = None if a.dry_run else Comfy(a.server)
    results = []
    for i, ov in enumerate(jobs):
        wf = apply_overrides(json.loads(json.dumps(base_wf)), ov, comfy)
        if a.dry_run:
            print(json.dumps(wf, indent=2))
            continue
        assert comfy is not None
        if a.wait_idle:
            while comfy.queue_busy():
                time.sleep(10)
        vram = comfy.free_vram_gb()
        if a.min_vram_gb and vram is not None and vram < a.min_vram_gb:
            raise SystemExit(f"free VRAM {vram} GB < required {a.min_vram_gb} GB — another process holds the GPU")
        pid = comfy.submit(wf)
        print(f"[{i + 1}/{len(jobs)}] queued {pid} (free VRAM {vram} GB)", file=sys.stderr)
        entry = comfy.wait(pid)
        files = comfy.download_outputs(entry, Path(a.out))
        results.append({"promptId": pid, "overrides": ov, "files": files})
    if not a.dry_run:
        print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
