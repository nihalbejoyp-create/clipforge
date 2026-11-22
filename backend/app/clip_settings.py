"""Clip settings defaults, validation and merge rules."""
from __future__ import annotations

from typing import Any

CLIP_COUNT_CHOICES = [1, 2, 5, 10, 20, 50, 100]
DURATION_CHOICES = [15, 30, 45, 60, 90, 120]
RESOLUTION_CHOICES = [720, 1080, 1440, 2160]
FORMATS = ["mp4", "mp4_hevc", "mov"]
LAYOUTS = [
    "vertical", "horizontal", "square", "portrait",
    "facecam_top", "facecam_bottom", "facecam", "gameplay", "auto",
]
LAYOUT_LABELS = {
    "vertical": "9:16 Vertical",
    "horizontal": "16:9 Horizontal",
    "square": "1:1 Square",
    "portrait": "4:5 Portrait",
    "facecam_top": "Facecam top / gameplay below",
    "facecam_bottom": "Facecam bottom / gameplay above",
    "facecam": "Facecam only",
    "gameplay": "Gameplay only",
    "auto": "Automatic smart reframe",
}
SUBTITLE_PRESETS = ["bold_pop", "karaoke", "boxed", "minimal", "neon", "clean_line"]
SUBTITLE_FONTS = [
    "Inter", "Arial", "Roboto", "Montserrat", "Poppins", "Bebas Neue", "Anton",
    "Oswald", "League Spartan", "Impact", "Open Sans", "Lato", "Archivo Black",
    "Barlow Condensed", "DM Sans", "Playfair Display",
]

DEFAULT_SUBTITLE: dict[str, Any] = {
    "enabled": True,
    "preset": "bold_pop",
    "font": "Inter",
    "font_size": 1.0,
    "weight": 800,
    "position": "bottom",          # top | middle | bottom
    "offset": 0.0,                 # -1..1 nudge of margin
    "uppercase": True,
    "primary_color": "#FFFFFF",
    "highlight_color": "#FFD400",
    "outline_color": "#000000",
    "outline_width": 4.0,
    "shadow": 2.0,
    "background": False,
    "background_color": "#000000",
    "background_opacity": 0.55,
    "animation": "word_pop",       # word_pop | karaoke | fade | none
    "max_words": 4,
    "emoji": False,
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "clip_count": 5,
    "clip_duration": 30,
    "custom_duration": None,
    "resolution": 1080,
    "audio_mode": "high",          # original | high | custom
    "audio_bitrate": 192,
    "format": "mp4",
    "layout": "vertical",
    "reframe": "fit_blur",         # fit_blur | crop
    "facecam_region": None,        # {"x":0..1,"y":0..1,"w":0..1,"h":0..1}
    "quality": "high",
    "highlight_sensitivity": 50,
    "allow_overlap": False,
    "whisper_model": "base",
    "language": "auto",
    "normalize_audio": True,
    "loudness_target": -14,
    "encoder": "auto",
    "subtitle": DEFAULT_SUBTITLE,
    # live-only tuning
    "live_pre_roll": 0.55,         # share of clip duration captured before the peak
    "live_buffer_seconds": 240,
    "live_unlimited": False,
    "live_sensitivity": 55,
    "live_analysis_window": 30,
}


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def normalise_settings(raw: dict | None) -> dict:
    """Merge user input over defaults, clamp everything to a valid range."""
    data = dict(DEFAULT_SETTINGS)
    raw = raw or {}
    subtitle_raw = raw.get("subtitle") or {}
    subtitle = dict(DEFAULT_SUBTITLE)
    if isinstance(subtitle_raw, dict):
        subtitle.update({k: v for k, v in subtitle_raw.items() if k in DEFAULT_SUBTITLE})

    for key, value in raw.items():
        if key in DEFAULT_SETTINGS and key != "subtitle":
            data[key] = value
    data["subtitle"] = subtitle

    count = int(_num(data.get("clip_count"), 5, 1, 100))
    data["clip_count"] = count
    if data.get("custom_duration"):
        data["clip_duration"] = int(_num(data["custom_duration"], 30, 5, 600))
        data["custom_duration"] = data["clip_duration"]
    else:
        data["clip_duration"] = int(_num(data.get("clip_duration"), 30, 5, 600))
    data["resolution"] = int(_num(data.get("resolution"), 1080, 360, 2160))
    data["audio_bitrate"] = int(_num(data.get("audio_bitrate"), 192, 64, 512))
    if data.get("audio_mode") not in {"original", "high", "custom"}:
        data["audio_mode"] = "high"
    if data.get("format") not in FORMATS:
        data["format"] = "mp4"
    if data.get("layout") not in LAYOUTS:
        data["layout"] = "vertical"
    if data.get("reframe") not in {"fit_blur", "crop"}:
        data["reframe"] = "fit_blur"
    if data.get("quality") not in {"compact", "balanced", "high"}:
        data["quality"] = "high"
    if data.get("encoder") not in {"auto", "videotoolbox", "software"}:
        data["encoder"] = "auto"
    data["highlight_sensitivity"] = int(_num(data.get("highlight_sensitivity"), 50, 0, 100))
    data["live_sensitivity"] = int(_num(data.get("live_sensitivity"), 55, 0, 100))
    data["live_buffer_seconds"] = int(_num(data.get("live_buffer_seconds"), 240, 60, 3600))
    data["live_analysis_window"] = int(_num(data.get("live_analysis_window"), 30, 10, 120))
    data["live_pre_roll"] = float(_num(data.get("live_pre_roll"), 0.55, 0.2, 0.9))
    data["allow_overlap"] = bool(data.get("allow_overlap"))
    data["live_unlimited"] = bool(data.get("live_unlimited"))
    data["normalize_audio"] = bool(data.get("normalize_audio", True))
    data["loudness_target"] = float(_num(data.get("loudness_target"), -14, -30, -6))
    if data.get("whisper_model") not in {"tiny", "base", "small", "medium"}:
        data["whisper_model"] = "base"
    if data.get("language") in (None, "", "auto"):
        data["language"] = "auto"
    region = data.get("facecam_region")
    if isinstance(region, dict):
        clean = {
            "x": float(_num(region.get("x"), 0, 0, 0.9)),
            "y": float(_num(region.get("y"), 0, 0, 0.9)),
            "w": float(_num(region.get("w"), 0.3, 0.1, 1.0)),
            "h": float(_num(region.get("h"), 0.3, 0.1, 1.0)),
        }
        clean["w"] = min(clean["w"], 1.0 - clean["x"])
        clean["h"] = min(clean["h"], 1.0 - clean["y"])
        data["facecam_region"] = clean
    else:
        data["facecam_region"] = None

    sub = data["subtitle"]
    if sub.get("preset") not in SUBTITLE_PRESETS:
        sub["preset"] = "bold_pop"
    if sub.get("font") not in SUBTITLE_FONTS:
        sub["font"] = "Inter"
    if sub.get("position") not in {"top", "middle", "bottom"}:
        sub["position"] = "bottom"
    if sub.get("animation") not in {"word_pop", "karaoke", "fade", "none"}:
        sub["animation"] = "word_pop"
    sub["enabled"] = bool(sub.get("enabled", True))
    sub["uppercase"] = bool(sub.get("uppercase", True))
    sub["background"] = bool(sub.get("background", False))
    sub["emoji"] = bool(sub.get("emoji", False))
    sub["font_size"] = float(_num(sub.get("font_size"), 1.0, 0.5, 2.0))
    sub["weight"] = int(_num(sub.get("weight"), 800, 300, 900))
    sub["outline_width"] = float(_num(sub.get("outline_width"), 4.0, 0, 12))
    sub["shadow"] = float(_num(sub.get("shadow"), 2.0, 0, 12))
    sub["offset"] = float(_num(sub.get("offset"), 0.0, -1.0, 1.0))
    sub["max_words"] = int(_num(sub.get("max_words"), 4, 1, 10))
    sub["background_opacity"] = float(_num(sub.get("background_opacity"), 0.55, 0, 1))
    return data


def merge_clip_settings(project_settings: dict, clip_settings: dict | None) -> dict:
    merged = normalise_settings(project_settings or {})
    if not clip_settings:
        return merged
    override = dict(clip_settings)
    sub_override = override.pop("subtitle", None)
    for key, value in override.items():
        if key in merged and key != "subtitle":
            merged[key] = value
    if isinstance(sub_override, dict):
        merged["subtitle"] = {**merged["subtitle"], **sub_override}
    return normalise_settings(merged)
