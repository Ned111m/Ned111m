import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in ("_lib", "mcp-extensions/ffmpeg-bridge", "mcp-extensions/vector-rag", "audio/voiceover-fairlight",
          "audio/ambience-sfx", "audio/sfx-for-gfx", "audio/nature-sfx", "audio/rhythm-sync",
          "motion-graphics/remotion/telemetry-overlays", "editing-color/davinci-resolve/python-api-scripts",
          "ai-automation/comfyui-nodes", "ai-automation/topaz-upscaling", "ai-automation/local-llm-agents",
          "publishing"):
    sys.path.insert(0, str(ROOT / p))

FIXTURES = ROOT / "tests" / "fixtures"


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=1", "-f", "lavfi", "-i", "sine=f=440:d=1:sample_rate=48000",
       "-c:v", "libx265", "-x265-params", "log-level=error", "-pix_fmt", "yuv420p10le",
       "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", "-c:a", "aac",
       "-shortest", str(d / "hlg.mp4"))
    ff("-f", "lavfi", "-i", "anoisesrc=c=brown:d=8:a=0.3:r=48000", "-af", "aformat=channel_layouts=stereo", str(d / "amb.wav"))
    ff("-f", "lavfi", "-i", "aevalsrc='if(gte(t,0.1),exp(-(t-0.1)*60)*sin(2*PI*900*(t-0.1)+1.2),0)':s=48000:d=0.5", str(d / "impact.wav"))
    ff("-f", "lavfi", "-i", "sine=f=800:d=1:r=48000", "-af", "afade=t=in:d=1:curve=tri", str(d / "riser.wav"))
    ff("-f", "lavfi", "-i",
       "aevalsrc='(1+2*eq(mod(floor((t-0.25)/0.5),4),1))*0.2*exp(-mod(t-0.25,0.5)*40)*sin(2*PI*120*t)*gte(t,0.25)':s=44100:d=12",
       str(d / "click120.wav"))
    return d
