#!/usr/bin/env python3
"""resolve_assemble — DaVinci Resolve Studio automation: manifest -> timeline -> grade -> markers -> render.

Runs against a *running* Resolve Studio (Preferences > System > General > External scripting: Local).

  resolve_assemble.py --manifest edit.json                                 # assemble only
  resolve_assemble.py --manifest edit.json --markers beats.json            # + beat markers
  resolve_assemble.py --manifest edit.json --drx cst-node-graphs/DLogM_to_709.drx \
                      --render-preset "YouTube 2160p" --target-dir D:/Renders --render
  resolve_assemble.py --media-dir D:/Footage/2026-10-12 --timeline "Maglaj raw" --fps 25 # quick dump

Manifest: shared-config/workflow-schemas/timeline-manifest.schema.json (frame-exact in/out, exclusive out).
Grades:   the CST node graph cannot be created through the API (Blackmagic does not expose node
          creation). Build it once (see ../cst-node-graphs/README.md), save as .drx, and this script
          applies it to every clip via Timeline.ApplyGradeFromDRX — per-gamma DRX is supported
          (--drx-hlg / --drx-dlogm) because HLG and D-Log M need different input CSTs.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_lib"))
import vpcore  # noqa: E402


def load_resolve():
    """Import DaVinciResolveScript from the platform default location (or RESOLVE_SCRIPT_API)."""
    defaults = {
        "Windows": r"%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting",
        "Darwin": "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting",
        "Linux": "/opt/resolve/Developer/Scripting",
    }
    libs = {
        "Windows": r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll",
        "Darwin": "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so",
        "Linux": "/opt/resolve/libs/Fusion/fusionscript.so",
    }
    api = os.path.expandvars(os.environ.get("RESOLVE_SCRIPT_API", defaults.get(platform.system(), "")))
    os.environ.setdefault("RESOLVE_SCRIPT_API", api)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", libs.get(platform.system(), ""))
    sys.path.append(os.path.join(api, "Modules"))
    try:
        import DaVinciResolveScript as dvr  # type: ignore
    except ImportError as e:
        raise SystemExit(f"DaVinciResolveScript not importable from {api}/Modules — is Resolve Studio installed?") from e
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise SystemExit("Resolve is not running or external scripting is disabled (Preferences > System > General).")
    return resolve


def _ok(result, what: str):
    if not result:
        raise RuntimeError(f"Resolve API call failed: {what}")
    return result


def get_project(resolve, name: str | None):
    pm = resolve.GetProjectManager()
    if name:
        proj = pm.LoadProject(name) or pm.CreateProject(name)
        _ok(proj, f"load/create project {name}")
    else:
        proj = _ok(pm.GetCurrentProject(), "current project")
    return proj


def configure_project(proj, fps: str, width: int, height: int, yrgb: bool) -> None:
    # Timeline fps can only be changed before the first timeline exists in the project.
    fr = float(vpcore.parse_fps(fps))
    fr_s = {23.976: "23.976", 29.97: "29.97", 59.94: "59.94"}.get(round(fr, 3), str(int(round(fr))))
    if not proj.GetTimelineCount():
        proj.SetSetting("timelineFrameRate", fr_s)
        proj.SetSetting("timelinePlaybackFrameRate", fr_s)
    proj.SetSetting("timelineResolutionWidth", str(width))
    proj.SetSetting("timelineResolutionHeight", str(height))
    if yrgb:
        # Default pipeline: DaVinci YRGB (non-managed) + manual CST nodes (input DWG/Intermediate working space).
        if not proj.SetSetting("colorScienceMode", "davinciYRGB"):
            vpcore.log("[resolve] could not set colorScienceMode (set Color Science = DaVinci YRGB manually)")


def import_files(media_pool, files: list[str]) -> dict[str, object]:
    uniq = sorted({str(Path(f).resolve()) for f in files})
    missing = [f for f in uniq if not Path(f).exists()]
    if missing:
        raise SystemExit(f"missing media: {missing[:5]}")
    items = media_pool.ImportMedia(uniq) or []
    by_path: dict[str, object] = {}
    for it in items:
        p = it.GetClipProperty("File Path")
        by_path[str(Path(p).resolve())] = it
    # Already-imported clips are not returned again by ImportMedia: search the root folder tree.
    if len(by_path) < len(uniq):
        stack = [media_pool.GetRootFolder()]
        while stack:
            folder = stack.pop()
            stack.extend(folder.GetSubFolderList() or [])
            for it in folder.GetClipList() or []:
                p = it.GetClipProperty("File Path")
                if p and str(Path(p).resolve()) in uniq:
                    by_path.setdefault(str(Path(p).resolve()), it)
    lost = [f for f in uniq if f not in by_path]
    if lost:
        raise RuntimeError(f"Resolve did not import: {lost[:5]}")
    return by_path


def assemble(proj, manifest: dict, base: Path) -> object:
    mp = proj.GetMediaPool()
    files = [str((base / c["file"]) if not Path(c["file"]).is_absolute() else c["file"]) for c in manifest["clips"]]
    pool = import_files(mp, files)
    tl = _ok(mp.CreateEmptyTimeline(manifest["name"]), f"create timeline {manifest['name']}")
    proj.SetCurrentTimeline(tl)
    tl.SetStartTimecode(manifest.get("startTimecode", "01:00:00:00"))
    max_track = max(c.get("track", 1) for c in manifest["clips"])
    while tl.GetTrackCount("video") < max_track:
        tl.AddTrack("video")
    # Append sequentially per track. Resolve endFrame is inclusive -> outFrame - 1.
    infos = []
    for c, f in zip(manifest["clips"], files):
        if c["outFrame"] <= c["inFrame"]:
            raise SystemExit(f"{c['file']}: outFrame must be > inFrame")
        infos.append({"mediaPoolItem": pool[str(Path(f).resolve())], "startFrame": c["inFrame"],
                      "endFrame": c["outFrame"] - 1, "trackIndex": c.get("track", 1)})
    items = mp.AppendToTimeline(infos)
    _ok(items and len(items) >= len(infos), "AppendToTimeline")
    expected = sum(c["outFrame"] - c["inFrame"] for c in manifest["clips"] if c.get("track", 1) == 1)
    actual = tl.GetEndFrame() - tl.GetStartFrame()
    if actual != expected:
        vpcore.log(f"[resolve] WARNING V1 length {actual} != manifest {expected} frames (check source fps vs timeline fps)")
    return tl


def apply_grades(tl, manifest: dict | None, drx: str | None, drx_hlg: str | None, drx_dlogm: str | None) -> int:
    """gradeMode 0 = no keyframes. Per-clip manifest gradeDrx > per-gamma DRX > global DRX."""
    items = tl.GetItemListInTrack("video", 1) or []
    clips = (manifest or {}).get("clips", [])
    applied = 0
    groups: dict[str, list] = {}
    for idx, item in enumerate(items):
        c = clips[idx] if idx < len(clips) else {}
        path = c.get("gradeDrx") or {"hlg": drx_hlg, "dlog-m": drx_dlogm}.get(c.get("gamma", ""), None) \
            or (manifest or {}).get("gradeDrx") or drx
        if path:
            groups.setdefault(str(Path(path).resolve()), []).append(item)
    for path, its in groups.items():
        if not Path(path).exists():
            raise SystemExit(f"DRX not found: {path}")
        _ok(tl.ApplyGradeFromDRX(path, 0, its), f"ApplyGradeFromDRX {path}")
        applied += len(its)
    return applied


def add_markers(tl, beats: dict | None, manifest: dict | None, downbeats_only: bool) -> int:
    n = 0
    tl_fps = vpcore.parse_fps(beats["fps"]) if beats else None
    for m in (manifest or {}).get("markers", []):
        n += bool(tl.AddMarker(m["frame"], m.get("color", "Blue"), m["name"], m.get("note", ""), m.get("duration", 1), ""))
    if beats:
        for b in beats["beats"]:
            if downbeats_only and not b["downbeat"]:
                continue
            color = "Blue" if b["downbeat"] else "Green"
            n += bool(tl.AddMarker(b["frame"], color, f"{'Bar' if b['downbeat'] else 'Beat'} {b['index'] + 1}",
                                   f"t={b['time']}s @ {tl_fps}", 1, "beat"))
        for i, o in enumerate(beats.get("onsets", [])):
            n += bool(tl.AddMarker(o["frame"], "Red", f"Hit {i + 1}", f"strength {o['strength']}", 1, "onset"))
    return n


def render(proj, preset: str | None, target_dir: str, name: str, start: bool) -> dict:
    if preset and not proj.LoadRenderPreset(preset):
        raise SystemExit(f"render preset '{preset}' not found (Deliver page > save preset with that name)")
    Path(target_dir).mkdir(parents=True, exist_ok=True)
    _ok(proj.SetRenderSettings({"SelectAllFrames": True, "TargetDir": str(Path(target_dir).resolve()), "CustomName": name}),
        "SetRenderSettings")
    job = _ok(proj.AddRenderJob(), "AddRenderJob")
    if start:
        _ok(proj.StartRendering([job], False), "StartRendering")
    return {"jobId": job, "started": start}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--manifest", help="timeline-manifest JSON")
    src.add_argument("--media-dir", help="dump every clip of a folder onto a new timeline (no trims)")
    ap.add_argument("--project", help="project name (default: current project)")
    ap.add_argument("--timeline", help="timeline name for --media-dir")
    ap.add_argument("--fps", default="25")
    ap.add_argument("--width", type=int, default=3840)
    ap.add_argument("--height", type=int, default=2160)
    ap.add_argument("--no-yrgb", action="store_true", help="leave colour science untouched")
    ap.add_argument("--drx", help="grade applied to every clip")
    ap.add_argument("--drx-hlg", help="grade for clips tagged gamma=hlg")
    ap.add_argument("--drx-dlogm", help="grade for clips tagged gamma=dlog-m")
    ap.add_argument("--markers", help="beat-markers JSON (beat_markers.py)")
    ap.add_argument("--downbeats-only", action="store_true")
    ap.add_argument("--render-preset")
    ap.add_argument("--target-dir")
    ap.add_argument("--render", action="store_true", help="start the render job (otherwise only queued)")
    a = ap.parse_args()

    if a.manifest:
        manifest = vpcore.read_json(a.manifest)
        vpcore.validate(manifest, "timeline-manifest")
        base = Path(a.manifest).resolve().parent
    else:
        exts = {".mp4", ".mov", ".mxf", ".mkv", ".braw"}  # DJI .LRF low-res proxies deliberately excluded
        files = sorted(str(p) for p in Path(a.media_dir).iterdir() if p.suffix.lower() in exts)
        if not files:
            raise SystemExit(f"no clips in {a.media_dir}")
        manifest = {"version": 1, "name": a.timeline or Path(a.media_dir).name, "fps": a.fps, "width": a.width,
                    "height": a.height, "clips": []}
        for f in files:
            info = vpcore.probe(f)
            v = vpcore.video_stream(info) or {}
            n = int(v["nb_frames"]) if str(v.get("nb_frames", "")).isdigit() else \
                vpcore.sec_to_frame(float(info["format"]["duration"]), vpcore.parse_fps(v.get("avg_frame_rate", a.fps)))
            manifest["clips"].append({"file": f, "inFrame": 0, "outFrame": n})
        base = Path(a.media_dir)

    beats = vpcore.read_json(a.markers) if a.markers else None
    if beats:
        vpcore.validate(beats, "beat-markers")
        if vpcore.parse_fps(beats["fps"]) != vpcore.parse_fps(manifest["fps"]):
            raise SystemExit(f"marker fps {beats['fps']} != timeline fps {manifest['fps']} — re-run beat_markers.py --fps")

    resolve = load_resolve()
    resolve.OpenPage("edit")
    proj = get_project(resolve, a.project)
    configure_project(proj, manifest["fps"], manifest["width"], manifest["height"], not a.no_yrgb)
    tl = assemble(proj, manifest, base)
    report = {"project": proj.GetName(), "timeline": tl.GetName(),
              "frames": tl.GetEndFrame() - tl.GetStartFrame(),
              "graded": apply_grades(tl, manifest, a.drx, a.drx_hlg, a.drx_dlogm),
              "markers": add_markers(tl, beats, manifest, a.downbeats_only)}
    if a.target_dir:
        report["render"] = render(proj, a.render_preset, a.target_dir, tl.GetName(), a.render)
    resolve.GetProjectManager().SaveProject()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
