"""Application configuration, paths and hardware detection."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
DATA_DIR = Path(os.environ.get("CLIPFORGE_DATA", PROJECT_ROOT / "data")).resolve()
PROJECTS_DIR = DATA_DIR / "projects"
LIVE_DIR = DATA_DIR / "live"
TEMP_DIR = DATA_DIR / "tmp"
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"
DB_PATH = DATA_DIR / "clipforge.sqlite3"
FRONTEND_OUT = PROJECT_ROOT / "frontend" / "out"

HOST = os.environ.get("CLIPFORGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLIPFORGE_PORT", "8756"))

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ts", ".flv"}
AUDIO_SAMPLE_RATE = 16000


def ensure_dirs() -> None:
    for path in (DATA_DIR, PROJECTS_DIR, LIVE_DIR, TEMP_DIR, FONTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def which(binary: str) -> str | None:
    return shutil.which(binary)


def _has_subtitle_filters(binary: str) -> bool:
    try:
        out = subprocess.run([binary, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20)
        text = out.stdout or ""
        return " ass " in text or " subtitles " in text
    except Exception:
        return False


def _pick_ffmpeg() -> str:
    """Prefer an ffmpeg that can burn subtitles (libass); fall back to system ffmpeg."""
    override = os.environ.get("CLIPFORGE_FFMPEG")
    if override and Path(override).exists():
        return override
    candidates: list[str] = []
    try:
        import imageio_ffmpeg

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    system = shutil.which("ffmpeg")
    if system:
        candidates.append(system)
    for candidate in candidates:
        if _has_subtitle_filters(candidate):
            return candidate
    return candidates[0] if candidates else "ffmpeg"


FFMPEG = _pick_ffmpeg()
FFPROBE = os.environ.get("CLIPFORGE_FFPROBE") or shutil.which("ffprobe") or "ffprobe"


def _run(cmd: list[str], timeout: int = 20) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout
    except Exception:
        return ""


def ffmpeg_version() -> str | None:
    if not Path(FFMPEG).exists() and not which(FFMPEG):
        return None
    line = _run([FFMPEG, "-version"]).splitlines()
    return line[0] if line else "unknown"


def ffmpeg_capabilities() -> dict:
    """Probe ffmpeg for available hardware acceleration and encoders."""
    caps = {
        "ffmpeg": Path(FFMPEG).exists() or bool(which(FFMPEG)),
        "ffprobe": Path(FFPROBE).exists() or bool(which(FFPROBE)),
        "ffmpeg_path": FFMPEG,
        "version": ffmpeg_version(),
        "videotoolbox": False,
        "encoders": {},
        "libass": False,
        "loudnorm": False,
    }
    if not caps["ffmpeg"]:
        return caps
    encoders_out = _run([FFMPEG, "-hide_banner", "-encoders"])
    for enc in ("h264_videotoolbox", "hevc_videotoolbox", "libx264", "libx265", "aac", "libopus"):
        caps["encoders"][enc] = enc in encoders_out
    hw = _run([FFMPEG, "-hide_banner", "-hwaccels"])
    caps["videotoolbox"] = "videotoolbox" in hw
    filters = _run([FFMPEG, "-hide_banner", "-filters"])
    caps["libass"] = " ass " in filters or "subtitles" in filters
    caps["loudnorm"] = "loudnorm" in filters
    return caps


def machine_info() -> dict:
    mem_bytes = 0
    try:
        mem_bytes = int(_run(["sysctl", "-n", "hw.memsize"]).strip() or 0)
    except Exception:
        mem_bytes = 0
    chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    cores = os.cpu_count() or 1
    machine = _run(["uname", "-m"]).strip()
    return {
        "chip": chip or machine,
        "arch": machine,
        "cores": cores,
        "ram_gb": round(mem_bytes / (1024**3), 1) if mem_bytes else None,
        "apple_silicon": machine == "arm64",
    }


def disk_free_gb(path: Path | None = None) -> float:
    try:
        usage = shutil.disk_usage(str(path or DATA_DIR))
        return round(usage.free / (1024**3), 1)
    except Exception:
        return 0.0


def installed_fonts() -> list[dict]:
    """Font families available to the subtitle renderer.

    Font files are stored as ``BebasNeue.ttf`` while the ASS style name is the real
    family (``Bebas Neue``), so map stems back onto the known family list.
    """
    import re as _re

    from .clip_settings import SUBTITLE_FONTS

    lookup = {_re.sub(r"[^a-z]", "", f.lower()): f for f in SUBTITLE_FONTS}
    found: list[dict] = []
    if not FONTS_DIR.exists():
        return found
    seen: set[str] = set()
    for f in sorted(FONTS_DIR.iterdir()):
        if f.suffix.lower() not in {".ttf", ".otf"} or not f.is_file():
            continue
        stem = f.stem.split("-")[0]
        key = _re.sub(r"[^a-z]", "", stem.lower())
        family = lookup.get(key, f.stem)
        if family in seen:
            continue
        seen.add(family)
        found.append({"family": family, "file": f.name})
    return found
