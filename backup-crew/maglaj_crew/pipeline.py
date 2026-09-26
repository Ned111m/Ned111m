"""Pre-production pipeline: the stage ORDER lives in code, the craft lives in the crew seats.

Why (e2e 2026-09-26, 4 runs on real video ideas, 0 completed): the local orchestrator delegated correctly but kept
ending its turn after announcing the next step ("Now I'm pulling the data..."), so results never came back. Here code
guarantees the order, the waiting and the output contract; each seat gets one bounded task:

  1. content-strategist -> picks ONE idea from real channel data (yt-dlp), returns JSON with the numbers it used
  2. footage-librarian  -> coverage for the chosen idea via search_footage (real clips only)
  3. storyteller        -> story treatment; every beat must cite a clip from step 2

Every stage must return a JSON object between <result> and </result>. If a seat stops early or forgets the block,
the SAME session is continued ("continue and finish") up to MAX_CONTINUES times. Step 3 is grounded in code: a beat
whose clip path is not in the footage index is rejected and sent back once for a fix."""
from __future__ import annotations

import json, re, time, uuid
from pathlib import Path

import requests

LIB = Path.home() / ".maglaj-crew" / "library.jsonl"
MAX_CONTINUES = 3


def _api() -> str:
    return "http://127.0.0.1:" + (Path.home() / ".qwenpaw" / "desktop_port").read_text().strip() + "/api"


def _agent_id(name: str) -> str:
    agents = requests.get(_api() + "/agents", timeout=30).json()["agents"]
    return next(a["id"] for a in agents if a["name"] == name)


def _chat(agent: str, session: str, text: str, timeout: int = 3600) -> str:
    body = {"input": [{"role": "user", "content": [{"type": "text", "text": text}]}], "session_id": session,
            "user_id": "pipeline", "channel": "console", "stream": True}
    out = []
    with requests.post(_api() + "/console/chat", json=body, headers={"X-Agent-Id": _agent_id(agent)}, stream=True, timeout=timeout) as r:
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if ev.get("object") == "content" and ev.get("type") == "text" and ev.get("delta"):
                out.append(ev.get("text", ""))
            if ev.get("object") == "response" and ev.get("status") in ("completed", "failed", "canceled"):
                break
    return "".join(out)


def _result(text: str):
    m = re.findall(r"<result>(.*?)</result>", text, flags=re.S)
    if not m:
        return None
    raw = m[-1].strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _stage(agent: str, task: str, log: list) -> dict:
    session = "pipe-" + uuid.uuid4().hex[:8]
    contract = ("\n\nWhen you are completely done, end your answer with ONE JSON object between <result> and </result>. "
                "Do the work with your tools first; do not announce steps you will not take in this same answer.")
    text = _chat(agent, session, task + contract)
    log.append({"agent": agent, "turn": 1, "chars": len(text)})
    res, n = _result(text), 1
    while res is None and n <= MAX_CONTINUES:
        n += 1
        text = _chat(agent, session, "Continue and FINISH the task now, using your tools. Then end with the <result>{...}</result> JSON block.")
        log.append({"agent": agent, "turn": n, "chars": len(text)})
        res = _result(text)
    if res is None:
        raise RuntimeError(f"{agent} did not return a <result> block after {n} turns")
    return res


def _indexed_paths() -> set[str]:
    if not LIB.exists():
        return set()
    return {json.loads(l)["path"].lower() for l in LIB.read_text(encoding="utf-8").splitlines() if l.strip()}


def preproduction(ideas: list[str], out_dir: str, brief: str = "") -> dict:
    """ideas: candidate video ideas (Nedim's words). out_dir: where story_treatment.md and pipeline.json are written."""
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    log, t0 = [], time.time()
    ideas_txt = "\n".join(f"{i + 1}. {x}" for i, x in enumerate(ideas))

    pick = _stage("Content Strategist",
                  "Pick ONE of these video ideas for the Dron Maglaj YouTube channel (youtube.com/@dronMaglaj) using REAL public "
                  "channel data: pull the channel's videos with yt-dlp (views, duration, upload date) and compare similar past "
                  "videos. Do not guess; cite the numbers you used.\n" + ideas_txt + ("\nNedim's note: " + brief if brief else "") +
                  '\nResult JSON: {"chosen_index": <1-based int>, "chosen": "...", "why": "...", '
                  '"evidence": [{"video": "...", "views": <int>, "relevance": "..."}], "rejected": [{"index": <int>, "why": "..."}]}', log)
    chosen = pick.get("chosen") or ideas[int(pick.get("chosen_index", 1)) - 1]

    cov = _stage("Assistant Editor / Footage Librarian",
                 f"Video idea: {chosen}\nUse search_footage (several different queries) to find the best REAL clips we already "
                 "have for this idea. Only list clips returned by search_footage.\n"
                 'Result JSON: {"clips": [{"path": "<exact path from search_footage>", "what": "...", "score": <float>}], '
                 '"gaps": ["what we do not have"]}', log)
    clips = [c for c in cov.get("clips", []) if isinstance(c, dict) and c.get("path")]
    known = _indexed_paths()
    clips = [c for c in clips if c["path"].lower() in known]
    if not clips:
        raise RuntimeError("footage-librarian returned no clip that exists in the footage index")

    clip_list = "\n".join(f"- {c['path']} :: {c.get('what', '')}" for c in clips[:25])
    story_task = (f"Video idea: {chosen}\nWhy this idea (from channel data): {pick.get('why', '')}\n"
                  f"Available REAL clips (use ONLY these exact paths):\n{clip_list}\n"
                  "Write the story treatment: logline (one sentence), subject, stake, the open question that keeps the viewer "
                  "watching, 4-7 story beats (each: what the viewer learns/feels + ONE clip path from the list + the sound), "
                  "opening image, closing image/payoff, and the reference video to beat.\n"
                  'Result JSON: {"logline": "...", "subject": "...", "stake": "...", "question": "...", '
                  '"beats": [{"beat": "...", "clip": "<exact path>", "sound": "..."}], "opening": "...", "payoff": "...", "reference": "..."}')
    story = _stage("Storyteller", story_task, log)
    bad = [b for b in story.get("beats", []) if str(b.get("clip", "")).lower() not in known]
    if bad:
        story = _stage("Storyteller", story_task + "\n\nYour previous beats cited clips that are NOT in our library: "
                       + "; ".join(str(b.get("clip")) for b in bad) + ". Use ONLY the listed paths.", log)
        bad = [b for b in story.get("beats", []) if str(b.get("clip", "")).lower() not in known]

    md = [f"# Story treatment: {chosen}", "", f"**Logline:** {story.get('logline', '')}", "",
          f"**Why this idea (channel data):** {pick.get('why', '')}", "",
          f"**Subject:** {story.get('subject', '')}  ", f"**Stake:** {story.get('stake', '')}  ",
          f"**Open question:** {story.get('question', '')}", "", "## Beats", ""]
    for i, b in enumerate(story.get("beats", []), 1):
        md.append(f"{i}. {b.get('beat', '')}  \n   Clip: `{b.get('clip', '')}`  \n   Sound: {b.get('sound', '')}")
    md += ["", f"**Opening:** {story.get('opening', '')}  ", f"**Payoff:** {story.get('payoff', '')}  ",
           f"**Reference to beat:** {story.get('reference', '')}", "", "## Footage gaps (for shoot-planner)", ""]
    md += [f"- {g}" for g in cov.get("gaps", [])]
    (out / "story_treatment.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    res = {"chosen": chosen, "pick": pick, "coverage": {"clips": clips, "gaps": cov.get("gaps", [])}, "story": story,
           "ungrounded_beats": bad, "turns": log, "secs": round(time.time() - t0),
           "treatment": str(out / "story_treatment.md")}
    (out / "pipeline.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return res
