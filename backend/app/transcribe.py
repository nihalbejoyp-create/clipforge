"""Local speech recognition via faster-whisper (no API keys, runs on-device)."""
from __future__ import annotations

import json
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Iterator

from .config import DATA_DIR

_models: dict[str, object] = {}
_model_lock = threading.Lock()

MODEL_DIR = DATA_DIR / "models"
ProgressCb = Callable[[float, str], None]

# Multi-hour sources are transcribed window by window. Decoding a 9 hour file in
# one call buffers the whole thing in memory and produces no progress (or
# cancellable points) for many minutes, which looks exactly like a hang.
CHUNK_SECONDS = 300
SAMPLE_RATE = 16000


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _load(model_size: str):
    from faster_whisper import WhisperModel

    key = model_size
    with _model_lock:
        if key in _models:
            return _models[key]
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        import os

        threads = min(8, os.cpu_count() or 4)
        model = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",
            download_root=str(MODEL_DIR),
            cpu_threads=threads,
            num_workers=1,
        )
        _models[key] = model
        return model


def ensure_model(model_size: str) -> None:
    _load(model_size)


def _clock(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _wav_info(path: Path) -> tuple[int, int, int, int] | None:
    """(channels, sample_width, rate, frames) for a plain PCM wav, else None."""
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    except Exception:
        return None


def _read_window(path: Path, start_s: float, dur_s: float):
    """Read one mono float32 window straight out of the PCM wav."""
    import numpy as np

    info = _wav_info(path)
    if not info:
        return None
    channels, width, rate, frames = info
    if channels != 1 or width != 2:
        return None
    with wave.open(str(path), "rb") as w:
        w.setpos(max(0, min(frames, int(start_s * rate))))
        raw = w.readframes(max(0, int(dur_s * rate)))
    if not raw:
        return np.zeros(0, dtype="float32")
    return np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0


def _decode_all(path: Path):
    from faster_whisper import decode_audio

    return decode_audio(str(path), sampling_rate=SAMPLE_RATE)


def _params(lang: str | None) -> dict:
    return {
        "language": lang,
        "vad_filter": True,
        "vad_parameters": {"min_silence_duration_ms": 400},
        "word_timestamps": True,
        "beam_size": 1,
        "condition_on_previous_text": False,
        "temperature": 0.0,
    }


def _collect(segments_iter, offset: float, words: list, segments: list,
             should_cancel: Callable[[], bool] | None, on_segment: Callable[[float], None]) -> None:
    for seg in segments_iter:
        if should_cancel and should_cancel():
            raise RuntimeError("canceled")
        start = offset + float(seg.start or 0)
        end = offset + float(seg.end or 0)
        text = (seg.text or "").strip()
        if text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": text})
        for w in (seg.words or []):
            token = (w.word or "").strip()
            if not token:
                continue
            words.append({
                "start": round(offset + float(w.start), 3),
                "end": round(offset + float(w.end), 3),
                "word": token,
            })
        on_segment(float(seg.end or 0))


def _transcribe_once(model, audio, lang: str | None, words: list, segments: list,
                     should_cancel, on_segment) -> None:
    segments_iter, _info = model.transcribe(audio, **_params(lang))
    _collect(segments_iter, 0.0, words, segments, should_cancel, on_segment)


def _write_checkpoint(path: Path, covered: float, words: list, segments: list,
                      total: float, detected: dict, model_size: str, lang: str | None) -> None:
    payload = {
        "covered": round(covered, 2),
        "total": round(total, 2),
        "words": words,
        "segments": segments,
        "language": detected.get("language"),
        "language_probability": detected.get("probability"),
        "model": model_size,
        "language_setting": lang,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def transcribe(
    audio_path: str | Path,
    model_size: str = "base",
    language: str | None = "auto",
    progress_cb: ProgressCb | None = None,
    should_cancel: Callable[[], bool] | None = None,
    duration: float | None = None,
    checkpoint: Path | None = None,
) -> dict:
    """Transcribe audio into words + segments with timestamps."""
    model = _load(model_size)
    lang = None if not language or language == "auto" else language
    path = Path(audio_path)

    info_wav = _wav_info(path)
    total = duration or 0
    audio_total = 0.0
    if info_wav:
        _channels, _width, rate, frames = info_wav
        if rate:
            audio_total = frames / float(rate)
    # Extracted audio can run slightly longer than the video container reports;
    # walk the real audio length so the loop can never overrun its end.
    total = max(total, audio_total)

    words: list[dict] = []
    segments: list[dict] = []
    started = time.time()
    detected = {"language": lang or "en", "probability": 0.0}
    resumed_from = 0.0

    # A multi-hour transcription can be interrupted by a restart; pick up the
    # checkpoint written after each window instead of starting over.
    if checkpoint and checkpoint.exists():
        try:
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            if saved.get("model") == model_size and float(saved.get("covered") or 0) > 1:
                words = saved.get("words") or []
                segments = saved.get("segments") or []
                resumed_from = float(saved["covered"])
                detected = {
                    "language": saved.get("language") or lang or "en",
                    "probability": float(saved.get("language_probability") or 0),
                }
        except Exception:
            words, segments, resumed_from = [], [], 0.0

    def report(covered: float, phase: str) -> None:
        if not progress_cb or total <= 0:
            return
        frac = min(0.99, covered / total)
        elapsed = time.time() - started
        eta = (elapsed / frac - elapsed) if frac > 0.02 else 0.0
        tail = f" · ~{_clock(eta)} left" if eta > 10 else ""
        progress_cb(frac, f"{phase} {_clock(covered)} / {_clock(total)}{tail}")

    def on_segment(end: float) -> None:
        # `end` is already chunk-relative; add the window offset exactly once.
        report(offset[0] + end, "transcribing")

    offset = [resumed_from]
    window = _read_window(path, resumed_from, CHUNK_SECONDS) if info_wav else None
    if window is not None and total > CHUNK_SECONDS * 1.2:
        # Long source: walk it window by window so progress and cancel work.
        step = float(CHUNK_SECONDS)
        end_of_audio = audio_total or total
        while offset[0] < end_of_audio - 0.5:
            if should_cancel and should_cancel():
                raise RuntimeError("canceled")
            window = _read_window(path, offset[0], step)
            if window is None or len(window) < SAMPLE_RATE // 2:
                break
            segments_iter, chunk_info = model.transcribe(window, **_params(lang))
            if not detected["probability"]:
                detected = {
                    "language": getattr(chunk_info, "language", lang or "en"),
                    "probability": float(getattr(chunk_info, "language_probability", 0) or 0),
                }
            _collect(segments_iter, offset[0], words, segments, should_cancel, on_segment)
            offset[0] += len(window) / float(SAMPLE_RATE)
            report(offset[0], "transcribing")
            if checkpoint:
                try:
                    _write_checkpoint(checkpoint, offset[0], words, segments, total,
                                      detected, model_size, lang)
                except OSError:
                    pass
    else:
        audio = _decode_all(path)
        if total <= 0:
            total = len(audio) / float(SAMPLE_RATE)
        segments_iter, info = model.transcribe(audio, **_params(lang))
        detected = {
            "language": getattr(info, "language", lang or "en"),
            "probability": float(getattr(info, "language_probability", 0) or 0),
        }
        _collect(segments_iter, 0.0, words, segments, should_cancel, on_segment)

    return {
        "language": detected["language"],
        "language_probability": detected["probability"],
        "duration": round(total, 2),
        "words": words,
        "segments": segments,
        "model": model_size,
    }
