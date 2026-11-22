"""Highlight detection: audio energy + transcript heuristics + ranking/dedupe."""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

HOOK_WORDS = {
    # stakes / curiosity
    "never", "always", "secret", "nobody", "everyone", "truth", "actually", "literally",
    "insane", "crazy", "unbelievable", "impossible", "biggest", "worst", "best", "first",
    "last", "only", "million", "billion", "thousand", "money", "rich", "broke", "free",
    "warning", "mistake", "problem", "wrong", "hate", "love", "died", "death", "kill",
    "fight", "win", "won", "lost", "lose", "fail", "failed", "success", "changed",
    "changed", "stop", "start", "wait", "look", "watch", "listen", "remember",
    "important", "huge", "terrible", "amazing", "incredible", "shocking", "exposed",
    "cheat", "hack", "trick", "learned", "lesson", "story", "happened", "because",
    "why", "how", "what", "who", "when", "where", "here", "now", "today",
    "laugh", "funny", "hilarious", "joke", "seriously", "honestly", "trust",
}

REACTION_MARKERS = {"oh", "wow", "whoa", "no", "yes", "what", "bro", "dude", "omg",
                    "holy", "damn", "shit", "fuck", "yooo", "lets", "go"}

QUESTION = re.compile(r"[?]")


# ---------------------------------------------------------------------------
# Audio energy
# ---------------------------------------------------------------------------

def energy_profile(path: str | Path, block: float = 0.5, sample_rate: int = 8000) -> tuple[np.ndarray, float]:
    """Per-block loudness in dBFS. Decodes audio only, never the video frames."""
    from .media import decode_pcm

    try:
        pcm = decode_pcm(path, sample_rate)
    except Exception:
        return np.zeros(1, dtype=np.float32), block
    if not pcm:
        return np.zeros(1, dtype=np.float32), block
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    step = max(1, int(sample_rate * block))
    n = len(samples) // step
    if n == 0:
        return np.zeros(1, dtype=np.float32), block
    trimmed = samples[: n * step].reshape(n, step)
    rms = np.sqrt(np.mean(np.square(trimmed), axis=1) + 1e-12)
    db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    return db.astype(np.float32), block


def _slice(db: np.ndarray, block: float, start: float, end: float) -> np.ndarray:
    a = max(0, int(start / block))
    b = min(len(db), max(a + 1, int(math.ceil(end / block))))
    seg = db[a:b]
    return seg if seg.size else np.zeros(1, dtype=np.float32)


def _rank(values: list[float]) -> list[float]:
    """Percentile rank 0..1 (ties get averaged rank)."""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 / (n - 1)
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


# ---------------------------------------------------------------------------
# Transcript structure
# ---------------------------------------------------------------------------

def build_sentences(words: list[dict], max_len: float = 18.0) -> list[dict]:
    units: list[dict] = []
    cur: list[dict] = []
    for w in words:
        cur.append(w)
        text = w.get("word", "")
        gap_next = 0.0
        duration = cur[-1]["end"] - cur[0]["start"]
        ends = text.endswith((".", "!", "?", "…"))
        if ends or duration >= max_len:
            units.append(_unit(cur))
            cur = []
    if cur:
        units.append(_unit(cur))
    # merge tiny trailing fragments
    merged: list[dict] = []
    for u in units:
        if merged and (u["end"] - u["start"]) < 0.6:
            merged[-1]["end"] = u["end"]
            merged[-1]["text"] += " " + u["text"]
            merged[-1]["words"].extend(u["words"])
        else:
            merged.append(u)
    return merged


def _unit(words: list[dict]) -> dict:
    return {
        "start": round(words[0]["start"], 3),
        "end": round(words[-1]["end"], 3),
        "text": " ".join(w["word"] for w in words),
        "words": words,
    }


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z']+", text.lower()) if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _title_from(text: str, limit: int = 64) -> str:
    clean = re.sub(r"\s+", " ", text).strip()
    if not clean:
        return "Highlight"
    cut = clean[:limit]
    if len(clean) > limit:
        cut = cut.rsplit(" ", 1)[0] + "…"
    return cut[0].upper() + cut[1:]


# ---------------------------------------------------------------------------
# Candidate scoring
# ---------------------------------------------------------------------------

def _sentence_windows(units: list[dict], target: float) -> list[dict]:
    lo = max(6.0, target * 0.62)
    hi = max(lo + 3.0, target * 1.38)
    windows: list[dict] = []
    n = len(units)
    for i in range(n):
        start = units[i]["start"]
        best = None
        j = i
        while j < n and units[j]["end"] - start <= hi:
            dur = units[j]["end"] - start
            if dur >= lo:
                cand = {"start": start, "end": units[j]["end"], "units": units[i:j + 1]}
                if best is None or abs(dur - target) < abs((best["end"] - best["start"]) - target):
                    best = cand
            j += 1
        if best:
            windows.append(best)
        elif j == i and units[i]["end"] - start < lo:
            windows.append({"start": start, "end": min(units[i]["end"], start + target),
                            "units": [units[i]]})
    return windows


def _energy_windows(db: np.ndarray, block: float, target: float, step: float | None = None) -> list[dict]:
    """Energy-peak sliding windows, used when there is no transcript."""
    if db.size < 2:
        return []
    step = step or max(1.0, target * 0.5)
    smooth = np.convolve(db, np.ones(3) / 3, mode="same")
    out: list[dict] = []
    span = max(1, int(target / block))
    for i in range(0, len(smooth) - span, max(1, int(step / block))):
        chunk = smooth[i:i + span]
        out.append({
            "start": round(i * block, 2),
            "end": round(i * block + span * block, 2),
            "peak": float(chunk.max()),
            "mean": float(chunk.mean()),
            "units": [],
        })
    return out


def analyze(
    words: list[dict],
    segments: list[dict],
    duration: float,
    energy_db: np.ndarray,
    block: float,
    settings: dict,
) -> list[dict]:
    """Rank moments and return up to clip_count non-duplicated clips, in timeline order."""
    target = float(settings.get("clip_duration") or 30)
    count = int(settings.get("clip_count") or 5)
    sensitivity = float(settings.get("highlight_sensitivity", 50)) / 100.0
    allow_overlap = bool(settings.get("allow_overlap"))

    units = build_sentences(words) if words else []
    if units:
        windows = _sentence_windows(units, target)
    else:
        windows = _energy_windows(energy_db, block, target)
    if not windows:
        return []

    for w in windows:
        db = _slice(energy_db, block, w["start"], w["end"])
        w["e_mean"] = float(db.mean())
        w["e_peak"] = float(db.max())
        w["e_dyn"] = float(db.max() - db.mean())
        text = " ".join(u["text"] for u in w.get("units", []))
        w["text"] = text
        toks = re.findall(r"[a-z']+", text.lower())
        dur = max(0.5, w["end"] - w["start"])
        w["rate"] = len(toks) / (dur / 10.0)
        hooks = sum(1 for t in toks if t in HOOK_WORDS)
        reacts = sum(1 for t in toks if t in REACTION_MARKERS)
        punct = text.count("!") + text.count("?") * 1.2 + text.count("…")
        w["hooks"] = (hooks * 1.0 + reacts * 1.4 + punct * 0.8) / max(1.0, dur / 10.0)
        w["complete"] = 1.0 if w.get("units") else 0.45
        w["fit"] = max(0.0, 1.0 - abs(dur - target) / max(target, 1.0))
        w["novelty"] = 0.0

    ranks = {
        key: _rank([w[key] for w in windows])
        for key in ("e_mean", "e_peak", "e_dyn", "rate", "hooks")
    }
    for i, w in enumerate(windows):
        w["score"] = round(100.0 * (
            0.26 * ranks["e_mean"][i]
            + 0.21 * ranks["e_peak"][i]
            + 0.11 * ranks["e_dyn"][i]
            + 0.13 * ranks["rate"][i]
            + 0.17 * ranks["hooks"][i]
            + 0.06 * w["complete"]
            + 0.06 * w["fit"]
        ), 1)
        w["why"] = _why(w)

    # Diversity: penalise windows whose text repeats earlier higher-scoring ones.
    order = sorted(range(len(windows)), key=lambda i: -windows[i]["score"])
    for pos, idx in enumerate(order):
        mine = _tokens(windows[idx]["text"])
        worst = 0.0
        for prev in order[:pos]:
            worst = max(worst, _jaccard(mine, _tokens(windows[prev]["text"])))
        if worst:
            windows[idx]["score"] = round(windows[idx]["score"] * (1.0 - 0.45 * worst), 1)

    chosen: list[dict] = []
    # Min gap tightens with sensitivity so users get more, closer moments.
    for relax in (0.0, 0.35, 0.7, 1.0):
        min_gap = (18.0 - 13.0 * sensitivity) * (1.0 - 0.55 * relax)
        for idx in sorted(range(len(windows)), key=lambda i: -windows[i]["score"]):
            if len(chosen) >= count:
                break
            w = windows[idx]
            if any(c is w for c in chosen):
                continue
            mid = (w["start"] + w["end"]) / 2
            conflict = False
            for c in chosen:
                cmid = (c["start"] + c["end"]) / 2
                ov = max(0.0, min(w["end"], c["end"]) - max(w["start"], c["start"]))
                share = ov / max(1e-6, min(w["end"] - w["start"], c["end"] - c["start"]))
                if ov > 0 and (share > (0.75 if allow_overlap else 0.12) or abs(mid - cmid) < min_gap):
                    conflict = True
                    break
                if _jaccard(_tokens(w["text"]), _tokens(c["text"])) > 0.62:
                    conflict = True
                    break
            if not conflict:
                chosen.append(w)
        if len(chosen) >= count:
            break

    # Fill remaining slots with evenly spaced fallback windows (never duplicate).
    if len(chosen) < count and duration > 0:
        slots = np.linspace(0, max(0.0, duration - target), num=count * 3)
        for start in slots:
            if len(chosen) >= count:
                break
            end = min(duration, start + target)
            if end - start < max(5.0, target * 0.5):
                continue
            if any(min(end, c["end"]) - max(start, c["start"]) > 0 for c in chosen):
                continue
            db = _slice(energy_db, block, start, end)
            w = {"start": round(float(start), 2), "end": round(float(end), 2),
                 "score": round(min(60.0, 20 + float(db.mean()) + 30), 1),
                 "text": "", "why": "fallback window", "units": []}
            w["why"] = "timeline coverage"
            chosen.append(w)

    chosen = chosen[:count]
    chosen.sort(key=lambda w: w["start"])
    clips = []
    for i, w in enumerate(chosen):
        clips.append({
            "idx": i,
            "start": round(w["start"], 2),
            "end": round(w["end"], 2),
            "score": w.get("score", 0),
            "title": _title_from(w.get("text", "")) if w.get("text") else f"Moment {i + 1}",
            "text": w.get("text", ""),
            "reason": w.get("why", ""),
        })
    return clips


def _why(w: dict) -> str:
    bits = []
    if w["e_peak"] > -18:
        bits.append("loud reaction")
    if w["e_dyn"] > 12:
        bits.append("dynamic audio")
    if w["hooks"] > 1.2:
        bits.append("strong hooks")
    if w["rate"] > 3.2:
        bits.append("fast speech")
    if w["complete"] >= 1.0:
        bits.append("complete thought")
    return ", ".join(bits) or "steady energy"


def detect_scene_cuts(path: str | Path, threshold: float = 0.35) -> list[float]:
    """Scene change timestamps (used to avoid mid-shot cuts and for live events)."""
    from .config import FFMPEG
    from .media import run

    res = run([
        FFMPEG, "-hide_banner", "-nostdin", "-i", str(path), "-vf",
        f"select='gt(scene,{threshold})',showinfo", "-f", "null", "-",
    ], timeout=1800)
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", res.stderr or "")]
    return times
