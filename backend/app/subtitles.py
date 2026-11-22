"""ASS subtitle generation with word-level highlighting and style presets."""
from __future__ import annotations

import math
from pathlib import Path

from .config import FONTS_DIR
from .media import hex_to_ass

EMOJI_MAP = {
    "laugh": "😂", "laughing": "😂", "funny": "😂", "hilarious": "🤣", "joke": "😆",
    "money": "💰", "dollars": "💰", "rich": "💰", "expensive": "💸", "profit": "📈",
    "win": "🏆", "won": "🏆", "victory": "🏆", "champion": "🏆",
    "fire": "🔥", "insane": "🤯", "crazy": "🤯", "unbelievable": "😲", "wow": "😮",
    "love": "❤️", "best": "⭐", "secret": "🤫", "warning": "⚠️", "stop": "🛑",
    "fast": "⚡", "speed": "⚡", "dead": "💀", "died": "💀", "scared": "😱",
}

PRESET_STYLE = {
    "bold_pop": dict(uppercase=True, background=False, outline=1.0, shadow=0.35, box_alpha=0),
    "karaoke": dict(uppercase=True, background=False, outline=1.0, shadow=0.3, box_alpha=0),
    "boxed": dict(uppercase=False, background=True, outline=0.0, shadow=0.0, box_alpha=1.0),
    "minimal": dict(uppercase=False, background=False, outline=0.45, shadow=0.0, box_alpha=0),
    "neon": dict(uppercase=True, background=False, outline=1.2, shadow=0.8, box_alpha=0),
    "clean_line": dict(uppercase=False, background=False, outline=0.55, shadow=0.15, box_alpha=0),
}

SENTENCE_END = {".", "!", "?", "…"}
SOFT_BREAK = {",", ";", ":", "—"}


def _ts(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - math.floor(t)) * 100))
    if cs == 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
            .replace("\r", " ").replace("\n", "\\N"))


def _chunks(words: list[dict], max_words: int, max_dur: float = 3.4) -> list[list[dict]]:
    """Group words into caption lines that break at punctuation / length limits."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        cur.append(w)
        text = w["word"].strip()
        too_long = len(cur) >= max_words
        too_slow = (cur[-1]["end"] - cur[0]["start"]) >= max_dur
        hard_break = text.endswith(tuple(SENTENCE_END))
        soft_break = text.endswith(tuple(SOFT_BREAK)) and len(cur) >= max(2, max_words - 1)
        if too_long or too_slow or hard_break or soft_break:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _emoji_for(text: str) -> str:
    low = text.lower()
    for key, emo in EMOJI_MAP.items():
        if key in low:
            return emo
    return ""


def build_ass(
    words: list[dict],
    clip_start: float,
    clip_end: float,
    width: int,
    height: int,
    subtitle: dict,
    out_path: str | Path,
) -> Path | None:
    """Write an .ass file for one clip. Returns None when there is nothing to show."""
    sub = subtitle or {}
    if not sub.get("enabled", True):
        return None
    local = []
    for w in words or []:
        start, end = float(w.get("start", 0)), float(w.get("end", 0))
        if end < clip_start - 0.1 or start > clip_end + 0.1:
            continue
        text = (w.get("word") or "").strip()
        if not text:
            continue
        local.append({
            "start": max(0.0, start - clip_start),
            "end": max(0.05, end - clip_start),
            "word": text,
        })
    if not local:
        return None

    preset = PRESET_STYLE.get(sub.get("preset", "bold_pop"), PRESET_STYLE["bold_pop"])
    uppercase = bool(sub.get("uppercase", preset["uppercase"]))
    use_box = bool(sub.get("background", preset["background"]))
    base_size = height * 0.052 * float(sub.get("font_size", 1.0))
    margin_v = int(height * 0.085) + int(float(sub.get("offset", 0)) * height * 0.22)
    margin_v = max(24, margin_v)
    alignment = {"bottom": 2, "middle": 5, "top": 8}.get(sub.get("position", "bottom"), 2)
    if alignment == 2:
        margin_v = max(24, margin_v)
    elif alignment == 8:
        margin_v = max(24, margin_v)
    else:
        margin_v = 0

    outline = float(sub.get("outline_width", 4.0)) * (base_size / 70.0) * preset["outline"]
    shadow = float(sub.get("shadow", 2.0)) * (base_size / 70.0) * max(0.05, preset["shadow"])
    outline = round(max(0.0, outline), 2)
    shadow = round(max(0.0, shadow), 2)

    primary = hex_to_ass(sub.get("primary_color", "#FFFFFF"))
    highlight = hex_to_ass(sub.get("highlight_color", "#FFD400"))
    outline_col = hex_to_ass(sub.get("outline_color", "#000000"))
    box_alpha = int((1.0 - float(sub.get("background_opacity", 0.55))) * 255) if use_box else 0
    back = hex_to_ass(sub.get("background_color", "#000000"), box_alpha)
    font = sub.get("font", "Inter")
    bold = -1 if int(sub.get("weight", 800)) >= 600 else 0
    border_style = 3 if use_box else 1
    max_words = int(sub.get("max_words", 4))
    animation = sub.get("animation", "word_pop")
    if preset is PRESET_STYLE["karaoke"] and animation == "word_pop":
        animation = "karaoke"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{base_size:.0f},{primary},{highlight},{outline_col},{back},{bold},0,0,0,100,100,0.6,0,{border_style},{outline},{shadow},{alignment},{int(width*0.06)},{int(width*0.06)},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    for chunk in _chunks(local, max_words):
        if not chunk:
            continue
        start = chunk[0]["start"]
        end = max(chunk[-1]["end"], start + 0.25) + 0.08
        texts = [(w["word"] if not uppercase else w["word"].upper()) for w in chunk]
        if animation == "karaoke":
            parts = []
            for i, w in enumerate(chunk):
                dur_cs = max(1, int(round((w["end"] - w["start"]) * 100)))
                parts.append(f"{{\\kf{dur_cs}}}{_esc(texts[i])}")
            body = " ".join(parts)
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{body}")
            continue
        if animation == "none":
            body = " ".join(_esc(t) for t in texts)
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{body}")
            continue
        if animation == "fade":
            body = f"{{\\fad(90,90)}}" + " ".join(_esc(t) for t in texts)
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{body}")
            continue
        # word_pop: one event per word, active word recoloured
        for i, w in enumerate(chunk):
            ev_start = w["start"]
            ev_end = max(w["end"], ev_start + 0.10)
            if i + 1 < len(chunk):
                ev_end = max(ev_end, min(chunk[i + 1]["start"] + 0.02, ev_end + 0.35))
            built = []
            for j, t in enumerate(texts):
                if j == i:
                    built.append(
                        f"{{\\c{highlight}\\fscx108\\fscy108\\t(0,90,\\fscx100\\fscy100)}}{_esc(t)}{{\\c{primary}}}"
                    )
                else:
                    built.append(_esc(t))
            line = " ".join(built)
            if i == 0:
                line = "{\\fad(70,0)}" + line
            events.append(f"Dialogue: 0,{_ts(ev_start)},{_ts(ev_end)},Default,,0,0,0,,{line}")
        if sub.get("emoji"):
            emo = _emoji_for(" ".join(w["word"] for w in chunk))
            if emo:
                events.append(
                    f"Dialogue: 1,{_ts(start)},{_ts(end)},Default,,0,0,0,,"
                    f"{{\\fs{int(base_size*1.5)}\\an8\\pos({int(width*0.86)},{int(height*0.12)})}}{emo}"
                )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return out_path


def ass_filter(path: Path) -> str:
    """ffmpeg filter expression for burning an .ass file with our bundled fonts."""
    import os

    name = os.path.basename(str(path))
    fonts = str(FONTS_DIR).replace("\\", "\\\\").replace(":", "\\:")
    safe = name.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    return f"ass={safe}:fontsdir='{fonts}'"


def sample_words() -> list[dict]:
    text = [
        ("This", 0.0, 0.28), ("is", 0.28, 0.48), ("the", 0.48, 0.68), ("moment", 0.68, 1.12),
        ("everything", 1.12, 1.62), ("changed.", 1.62, 2.10),
        ("Nobody", 2.30, 2.72), ("saw", 2.72, 2.92), ("it", 2.92, 3.08), ("coming.", 3.08, 3.55),
        ("Watch", 3.80, 4.05), ("this", 4.05, 4.25), ("again.", 4.25, 4.70),
    ]
    return [{"word": w, "start": s, "end": e} for w, s, e in text]
