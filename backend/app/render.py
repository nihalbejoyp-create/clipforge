"""Clip rendering: layout composition, subtitle burn-in, audio muxing, encoding."""
from __future__ import annotations

import os
from pathlib import Path

from . import faces, subtitles
from .clip_settings import merge_clip_settings
from .config import FFMPEG, FONTS_DIR
from .media import (
    FFmpegError, aspect_for_layout, audio_args, audio_filter, canvas_size, effective_resolution,
    encoder_for, even, ffmpeg_with_progress, probe_media, quality_numbers, thumbnail,
)

FACECAM_LAYOUTS = {"facecam_top", "facecam_bottom", "facecam"}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _cover_dims(cw: int, ch: int, sw: int, sh: int) -> tuple[int, int]:
    factor = max(cw / max(sw, 1), ch / max(sh, 1))
    return even(sw * factor), even(sh * factor)


def _cover_chain(cw: int, ch: int, sw: int, sh: int, cx: float | None = None, cy: float | None = None) -> str:
    w, h = _cover_dims(cw, ch, sw, sh)
    x = int(round(((cx if cx is not None else 0.5) * w) - cw / 2))
    y = int(round(((cy if cy is not None else 0.5) * h) - ch / 2))
    x = int(_clamp(x, 0, max(0, w - cw)))
    y = int(_clamp(y, 0, max(0, h - ch)))
    x -= x % 2
    y -= y % 2
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={cw}:{ch}:{x}:{y},setsar=1"


def _blurred_background(cw: int, ch: int, sw: int, sh: int, sigma: float = 42) -> str:
    """Cover-crop, then blur at 1/4 scale and scale back up.

    gblur is O(pixels), so blurring a 2160x3840 frame directly cost most of the
    render time. A background that is about to be blurred to mush looks
    identical when it is downscaled first — and 16x cheaper.
    """
    bw = even(cw / 4)
    bh = even(ch / 4)
    # sigma is given in full-frame pixels and divided with the scale, so callers
    # can reuse a background's sigma regardless of the downscale.
    return (f"{_cover_chain(cw, ch, sw, sh)},scale={bw}:{bh},gblur=sigma={sigma / 4:g},"
            f"scale={cw}:{ch}:flags=bicubic")


def _framing_mode(settings: dict) -> str:
    reframe = settings.get("reframe", "fit_blur")
    if settings.get("layout") == "auto":
        return "crop"
    return reframe


def build_video_graph(
    src_w: int,
    src_h: int,
    cw: int,
    ch: int,
    settings: dict,
    face_center: tuple[float, float] | None = None,
    ass_expr: str | None = None,
) -> tuple[str, str]:
    """Return (filter_complex, output_label) for the requested layout."""
    layout = settings.get("layout", "vertical")
    mode = _framing_mode(settings)
    cx = face_center[0] if face_center else None
    cy = face_center[1] if face_center else None
    graph: list[str] = []

    if layout in FACECAM_LAYOUTS:
        region = settings.get("facecam_region") or {"x": 0.0, "y": 0.0, "w": 0.4, "h": 0.4}
        rx, ry = int(round(region["x"] * src_w)), int(round(region["y"] * src_h))
        rw = even(min(src_w - rx, region["w"] * src_w))
        rh = even(min(src_h - ry, region["h"] * src_h))
        crop_fc = f"crop={rw}:{rh}:{rx}:{ry},setsar=1"

        if layout == "facecam":
            graph.append(f"[0:v]fps=30,{crop_fc},{_cover_chain(cw, ch, rw, rh)}[base]")
        else:
            cam_h = even(ch * 0.42)
            gp_h = even(ch - cam_h)
            graph.append(
                f"[0:v]fps=30,split=3[fc_src][gp_src][gp_bg];"
                f"[fc_src]{crop_fc},scale={cw}:{cam_h}:force_original_aspect_ratio=increase,"
                f"crop={cw}:{cam_h},setsar=1[fc]"
            )
            # Same 1/4-scale blur as the fit-blur layout, then dimmed.
            graph.append(f"[gp_bg]{_blurred_background(cw, gp_h, src_w, src_h, 32)},"
                         "eq=brightness=-0.08:saturation=0.85[gbbg]")
            graph.append(f"[gp_src]scale={cw}:{gp_h}:force_original_aspect_ratio=decrease[gpfit]")
            graph.append("[gbbg][gpfit]overlay=(W-w)/2:(H-h)/2[gp]")
            order = "[fc][gp]" if layout == "facecam_top" else "[gp][fc]"
            graph.append(f"{order}vstack=inputs=2[base]")
    elif mode == "crop":
        graph.append(f"[0:v]fps=30,{_cover_chain(cw, ch, src_w, src_h, cx, cy)}[base]")
    else:
        graph.append(
            f"[0:v]fps=30,split=2[bg][fg];"
            f"[bg]{_blurred_background(cw, ch, src_w, src_h, 42)},eq=brightness=-0.12:saturation=0.8[bgblur];"
            f"[fg]scale={cw}:{ch}:force_original_aspect_ratio=decrease,setsar=1[fgfit];"
            f"[bgblur][fgfit]overlay=(W-w)/2:(H-h)/2[base]"
        )

    graph.append(f"[base]format=yuv420p,{ass_expr}[vout]" if ass_expr else "[base]format=yuv420p[vout]")
    return ";".join(graph), "[vout]"


def render_clip(
    source: str | Path,
    start: float,
    end: float,
    settings: dict,
    out_path: str | Path,
    words: list[dict] | None = None,
    progress_cb=None,
    should_cancel=None,
    info: dict | None = None,
    workdir: str | Path | None = None,
) -> Path:
    """Render a single clip to out_path. Raises FFmpegError on failure."""
    source = Path(source)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(workdir or out_path.parent)
    workdir.mkdir(parents=True, exist_ok=True)
    info = info or probe_media(source)
    sw, sh = info["width"], info["height"]
    if not sw or not sh:
        raise FFmpegError("source has no video stream")

    layout = settings.get("layout", "vertical")
    aspect = aspect_for_layout(layout)
    render_res = effective_resolution(int(settings.get("resolution", 1080)), sw, sh)
    cw, ch = canvas_size(render_res, aspect)
    duration = max(0.5, float(end) - float(start))

    # Face-aware framing: only needed for crop-style reframes.
    face_center = None
    if _framing_mode(settings) == "crop" and (layout in {"auto", "vertical", "square", "portrait", "horizontal"}):
        sample = [start + duration * f for f in (0.2, 0.5, 0.8)]
        face_center = faces.face_center(source, sample)

    ass_path = None
    ass_expr = None
    if settings.get("subtitle", {}).get("enabled", True) and words:
        ass_path = subtitles.build_ass(words, start, end, cw, ch, settings["subtitle"], workdir / f"{out_path.stem}.ass")
        if ass_path:
            ass_expr = subtitles.ass_filter(ass_path)

    graph, vlabel = build_video_graph(sw, sh, cw, ch, settings, face_center, ass_expr)

    fmt = settings.get("format", "mp4")
    bitrate, crf = quality_numbers(render_res, settings.get("quality", "high"))
    # libx264/libx265 "medium" at 4K is several times slower than "veryfast" for
    # a difference nobody sees after a social re-encode; CRF still guards quality.
    preset = "veryfast" if render_res >= 1440 else "medium"
    vargs, vname = encoder_for(fmt, prefer_hw=settings.get("encoder", "auto") != "software", preset=preset)
    vargs = [a.replace("{bitrate}", bitrate).replace("{crf}", crf) for a in vargs]

    info_audio = bool(info.get("has_audio"))
    aargs = audio_args(settings, info_audio, int(info.get("sample_rate") or 48000))
    afilter = audio_filter(settings) if info_audio else None

    def build(venc: list[str]) -> list[str]:
        cmd = [FFMPEG, "-y", "-ss", f"{max(0.0, float(start)):.3f}", "-t", f"{duration:.3f}",
               "-i", str(source), "-filter_complex", graph, "-map", vlabel]
        if info_audio:
            cmd += ["-map", "0:a:0"]
            if afilter:
                cmd += ["-af", afilter]
            cmd += aargs
        else:
            cmd += ["-an"]
        cmd += venc
        if fmt == "mov":
            cmd += ["-f", "mov", "-movflags", "+faststart"]
        else:
            cmd += ["-movflags", "+faststart"]
        cmd.append(str(out_path))
        return cmd

    try:
        ffmpeg_with_progress(build(vargs), duration, progress_cb=progress_cb, cwd=str(workdir),
                             should_cancel=should_cancel, stage=f"rendering {out_path.name}")
    except FFmpegError:
        if should_cancel and should_cancel():
            raise
        if vname.startswith("lib"):
            raise
        # A hardware encoder can refuse a frame size or level on a given Mac.
        # Retry the same clip in software instead of failing the render.
        sw_args, _sw_name = encoder_for(fmt, prefer_hw=False, preset=preset)
        sw_args = [a.replace("{bitrate}", bitrate).replace("{crf}", crf) for a in sw_args]
        if progress_cb:
            progress_cb(0.0, "hardware encoder failed — retrying in software")
        ffmpeg_with_progress(build(sw_args), duration, progress_cb=progress_cb, cwd=str(workdir),
                             should_cancel=should_cancel, stage=f"rendering {out_path.name} (software)")
    if not out_path.exists() or out_path.stat().st_size < 1024:
        raise FFmpegError(f"render produced no output ({out_path.name})")
    if ass_path:
        try:
            ass_path.unlink(missing_ok=True)
        except OSError:
            pass
    return out_path


def make_thumb(source: str | Path, t: float, out_path: str | Path, duration: float = 0) -> Path | None:
    try:
        return thumbnail(source, t, out_path)
    except FFmpegError:
        return None


def render_subtitle_preview(settings: dict, out_path: str | Path) -> Path:
    """Short rendered preview of the subtitle style on a neutral background."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sub = settings.get("subtitle", {})
    layout = settings.get("layout", "vertical")
    cw, ch = canvas_size(int(settings.get("resolution", 1080)), aspect_for_layout(layout))
    preview_cw, preview_ch = min(cw, 540), min(ch, 960)
    words = subtitles.sample_words()
    ass = subtitles.build_ass(words, 0.0, 5.0, preview_cw, preview_ch, sub, out_path.with_suffix(".ass"))
    expr = subtitles.ass_filter(ass) if ass else None
    graph = "[0:v]format=yuv420p[bg]"
    if expr:
        graph += f";[bg]{expr}[vout]"
    else:
        graph += ";[bg]null[vout]"
    cmd = [
        FFMPEG, "-y", "-f", "lavfi", "-i",
        f"color=c=0x12151d:s={preview_cw}x{preview_ch}:d=5:r=30",
        "-filter_complex", graph, "-map", "[vout]",
    ]
    if _has_vt():
        cmd += ["-c:v", "h264_videotoolbox", "-b:v", "3M", "-allow_sw", "1"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
    ffmpeg_with_progress(cmd, 5.0, cwd=str(out_path.parent))
    if ass:
        ass.unlink(missing_ok=True)
    return out_path


def _has_vt() -> bool:
    from .config import ffmpeg_capabilities

    return bool(ffmpeg_capabilities().get("encoders", {}).get("h264_videotoolbox"))
