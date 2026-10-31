#!/usr/bin/env python3
"""dji_srt_to_props — DJI flight SRT -> per-frame Remotion telemetry props (telemetry-props.schema.json).

Supported block formats
  * Mini 4 Pro / Air 3 / Mavic 3 (per-frame):
      <font size="28">FrameCnt: 1, DiffTime: 33ms
      2024-06-15 12:03:43.105
      [iso: 100] [shutter: 1/1000.0] [fnum: 1.7] [ev: 0] [color_md: dlog_m] [focal_len: 24.00]
      [latitude: 44.55123] [longitude: 18.09876] [rel_alt: 52.300 abs_alt: 231.118] [ct: 5500] </font>
  * Legacy (Mavic 2 / Mini 2):  GPS (18.0987, 44.5512, 15) ... H 52.30m ...

Derived per output frame (resampled to the timeline fps, samples[i].frame == i):
  speedMs     horizontal ground speed (haversine over a centred window, default 1.0 s — suppresses GPS jitter)
  vSpeedMs    vertical speed from rel_alt over the same window
  headingDeg  course over ground (held when moving < 0.5 m/s)
  distanceM   cumulative path length of the smoothed track

Not in DJI SRTs (do not fake): battery %, gimbal pitch, wind. Those require the flight log (.txt -> CSV).
abs_alt is barometric/GPS fused and unreliable between flights — overlays should show relAlt.

  dji_srt_to_props.py DJI_0042.SRT --video DJI_0042.MP4 --out public/telemetry/DJI_0042.json
  dji_srt_to_props.py DJI_0042.SRT --fps 25 --window 1.5
"""
from __future__ import annotations

import argparse
import bisect
import math
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "_lib"))
import vpcore  # noqa: E402

_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")
_KV = re.compile(r"([a-z_]+)\s*:\s*(-?[\d.]+(?:/[\d.]+)?|[a-z_]+)", re.I)
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,:]\d+)?)")
_LEGACY_GPS = re.compile(r"GPS\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*(?:,\s*(-?[\d.]+))?\)")
_LEGACY_H = re.compile(r"\bH\s*(-?[\d.]+)\s*m", re.I)
_TAG = re.compile(r"<[^>]+>")

R_EARTH = 6371008.8


def _ts(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / (10 ** len(ms))


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v is not None else None
    except ValueError:
        return None


def parse_srt(text: str) -> list[dict]:
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    out = []
    for b in blocks:
        m = _TIME.search(b)
        if not m:
            continue
        g = m.groups()
        t0 = _ts(*g[:4])
        body = _TAG.sub(" ", b[m.end():])
        kv = {k.lower(): v for k, v in _KV.findall(body)}
        rec: dict = {"t": t0}
        rec["lat"] = _num(kv.get("latitude"))
        rec["lon"] = _num(kv.get("longitude") or kv.get("longtitude"))  # DJI typo seen in some firmwares
        rec["relAlt"] = _num(kv.get("rel_alt"))
        rec["absAlt"] = _num(kv.get("abs_alt"))
        lg = _LEGACY_GPS.search(body)
        if rec["lat"] is None and lg:
            rec["lon"], rec["lat"] = float(lg.group(1)), float(lg.group(2))  # legacy order is (lon, lat, sats)
            h = _LEGACY_H.search(body)
            rec["relAlt"] = float(h.group(1)) if h else None
        if rec["lat"] is not None and rec["lon"] is not None and abs(rec["lat"]) < 1e-6 and abs(rec["lon"]) < 1e-6:
            rec["lat"] = rec["lon"] = None  # no GPS lock
        rec["iso"] = _num(kv.get("iso"))
        rec["shutter"] = kv.get("shutter")
        rec["fnum"] = _num(kv.get("fnum"))
        if rec["fnum"] and rec["fnum"] > 50:  # some firmwares write 170 for f/1.7
            rec["fnum"] /= 100
        rec["ev"] = _num(kv.get("ev"))
        rec["ct"] = _num(kv.get("ct"))
        rec["focalLen"] = _num(kv.get("focal_len"))
        rec["colorMd"] = kv.get("color_md")
        d = _DATE.search(body)
        rec["date"] = d.group(1) if d else None
        out.append(rec)
    out.sort(key=lambda r: r["t"])
    return out


def haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R_EARTH * math.asin(min(1.0, math.sqrt(a)))


def bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _interp(recs: list[dict], times: list[float], key: str, t: float) -> float | None:
    """Linear interpolation over records that have `key`; holds the edges."""
    i = bisect.bisect_right(times, t)
    lo = next((j for j in range(i - 1, -1, -1) if recs[j][key] is not None), None)
    hi = next((j for j in range(i, len(recs)) if recs[j][key] is not None), None)
    if lo is None and hi is None:
        return None
    if lo is None:
        return recs[hi][key]
    if hi is None or times[hi] == times[lo]:
        return recs[lo][key]
    w = (t - times[lo]) / (times[hi] - times[lo])
    return recs[lo][key] + w * (recs[hi][key] - recs[lo][key])


def _nearest(recs: list[dict], times: list[float], key: str, t: float):
    i = max(0, bisect.bisect_right(times, t) - 1)
    return recs[i][key]


def build_props(srt_path: str, fps_s: str, duration_frames: int | None = None, window: float = 1.0,
                device: str = "DJI") -> dict:
    recs = parse_srt(Path(srt_path).read_text(encoding="utf-8", errors="replace"))
    if not recs:
        raise SystemExit(f"{srt_path}: no SRT blocks parsed")
    fps = vpcore.parse_fps(fps_s)
    times = [r["t"] for r in recs]
    if duration_frames is None:
        last = recs[-1]["t"] + (recs[-1]["t"] - recs[-2]["t"] if len(recs) > 1 else 1 / float(fps))
        duration_frames = max(1, vpcore.sec_to_frame(last, fps))
    has_gps = any(r["lat"] is not None for r in recs)

    # 1) resample raw fields to frame grid
    raw = []
    for f in range(duration_frames):
        t = vpcore.frame_to_sec(f, fps)
        raw.append({k: _interp(recs, times, k, t) for k in ("lat", "lon", "relAlt", "absAlt")} | {"t": t})

    # 2) centred moving average of position (window seconds) -> smoothed track
    half = max(1, vpcore.sec_to_frame(window / 2, fps))
    n = len(raw)

    def smooth(key: str) -> list[float | None]:
        vals = [r[key] for r in raw]
        if all(v is None for v in vals):
            return vals
        pref, cnt = [0.0], [0]
        for v in vals:
            pref.append(pref[-1] + (v or 0.0))
            cnt.append(cnt[-1] + (v is not None))
        out = []
        for i in range(n):
            h = min(half, i, n - 1 - i)  # symmetric window shrinks at the edges -> no positional bias
            a, b = i - h, i + h + 1
            c = cnt[b] - cnt[a]
            out.append((pref[b] - pref[a]) / c if c else None)
        return out

    slat, slon, salt = smooth("lat"), smooth("lon"), smooth("relAlt")

    samples = []
    dist = 0.0
    heading = None
    for i in range(n):
        a, b = max(0, i - half), min(n - 1, i + half)
        dt = (b - a) / float(fps)
        speed = vspeed = 0.0
        if has_gps and dt > 0 and None not in (slat[a], slon[a], slat[b], slon[b]):
            d = haversine(slat[a], slon[a], slat[b], slon[b])
            speed = d / dt
            if speed >= 0.5:
                heading = bearing(slat[a], slon[a], slat[b], slon[b])
        if dt > 0 and salt[a] is not None and salt[b] is not None:
            vspeed = (salt[b] - salt[a]) / dt
        if i > 0 and None not in (slat[i - 1], slon[i - 1], slat[i], slon[i]):
            dist += haversine(slat[i - 1], slon[i - 1], slat[i], slon[i])
        t = raw[i]["t"]
        samples.append({
            "frame": i, "t": round(t, 5),
            "lat": None if raw[i]["lat"] is None else round(raw[i]["lat"], 7),
            "lon": None if raw[i]["lon"] is None else round(raw[i]["lon"], 7),
            "relAlt": None if raw[i]["relAlt"] is None else round(raw[i]["relAlt"], 2),
            "absAlt": None if raw[i]["absAlt"] is None else round(raw[i]["absAlt"], 2),
            "speedMs": round(speed, 3), "vSpeedMs": round(vspeed, 3),
            "headingDeg": None if heading is None else round(heading, 1),
            "distanceM": round(dist, 2),
            "iso": _nearest(recs, times, "iso", t), "shutter": _nearest(recs, times, "shutter", t),
            "fnum": _nearest(recs, times, "fnum", t), "ev": _nearest(recs, times, "ev", t),
            "ct": _nearest(recs, times, "ct", t), "focalLen": _nearest(recs, times, "focalLen", t),
            "colorMd": _nearest(recs, times, "colorMd", t),
        })

    lats = [s["lat"] for s in samples if s["lat"] is not None]
    lons = [s["lon"] for s in samples if s["lon"] is not None]
    alts = [s["relAlt"] for s in samples if s["relAlt"] is not None]
    fps_str = f"{fps.numerator}/{fps.denominator}" if fps.denominator != 1 else str(fps.numerator)
    props = {
        "version": 1, "fps": fps_str, "durationInFrames": duration_frames,
        "source": {"file": str(Path(srt_path).name), "device": device, "startTime": recs[0]["date"], "hasGps": has_gps},
        "bounds": {"minLat": min(lats, default=None), "maxLat": max(lats, default=None),
                   "minLon": min(lons, default=None), "maxLon": max(lons, default=None),
                   "minAlt": min(alts, default=None), "maxAlt": max(alts, default=None),
                   "maxSpeedMs": round(max(s["speedMs"] for s in samples), 3), "totalDistanceM": round(dist, 2)},
        "samples": samples,
    }
    vpcore.validate(props, "telemetry-props")
    return props


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("srt")
    ap.add_argument("--video", help="matching clip: fps + exact frame count are read from it")
    ap.add_argument("--fps", help="timeline fps when --video is not given (e.g. 25, 30000/1001)")
    ap.add_argument("--window", type=float, default=1.0, help="speed/heading smoothing window, seconds")
    ap.add_argument("--device", default="DJI Mini 4 Pro")
    ap.add_argument("--out", help="output JSON (default: <srt>.telemetry.json)")
    a = ap.parse_args()
    dur = None
    fps = a.fps
    if a.video:
        info = vpcore.probe(a.video)
        v = vpcore.video_stream(info)
        fps = fps or v["avg_frame_rate"]
        if v.get("nb_frames", "").isdigit():
            dur = int(v["nb_frames"])
        else:
            dur = vpcore.sec_to_frame(float(info["format"]["duration"]), vpcore.parse_fps(fps))
    if not fps:
        ap.error("--fps or --video required")
    props = build_props(a.srt, fps, dur, a.window, a.device)
    out = a.out or str(Path(a.srt).with_suffix(".telemetry.json"))
    vpcore.write_json(out, props)
    b = props["bounds"]
    print(f"{props['durationInFrames']} frames @ {props['fps']} | gps={props['source']['hasGps']} | "
          f"alt {b['minAlt']}..{b['maxAlt']} m | vmax {b['maxSpeedMs']} m/s | path {b['totalDistanceM']} m -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
