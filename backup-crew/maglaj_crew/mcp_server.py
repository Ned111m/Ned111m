"""maglaj-crew MCP server: the measured tool layer the whole backup crew shares.

One definition, used by every frontend (QwenPaw agents, Open Interpreter, Claude Code via CCR,
the LangGraph standby). Each agent gets an allowlisted subset of these tools per its role
(crew/roster.json), because a 30B local model loses precision when shown hundreds of tools.

Rules this layer enforces by construction:
- The first team's real scripts are called, never reimplemented (qc_render, master_audio, ...).
- Every verdict is measured (ffmpeg ebur128/ffprobe/scene detect), never asserted by a model.
- Vision QC is a cross-check of two different model families; disagreement is surfaced, not hidden.
"""
from __future__ import annotations

import base64, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

import requests
from mcp.server.mcpserver import MCPServer
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
MODELS = json.loads((REPO / "crew" / "crew_models.json").read_text(encoding="utf-8"))
from maglaj_crew.localpaths import PATHS as _PATHS
PROJECT = Path(os.environ.get("MAGLAJ_PROJECT", _PATHS["project"]))
SCRIPTS = PROJECT / "scripts"
PY = os.environ.get("MAGLAJ_PY", _PATHS.get("python", "python"))  # real interpreter path: crew/local/paths.json (never the python3 Store stub)
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
WORK = Path(os.environ.get("MAGLAJ_CREW_WORK", Path.home() / ".maglaj-crew" / "work")); WORK.mkdir(parents=True, exist_ok=True)

mcp = MCPServer("maglaj-crew")


def _run(args: list[str], timeout: int = 3600, cwd: Path | None = None, full: bool = False) -> dict:
    """full=True keeps the whole stderr: per-frame ffmpeg filters (signalstats, scdet, showinfo) print one line per
    frame and a truncated tail silently drops most of the film (scrim_scan passed a known-bad control this way)."""
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=cwd)
    if full:
        return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    return {"exit_code": p.returncode, "stdout": p.stdout[-12000:], "stderr": p.stderr[-6000:]}


def _script(name: str, *args: str, timeout: int = 3600) -> dict:
    return _run([PY, str(SCRIPTS / name), *args], timeout=timeout, cwd=PROJECT)


def _json_report(name: str, args: list[str], timeout: int = 7200) -> dict:
    out = Path(tempfile.mkstemp(suffix=".json", dir=WORK)[1])
    r = _script(name, *args, "--json", str(out), timeout=timeout)
    try:
        r["report"] = json.loads(out.read_text(encoding="utf-8"))
    except Exception:
        r["report"] = None
    return r


# ---------------------------------------------------------------- first-team scripts (real, unmodified)
@mcp.tool()
def qc_render(video: str, profile: str = "", skip_slow: bool = False) -> dict:
    """Delivery QC of a finished render (scripts/qc_render.py). Mandatory before calling any render done.
    profile: youtube-4k | youtube-1080 | shorts (empty = inferred)."""
    a = [video] + (["--profile", profile] if profile else []) + (["--skip-slow"] if skip_slow else [])
    return _json_report("qc_render.py", a)


@mcp.tool()
def validate_sources(folder: str, at_seconds: float | None = None) -> dict:
    """Vet source footage before an edit (scripts/validate_sources.py)."""
    return _json_report("validate_sources.py", [folder] + (["--at", str(at_seconds)] if at_seconds is not None else []))


@mcp.tool()
def color_pipeline_check(video: str, skip_slow: bool = False) -> dict:
    """Color-bible consistency QA for a render (scripts/color_pipeline_check.py)."""
    return _json_report("color_pipeline_check.py", [video] + (["--skip-slow"] if skip_slow else []))


@mcp.tool()
def flight_quality(clips: list[str], top: int = 5) -> dict:
    """Rank the smoothest drone-flight windows in raw clips (scripts/flight_quality.py). Use before picking shots."""
    return _json_report("flight_quality.py", [*clips, "--top", str(top)])


@mcp.tool()
def master_audio(mode: str, video: str, out: str, vo: str = "", beds: list[str] | None = None) -> dict:
    """Master audio to -14 LUFS / -1 dBTP (scripts/master_audio.py). mode: stems | mixdown.
    beds: items 'PATH:START:DUR' in seconds."""
    a = [mode, video, "-o", out] + (["--vo", vo] if vo else [])
    for b in beds or []:
        a += ["--bed", b]
    return _script("master_audio.py", *a)


@mcp.tool()
def deliver_dnxhr(src: str, out: str, check_only: bool = False) -> dict:
    """Final master: DNxHR HQX 10-bit 4K .mov, read back and asserted (scripts/deliver_dnxhr.py). Exit 1 = NOT to spec."""
    return _script("deliver_dnxhr.py", src, "-o", out, *(["--check-only"] if check_only else []), timeout=14400)


@mcp.tool()
def beat_grid(track: str, cuts: list[float] | None = None, cuts_from_segments: str = "") -> dict:
    """Beat grid of a music track and cut-to-beat offsets (scripts/beat_grid.py, keeps the 0.25s VO-sync guard)."""
    a = [track] + (["--cuts", ",".join(str(c) for c in cuts)] if cuts else []) + (["--cuts-from", cuts_from_segments] if cuts_from_segments else [])
    return _json_report("beat_grid.py", a)


@mcp.tool()
def check_vo_timing() -> dict:
    """Verify VO pause constants agree across the 3 files that carry them (scripts/check_vo_timing.py)."""
    return _script("check_vo_timing.py")


@mcp.tool()
def transcribe(path: str, language: str = "bs", out_basename: str = "") -> dict:
    """faster-whisper large-v3 transcription with word timings (scripts/transcribe.py). language 'auto' to detect."""
    return _script("transcribe.py", path, "--language", language, *(["-o", out_basename] if out_basename else []))


@mcp.tool()
def audio_critic(audio_path: str, question: str = "") -> dict:
    """Listen to real audio with Qwen2-Audio (scripts/audio_critic.py): real-vs-synthetic, artifacts, does the ambience fit."""
    return _script("audio_critic.py", audio_path, *([question] if question else []), timeout=1800)


# ---------------------------------------------------------------- measurement primitives
@mcp.tool()
def probe(path: str) -> dict:
    """ffprobe summary: duration, codec, profile, resolution, fps, pix_fmt, bitrate, audio."""
    r = _run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    if r["exit_code"]:
        return {"error": r["stderr"]}
    j = json.loads(r["stdout"])
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    return {"duration_s": float(j["format"].get("duration", 0)), "bit_rate": j["format"].get("bit_rate"),
            "video": {k: v.get(k) for k in ("codec_name", "profile", "width", "height", "r_frame_rate", "pix_fmt", "bits_per_raw_sample")},
            "audio": a and {k: a.get(k) for k in ("codec_name", "sample_rate", "channels", "bit_rate")}}


@mcp.tool()
def measure_loudness(path: str) -> dict:
    """EBU R128: integrated LUFS, LRA, true peak dBTP. Delivery target -14 LUFS, <= -1 dBTP, LRA 6-11 LU."""
    err = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"])["stderr"]
    tail = err[err.rfind("Summary:"):]
    g = lambda k: (m := re.search(k + r":\s+(-?[\d.]+|-inf)", tail)) and float(m.group(1))
    return {"integrated_lufs": g("I"), "lra_lu": g("LRA"), "true_peak_dbtp": g("Peak")}


_TRANSNET = None


@mcp.tool()
def detect_shots(path: str) -> dict:
    """Shot boundaries INCLUDING dissolves/fades (TransNetV2 neural detector). Frame-difference cut detection misses
    gradual transitions: on a real 18-shot final it found 4 boundaries where TransNetV2 found the real 17 (verified 2026-09-24)."""
    global _TRANSNET
    if _TRANSNET is None:
        from transnetv2_pytorch import TransNetV2
        _TRANSNET = TransNetV2(device="cpu"); _TRANSNET.eval()
    sh = _TRANSNET.detect_scenes(path)
    return {"shots": [{"start_s": float(x["start_time"]), "end_s": float(x["end_time"]), "p": round(float(x["probability"]), 2)} for x in sh]}


@mcp.tool()
def sample_frames(video: str, count: int = 24, width: int = 1280) -> dict:
    """Extract `count` frames evenly across the WHOLE runtime (not the first minute) for vision QC."""
    dur = probe(video)["duration_s"]
    out = WORK / ("frames_" + re.sub(r"\W+", "_", Path(video).stem)); out.mkdir(exist_ok=True)
    paths = []
    for i in range(count):
        t = dur * (i + 0.5) / count; p = out / f"f_{i:03d}_{t:08.2f}.jpg"
        _run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-vf", f"scale={width}:-2", str(p)])
        paths.append({"t": round(t, 2), "path": str(p)})
    return {"frames": paths}


# ---------------------------------------------------------------- vision (two-family cross-check)
def _ask(model: str, question: str, images: list[str], think: bool | None = None) -> str:
    body = {"model": model, "stream": False, "options": {"num_ctx": 16384},
            "messages": [{"role": "user", "content": question, "images": [base64.b64encode(Path(i).read_bytes()).decode() for i in images]}]}
    if think is not None:
        body["think"] = think
    # never pass format=json to qwen3-vl: it returns an empty string (measured 2026-09-24)
    from maglaj_crew.gpulock import gpu_lock
    with gpu_lock(f"vision {model} pid {os.getpid()}"):
        r = requests.post(f"{OLLAMA}/api/chat", json=body, timeout=1800).json()
    return r.get("message", {}).get("content", r.get("error", "")).strip()


@mcp.tool()
def vision_qc(images: list[str], question: str) -> dict:
    """Ask the primary AND the independent vision model the same question about real frame(s).
    Returns both answers and whether they agree. Disagreement = look again / escalate, never pick the convenient one."""
    a = _ask(MODELS["vision_primary"], question, images)
    b = _ask(MODELS["vision_second"], question, images)
    norm = lambda s: s.lower().lstrip("*").strip()[:3]
    return {"primary": {"model": MODELS["vision_primary"], "answer": a}, "second": {"model": MODELS["vision_second"], "answer": b},
            "agree": norm(a) == norm(b)}


@mcp.tool()
def frame_defect_scan(video: str, count: int = 24) -> dict:
    """Whole-runtime no-go scan: samples frames and asks both vision models about the channel's hard no-gos
    (dark scrim, overlapping text, burned-in paragraph of narration). Returns per-frame flags with timestamps."""
    # No "scrim" question here on purpose: on the 146-question eval no local VLM detected a scrim reliably (best 38/50),
    # and on the gate control it flagged clean frames. Scrims are MEASURED by scrim_scan instead.
    qs = {
        "overlap": "Do two separate pieces of overlaid text collide or overlap each other? If there is no text, answer no. Answer yes or no.",
        "paragraph": "Is there a long paragraph (more than ~12 words) of subtitle-like text burned onto the picture? Answer yes or no.",
    }
    frames = sample_frames(video, count)["frames"]
    jobs = [(f, k, q) for f in frames for k, q in qs.items()]
    # Model-major order: one 16 GB GPU holds one of these models at a time, so asking A then B per frame
    # would reload a 17-23 GB model on every question (~144 reloads for 24 frames). This loads each model once.
    ans = {m: [_ask(MODELS[m], q, [f["path"]], think=False).lower().lstrip("*").startswith("yes") for f, k, q in jobs]
           for m in ("vision_primary", "vision_second")}
    # Per-question rule, measured 2026-09-25 (crew-eval/para_vlm_eval.py, vision_eval.jsonl):
    #   overlap   -> flag if EITHER model says yes (27/28; OCR lost at 16-17/28, it merges colliding text)
    #   paragraph -> flag only if BOTH say yes (10/10 caught, 3 false alarms vs 5 with either)
    rule = {"overlap": lambda a, b: a or b, "paragraph": lambda a, b: a and b}
    flags = [{"t": f["t"], "frame": f["path"], "defect": k, "both_models": a and b}
             for (f, k, q), a, b in zip(jobs, ans["vision_primary"], ans["vision_second"]) if rule[k](a, b)]
    return {"frames_checked": count, "flags": flags,
            "note": "both_models=false means the two model families disagree: look at that frame before calling it."}


# ---------------------------------------------------------------- scrim / black-fade scan (objective, no LLM)
@mcp.tool()
def scrim_scan(video: str, fps: float = 4.0, edge_s: float = 1.0) -> dict:
    """Measured detector for two channel no-gos: a dark scrim laid over footage, and black fades/cards mid-video.
    A scrim multiplies every level by the same factor, so mean luma AND the 90th-percentile luma drop by the SAME
    ratio inside one continuous shot; a camera move changes them by different ratios. Why not a vision model: on
    146 real-frame questions the best local model got single-frame scrim right only 38/50 (2026-09-24 eval).
    Limitation: a scrim present for an ENTIRE shot has no in-shot step; compare that shot against its source clip."""
    err = _run(["ffmpeg", "-hide_banner", "-i", video, "-vf", f"fps={fps},scale=320:-2,signalstats,metadata=print",
                "-an", "-f", "null", "-"], full=True)["stderr"]
    t = [float(x) for x in re.findall(r"pts_time:([\d.]+)", err)]
    avg = [float(x) for x in re.findall(r"lavfi\.signalstats\.YAVG=([\d.]+)", err)]
    high = [float(x) for x in re.findall(r"lavfi\.signalstats\.YHIGH=([\d.]+)", err)]
    n = min(len(t), len(avg), len(high)); dur = t[n - 1] if n else 0
    shots = [x["start_s"] for x in detect_shots(video)["shots"][1:]]
    shot = lambda s: sum(1 for c in shots if c <= s)
    k = max(1, int(round(0.5 * fps)))  # compare against 0.5 s earlier, same shot
    scrim, black = [], []
    for i in range(k, n - k):
        if t[i] < edge_s or t[i] > dur - edge_s:
            continue
        if avg[i] < 20:
            black.append(round(t[i], 2)); continue
        j = i - k
        near_cut = any(t[j] - 0.15 <= c <= t[i] + 0.15 for c in shots)  # sample timestamps sit a frame off cut boundaries
        if near_cut or avg[j] < 20:
            continue
        ra, rh = avg[i] / max(avg[j], 1), high[i] / max(high[j], 1)
        held = all(avg[m] / max(avg[j], 1) < 0.9 for m in range(i, min(n, i + k + 1)))  # stays down, not a flash
        if ra < 0.9 and rh < 0.9 and abs(ra - rh) < 0.05 and held:
            scrim.append({"t": round(t[i], 2), "level_ratio": round((ra + rh) / 2, 2)})
    spans = lambda ts: [[a, b] for a, b in _merge(ts, 1.0 / fps + 0.01)]
    return {"duration_s": round(dur, 2), "scrim_onsets": scrim[:40], "black_spans_s": spans(black),
            "verdict": "FAIL" if scrim or black else "PASS"}


def _merge(ts: list[float], gap: float) -> list[tuple[float, float]]:
    out = []
    for x in ts:
        if out and x - out[-1][1] <= gap:
            out[-1] = (out[-1][0], x)
        else:
            out.append((x, x))
    return out


# ---------------------------------------------------------------- repeat-shot audit (objective, no LLM)
def _dhash(p: str, n: int = 16) -> int:
    im = Image.open(p).convert("L").resize((n + 1, n))
    px = list(im.getdata()); bits = 0
    for y in range(n):
        for x in range(n):
            bits = (bits << 1) | (px[y * (n + 1) + x] > px[y * (n + 1) + x + 1])
    return bits


@mcp.tool()
def repeat_shot_audit(video: str, step_s: float = 1.0, max_bits: int = 40) -> dict:
    """Find shots that appear twice (incl. orbit/hover near-duplicates) by perceptual hash across the whole runtime.
    Pairs inside the same continuous shot are excluded using TransNetV2 shot boundaries (catches dissolves)."""
    dur = probe(video)["duration_s"]
    cuts = [x["start_s"] for x in detect_shots(video)["shots"][1:]]
    seg = lambda t: sum(1 for c in cuts if c <= t)
    out = WORK / ("rep_" + re.sub(r"\W+", "_", Path(video).stem)); out.mkdir(exist_ok=True)
    _run(["ffmpeg", "-loglevel", "error", "-y", "-i", video, "-vf", f"fps=1/{step_s},scale=320:-2", str(out / "s_%05d.jpg")])
    fr = sorted(out.glob("s_*.jpg")); hs = [(i * step_s + step_s / 2, _dhash(str(p)), str(p)) for i, p in enumerate(fr)]
    hits = []
    for i in range(len(hs)):
        for j in range(i + 1, len(hs)):
            if seg(hs[i][0]) == seg(hs[j][0]):
                continue
            d = bin(hs[i][1] ^ hs[j][1]).count("1")
            if d <= max_bits:
                hits.append({"t_a": round(hs[i][0], 1), "t_b": round(hs[j][0], 1), "hamming": d, "frame_a": hs[i][2], "frame_b": hs[j][2]})
    hits.sort(key=lambda h: h["hamming"])
    return {"duration_s": dur, "segments": len(cuts) + 1, "candidate_repeats": hits[:40],
            "note": "Candidates only: confirm each with vision_qc(same_shot) on frame_a/frame_b before calling it a defect."}


# ---------------------------------------------------------------- footage library (semantic search over the raw library)
LIB = Path.home() / ".maglaj-crew" / "library.jsonl"
_LIB_CACHE = None
VIDEO_EXT = {".mp4", ".mov", ".mxf", ".mkv"}


def _embed(text: str) -> list[float]:
    r = requests.post(f"{OLLAMA}/api/embed", json={"model": MODELS["embedding"], "input": text}, timeout=600).json()
    return r["embeddings"][0]


@mcp.tool()
def index_footage(folder: str, recursive: bool = True, limit: int = 200) -> dict:
    """Index raw clips: 3 real frames per clip -> caption (fast vision model) -> embedding. Incremental (skips clips
    already indexed at the same size+mtime). Run on a folder, then use search_footage."""
    seen = {}
    if LIB.exists():
        for line in LIB.read_text(encoding="utf-8").splitlines():
            e = json.loads(line); seen[e["path"]] = e["sig"]
    files = [p for p in (Path(folder).rglob("*") if recursive else Path(folder).iterdir()) if p.suffix.lower() in VIDEO_EXT]
    done, skipped = [], 0
    with LIB.open("a", encoding="utf-8") as fh:
        for p in files:
            if len(done) >= limit:
                break
            st = p.stat(); sig = f"{st.st_size}:{int(st.st_mtime)}"
            if seen.get(str(p)) == sig:
                skipped += 1; continue
            dur = probe(str(p)).get("duration_s") or 0
            if dur <= 0:
                continue
            tmp = WORK / "lib"; tmp.mkdir(exist_ok=True); imgs = []
            for k, frac in enumerate((0.2, 0.5, 0.8)):
                f = tmp / f"{k}.jpg"
                _run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{dur * frac:.2f}", "-i", str(p), "-frames:v", "1", "-vf", "scale=768:-2", str(f)])
                imgs.append(str(f))
            cap = _ask(MODELS["vision_fast_sampler"], "These are 3 frames (20%, 50%, 80%) from one raw drone/action-cam clip. For a video editor's "
                       "search index, describe in one paragraph: subject and place type, landmarks, time of day and light, weather, season, "
                       "camera motion between the frames, notable objects or people.", imgs, think=False)
            e = {"path": str(p), "sig": sig, "duration_s": round(dur, 2), "caption": cap, "embedding": _embed(cap)}
            fh.write(json.dumps(e, ensure_ascii=False) + "\n"); fh.flush()
            done.append({"path": str(p), "caption": cap[:160]})
    return {"indexed": len(done), "skipped_unchanged": skipped, "found": len(files), "library": str(LIB), "sample": done[:5]}


@mcp.tool()
def search_footage(query: str, top_k: int = 10) -> dict:
    """Semantic search of the indexed raw library. Returns clip paths, durations, captions and similarity."""
    if not LIB.exists():
        return {"error": "library empty, run index_footage first"}
    import numpy as np
    global _LIB_CACHE
    mt = LIB.stat().st_mtime
    if _LIB_CACHE is None or _LIB_CACHE[0] != mt:
        ents = [json.loads(l) for l in LIB.read_text(encoding="utf-8").splitlines() if l.strip()]
        X = np.asarray([e.pop("embedding") for e in ents], dtype=np.float32)
        X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
        _LIB_CACHE = (mt, ents, X)
    _, ents, X = _LIB_CACHE
    q = np.asarray(_embed(query), dtype=np.float32); q /= np.linalg.norm(q) + 1e-9
    sims = X @ q
    hits = [(float(sims[i]), ents[i]) for i in np.argsort(-sims)[:top_k]]
    return {"results": [{"score": round(s, 3), "path": e["path"], "duration_s": e["duration_s"], "caption": e["caption"]} for s, e in hits[:top_k]]}


# ---------------------------------------------------------------- web research (no API key)
SEARXNG = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8890")


@mcp.tool()
def web_search(query: str, max_results: int = 10) -> dict:
    """Web search. Primary: local SearXNG (Docker container `searxng`, aggregates Google/Brave/etc., no key).
    Fallback: DuckDuckGo via ddgs. Cite the URLs you use; never state a fact without one."""
    try:
        r = requests.get(f"{SEARXNG}/search", params={"q": query, "format": "json"}, timeout=30).json()
        res = [{"title": x.get("title"), "url": x.get("url"), "snippet": (x.get("content") or "")[:300]} for x in r.get("results", [])[:max_results]]
        if res:
            return {"source": "searxng", "results": res}
    except Exception as e:
        err = str(e)
    from ddgs import DDGS
    res = [{"title": x["title"], "url": x["href"], "snippet": x.get("body", "")[:300]} for x in DDGS().text(query, max_results=max_results)]
    return {"source": "ddgs", "results": res}


@mcp.tool()
def web_fetch(url: str, max_chars: int = 20000) -> dict:
    """Fetch a web page and return its main text (trafilatura, boilerplate removed)."""
    import trafilatura
    html = trafilatura.fetch_url(url)
    if not html:
        return {"error": f"could not fetch {url}"}
    txt = trafilatura.extract(html, include_links=False) or ""
    return {"url": url, "chars": len(txt), "text": txt[:max_chars]}


# ---------------------------------------------------------------- ComfyUI on demand
COMFY = Path(r"C:\AI\ComfyUI_Master"); BRIDGE = Path(r"C:\AI\comfyui-mcp-server")


def _port_up(port: int) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


@mcp.tool()
def start_comfyui(wait_s: int = 180) -> dict:
    """Start ComfyUI (8188) and its MCP bridge (9000) if they are down, then wait until both answer.
    Uses the verified launch (`main.py --reserve-vram 1.5`; the old start.bat died on the removed --normalvram flag).
    Call this before any Woosh/ACE-Step/LTX/Wan/RIFE job instead of reporting generation as unavailable."""
    started = []
    if not _port_up(8188):
        _spawn_in(COMFY, [str(COMFY / "venv" / "Scripts" / "python.exe"), "main.py", "--reserve-vram", "1.5", "--preview-method", "auto"]); started.append("comfyui")
    t0 = time.time()
    while not _port_up(8188) and time.time() - t0 < wait_s:
        time.sleep(3)
    if _port_up(8188) and not _port_up(9000):
        _spawn_in(BRIDGE, [str(BRIDGE / "venv" / "Scripts" / "python.exe"), "server.py"]); started.append("bridge")
    while not _port_up(9000) and time.time() - t0 < wait_s:
        time.sleep(2)
    return {"comfyui_8188": _port_up(8188), "bridge_9000": _port_up(9000), "started": started, "secs": round(time.time() - t0)}


def _spawn_in(cwd: Path, cmd: list[str]) -> None:
    global REPO
    saved = REPO
    try:
        REPO = cwd  # _spawn_detached uses REPO as the working directory
        _spawn_detached(cmd)
    finally:
        REPO = saved



# Same-shot confirmation. Measured 2026-09-25 (crew-eval/repeat_confirm_embed.py): DINOv2-large separates true repeats
# (>= 0.988) from distinct shots (<= 0.837). Vision LLMs failed: qwen3.8 called two different shots "same" 3/3,
# qwen3-vl caught 1/15, the old both-must-agree rule caught 0/5 (that is why the 2026-09-25 gate missed a known repeat).
# 0.88..0.93 = near-duplicate (orbit/hover of the same subject), which Nedim also counts as a defect: WARN, look.
SAME_SHOT_FAIL, SAME_SHOT_WARN = 0.93, 0.88
CREW_ML_PY = r"C:\AI\crew-ml\.venv\Scripts\python.exe"
SAME_SHOT_PY = r"C:\AI\crew-ml\same_shot.py"


def _same_shot(video: str, pairs: list) -> list:
    """[((t_a, t_b), similarity)] on full-res frames. Fails loudly if the crew-ml venv is missing (no weaker fallback)."""
    if not pairs:
        return []
    out = WORK / f"same_{Path(video).stem}"; out.mkdir(exist_ok=True); imgs = []
    for t_a, t_b in pairs:
        pr = []
        for t in (t_a, t_b):
            f = out / f"f_{t:.2f}.jpg"
            if not f.exists():
                _run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t}", "-i", video, "-frames:v", "1", "-vf", "scale=1280:-2", str(f)])
            pr.append(str(f))
        imgs.append(pr)
    pj = out / "pairs.json"; pj.write_text(json.dumps(imgs), encoding="utf-8")
    from maglaj_crew.gpulock import gpu_lock
    with gpu_lock(f"same_shot pid {os.getpid()}"):
        r = subprocess.run([CREW_ML_PY, SAME_SHOT_PY, str(pj)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"same_shot (DINOv2) failed: {r.stderr[-800:]}")
    sims = json.loads(r.stdout.strip().splitlines()[-1])
    return list(zip([tuple(p) for p in pairs], sims))


# ---------------------------------------------------------------- motion (temporal) defects
@mcp.tool()
def temporal_scan(video: str) -> dict:
    """Motion defects frame-by-frame checks cannot see: dropped frames / jump cut (discontinuity), flicker, 1-3 frame
    black flash (all FAIL) and freeze (WARN: a held graphic can be intentional). Measured 2026-09-25: 22/24 caught,
    0/12 false alarms over 4 clip sets (2 unseen); also flagged a real mid-shot grade jump in a finished long-form video.
    A local video LLM (Qwen3-VL-8B) caught 0/7 on the same clips and was rejected. Run it via start_job."""
    from maglaj_crew import temporal
    return temporal.scan(video, detect_shots(video).get("shots", []))

# ---------------------------------------------------------------- delivery gate (the crew's equivalent of delivery-gate.mjs)
@mcp.tool()
def delivery_gate(video: str, frames: int = 24) -> dict:
    """THE gate. A render may only be called done when this returns verdict PASS in the same task. Run it via start_job.
    Composes the first team's qc_render (container, loudness, silence/black/freeze, cuts+repeats) with what the
    2026-09-24 eval showed it lacks: scrim_scan (measured), TransNetV2 repeat audit (sees dissolves) with every candidate
    confirmed by DINOv2-large similarity on full-res frames (vision LLMs measured unreliable for this, 2026-09-25), and a whole-runtime vision no-go scan (qwen3.8:27b + qwen3-vl, 98% recall, 0 false alarms)."""
    fails, notes = [], {}
    tm, t0 = {}, time.time()
    qc = qc_render(video); tm["qc_render"] = round(time.time() - t0); t0 = time.time()
    notes["qc_render"] = [c for c in (qc.get("report") or {}).get("checks", []) if c.get("status") in ("FAIL", "WARN")]
    if qc["exit_code"] != 0:
        fails.append("qc_render: " + "; ".join(f"{c['name']}: {c.get('detail', '')}" for c in notes["qc_render"] if c.get("status") == "FAIL"))
    sc = scrim_scan(video); notes["scrim_scan"] = sc; tm["scrim_scan"] = round(time.time() - t0); t0 = time.time()
    if sc["verdict"] == "FAIL":
        fails.append(f"scrim/black: scrim onsets {[x['t'] for x in sc['scrim_onsets']]}, black spans {sc['black_spans_s']}")
    rep = repeat_shot_audit(video)
    sims = _same_shot(video, [(h["t_a"], h["t_b"]) for h in rep["candidate_repeats"][:12]])
    confirmed = [(t_a, t_b, s) for (t_a, t_b), s in sims if s >= SAME_SHOT_FAIL]
    near = [(t_a, t_b, s) for (t_a, t_b), s in sims if SAME_SHOT_WARN <= s < SAME_SHOT_FAIL]
    tm["repeats"] = round(time.time() - t0); t0 = time.time()
    notes["repeats_confirmed"], notes["near_duplicates"] = confirmed, near
    if confirmed:
        fails.append(f"repeated shots (DINOv2 similarity >= {SAME_SHOT_FAIL}): {confirmed}")
    tp = temporal_scan(video); notes["motion"] = tp["events"]; tm["temporal_scan"] = round(time.time() - t0); t0 = time.time()
    mf = [e for e in tp["events"] if e.get("severity") == "FAIL"]
    if mf:
        fails.append("motion defects: " + ", ".join(f"{e['type']}@{e['t']}s" for e in mf[:10]))
    fd = frame_defect_scan(video, frames); notes["frame_defects"] = fd["flags"]; tm["frame_defect_scan"] = round(time.time() - t0)
    notes["timings_s"] = tm
    if fd["flags"]:
        fails.append("vision no-go flags: " + ", ".join(f"{f['defect']}@{f['t']}s" for f in fd["flags"][:12]))
    return {"video": video, "verdict": "FAIL" if fails else "PASS", "fails": fails, "details": notes,
            "rule": "Do not report this render as done unless verdict is PASS. Fix every fail in ONE batched pass, re-render once, re-gate."}


# ---------------------------------------------------------------- background jobs (long tools survive call timeouts)
JOBS = Path.home() / ".maglaj-crew" / "jobs"; JOBS.mkdir(parents=True, exist_ok=True)
LONG_TOOLS = {"temporal_scan", "qc_render", "deliver_dnxhr", "master_audio", "validate_sources", "color_pipeline_check", "flight_quality",
              "transcribe", "audio_critic", "frame_defect_scan", "repeat_shot_audit", "index_footage", "detect_shots", "beat_grid",
              "delivery_gate", "scrim_scan"}


@mcp.tool()
def start_job(tool: str, args: dict) -> dict:
    """Run a LONG tool in a detached process and return a job_id immediately. Use for renders, qc_render,
    deliver_dnxhr, master_audio, scans and indexing (they outlast the 60-120 s tool-call limit). Then poll job_status."""
    if tool not in LONG_TOOLS:
        return {"error": f"{tool} is not a long tool; call it directly. Long tools: {sorted(LONG_TOOLS)}"}
    if tool not in _ALLOWED:
        return {"error": f"{tool} is not in this seat's allowlist"}
    import uuid
    job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    (JOBS / f"{job_id}.json").write_text(json.dumps({"job_id": job_id, "tool": tool, "args": args, "status": "queued",
                                                     "created": time.time()}, ensure_ascii=False), encoding="utf-8")
    _spawn_detached([sys.executable, "-m", "maglaj_crew.jobrunner", job_id])
    return {"job_id": job_id, "status": "queued", "next": "call job_status(job_id) until status is done or error"}


def _spawn_detached(cmd: list[str]) -> None:
    """Start a process that outlives this MCP server. On Windows the MCP stdio client puts the server in a Job
    Object with kill-on-close, and the uv venv python.exe is a launcher whose real interpreter child stays in that
    job even with CREATE_BREAKAWAY_FROM_JOB (measured 2026-09-24: jobs stuck at 'queued'). WMI Win32_Process.Create
    starts the process under the WMI provider, outside any job, so it survives the session."""
    if os.name != "nt":
        subprocess.Popen(cmd, cwd=str(REPO), start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); return
    line = subprocess.list2cmdline(cmd).replace("'", "''")
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"(Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{{CommandLine='{line}'; "
                        f"CurrentDirectory='{REPO}'}}).ReturnValue"], capture_output=True, text=True)
    if r.stdout.strip() != "0":
        raise RuntimeError(f"WMI process create failed: {r.stdout.strip()} {r.stderr[-300:]}")


@mcp.tool()
def job_status(job_id: str, wait_s: int = 90) -> dict:
    """Status/result of a background job started with start_job. status: queued | waiting_gpu | running | done | error.
    Waits up to wait_s seconds (default 90, under the 120 s frontend call limit) for the job to finish before
    answering, so just call it again if it is still running. Measured 2026-09-25: rapid polling made every poll
    reload the agent model while the gate held the GPU for vision, turning 26 s agent turns into ~4 min."""
    deadline = time.time() + max(0, min(int(wait_s), 110))
    while True:
        d = _job_status_once(job_id)
        if d.get("status") in ("done", "error") or "error" in d and "status" not in d or time.time() >= deadline:
            return d
        time.sleep(3)


def _job_status_once(job_id: str) -> dict:
    p = JOBS / f"{job_id}.json"
    if not p.exists():
        return {"error": f"unknown job {job_id}"}
    d = json.loads(p.read_text(encoding="utf-8"))
    if d.get("status") == "waiting_gpu":
        d["note"] = f"queued: the GPU is busy with {d.get('gpu_held_by')}; keep polling"
    if d.get("status") in ("running", "waiting_gpu", "queued") and d.get("started"):
        d["elapsed_s"] = round(time.time() - d["started"])
    stale = d.get("heartbeat") and time.time() - d["heartbeat"] > 120
    if d.get("status") in ("running", "waiting_gpu") or (d.get("status") == "queued" and time.time() - d.get("created", 0) > 120):
        if stale or not _pid_alive(d.get("pid")):
            # the runner died without writing a result (killed / native crash): never report it as running
            tail = ""
            lp = JOBS / f"{job_id}.log"
            if lp.exists():
                tail = lp.read_text(encoding="utf-8", errors="replace")[-2000:]
            d.update(status="error", error="job runner process died without a result", log_tail=tail)
            p.write_text(json.dumps(d, ensure_ascii=False, default=str), encoding="utf-8")
    return d


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    if os.name != "nt":
        try:
            os.kill(int(pid), 0); return True
        except OSError:
            return False
    r = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH", "/FO", "CSV"], capture_output=True, text=True)
    return f'"{int(pid)}"' in r.stdout


# ---------------------------------------------------------------- health (why is it stuck?)
@mcp.tool()
def crew_health() -> dict:
    """Is anything stuck, and where? Call before a long task and whenever a job seems to hang. Checks Ollama and the
    loaded models, the machine-wide GPU queue, every unfinished job (dead process / stale heartbeat are marked error),
    the Hindsight memory queue, SearXNG, QwenPaw and Langfuse (traces, http://localhost:3000). verdict OK | WARN | FAIL with the reason per service."""
    from maglaj_crew.gpulock import holder
    out, problems, warns = {}, [], []

    def get(url, timeout=4):
        try:
            r = requests.get(url, timeout=timeout); return r.status_code, r
        except Exception as e:
            return None, e

    code, r = get(f"{OLLAMA}/api/ps")
    if code == 200:
        ms = r.json().get("models", [])
        out["ollama"] = [{"model": m["name"], "ctx": m.get("context_length"), "vram_gb": round(m.get("size_vram", 0) / 2**30, 1),
                          "size_gb": round(m.get("size", 0) / 2**30, 1)} for m in ms]
        ctxs = {m.get("context_length") for m in ms if m["name"].startswith(MODELS["orchestrator"])}
        if len(ctxs) > 1:
            warns.append(f"{MODELS['orchestrator']} loaded with several context sizes {ctxs}: clients disagree on num_ctx, Ollama will keep reloading it")
    else:
        problems.append(f"Ollama not answering: {r}")

    h = holder()
    if h:
        age = round(time.time() - h.get("since", time.time()))
        out["gpu_queue"] = {"held_by": h.get("label"), "for_s": age, "holder_alive": _pid_alive(h.get("pid"))}
        if not _pid_alive(h.get("pid")):
            warns.append("GPU lock holder is dead (the OS releases the lock; stale holder file only)")
        elif age > 3 * 3600:
            warns.append(f"GPU held for {age} s by {h.get('label')}")
    else:
        out["gpu_queue"] = "free"

    jobs = []
    for p in sorted(JOBS.glob("*.json"), key=lambda p: p.stat().st_mtime)[-50:]:
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("status") in ("queued", "waiting_gpu", "running"):
            d = _job_status_once(d["job_id"])
            jobs.append({k: d.get(k) for k in ("job_id", "tool", "status", "elapsed_s", "gpu_held_by", "error")})
            if d["status"] == "error":
                problems.append(f"job {d['job_id']} ({d.get('tool')}) died: {d.get('error')}")
    out["unfinished_jobs"] = jobs

    code, r = get("http://127.0.0.1:8888/v1/default/banks/maglaj-crew/operations?limit=50")
    if code == 200:
        ops = [o for o in r.json().get("operations", []) if o["status"] in ("pending", "processing")]
        out["memory_queue"] = [{"type": o["task_type"], "status": o["status"], "since": o["updated_at"]} for o in ops]
        if len(ops) > 5:
            warns.append(f"Hindsight has {len(ops)} queued memory operations competing for {MODELS['orchestrator']}")
    else:
        warns.append("Hindsight memory (:8888) not answering: agents cannot recall Nedim's rules. Start C:\\AI\\crew-memory\\start_hindsight.cmd")

    for name, url in (("searxng", f"{SEARXNG}/healthz"), ("langfuse", "http://127.0.0.1:3000/api/public/health")):
        code, _ = get(url)
        out[name] = "up" if code == 200 else "down"
        if code != 200:
            (problems if name == "searxng" else warns).append(f"{name} down")
    try:
        port = (Path.home() / ".qwenpaw" / "desktop_port").read_text().strip()
        code, _ = get(f"http://127.0.0.1:{port}/api/agents")
        out["qwenpaw"] = "up" if code == 200 else "down"
    except Exception:
        out["qwenpaw"] = "not running"

    out["verdict"] = "FAIL" if problems else "WARN" if warns else "OK"
    out["problems"], out["warnings"] = problems, warns
    return out


# ---------------------------------------------------------------- per-seat tool allowlist
_ALLOWED: set[str] = set()
def _apply_role_filter() -> None:
    import asyncio
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    role = os.environ.get("MAGLAJ_CREW_ROLE")
    if not role:
        _ALLOWED.update(names)
        return
    roster = json.loads((REPO / "crew" / "roster.json").read_text(encoding="utf-8"))
    seat = next(s for s in roster["seats"] if s["id"] == role)
    _ALLOWED.update(set(roster["common_tools"]) | set(seat["tools"]))
    for n in names - _ALLOWED:
        mcp.remove_tool(n)


_apply_role_filter()

if __name__ == "__main__":
    mcp.run()
