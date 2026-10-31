"""End-to-end tests on synthetic media (ffmpeg required; librosa/soundfile for analysis tests)."""
import json
import math
import subprocess
from fractions import Fraction

import pytest

import vpcore
from conftest import FIXTURES, ROOT, ff

sf = pytest.importorskip("soundfile")
np = pytest.importorskip("numpy")


def test_probe_detects_hlg(media):
    import ffkit
    s = ffkit.summarize(str(media / "hlg.mp4"))
    assert s["video"]["gamma"] == "hlg" and s["video"]["fps"] == "25"


def test_encode_with_cpu_fallback_and_to709(media, tmp_path, monkeypatch):
    import ffkit
    monkeypatch.setenv("VP_FORCE_CPU", "1")
    vpcore.encoder_works.cache_clear()
    r = ffkit.encode(str(media / "hlg.mp4"), str(tmp_path / "yt.mp4"), "hevc-nvenc-youtube-4k", to709=True)
    out = ffkit.summarize(r["output"])
    assert out["video"]["codec"] == "hevc" and out["video"]["colorTransfer"] == "bt709"
    assert out["video"]["pixFmt"] == "yuv420p10le"
    assert abs(ffkit.measure_loudness(r["output"])["integratedLufs"] - (-14)) <= 0.5
    vpcore.encoder_works.cache_clear()


def test_proxy_scales_and_skips(media, tmp_path):
    import ffkit
    ff("-f", "lavfi", "-i", "testsrc2=s=1920x1440:r=25:d=0.4", "-c:v", "libx264", str(tmp_path / "big.mp4"))
    r = ffkit.proxy([str(tmp_path / "big.mp4")], str(tmp_path / "px"), "h264-proxy-720")
    assert r[0]["height"] == 720 and r[0]["width"] == 960
    assert ffkit.proxy([str(tmp_path / "big.mp4")], str(tmp_path / "px"), "h264-proxy-720")[0]["skipped"]


def test_ambience_loop_is_seamless(media, tmp_path):
    import ambience_loop
    r = ambience_loop.make_loop(str(media / "amb.wav"), str(tmp_path / "loop.wav"), 1.0)
    y, sr = sf.read(tmp_path / "loop.wav")
    assert abs(len(y) / sr - 7.0) < 1e-3
    typical = np.percentile(np.abs(np.diff(y, axis=0)).max(1), 99.9)
    assert np.abs(y[0] - y[-1]).max() <= typical
    assert r["loopSec"] == pytest.approx(7.0, abs=1e-3)


def test_vo_chain_hits_target(media, tmp_path):
    import vo_chain
    ff("-f", "lavfi", "-i", "anoisesrc=c=pink:d=6:a=0.1:r=48000", "-f", "lavfi", "-i", "sine=f=220:d=6:r=48000",
       "-filter_complex", "[1]volume='if(gt(mod(t,2),1),1,0)':eval=frame[s];[0][s]amix=normalize=0", str(tmp_path / "vo.wav"))
    r = vo_chain.process(str(tmp_path / "vo.wav"), str(tmp_path / "vo_out.wav"), "narration", "0.1:0.9", -16, -1.5, 7)
    assert abs(r["output"]["integratedLufs"] + 16) <= 0.5
    assert r["output"]["truePeakDbtp"] <= -1.0


def test_sfx_cues_are_sample_accurate(media, tmp_path):
    import sfx_cue_mix
    sheet = {"version": 1, "fps": "30000/1001", "durationInFrames": 120, "sampleRate": 48000, "masterGainDb": -6,
             "cues": [{"frame": 30, "file": str(media / "impact.wav"), "align": "peak"},
                      {"frame": 90, "file": str(media / "riser.wav"), "align": "end", "pan": -0.5},
                      {"frame": 1, "file": str(media / "impact.wav"), "align": "peak", "fadeOutMs": 50}]}
    (tmp_path / "cues.json").write_text(json.dumps(sheet))
    r = sfx_cue_mix.render(str(tmp_path / "cues.json"), str(tmp_path / "stem.wav"), report=True)
    y, sr = sf.read(tmp_path / "stem.wav")
    assert len(y) == math.floor(120 * 48000 * 1001 / 30000 + 0.5) == r["samples"]
    m = np.abs(y).max(1)
    hit = math.floor(30 * 48000 / Fraction(30000, 1001) + 0.5)
    assert 40000 + int(m[40000:56000].argmax()) == hit
    riser_end = math.floor(90 * 48000 / Fraction(30000, 1001) + 0.5)
    assert np.nonzero(m[100000:] > 1e-4)[0][-1] + 100000 == riser_end - 1
    seg = y[riser_end - 14000: riser_end]
    rms = np.sqrt((seg ** 2).mean(0))
    assert rms[0] > rms[1] * 2  # panned left
    assert r["plan"][2]["startSample"] < 0  # pre-roll trimmed, not shifted


def test_beat_markers_on_click_track(media, tmp_path):
    pytest.importorskip("librosa")
    import beat_markers
    d = beat_markers.analyse(str(media / "click120.wav"), "25", snap_ms=60)
    assert d["tempoBpm"] == pytest.approx(120, abs=1.5)
    errs = [abs(b["time"] - (0.25 + 0.5 * round((b["time"] - 0.25) / 0.5))) for b in d["beats"]]
    assert max(errs) < 0.005  # < 5 ms after attack snapping
    accented = [b for b in d["beats"] if b["downbeat"]]
    assert all(abs((b["time"] - 0.75) % 2.0) < 0.01 or abs((b["time"] - 0.75) % 2.0 - 2.0) < 0.01 for b in accented)
    beat_markers.write_edl(d, tmp_path / "m.edl", "01:00:00:00", "m")
    txt = (tmp_path / "m.edl").read_text()
    assert "|C:ResolveColorBlue |M:Bar" in txt and txt.startswith("TITLE: m\nFCM: NON-DROP FRAME")


def test_telemetry_from_srt(tmp_path):
    import dji_srt_to_props as t
    props = t.build_props(str(FIXTURES / "DJI_SYNTH_MINI4PRO.SRT"), "25")
    assert props["durationInFrames"] == 250 and len(props["samples"]) == 250
    assert all(s["frame"] == i for i, s in enumerate(props["samples"]))
    s = props["samples"][150]  # t = 6 s: alt = 40 + 18 - 4.32, climb = 3 - 0.24*6
    assert s["relAlt"] == pytest.approx(53.68, abs=0.05)
    assert s["vSpeedMs"] == pytest.approx(1.56, abs=0.1)
    assert s["colorMd"] == "dlog_m" and s["fnum"] == 1.7
    # orbit r=30 m, 1.5*pi rad in 10 s + 0.5 m/s drift -> ~14.1 m/s
    assert 12.5 < s["speedMs"] < 15.5
    assert props["bounds"]["totalDistanceM"] == pytest.approx(30 * 1.5 * math.pi, rel=0.08)


def test_telemetry_legacy_and_no_gps():
    import dji_srt_to_props as t
    legacy = "1\n00:00:00,000 --> 00:00:01,000\nF/2.8, SS 320, ISO 100, EV 0, GPS (18.0987, 44.5512, 15), D 12m, H 40.5m\n"
    r = t.parse_srt(legacy)
    assert r[0]["lat"] == 44.5512 and r[0]["lon"] == 18.0987 and r[0]["relAlt"] == 40.5
    nogps = "1\n00:00:00,000 --> 00:00:00,033\n[latitude: 0.000000] [longitude: 0.000000] [rel_alt: 1.0 abs_alt: 2.0]\n"
    assert t.parse_srt(nogps)[0]["lat"] is None


def test_ir_convolution_has_tail(media, tmp_path):
    import sfx_library
    ff("-f", "lavfi", "-i", "anoisesrc=d=1.0:a=1:r=48000", "-af", "afade=t=out:d=1.0", str(tmp_path / "ir.wav"))
    sfx_library.convolve(str(media / "impact.wav"), str(tmp_path / "ir.wav"), str(tmp_path / "c.wav"), 0.4, 0)
    y, sr = sf.read(tmp_path / "c.wav")
    assert len(y) / sr > 0.6  # longer than the 0.5 s dry source


def test_sfx_tagging(media, tmp_path):
    import shutil
    import sfx_library
    shutil.copy(media / "impact.wav", tmp_path / "ui_click_hit.wav")
    shutil.copy(media / "amb.wav", tmp_path / "forest_river_wind.wav")
    r = sfx_library.tag_dir(str(tmp_path), location="Maglaj")
    assert r["tagged"] == 2
    side = json.loads((tmp_path / "ui_click_hit.wav.asset.json").read_text())
    assert side["kind"] == "sfx" and {"gfx", "click", "impact"} <= set(side["tags"])
    assert side["peakOffsetSec"] == pytest.approx(0.1, abs=0.003)
    amb = json.loads((tmp_path / "forest_river_wind.wav.asset.json").read_text())
    assert amb["kind"] == "ambience" and "water" in amb["tags"]


def test_tvai_slowmo_chain_timing(media, tmp_path):
    """Same retime chain as tvai.py with tvai_fi replaced by a stock frame-rate up-conversion."""
    src = media / "hlg.mp4"
    vf = "fps=100,setpts=4*PTS,fps=25"
    ff("-i", str(src), "-vf", vf, "-an", "-c:v", "libx264", str(tmp_path / "s.mp4"))
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(tmp_path / "s.mp4")], capture_output=True, text=True).stdout)
    assert d == pytest.approx(4.0, abs=0.1)
