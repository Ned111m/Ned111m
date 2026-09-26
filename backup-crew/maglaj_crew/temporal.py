"""Temporal (motion) defect scan: the defects a frame-by-frame check cannot see.

Why this exists (measured 2026-09-25, crew-eval/video_defects): on 7 clips with known motion defects the existing gate
caught 3/7 (repeat, black flash, freeze by accident). A local video LLM (Qwen3-VL-8B, native video) caught 0/7 and said
"no black frames" / "no frozen picture" on clips that had them, so it was rejected. These defects are signal-level and are
MEASURED here instead:
  freeze        >= 0.5 s of (near-)identical consecutive frames inside one shot
  stutter       repeated duplicate frames (judder) in a 1 s window, short of a freeze
  flicker       luma swinging up/down/up frame to frame inside one shot
  discontinuity a frame-difference spike inside a shot that TransNetV2 does not call a cut (dropped frames, jump cut)
  black_flash   1-3 near-black frames inside a shot
One decode at 320x180 grayscale via ffmpeg; shot boundaries come from TransNetV2 (detect_shots)."""
from __future__ import annotations

import subprocess
import numpy as np

W, H = 320, 180
DUP_EPS = 0.35          # mean |diff| (0-255) below which two frames count as identical (encode noise ~1-3 on real motion)
FREEZE_MIN_S = 0.5     # freeze is a WARN (a held graphic can be intentional); jumps/flicker/black flash are FAIL
FLICKER_DLUMA = 6.0     # luma step (0-255) counted as a swing
SPIKE_RATIO = 3.5       # diff spike vs the shot's local median motion
SPIKE_MIN = 6.0
BLACK_LUMA = 16.0
CUT_GUARD = 2           # frames either side of a TransNetV2 boundary are ignored


def _decode(video: str):
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate",
                            "-of", "csv=p=0", video], capture_output=True, text=True).stdout.strip()
    num, den = (probe.split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", video, "-vf", f"scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(np.float32)
    return f, fps


def scan(video: str, shots: list[dict]) -> dict:
    f, fps = _decode(video)
    n = len(f)
    if n < 3:
        return {"events": [], "frames": n, "fps": fps}
    diff = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))          # diff[i] = frame i -> i+1
    luma = f.mean(axis=(1, 2))
    bounds = sorted({int(round(s["start_s"] * fps)) for s in shots[1:]})
    near_cut = np.zeros(n, bool)
    for b in bounds:
        near_cut[max(0, b - CUT_GUARD):b + CUT_GUARD + 1] = True
    seg_starts = [0] + bounds + [n]
    events = []

    def ev(kind, i, **kw):
        events.append({"type": kind, "t": round(i / fps, 2), **kw})

    # black flash, whole video, independent of TransNetV2 (which labels a black frame as a cut): 1-3 near-black frames
    # with normal picture on BOTH sides. A fade is gradual, so it never has a bright frame right next to the black run.
    dark_all = luma < BLACK_LUMA
    i = 1
    while i < n - 1:
        if dark_all[i]:
            j = i
            while j < n and dark_all[j]: j += 1
            if 1 <= j - i <= 3 and j < n and luma[i - 1] > 3 * BLACK_LUMA and luma[j] > 3 * BLACK_LUMA:
                ev("black_flash", i, frames=j - i)
            i = j
        else:
            i += 1

    for a, b in zip(seg_starts[:-1], seg_starts[1:]):
        if b - a < 4:
            continue
        d = diff[a:b - 1]; lu = luma[a:b]
        dup = d < DUP_EPS
        # judder = holds ALTERNATING with real motion (hold, move, hold, move). A slow hover is uniformly tiny motion and
        # also reads as "dup"; only count a hold whose neighbour moves clearly (seen 2026-09-25 on a real drone hover).
        motion_ref = np.median(d[d >= DUP_EPS]) if (d >= DUP_EPS).any() else 0.0
        jud = np.array([bool(x) and ((i > 0 and d[i - 1] >= max(3 * DUP_EPS, 0.5 * motion_ref)) or
                                     (i + 1 < len(d) and d[i + 1] >= max(3 * DUP_EPS, 0.5 * motion_ref))) for i, x in enumerate(dup)])
        # freeze: long run of duplicates
        run = 0
        for i, x in enumerate(list(dup) + [False]):
            if x:
                run += 1
                continue
            if run >= int(FREEZE_MIN_S * fps):  # report the FULL hold length, not the moment it crossed the threshold
                ev("freeze", a + i - run, seconds=round(run / fps, 2))
            run = 0
        # stutter: duplicates scattered in a 1 s window (not one long run)
        win = int(fps)
        flagged = False
        for i in range(0, max(1, len(dup) - win)):
            w = jud[i:i + win]
            longest, cur = 0, 0
            for x in dup[i:i + win]:
                cur = cur + 1 if x else 0; longest = max(longest, cur)
            if not flagged and w.sum() >= max(4, win // 5) and longest < int(FREEZE_MIN_S * fps):
                ev("stutter", a + i, dup_frames=int(w.sum())); flagged = True
        # flicker: >= 4 alternating luma swings within 0.5 s
        dl = np.diff(lu)
        sw = [(j, np.sign(x)) for j, x in enumerate(dl) if abs(x) >= FLICKER_DLUMA]
        for k in range(len(sw) - 3):
            js = [sw[k + m][0] for m in range(4)]; sg = [sw[k + m][1] for m in range(4)]
            if js[-1] - js[0] <= fps / 2 and all(sg[m] != sg[m + 1] for m in range(3)):
                ev("flicker", a + js[0]); break
        # discontinuity: diff spike vs local motion, not at a TransNetV2 cut, not a black/flicker frame
        for i in range(len(d)):
            g = a + i
            if near_cut[g] or near_cut[min(n - 1, g + 1)] or dark_all[g] or dark_all[min(n - 1, g + 1)]:
                continue
            lo, hi = max(0, i - 15), min(len(d), i + 16)
            local = np.median(np.r_[d[lo:i], d[i + 1:hi]]) if hi - lo > 3 else 0
            # a dropped-frame jump or jump cut is ONE isolated spike; a fast camera move (whip pan, tilt) is a run of
            # high differences with motion blur (seen 2026-09-25 on a real drone tilt-down), so require isolation
            prev_d = d[i - 1] if i > 0 else 0.0
            next_d = d[i + 1] if i + 1 < len(d) else 0.0
            isolated = prev_d < d[i] / 2 and next_d < d[i] / 2
            if d[i] >= SPIKE_MIN and d[i] >= SPIKE_RATIO * max(local, 1.0) and isolated:
                ev("discontinuity", g + 1, spike=round(float(d[i]), 1), local=round(float(local), 1))
    # a flicker also produces diff spikes and a black flash produces dark-frame jumps: keep the more specific label
    specific = [(e["t"]) for e in events if e["type"] in ("flicker", "black_flash")]
    events = [e for e in events if not (e["type"] == "discontinuity" and any(abs(e["t"] - t) < 0.6 for t in specific))]
    for e in events:
        e["severity"] = "WARN" if e["type"] == "freeze" else "FAIL"
    return {"events": events, "frames": n, "fps": round(fps, 3), "shots": len(shots),
            "verdict": "FAIL" if any(e["severity"] == "FAIL" for e in events) else ("WARN" if events else "PASS")}
