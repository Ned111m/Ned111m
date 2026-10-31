"""Integration logic against mocks: Resolve API, ComfyUI HTTP, llama-server/Ollama, Qdrant, YouTube metadata."""
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from conftest import ROOT


# ---------------------------------------------------------------- Resolve (fake API surface)
class FakeItem:
    def __init__(self, path):
        self.path = path

    def GetClipProperty(self, k):
        return self.path if k == "File Path" else None


class FakeTimeline:
    def __init__(self, name):
        self.name, self.items, self.markers, self.drx, self.tracks = name, [], [], [], 1

    def GetName(self): return self.name
    def SetStartTimecode(self, tc): return True
    def GetTrackCount(self, kind): return self.tracks
    def AddTrack(self, kind): self.tracks += 1; return True
    def GetStartFrame(self): return 90000
    def GetEndFrame(self): return 90000 + sum(i["endFrame"] - i["startFrame"] + 1 for i in self.items if i["trackIndex"] == 1)
    def GetItemListInTrack(self, kind, idx): return [i for i in self.items if i["trackIndex"] == idx]
    def ApplyGradeFromDRX(self, path, mode, items): self.drx.append((path, mode, len(items))); return True
    def AddMarker(self, *a): self.markers.append(a); return True


class FakeMediaPool:
    def __init__(self): self.tl = None
    def ImportMedia(self, paths): return [FakeItem(p) for p in paths]
    def GetRootFolder(self): return None
    def CreateEmptyTimeline(self, name): self.tl = FakeTimeline(name); return self.tl
    def AppendToTimeline(self, infos): self.tl.items.extend(infos); return infos


class FakeProject:
    def __init__(self): self.mp, self.settings, self.jobs = FakeMediaPool(), {}, []
    def GetMediaPool(self): return self.mp
    def GetTimelineCount(self): return 0
    def SetSetting(self, k, v): self.settings[k] = v; return True
    def SetCurrentTimeline(self, tl): return True
    def GetName(self): return "Test"
    def LoadRenderPreset(self, n): return n == "YouTube 2160p"
    def SetRenderSettings(self, s): self.render = s; return True
    def AddRenderJob(self): self.jobs.append("job1"); return "job1"
    def StartRendering(self, jobs, interactive): return True


def test_resolve_assemble_logic(tmp_path):
    import resolve_assemble as ra
    for n in ("a.mp4", "b.mp4"):
        (tmp_path / n).write_bytes(b"x")
    drx = tmp_path / "HLG_to_709.drx"
    drx.write_bytes(b"drx")
    manifest = {"version": 1, "name": "Maglaj cut", "fps": "25", "width": 3840, "height": 2160,
                "clips": [{"file": "a.mp4", "inFrame": 10, "outFrame": 60, "gamma": "hlg"},
                          {"file": "b.mp4", "inFrame": 0, "outFrame": 25, "gamma": "dlog-m"}],
                "markers": [{"frame": 12, "name": "drop", "color": "Red"}]}
    import vpcore
    vpcore.validate(manifest, "timeline-manifest")
    proj = FakeProject()
    ra.configure_project(proj, "25", 3840, 2160, True)
    assert proj.settings["timelineFrameRate"] == "25" and proj.settings["colorScienceMode"] == "davinciYRGB"
    tl = ra.assemble(proj, manifest, tmp_path)
    assert [(i["startFrame"], i["endFrame"]) for i in tl.items] == [(10, 59), (0, 24)]  # inclusive end
    assert tl.GetEndFrame() - tl.GetStartFrame() == 75
    assert ra.apply_grades(tl, manifest, None, str(drx), None) == 1  # only the hlg clip has a DRX
    assert tl.drx[0][1] == 0
    beats = {"version": 1, "audio": "m.wav", "fps": "25", "tempoBpm": 120,
             "beats": [{"index": 0, "time": 0.0, "frame": 0, "strength": 1, "downbeat": True},
                       {"index": 1, "time": 0.5, "frame": 13, "strength": 0.5, "downbeat": False}]}
    assert ra.add_markers(tl, beats, manifest, downbeats_only=True) == 2
    r = ra.render(proj, "YouTube 2160p", str(tmp_path / "renders"), "Maglaj cut", start=False)
    assert r == {"jobId": "job1", "started": False} and proj.render["SelectAllFrames"] is True


# ---------------------------------------------------------------- ComfyUI mock server
class _Comfy(BaseHTTPRequestHandler):
    posted = []

    def log_message(self, *a): pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/prompt":
            _Comfy.posted.append(json.loads(body))
            self._json({"prompt_id": "p1", "number": 0, "node_errors": {}})

    def do_GET(self):
        if self.path == "/queue":
            self._json({"queue_running": [], "queue_pending": []})
        elif self.path == "/system_stats":
            self._json({"devices": [{"name": "cuda:0", "vram_free": 20 * 1024 ** 3}]})
        elif self.path.startswith("/history/p1"):
            self._json({"p1": {"status": {"status_str": "success", "completed": True},
                               "outputs": {"9": {"images": [{"filename": "out_00001.png", "subfolder": "", "type": "output"}]}}}})
        elif self.path.startswith("/view"):
            self.send_response(200); self.send_header("Content-Length", "4"); self.end_headers(); self.wfile.write(b"\x89PNG")


@pytest.fixture
def comfy_server():
    srv = HTTPServer(("127.0.0.1", 0), _Comfy)
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_comfy_queue_roundtrip(comfy_server, tmp_path):
    import comfy_queue as cq
    wf = {"3": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20}, "_meta": {"title": "Sampler"}},
          "6": {"class_type": "CLIPTextEncode", "inputs": {"text": ""}, "_meta": {"title": "Positive Prompt"}}}
    patched = cq.apply_overrides(wf, cq.parse_sets(["Sampler.steps=30", "Positive Prompt.text=drone over Bosna", "3.seed=random"]))
    assert patched["3"]["inputs"]["steps"] == 30 and patched["6"]["inputs"]["text"] == "drone over Bosna"
    assert patched["3"]["inputs"]["seed"] != 1
    with pytest.raises(SystemExit):
        cq.apply_overrides(wf, {"3.nope": "1"})
    c = cq.Comfy(comfy_server)
    assert not c.queue_busy() and c.free_vram_gb() == 20.0
    pid = c.submit(patched)
    files = c.download_outputs(c.wait(pid, poll=0.01), tmp_path)
    assert Path(files[0]).read_bytes() == b"\x89PNG"
    assert _Comfy.posted[-1]["prompt"]["6"]["inputs"]["text"] == "drone over Bosna"


# ---------------------------------------------------------------- llm_route
class _LLM(BaseHTTPRequestHandler):
    slots_busy = False

    def log_message(self, *a): pass

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        if self.path == "/slots":
            self._json([{"id": 0, "is_processing": _LLM.slots_busy}])
        elif self.path == "/api/ps":
            self._json({"models": [{"name": "strata-model:latest"}]})


def test_llm_route_busy_probe_and_rules():
    import llm_route as lr
    srv = HTTPServer(("127.0.0.1", 0), _LLM)
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    base = f"http://127.0.0.1:{srv.server_port}/v1"
    try:
        assert lr.busy({"base": base, "busyProbe": "llama-slots"}, "x")[0] is False
        _LLM.slots_busy = True
        assert lr.busy({"base": base, "busyProbe": "llama-slots"}, "x")[0] is True
        b, why = lr.busy({"base": base, "busyProbe": "ollama-ps"}, "qwen3:14b")
        assert b and "would evict" in why  # never evicts another agent's resident model
        assert lr.busy({"base": base, "busyProbe": "ollama-ps"}, "strata-model:latest")[0] is False
    finally:
        srv.shutdown()
    sysm, user = lr.render("SYSTEM:\nlang {{language}}\nUSER:\n{{brief}}", {"language": "bs", "brief": "Maglaj"})
    assert sysm == "lang bs" and user == "Maglaj"
    assert lr.check_youtube("chapters", {"chapters": [{"timeSec": 0, "title": "a"}, {"timeSec": 5, "title": "b"}]})
    assert lr.parse_json('<think>x</think>{"a": 1}') == {"a": 1}


# ---------------------------------------------------------------- YouTube metadata
def test_youtube_metadata(tmp_path):
    import vpcore
    import youtube_upload as yu
    (tmp_path / "v.mp4").write_bytes(b"x")
    meta = {"version": 1, "video": "v.mp4", "title": "Maglaj iz zraka 4K", "description": "Stari grad.",
            "privacyStatus": "private", "tags": ["Maglaj", "dron"], "containsSyntheticMedia": False,
            "chapters": [{"timeSec": 0, "title": "Uvod"}, {"timeSec": 15, "title": "Tvrđava"}, {"timeSec": 75, "title": "Bosna"}]}
    vpcore.validate(meta, "publish-metadata")
    assert yu.check(meta, tmp_path) == []
    body = yu.build_body(meta)
    assert body["snippet"]["description"].endswith("0:00 Uvod\n0:15 Tvrđava\n1:15 Bosna")
    assert body["status"]["containsSyntheticMedia"] is False
    bad = dict(meta, chapters=[{"timeSec": 3, "title": "x"}], publishAt="2026-11-01T17:00:00Z", privacyStatus="public")
    errs = yu.check(bad, tmp_path)
    assert any("0:00" in e for e in errs) and any("publishAt" in e for e in errs)
    with pytest.raises(ValueError):
        vpcore.validate(dict(meta, title="<bad>"), "publish-metadata")


# ---------------------------------------------------------------- vector-rag (stub embedder, embedded Qdrant)
def test_asset_index_roundtrip(tmp_path, monkeypatch):
    pytest.importorskip("qdrant_client")
    import numpy as np
    import asset_index as ai

    class StubEmbedding:  # deterministic bag-of-words hashing -> no model download
        def __init__(self, *_a, **_k): pass

        def embed(self, docs):
            for d in docs:
                v = np.zeros(64, dtype=np.float32)
                for w in d.lower().replace("|", " ").replace(",", " ").split():
                    v[hash(w) % 64] += 1
                yield v / (np.linalg.norm(v) or 1)

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=StubEmbedding))
    lib = tmp_path / "lib"
    lib.mkdir()
    for name, kind, tags in (("river_bed.wav", "ambience", ["nature", "water", "river"]),
                             ("ui_click.wav", "sfx", ["gfx", "click"])):
        (lib / f"{name}.asset.json").write_text(json.dumps({"version": 1, "path": str(lib / name), "kind": kind,
                                                            "durationSec": 3, "tags": tags}))
    r = ai.index([str(lib)], "t", str(tmp_path / "qd"))
    assert r["indexed"] == 2 and r["vector"] == "fast-all-minilm-l6-v2"
    hits = ai.search("river water", "t", str(tmp_path / "qd"), 2, None)
    assert hits[0]["path"].endswith("river_bed.wav")
    only_sfx = ai.search("river water", "t", str(tmp_path / "qd"), 2, "sfx")
    assert [h["kind"] for h in only_sfx] == ["sfx"]
