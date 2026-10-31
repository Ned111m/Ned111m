import json
from fractions import Fraction

import pytest

import vpcore
from conftest import ROOT


@pytest.mark.parametrize("val,exp", [("25", Fraction(25)), ("29.97", Fraction(30000, 1001)),
                                     ("30000/1001", Fraction(30000, 1001)), (59.94, Fraction(60000, 1001)),
                                     ("23.976", Fraction(24000, 1001)), (50, Fraction(50))])
def test_parse_fps(val, exp):
    assert vpcore.parse_fps(val) == exp


def test_sec_to_frame_is_js_math_round():
    assert vpcore.sec_to_frame(0.5, 1) == 1          # Python round() would give 0
    assert vpcore.sec_to_frame(2.5, 1) == 3
    assert vpcore.sec_to_frame(1.0, Fraction(30000, 1001)) == 30
    assert vpcore.sec_to_frame(10.01, Fraction(30000, 1001)) == 300


def test_timecode_roundtrip():
    fps = Fraction(25)
    assert vpcore.frame_to_tc(0, fps, "01:00:00:00") == "01:00:00:00"
    assert vpcore.frame_to_tc(25 * 61 + 3, fps) == "00:01:01:03"
    assert vpcore.tc_to_frame("00:01:01:03", fps) == 25 * 61 + 3


def test_cpu_fallback_rewrites_nvenc():
    args = ["-y", "-hwaccel", "cuda", "-i", "in.mp4", "-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq",
            "-rc", "vbr", "-cq", "18", "-b:v", "0", "-multipass", "fullres", "-pix_fmt", "p010le", "out.mp4"]
    out = vpcore.cpu_fallback(args)
    assert "-hwaccel" not in out and "hevc_nvenc" not in out
    assert out[out.index("-c:v") + 1] == "libx265"
    assert out[out.index("-crf") + 1] == "18"
    assert out[out.index("-pix_fmt") + 1] == "yuv420p10le"
    assert out[out.index("-preset") + 1] == "slow"
    assert "-tune" not in out and "-multipass" not in out and "-rc" not in out
    assert out[-1] == "out.mp4" and "hvc1" in out


@pytest.mark.parametrize("p", sorted((ROOT / "shared-config" / "render-presets").glob("*.json")), ids=lambda p: p.stem)
def test_presets_valid(p):
    vpcore.validate(json.loads(p.read_text()), "render-preset")


def test_all_schemas_are_valid_jsonschema():
    import jsonschema
    for p in (ROOT / "shared-config" / "workflow-schemas").glob("*.schema.json"):
        jsonschema.Draft202012Validator.check_schema(json.loads(p.read_text()))


def test_validate_rejects_bad_cue_sheet():
    with pytest.raises(ValueError, match="sfx-cue-sheet"):
        vpcore.validate({"version": 1, "fps": "25", "durationInFrames": 10,
                         "cues": [{"frame": -1, "file": "x.wav"}]}, "sfx-cue-sheet")
