#!/usr/bin/env python3
"""beat_markers — librosa transient/beat analysis -> frame-exact cut markers.

Outputs (all from one analysis, all frame-quantised with Math.round(sec * fps)):
  <out>.json   beat-markers.schema.json  -> Remotion props, sfx cue sheets, resolve_assemble.py --markers
  <out>.edl    Resolve "Timeline > Import > Timeline Markers from EDL" (downbeats blue, beats green, onsets red)
  <out>.csv    frame,timecode,type,strength  (spreadsheets / Premiere marker scripts)

  beat_markers.py music.wav --fps 25 --out markers/music
  beat_markers.py music.wav --fps 30000/1001 --beats-per-bar 4 --onsets --start-tc 01:00:00:00

Quantisation caveat: a beat that falls between two frames is placed on the nearest frame (max error
half a frame = 20 ms @ 25 fps). Cut on `frame`; SFX alignment uses `time` for sample accuracy.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_lib"))
import vpcore  # noqa: E402


def _snap_to_attacks(y, sr: int, beat_times, window: float):
    """The onset envelope peaks after the attack (STFT window lag, ~10-40 ms). Move each beat to the
    steepest rise of a 5.8 ms RMS envelope within [t - window, t + window/3] (the audible attack)."""
    import librosa
    import numpy as np
    flen, hop = 128, 16  # 5.8 ms window, 0.73 ms resolution @ 22.05 kHz
    rms = librosa.feature.rms(y=y, frame_length=flen, hop_length=hop, center=True)[0]
    rise = np.diff(rms, prepend=rms[0])
    t_axis = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    out = np.array(beat_times, dtype=float)
    for i, t in enumerate(out):
        lo, hi = np.searchsorted(t_axis, [t - window, t + window / 3])
        if hi - lo < 2:
            continue
        k = lo + int(np.argmax(rise[lo:hi]))
        if rise[k] > 0:
            out[i] = t_axis[k]
    return out


def analyse(path: str, fps_s: str, beats_per_bar: int = 4, with_onsets: bool = False,
            start_bpm: float | None = None, onset_pct: float = 90.0, snap_ms: float = 60.0) -> dict:
    try:
        import librosa
        import numpy as np
    except ImportError as e:  # pragma: no cover
        raise SystemExit("pip install librosa soundfile numpy") from e
    fps = vpcore.parse_fps(fps_s)
    y, sr = librosa.load(path, sr=22050, mono=True)
    hop = 256  # 11.6 ms resolution @ 22.05 kHz (< half a frame at 60 fps)
    env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, aggregate=np.median)
    kw = {"start_bpm": start_bpm} if start_bpm else {}
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=env, sr=sr, hop_length=hop, trim=False, **kw)
    tempo = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop)
    if snap_ms > 0:
        beat_times = _snap_to_attacks(y, sr, beat_times, snap_ms / 1000.0)
    peak = float(env.max()) or 1.0
    # local max of the envelope within +-2 hops: robust strength at each beat
    strengths = [float(env[max(0, b - 2): b + 3].max()) / peak for b in beat_frames]
    # Downbeat phase: the bar position whose beats carry the most onset energy.
    phase = 0
    if len(strengths) >= beats_per_bar * 2:
        sums = [sum(strengths[p::beats_per_bar]) / len(strengths[p::beats_per_bar]) for p in range(beats_per_bar)]
        phase = int(max(range(beats_per_bar), key=lambda p: sums[p]))
    beats = [{"index": i, "time": round(float(t), 5), "frame": vpcore.sec_to_frame(t, fps),
              "strength": round(min(1.0, s), 4), "downbeat": (i - phase) % beats_per_bar == 0}
             for i, (t, s) in enumerate(zip(beat_times, strengths))]
    fps_str = f"{fps.numerator}/{fps.denominator}" if fps.denominator != 1 else str(fps.numerator)
    out = {"version": 1, "audio": str(Path(path).resolve()), "fps": fps_str, "sampleRate": sr,
           "durationSec": round(len(y) / sr, 4), "tempoBpm": round(tempo, 3), "beatsPerBar": beats_per_bar,
           "downbeatPhase": phase, "beats": beats}
    if with_onsets:
        on = librosa.onset.onset_detect(onset_envelope=env, sr=sr, hop_length=hop, backtrack=False)
        thr = float(np.percentile(env[on], onset_pct)) if len(on) else 0.0
        out["onsets"] = [{"time": round(float(librosa.frames_to_time(o, sr=sr, hop_length=hop)), 5),
                          "frame": vpcore.sec_to_frame(float(librosa.frames_to_time(o, sr=sr, hop_length=hop)), fps),
                          "strength": round(float(env[o]) / peak, 4)} for o in on if env[o] >= thr]
    vpcore.validate(out, "beat-markers")
    return out


def write_edl(data: dict, path: Path, start_tc: str, title: str) -> None:
    fps = vpcore.parse_fps(data["fps"])
    rows = [(b["frame"], "ResolveColorBlue" if b["downbeat"] else "ResolveColorGreen",
             f"{'Bar' if b['downbeat'] else 'Beat'} {b['index'] + 1}") for b in data["beats"]]
    rows += [(o["frame"], "ResolveColorRed", f"Hit {i + 1}") for i, o in enumerate(data.get("onsets", []))]
    rows.sort(key=lambda r: r[0])
    dropframe = fps.denominator == 1001
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    for n, (frame, color, name) in enumerate(rows, 1):
        tc_in = vpcore.frame_to_tc(frame, fps, start_tc)
        tc_out = vpcore.frame_to_tc(frame + 1, fps, start_tc)
        lines.append(f"{n:03d}  001      V     C        {tc_in} {tc_out} {tc_in} {tc_out}  ")
        lines.append(f" |C:{color} |M:{name} |D:1")
        lines.append("")
    if dropframe:
        vpcore.log("[beat_markers] NTSC rate: EDL uses NDF timecode on the rounded timebase (Resolve default for NDF timelines)")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_csv(data: dict, path: Path, start_tc: str) -> None:
    fps = vpcore.parse_fps(data["fps"])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["frame", "timecode", "time", "type", "strength"])
        rows = [(b["frame"], b["time"], "downbeat" if b["downbeat"] else "beat", b["strength"]) for b in data["beats"]]
        rows += [(o["frame"], o["time"], "onset", o["strength"]) for o in data.get("onsets", [])]
        for fr, t, typ, s in sorted(rows):
            w.writerow([fr, vpcore.frame_to_tc(fr, fps, start_tc), t, typ, s])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--fps", required=True, help="timeline fps, e.g. 25, 30000/1001, 59.94")
    ap.add_argument("--out", help="output path stem (default: next to audio)")
    ap.add_argument("--beats-per-bar", type=int, default=4)
    ap.add_argument("--start-bpm", type=float, help="tempo prior if the tracker locks to half/double time")
    ap.add_argument("--snap-ms", type=float, default=60.0, help="snap beats to attack start within +-ms (0 = off)")
    ap.add_argument("--onsets", action="store_true", help="also export strong transients (top 10%%)")
    ap.add_argument("--start-tc", default="01:00:00:00", help="timeline start TC for EDL/CSV")
    a = ap.parse_args()
    data = analyse(a.audio, a.fps, a.beats_per_bar, a.onsets, a.start_bpm, snap_ms=a.snap_ms)
    stem = Path(a.out) if a.out else Path(a.audio).with_suffix("")
    stem.parent.mkdir(parents=True, exist_ok=True)
    vpcore.write_json(stem.with_suffix(".json"), data)
    write_edl(data, stem.with_suffix(".edl"), a.start_tc, stem.name)
    write_csv(data, stem.with_suffix(".csv"), a.start_tc)
    print(f"tempo {data['tempoBpm']} BPM | {len(data['beats'])} beats | downbeat phase {data['downbeatPhase']}"
          f" | {len(data.get('onsets', []))} onsets -> {stem}.json/.edl/.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
