"""FFmpeg / FFprobe helpers: probing, thumbnails, audio extraction, progress-aware runs."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from .config import FFMPEG, FFPROBE, ffmpeg_capabilities

ProgressCb = Callable[[float, str], None]


class FFmpegError(RuntimeError):
    pass


def run(cmd: list[str], timeout: int | None = None, cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)


def run_bytes(cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
    """Run a command and capture raw stdout bytes (audio/PCM work)."""
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def ffprobe(path: str | Path) -> dict:
    out = run(
        [
            FFPROBE, "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        timeout=120,
    )
    if out.returncode != 0:
        raise FFmpegError(f"ffprobe failed for {path}: {out.stderr.strip()[:500]}")
    return json.loads(out.stdout or "{}")


def _fraction(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            d = float(den)
            return float(num) / d if d else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def probe_media(path: str | Path) -> dict:
    """Normalised media description used across the app."""
    info = ffprobe(path)
    streams = info.get("streams", [])
    fmt = info.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or 0)
    if not duration and video:
        duration = float(video.get("duration") or 0)
    rotation = 0
    if video:
        for sd in video.get("side_data_list", []) or []:
            if "rotation" in sd:
                rotation = abs(int(float(sd["rotation"])))
    width = int(video.get("width") or 0) if video else 0
    height = int(video.get("height") or 0) if video else 0
    if rotation in (90, 270):
        width, height = height, width
    return {
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "fps": round(_fraction((video or {}).get("avg_frame_rate")) or _fraction((video or {}).get("r_frame_rate")), 3),
        "has_video": bool(video),
        "has_audio": bool(audio),
        "vcodec": (video or {}).get("codec_name"),
        "acodec": (audio or {}).get("codec_name"),
        "audio_channels": int((audio or {}).get("channels") or 0),
        "sample_rate": int((audio or {}).get("sample_rate") or 0),
        "audio_bitrate": int((audio or {}).get("bit_rate") or 0),
        "bitrate": int(fmt.get("bit_rate") or 0),
        "size": int(fmt.get("size") or 0),
        "container": fmt.get("format_name", ""),
    }


def ffmpeg_with_progress(
    cmd: list[str],
    duration: float | None,
    progress_cb: ProgressCb | None = None,
    cwd: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    stage: str = "rendering",
) -> str:
    """Run ffmpeg, parsing -progress pipe:1 for reliable percentage reporting."""
    full = [cmd[0], "-hide_banner", "-nostdin", "-progress", "pipe:1", "-nostats", *cmd[1:]]
    if os.environ.get("CLIPFORGE_DEBUG"):
        print("[ffmpeg] " + " ".join(full), flush=True)
    # Lower the render's scheduling priority so the rest of the Mac (and the
    # UI you are clicking in) stays responsive while a long export runs.
    nice = shutil.which("nice")
    launch = [nice, "-n", "10", *full] if nice and os.name == "posix" else full
    proc = subprocess.Popen(
        launch, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd,
        bufsize=1, universal_newlines=True,
    )
    stderr_chunks: list[str] = []

    def drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_chunks.append(line)
            if len(stderr_chunks) > 400:
                del stderr_chunks[:200]

    reader = threading.Thread(target=drain_stderr, daemon=True)
    reader.start()

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if should_cancel and should_cancel():
            proc.kill()
            raise FFmpegError("canceled")
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                us = int(line.split("=", 1)[1])
            except ValueError:
                continue
            if duration and duration > 0 and progress_cb:
                pct = max(0.0, min(0.999, (us / 1_000_000) / duration))
                progress_cb(pct, stage)
        elif line.startswith("progress=end") and progress_cb:
            progress_cb(1.0, stage)
    proc.wait()
    reader.join(timeout=5)
    err = "".join(stderr_chunks)
    if proc.returncode != 0:
        tail = "\n".join([ln for ln in err.strip().splitlines() if ln.strip()][-12:])
        raise FFmpegError(f"ffmpeg failed (exit {proc.returncode}): {tail[:2000]}")
    return err


def extract_audio(src: str | Path, dst: str | Path, sample_rate: int = 16000) -> Path:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    res = run([
        FFMPEG, "-hide_banner", "-nostdin", "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst),
    ], timeout=3600)
    if res.returncode != 0 or not dst.exists():
        raise FFmpegError(f"audio extraction failed: {res.stderr.strip()[-800:]}")
    return dst


def decode_pcm(path: str | Path, sample_rate: int = 8000) -> bytes:
    """Decode any media to mono s16le PCM bytes (streamed, never loads video)."""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
        capture_output=True, timeout=7200,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"pcm decode failed: {proc.stderr.decode('utf-8', 'ignore')[-500:]}")
    return proc.stdout


def thumbnail(src: str | Path, t: float, dst: str | Path, width: int = 480) -> Path:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    res = run([
        FFMPEG, "-hide_banner", "-nostdin", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(src),
        "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(dst),
    ], timeout=120)
    if res.returncode != 0 or not dst.exists():
        raise FFmpegError(f"thumbnail failed: {res.stderr.strip()[-400:]}")
    return dst


def frame_array(src: str | Path, t: float, width: int = 640) -> bytes:
    """Raw RGB24 frame at timestamp t (used for face detection)."""
    res = run_bytes([
        FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-ss", f"{max(0.0, t):.3f}",
        "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ], timeout=120)
    return res.stdout or b""


# ---------------------------------------------------------------------------
# Encoding profiles
# ---------------------------------------------------------------------------

def encoder_for(fmt: str, prefer_hw: bool = True, preset: str = "medium") -> tuple[list[str], str]:
    """Return (quality args, encoder name) for the requested output format."""
    caps = ffmpeg_capabilities()
    use_vt = prefer_hw and caps.get("videotoolbox")
    if fmt == "mp4_hevc":
        if use_vt and caps["encoders"].get("hevc_videotoolbox"):
            return ["-c:v", "hevc_videotoolbox", "-b:v", "{bitrate}", "-allow_sw", "1",
                    "-tag:v", "hvc1", "-profile:v", "main"], "hevc_videotoolbox"
        return ["-c:v", "libx265", "-preset", preset, "-crf", "{crf}", "-tag:v", "hvc1"], "libx265"
    if use_vt and caps["encoders"].get("h264_videotoolbox"):
        # No -level here: a 4K frame (e.g. 2160x3840 vertical) exceeds level 4.2
        # and VideoToolbox refuses to open the encoder with "Cannot prepare
        # encoder: -12902" at the very end of a render.
        return ["-c:v", "h264_videotoolbox", "-b:v", "{bitrate}", "-allow_sw", "1",
                "-profile:v", "high"], "h264_videotoolbox"
    return ["-c:v", "libx264", "-preset", preset, "-crf", "{crf}", "-profile:v", "high"], "libx264"


def quality_numbers(resolution: int, quality: str) -> tuple[str, str]:
    """Target bitrate (for hardware encoders) and CRF (for software encoders)."""
    base = {720: 4_500_000, 1080: 9_000_000, 1440: 16_000_000, 2160: 32_000_000}.get(resolution, 8_000_000)
    factor = {"compact": 0.6, "balanced": 1.0, "high": 1.35}.get(quality, 1.0)
    crf = {"compact": 25, "balanced": 21, "high": 18}.get(quality, 20)
    return str(int(base * factor)), str(crf)


def audio_args(settings: dict, has_audio: bool, source_sample_rate: int = 48000) -> list[str]:
    if not has_audio:
        return ["-an"]
    mode = settings.get("audio_mode", "high")
    bitrate = int(settings.get("audio_bitrate") or 192)
    if mode == "original":
        rate = source_sample_rate if source_sample_rate in {44100, 48000, 96000, 32000, 22050} else 48000
        return ["-c:a", "aac", "-b:a", f"{max(128, bitrate)}k", "-ar", str(rate), "-ac", "2"]
    if mode == "custom":
        rate = 48000
        return ["-c:a", "aac", "-b:a", f"{bitrate}k", "-ar", str(rate), "-ac", "2"]
    return ["-c:a", "aac", "-b:a", f"{max(256, bitrate)}k", "-ar", "48000", "-ac", "2"]


def audio_filter(settings: dict) -> str | None:
    parts: list[str] = []
    if settings.get("normalize_audio", True):
        target = float(settings.get("loudness_target") or -14)
        parts.append(f"loudnorm=I={target}:TP=-1.5:LRA=11")
    parts.append("aresample=async=1:first_pts=0")
    return ",".join(parts) if parts else None


def even(n: float) -> int:
    return int(max(2, round(n / 2) * 2))


def canvas_size(resolution: int, aspect: str) -> tuple[int, int]:
    """Short-side based canvas sizing for the requested aspect ratio."""
    s = {720: 720, 1080: 1080, 1440: 1440, 2160: 2160}.get(int(resolution), 1080)
    if aspect == "16:9":
        return even(s * 16 / 9), even(s)
    if aspect == "9:16":
        return even(s), even(s * 16 / 9)
    if aspect == "1:1":
        return even(s), even(s)
    if aspect == "4:5":
        return even(s), even(s * 5 / 4)
    return even(s * 16 / 9), even(s)


def effective_resolution(requested: int, width: int, height: int) -> int:
    """Never upscale: cap the requested tier to what the source can actually resolve."""
    if not width or not height:
        return int(requested)
    short = min(int(width), int(height))
    tier = 720
    for candidate in (2160, 1440, 1080, 720):
        if short >= candidate * 0.95:
            tier = candidate
            break
    return min(int(requested), tier)


def aspect_for_layout(layout: str) -> str:
    return {
        "vertical": "9:16",
        "horizontal": "16:9",
        "square": "1:1",
        "portrait": "4:5",
        "facecam_top": "9:16",
        "facecam_bottom": "9:16",
        "facecam": "9:16",
        "gameplay": "16:9",
        "auto": "9:16",
    }.get(layout, "9:16")


def escape_filter_path(path: str) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'").replace("[", "\\[").replace("]", "\\]")


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def hex_to_ass(color: str, alpha: int = 0) -> str:
    """#RRGGBB -> &HAABBGGRR (ASS is BGR with 00=opaque)."""
    c = (color or "#FFFFFF").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    while len(c) < 6:
        c += "0"
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        r = g = b = 255
    a = int(clamp(alpha, 0, 255))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def probe_duration(path: str | Path) -> float:
    try:
        return probe_media(path)["duration"]
    except Exception:
        return 0.0


def keyframe_positions(path: str | Path) -> list[float]:
    res = run([
        FFPROBE, "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
        "-show_entries", "frame=pts_time", "-of", "csv=p=0", str(path),
    ], timeout=600)
    times = []
    for line in res.stdout.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            times.append(float(line))
        except ValueError:
            continue
    return times


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _.-]+", "", name or "clip").strip().replace(" ", "_")
    return cleaned[:60] or "clip"


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"


def ceil_even(n: float) -> int:
    return int(math.ceil(n / 2) * 2)
