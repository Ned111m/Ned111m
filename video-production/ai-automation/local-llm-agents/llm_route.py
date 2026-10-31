#!/usr/bin/env python3
"""llm_route — task -> local model router for llama-server / Ollama (OpenAI-compatible API).

  llm_route.py seo-metadata --var brief=@brief.txt --var location="Maglaj, BiH" --var keyword="Maglaj dron" \
               --var footage="DJI Mini 4 Pro 4K HLG" --var language=bosanski --out seo.json
  llm_route.py chapters --var durationSec=642 --var scenes=@scenes.txt --var language=bosanski
  llm_route.py --status                              # endpoint health + load, read-only

Co-existence with other agents (e.g. a Strata agent on the same GPU): before each request the target
endpoint's load is probed read-only (llama-server /slots, Ollama /api/ps). If every slot is busy, or
Ollama would have to load a *different* model while another is resident (VRAM eviction), the router waits
(--max-wait) instead of preempting. Nothing is ever unloaded, restarted or reconfigured.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def busy(endpoint: dict, model: str) -> tuple[bool, str]:
    base = endpoint["base"].rsplit("/v1", 1)[0]
    probe = endpoint.get("busyProbe")
    try:
        if probe == "llama-slots":
            slots = _get(base + "/slots")
            if isinstance(slots, list) and slots:
                free = [s for s in slots if not s.get("is_processing", s.get("state", 0) != 0)]
                return (not free, f"{len(slots) - len(free)}/{len(slots)} slots busy")
        elif probe == "ollama-ps":
            resident = [m.get("name", "") for m in _get(base + "/api/ps").get("models", [])]
            if resident and not any(r == model or r.split(":")[0] == model.split(":")[0] for r in resident):
                return True, f"other model resident: {resident} (would evict)"
            return False, f"resident: {resident or 'none'}"
    except urllib.error.HTTPError as e:
        if e.code in (404, 501):  # /slots disabled (--no-slots) -> can't tell; don't block
            return False, "load probe unavailable"
        raise
    return False, "idle"


def render(template: str, variables: dict[str, str]) -> tuple[str, str]:
    missing = sorted(set(re.findall(r"{{(\w+)}}", template)) - set(variables))
    if missing:
        raise SystemExit(f"missing --var: {', '.join(missing)}")
    text = re.sub(r"{{(\w+)}}", lambda m: variables[m.group(1)], template)
    sys_part, _, user_part = text.partition("USER:")
    return sys_part.replace("SYSTEM:", "", 1).strip(), user_part.strip()


def chat(endpoint: dict, task: dict, system: str, user: str, timeout: float) -> str:
    body = {"model": task["model"], "temperature": task["temperature"], "max_tokens": task["maxTokens"],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if task.get("json"):
        body["response_format"] = {"type": "json_object"}
    headers = {"Content-Type": "application/json"}
    key_env = endpoint.get("apiKeyEnv")
    if key_env and os.environ.get(key_env):
        headers["Authorization"] = f"Bearer {os.environ[key_env]}"
    req = urllib.request.Request(endpoint["base"].rstrip("/") + "/chat/completions", json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    content = data["choices"][0]["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()  # reasoning models


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


def check_youtube(task_name: str, obj: dict) -> list[str]:
    errs = []
    if task_name == "seo-metadata":
        if len(obj.get("title", "")) > 100:
            errs.append("title > 100 chars")
        if len(",".join(obj.get("tags", []))) > 500:
            errs.append("tags > 500 chars")
        if re.search(r"[<>]", obj.get("title", "") + obj.get("description", "")):
            errs.append("< or > not allowed by YouTube")
    if task_name == "chapters":
        ch = obj.get("chapters", [])
        if not ch or ch[0].get("timeSec") != 0:
            errs.append("first chapter must be at 0")
        if len(ch) < 3:
            errs.append("need >= 3 chapters")
        if any(b["timeSec"] - a["timeSec"] < 10 for a, b in zip(ch, ch[1:])):
            errs.append("chapters must be >= 10 s apart")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("task", nargs="?")
    ap.add_argument("--routes", default=str(HERE / "routes.json"))
    ap.add_argument("--var", action="append", default=[], help="name=value or name=@file")
    ap.add_argument("--model", help="override task model")
    ap.add_argument("--endpoint", help="override task endpoint")
    ap.add_argument("--max-wait", type=float, default=900, help="seconds to wait for a busy endpoint")
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--retries", type=int, default=3, help="re-ask on invalid JSON / YouTube rule violations")
    ap.add_argument("--out")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    routes = json.loads(Path(a.routes).read_text())

    if a.status:
        for name, ep in routes["endpoints"].items():
            try:
                models = [m["id"] for m in _get(ep["base"].rstrip("/") + "/models").get("data", [])]
                b, why = busy(ep, models[0] if models else "")
                print(f"{name:13} UP   models={models} busy={b} ({why})")
            except Exception as e:  # noqa: BLE001
                print(f"{name:13} DOWN {type(e).__name__}: {e}")
        return 0
    if not a.task or a.task not in routes["tasks"]:
        ap.error(f"task required: {sorted(routes['tasks'])}")

    task = dict(routes["tasks"][a.task])
    if a.model:
        task["model"] = a.model
    ep = routes["endpoints"][a.endpoint or task["endpoint"]]
    variables = {}
    for v in a.var:
        k, _, val = v.partition("=")
        variables[k] = Path(val[1:]).read_text(encoding="utf-8") if val.startswith("@") else val
    system, user = render((HERE / task["prompt"]).read_text(encoding="utf-8"), variables)

    t0 = time.time()
    while True:
        b, why = busy(ep, task["model"])
        if not b:
            break
        if time.time() - t0 > a.max_wait:
            raise SystemExit(f"endpoint busy for {a.max_wait}s ({why}); not preempting")
        print(f"[llm_route] waiting: {why}", file=sys.stderr)
        time.sleep(15)

    out: object = None
    for attempt in range(1, a.retries + 1):
        text = chat(ep, task, system, user, a.timeout)
        if not task.get("json"):
            out = text
            break
        try:
            obj = parse_json(text)
            errs = check_youtube(a.task, obj)
            if not errs:
                out = obj
                break
            user += f"\n\nYour previous answer violated: {'; '.join(errs)}. Fix and return JSON only."
        except (ValueError, json.JSONDecodeError) as e:
            user += f"\n\nYour previous answer was not valid JSON ({e}). Return JSON only."
        print(f"[llm_route] attempt {attempt} rejected", file=sys.stderr)
    if out is None:
        raise SystemExit("model did not produce a valid answer")
    s = out if isinstance(out, str) else json.dumps(out, indent=2, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(s + "\n", encoding="utf-8")
    print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
