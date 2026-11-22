"""Face detection for facecam discovery and smart reframing (OpenCV when available)."""
from __future__ import annotations

import statistics
from pathlib import Path

from .config import PROJECT_ROOT
from .media import frame_array

_cascade = None
_cv_ok: bool | None = None
CASCADE_PATH = PROJECT_ROOT / "assets" / "models" / "haarcascade_frontalface_default.xml"


def _load_cascade():
    global _cascade, _cv_ok
    if _cv_ok is not None:
        return _cascade
    _cascade = None
    _cv_ok = False
    try:
        import cv2

        candidates = [CASCADE_PATH]
        try:
            candidates.append(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        except Exception:
            pass
        for path in candidates:
            if not Path(path).exists():
                continue
            cascade = cv2.CascadeClassifier(str(path))
            if not cascade.empty():
                _cascade = cascade
                _cv_ok = True
                break
    except Exception:
        _cv_ok = False
    return _cascade


def available() -> bool:
    return bool(_load_cascade())


def detect_faces(path: str | Path, t: float, width: int = 480) -> list[tuple[float, float, float, float]]:
    """Return faces as normalised (x, y, w, h) rectangles of the frame."""
    cascade = _load_cascade()
    if cascade is None:
        return []
    import numpy as np

    raw = frame_array(path, t, width=width)
    if not raw:
        return []
    import cv2

    try:
        img = np.frombuffer(raw, dtype=np.uint8)
        h = len(img) // (width * 3)
        if h <= 0:
            return []
        img = img[: h * width * 3].reshape((h, width, 3))
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(28, 28))
        out = []
        for (x, y, w, hh) in faces:
            out.append((x / width, y / h, w / width, hh / h))
        return out
    except Exception:
        return []


def sample_times(duration: float, count: int = 7) -> list[float]:
    if duration <= 0:
        return [0.0]
    return [max(0.0, duration * (i + 0.5) / count) for i in range(count)]


def detect_facecam_region(path: str | Path, duration: float) -> dict | None:
    """Find a corner/edge facecam by voting over sampled frames."""
    if not available() or duration <= 0:
        return None
    votes: dict[str, int] = {}
    boxes: list[tuple[float, float, float, float]] = []
    for t in sample_times(duration, 8):
        faces = detect_faces(path, t)
        for (x, y, w, h) in faces:
            if w < 0.03 or w > 0.35:
                continue  # too small (noise) or too large (full-frame talking head)
            cx, cy = x + w / 2, y + h / 2
            key = ("L" if cx < 0.5 else "R") + ("T" if cy < 0.5 else "B")
            votes[key] = votes.get(key, 0) + 1
            boxes.append((x, y, w, h))
    if not votes:
        return None
    best = max(votes, key=votes.get)
    if votes[best] < 2:
        return None
    # Facecam regions are usually a fixed rectangle in one quadrant.
    region = {
        "LT": (0.0, 0.0, 0.5, 0.5),
        "RT": (0.5, 0.0, 0.5, 0.5),
        "LB": (0.0, 0.5, 0.5, 0.5),
        "RB": (0.5, 0.5, 0.5, 0.5),
    }.get(best, (0.0, 0.0, 0.5, 0.5))
    x0, y0, w0, h0 = region
    # Tighten to the face cluster inside that quadrant.
    inside = [b for b in boxes if x0 <= b[0] + b[2] / 2 <= x0 + w0 and y0 <= b[1] + b[3] / 2 <= y0 + h0]
    if inside:
        mx = statistics.median(b[0] + b[2] / 2 for b in inside)
        my = statistics.median(b[1] + b[3] / 2 for b in inside)
        mw = statistics.median(b[2] for b in inside) * 3.4
        mh = mw * 1.25
        nx = max(0.0, min(1.0, mx - mw / 2))
        ny = max(0.0, min(1.0, my - mh / 2))
        return {"x": round(float(nx), 3), "y": round(float(ny), 3),
                "w": round(float(min(mw, 1.0 - nx)), 3), "h": round(float(min(mh, 1.0 - ny)), 3)}
    return {"x": x0, "y": y0, "w": w0, "h": h0}


def face_center(path: str | Path, timestamps: list[float]) -> tuple[float, float] | None:
    """Median normalised centre of the largest face across several frames."""
    if not available():
        return None
    xs: list[float] = []
    ys: list[float] = []
    for t in timestamps:
        faces = detect_faces(path, t)
        if not faces:
            continue
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
        if w * h < 0.004:
            continue
        xs.append(x + w / 2)
        ys.append(y + h / 2)
    if not xs:
        return None
    return float(statistics.median(xs)), float(statistics.median(ys))
