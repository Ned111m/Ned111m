"""vpcore — shared primitives for the video-production skill repo.

Stdlib only. Every CLI in this repo imports from here so that frame math,
ffmpeg invocation, encoder fallback and schema validation behave identically.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "shared-config" / "workflow-schemas"
PRESET_DIR = ROOT / "shared-config" / "render-presets"


# ---------------------------------------------------------------- frame math
def parse_fps(value: str | float | int | Fraction) -> Fraction:
    """'30000/1001', '29.97', 25, Fraction -> exact Fraction.

    NTSC decimals are snapped to their exact rational (29.97 -> 30000/1001).
    """
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    s = str(value).strip()
    if "/" in s:
        n, d = s.split("/", 1)
        return Fraction(int(n), int(d))
    f = float(s)
    for base in (24, 30, 48, 60, 120, 240):
        ntsc = Fraction(base * 1000, 1001)
        if abs(f - float(ntsc)) < 0.006:
            return ntsc
    return Fraction(s).limit_denominator(1001)


def sec_to_frame(sec: float, fps: Fraction | float) -> int:
    """JS Math.round(sec * fps) semantics (round half up, not banker's)."""
    return math.floor(float(sec) * float(fps) + 0.5)


def frame_to_sec(frame: int, fps: Fraction | float) -> float:
    return float(Fraction(frame) / Fraction(fps)) if isinstance(fps, Fraction) else frame / fps


def frame_to_tc(frame: int, fps: Fraction, start_tc: str = "00:00:00:00") -> str:
    """Non-drop-frame timecode on the rounded integer timebase."""
    base = round(float(fps))
    frame += tc_to_frame(start_tc, fps)
    ff = frame % base
    s = frame // base
    return f"{s // 3600:02d}:{(s // 60) % 60:02d}:{s % 60:02d}:{ff:02d}"


def tc_to_frame(tc: str, fps: Fraction) -> int:
    hh, mm, ss, ff = (int(x) for x in re.split(r"[:;]", tc))
    base = round(float(fps))
    return ((hh * 60 + mm) * 60 + ss) * base + ff


def db_to_lin(db: float) -> float:
    return 10.0 ** (db / 20.0)


# ---------------------------------------------------------------- io helpers
def read_json(path: str | Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(path: str | Path, obj: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, p)
    return p


def log(*a: Any) -> None:
    print(*a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- schemas
@lru_cache(maxsize=1)
def _registry():
    from referencing import Registry, Resource  # ships with jsonschema >= 4.18
    reg = Registry()
    for p in SCHEMA_DIR.glob("*.schema.json"):
        doc = read_json(p)
        reg = reg.with_resource(doc["$id"], Resource.from_contents(doc))
    return reg


def validate(obj: Any, schema_name: str) -> None:
    """Validate against shared-config/workflow-schemas/<name>.schema.json.

    Raises ValueError on mismatch. If `jsonschema` is not installed the check
    is skipped with a warning (CLIs stay usable on a bare Python).
    """
    schema_path = SCHEMA_DIR / f"{schema_name}.schema.json"
    try:
        import jsonschema  # type: ignore
    except ImportError:
        log(f"[vpcore] jsonschema not installed; skipped validation of {schema_name}")
        return
    schema = read_json(schema_path)
    validator = jsonschema.Draft202012Validator(schema, registry=_registry(), format_checker=jsonschema.FormatChecker())
    errors = sorted(validator.iter_errors(obj), key=lambda e: list(e.path))
    if errors:
        msg = "; ".join(f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors[:10])
        raise ValueError(f"{schema_name} schema violation: {msg}")


# ---------------------------------------------------------------- ffmpeg
def which(binary: str, env_var: str | None = None) -> str:
    if env_var and os.environ.get(env_var):
        return os.environ[env_var]
    found = shutil.which(binary)
    if not found:
        raise FileNotFoundError(f"{binary} not found on PATH (set {env_var or binary.upper()})")
    return found


def ffmpeg_bin() -> str:
    return which("ffmpeg", "VP_FFMPEG")


def ffprobe_bin() -> str:
    return which("ffprobe", "VP_FFPROBE")


def probe(path: str | Path) -> dict:
    out = subprocess.run(
        [ffprobe_bin(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def video_stream(info: dict) -> dict | None:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)


def audio_stream(info: dict) -> dict | None:
    return next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)


@lru_cache(maxsize=None)
def encoder_works(encoder: str) -> bool:
    """Real 1-frame test encode — the encoder list alone lies when no GPU/driver is present."""
    if os.environ.get("VP_FORCE_CPU") == "1" and "nvenc" in encoder:
        return False
    pix = "p010le" if encoder == "hevc_nvenc" else "yuv420p"
    cmd = [ffmpeg_bin(), "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
           "-frames:v", "1", "-pix_fmt", pix, "-c:v", encoder, "-f", "null", "-"]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=30).returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# NVENC -> CPU parameter translation. Quality targets are matched approximately:
# NVENC -cq 18 ~ x265 -crf 18 (visually transparent for drone footage at 4K).
_CPU_FALLBACK = {
    "hevc_nvenc": ("libx265", {"-preset": "slow", "-tune": None, "-rc": None, "-cq": "-crf",
                               "-multipass": None, "-spatial-aq": None, "-temporal-aq": None,
                               "-aq-strength": None, "-rc-lookahead": None, "-b_ref_mode": None,
                               "-bf": None}),
    "h264_nvenc": ("libx264", {"-preset": "slow", "-tune": None, "-rc": None, "-cq": "-crf",
                               "-multipass": None, "-spatial-aq": None, "-temporal-aq": None,
                               "-aq-strength": None, "-rc-lookahead": None, "-b_ref_mode": None,
                               "-bf": None}),
}
_PIX_FALLBACK = {"p010le": "yuv420p10le", "nv12": "yuv420p"}
_HWACCEL_FLAGS = ("-hwaccel", "-hwaccel_output_format")


def cpu_fallback(args: list[str]) -> list[str]:
    """Rewrite an NVENC/CUDA ffmpeg argv into an equivalent CPU argv."""
    out: list[str] = []
    i = 0
    enc_from = None
    while i < len(args):
        a = args[i]
        if a in _HWACCEL_FLAGS:
            i += 2
            continue
        if a in ("-c:v", "-vcodec", "-codec:v") and i + 1 < len(args) and args[i + 1] in _CPU_FALLBACK:
            enc_from = args[i + 1]
            out += [a, _CPU_FALLBACK[enc_from][0]]
            i += 2
            continue
        out.append(a)
        i += 1
    if enc_from is None:
        return out
    mapping = _CPU_FALLBACK[enc_from][1]
    res: list[str] = []
    i = 0
    seen_preset = False
    while i < len(out):
        a = out[i]
        if a in mapping:
            new = mapping[a]
            if a == "-preset":
                res += ["-preset", new]  # p1..p7 have no meaning for x26x
                seen_preset = True
            elif new is not None:
                res += [new, out[i + 1]]
            i += 2
            continue
        if a == "-pix_fmt" and i + 1 < len(out) and out[i + 1] in _PIX_FALLBACK:
            res += ["-pix_fmt", _PIX_FALLBACK[out[i + 1]]]
            i += 2
            continue
        if a in ("-vf", "-filter_complex") and i + 1 < len(out):
            res += [a, out[i + 1].replace("scale_cuda", "scale").replace("hwdownload,", "")]
            i += 2
            continue
        res.append(a)
        i += 1
    if not seen_preset:
        res[-1:-1] = ["-preset", "slow"]
    if enc_from == "hevc_nvenc" and "-tag:v" not in res:
        res[-1:-1] = ["-tag:v", "hvc1"]
    return res


_NVENC_ERRORS = re.compile(
    r"(No NVENC capable devices|Cannot load libcuda|Cannot load libnvidia-encode|OpenEncodeSessionEx failed"
    r"|No capable devices found|CUDA_ERROR|Device creation failed|Unknown encoder '\w+_nvenc'"
    r"|Error while opening encoder.*nvenc|Driver does not support the required nvenc API)",
    re.I,
)
_ZSCALE_PATH = re.compile(r"(no path between colorspaces|code 3074|zscale.*unknown)", re.I)


def _fix_zscale(args: list[str]) -> list[str]:
    """Untagged sources break zscale; force explicit HLG/BT.2020 input tags."""
    res = []
    for a in args:
        if "zscale=t=linear" in a and "tin=" not in a:
            a = a.replace("zscale=t=linear", "zscale=tin=arib-std-b67:pin=bt2020:min=bt2020nc:t=linear", 1)
        res.append(a)
    return res


class FFmpegError(RuntimeError):
    def __init__(self, cmd: Sequence[str], code: int, stderr: str):
        self.cmd, self.code, self.stderr = list(cmd), code, stderr
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        super().__init__(f"ffmpeg exited {code}\n$ {' '.join(cmd)}\n{tail}")


Fixer = Callable[[list[str], str], "list[str] | None"]


def _default_fixers() -> list[Fixer]:
    def nvenc(args: list[str], err: str) -> list[str] | None:
        if _NVENC_ERRORS.search(err) or any(a.endswith("_nvenc") for a in args) and "Error while opening encoder" in err:
            log("[vpcore] NVENC/CUDA unavailable -> CPU fallback")
            new = cpu_fallback(args)
            return new if new != args else None
        return None

    def zscale(args: list[str], err: str) -> list[str] | None:
        if _ZSCALE_PATH.search(err):
            log("[vpcore] zscale colorspace path error -> forcing explicit input tags")
            new = _fix_zscale(args)
            return new if new != args else None
        return None

    def pixfmt(args: list[str], err: str) -> list[str] | None:
        m = re.search(r"Incompatible pixel format '(\w+)'.*?Using pixel format '(\w+)'|"
                      r"Specified pixel format (\w+) is invalid or not supported", err)
        if m and "-pix_fmt" in args:
            i = args.index("-pix_fmt")
            cur = args[i + 1]
            if cur in _PIX_FALLBACK:
                log(f"[vpcore] pix_fmt {cur} rejected -> {_PIX_FALLBACK[cur]}")
                return args[:i + 1] + [_PIX_FALLBACK[cur]] + args[i + 2:]
        return None

    return [nvenc, zscale, pixfmt]


def run_ffmpeg(args: Sequence[str], retries: int = 3, fixers: Iterable[Fixer] | None = None,
               capture: bool = True) -> subprocess.CompletedProcess:
    """Run ffmpeg with autonomous error recovery.

    `args` excludes the binary. On failure the stderr is matched against known
    failure classes (NVENC/CUDA missing, zscale colorspace path, pix_fmt) and the
    argv is corrected and retried, up to `retries` attempts, before raising.
    """
    argv = list(args)
    if any(a.endswith("_nvenc") for a in argv) and not all(
            encoder_works(argv[i + 1]) for i, a in enumerate(argv[:-1])
            if a in ("-c:v", "-vcodec") and argv[i + 1].endswith("_nvenc")):
        log("[vpcore] NVENC probe failed up-front -> CPU fallback")
        argv = cpu_fallback(argv)
    fx = list(fixers) if fixers is not None else _default_fixers()
    last_err = ""
    for attempt in range(1, retries + 1):
        cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin"] + argv
        proc = subprocess.run(cmd, capture_output=capture, text=True)
        if proc.returncode == 0:
            return proc
        last_err = proc.stderr or ""
        log(f"[vpcore] attempt {attempt}/{retries} failed (exit {proc.returncode})")
        fixed = None
        for f in fx:
            fixed = f(argv, last_err)
            if fixed:
                break
        if not fixed:
            break
        argv = fixed
    raise FFmpegError([ffmpeg_bin()] + argv, proc.returncode, last_err)


def decode_pcm_mono(path: str | Path, sr: int = 48000) -> "list[float]":
    """Decode any audio to mono float samples via ffmpeg (stdlib array, no numpy)."""
    import array
    raw = subprocess.run(
        [ffmpeg_bin(), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        check=True, capture_output=True,
    ).stdout
    a = array.array("f")
    a.frombytes(raw[: len(raw) - len(raw) % 4])
    if sys.byteorder != "little":
        a.byteswap()
    return a.tolist()


def load_preset(name: str) -> dict:
    p = PRESET_DIR / f"{name}.json"
    if not p.exists():
        avail = ", ".join(sorted(x.stem for x in PRESET_DIR.glob("*.json")))
        raise FileNotFoundError(f"render preset '{name}' not found. Available: {avail}")
    preset = read_json(p)
    validate(preset, "render-preset")
    return preset
