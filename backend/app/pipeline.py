"""Job orchestration: ingest, transcription+analysis, clip rendering, resume."""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from . import analysis, db, faces, render, transcribe, youtube
from .clip_settings import merge_clip_settings, normalise_settings
from .config import PROJECTS_DIR, TEMP_DIR, disk_free_gb, ensure_dirs
from .media import FFmpegError, extract_audio, probe_media, safe_filename, thumbnail

# Three lanes: heavy rendering, an interactive lane for ingest/analysis, and a
# single upload lane. Sharing one pool meant a 24-clip 4K export held every slot
# for ~45 minutes, so a freshly created project sat at "queued" and never even
# started downloading.
RENDER_MAX = 3
RENDER_EXECUTOR = ThreadPoolExecutor(max_workers=RENDER_MAX, thread_name_prefix="clipforge-render")
PIPELINE_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-pipeline")
UPLOAD_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-upload")
_active: dict[str, dict] = {}  # job_id -> job row (kind + payload for CPU maths)
_active_lock = threading.Lock()
_dispatcher: threading.Thread | None = None
_stop = threading.Event()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def project_paths(pid: str) -> dict[str, Path]:
    root = PROJECTS_DIR / pid
    return {
        "root": root,
        "clips": root / "clips",
        "thumbs": root / "clips" / "thumbs",
        "work": root / "work",
        "transcript": root / "transcript.json",
    }


def paths_for(pid: str) -> dict[str, Path]:
    p = project_paths(pid)
    for key in ("root", "clips", "thumbs", "work"):
        p[key].mkdir(parents=True, exist_ok=True)
    return p


def load_transcript(pid: str) -> dict:
    path = project_paths(pid)["transcript"]
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def enqueue(kind: str, project_id: str | None = None, payload: dict | None = None, message: str = "queued") -> dict:
    # Two encoders on one clip would double the CPU for a single output file and
    # interleave writes into it, so a re-render request joins the pending job.
    # Uploads dedupe the same way: a double click must not post two videos.
    clip_id = (payload or {}).get("clip_id")
    if kind in {"render", "upload"} and clip_id:
        for row in db.query("SELECT id, payload FROM jobs WHERE kind=? "
                            "AND status IN ('queued','running')", (kind,)):
            if (row.get("payload") or {}).get("clip_id") == clip_id:
                return db.get("jobs", row["id"]) or row
    job = {
        "id": db.new_id(),
        "project_id": project_id,
        "kind": kind,
        "status": "queued",
        "progress": 0,
        "message": message,
        "payload": payload or {},
        "created_at": db.now(),
        "updated_at": db.now(),
    }
    db.insert("jobs", job)
    return job


def _update_job(job_id: str, **fields) -> None:
    db.update("jobs", job_id, fields)


def _cancelled(job_id: str) -> bool:
    row = db.query_one("SELECT status FROM jobs WHERE id=?", (job_id,))
    return bool(row and row["status"] == "canceled")


def _progress(job_id: str, frac: float, message: str | None = None) -> None:
    data = {"progress": round(max(0.0, min(1.0, frac)) * 100, 1)}
    if message:
        data["message"] = message
    db.update("jobs", job_id, data)


def recover_stuck_jobs() -> int:
    """Jobs left 'running' by a previous process are re-queued instead of hanging forever."""
    rows = db.query("SELECT id, kind, project_id FROM jobs WHERE status='running'")
    for row in rows:
        db.update("jobs", row["id"], {"status": "queued", "progress": 0,
                                     "message": "resuming after restart"})
        if row["project_id"] and row["kind"] in {"ingest", "analyze"}:
            db.update("projects", row["project_id"], {"status": "created"})
    db.execute("UPDATE jobs SET status='queued', progress=0, message='resuming after restart' "
               "WHERE status='running'")
    db.execute("UPDATE clips SET status='pending', progress=0 WHERE status='rendering'")
    return len(rows)


def start_workers() -> None:
    global _dispatcher
    ensure_dirs()
    if _dispatcher and _dispatcher.is_alive():
        return
    try:
        resumed = recover_stuck_jobs()
        if resumed:
            print(f"[startup] re-queued {resumed} interrupted job(s)")
    except Exception:
        traceback.print_exc()
    _stop.clear()
    _dispatcher = threading.Thread(target=_dispatch_loop, name="clipforge-dispatch", daemon=True)
    _dispatcher.start()


def stop_workers() -> None:
    _stop.set()


def _dispatch_loop() -> None:
    while not _stop.is_set():
        try:
            _dispatch_once()
        except Exception:
            traceback.print_exc()
        time.sleep(0.5)


def _claim(job: dict, executor: ThreadPoolExecutor) -> bool:
    with _active_lock:
        if job["id"] in _active:
            return False
        _active[job["id"]] = job
    try:
        executor.submit(_run_job, job["id"])
    except Exception:
        with _active_lock:
            _active.pop(job["id"], None)
        raise
    return True


def _render_cost(payload: dict | None) -> float:
    """How much of the render budget this clip will spend, in 1080p units.

    Clip settings override the project's, so a per-clip 4K or 1440p render is
    charged what it actually costs. Deliberately uncached: an edited resolution
    has to be re-priced before the next dispatch pass, and two indexed reads per
    queued clip is cheaper than a stale budget.
    """
    clip_id = (payload or {}).get("clip_id")
    res = 1080
    if clip_id:
        clip = db.get("clips", clip_id) or {}
        project = db.get("projects", clip.get("project_id") or "") or {}
        settings = merge_clip_settings(project.get("settings") or {}, clip.get("settings") or {})
        res = settings["resolution"]
    if res >= 2160:
        return 3.0
    if res >= 1440:
        return 1.5
    return 1.0


def _render_budget() -> float:
    """Total render CPU weight allowed at once, in 1080p-render units.

    A 4K vertical export spends ~3 cores per clip on scaling and blur, so three
    of them at once oversubscribe a 10-core machine and every click stutters.
    The old rules allowed one 4K, two 1440p or `cores // 3` 1080p renders, so
    that many 1080p renders is the budget and heavier exports spend more of it.
    """
    return float(min(RENDER_MAX, max(1, (os.cpu_count() or 4) // 3)))


def _dispatch_once() -> None:
    with _active_lock:
        active = list(_active.values())
    active_render = [job for job in active if job["kind"] == "render"]
    active_pipeline = sum(1 for job in active if job["kind"] in {"ingest", "analyze"})

    # Ingest and analysis always get their slot first: downloads and Whisper must
    # keep moving while clips render in the background.
    if active_pipeline < 1:
        row = db.query_one(
            "SELECT * FROM jobs WHERE status='queued' AND kind IN ('ingest','analyze') "
            "ORDER BY created_at ASC LIMIT 1"
        )
        if row:
            _claim(row, PIPELINE_EXECUTOR)

    # Uploads spend bandwidth, not CPU, so they never touch the render budget —
    # but only one runs at a time so two 200 MB clips don't split the uplink.
    if not any(job["kind"] == "upload" for job in active):
        row = db.query_one(
            "SELECT * FROM jobs WHERE status='queued' AND kind='upload' "
            "ORDER BY created_at ASC LIMIT 1"
        )
        if row:
            _claim(row, UPLOAD_EXECUTOR)

    # Spend the budget on everything in flight, including the clips already
    # claimed but not yet writing progress, then only start work that fits.
    free = _render_budget() - sum(_render_cost(job.get("payload")) for job in active_render)
    if free < 1.0:  # no clip costs less than one unit
        return
    rows = db.query(
        "SELECT * FROM jobs WHERE status='queued' AND kind='render' "
        "ORDER BY created_at ASC LIMIT 50"
    )
    busy = {(job.get("payload") or {}).get("clip_id") for job in active_render}
    for job in rows:
        if free < 1.0:
            break
        clip_id = (job.get("payload") or {}).get("clip_id")
        if clip_id and clip_id in busy:
            continue
        cost = _render_cost(job.get("payload"))
        if cost > free:
            # A 4K clip queued behind lighter ones must not start a fourth
            # encoder; skip it and keep the slots busy with clips that fit.
            continue
        if _claim(job, RENDER_EXECUTOR):
            free -= cost
            if clip_id:
                busy.add(clip_id)


def _run_job(job_id: str) -> None:
    job = db.get("jobs", job_id)
    try:
        if not job:
            return
        if job["status"] != "queued":
            return
        _update_job(job_id, status="running", message="starting", progress=1)
        kind = job["kind"]
        if kind == "ingest":
            run_ingest(job)
        elif kind == "analyze":
            run_analyze(job)
        elif kind == "render":
            run_render(job)
        elif kind == "upload":
            run_upload(job)
        else:
            raise RuntimeError(f"unknown job kind {kind}")
        _update_job(job_id, status="done", progress=100, message="completed")
    except Exception as exc:  # noqa: BLE001
        message = str(exc) or exc.__class__.__name__
        if "canceled" in message.lower():
            _update_job(job_id, status="canceled", message="canceled")
            # A cancelled job must not leave the project stuck mid-analysis,
            # otherwise every later "generate" is refused as already processing.
            pid = job.get("project_id")
            if pid:
                if job["kind"] in {"analyze", "ingest"}:
                    db.update("projects", pid, {"status": "created", "error": None})
                elif job["kind"] == "render":
                    clip_id = (job.get("payload") or {}).get("clip_id")
                    if clip_id:
                        db.update("clips", clip_id, {"status": "pending", "progress": 0})
        else:
            _update_job(job_id, status="failed", error=message[:4000], message="failed")
            print(f"[job {job_id}] failed: {message}\n{traceback.format_exc()}")
            _mark_project_failure(job, message)
    finally:
        with _active_lock:
            _active.pop(job_id, None)


def _mark_project_failure(job: dict, message: str) -> None:
    pid = job.get("project_id")
    if not pid:
        return
    if job["kind"] in {"ingest", "analyze"}:
        db.update("projects", pid, {"status": "failed", "error": message[:2000]})
    elif job["kind"] == "render":
        clip_id = (job.get("payload") or {}).get("clip_id")
        if clip_id:
            db.update("clips", clip_id, {"status": "failed", "error": message[:2000]})


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def validate_url(url: str) -> dict:
    """Resolve a URL without downloading so bad links fail fast."""
    import yt_dlp

    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "skip_download": True,
            "extract_flat": False, "socket_timeout": 20}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise ValueError(f"Could not read that link: {str(exc).splitlines()[0][:300]}") from exc
    if not info:
        raise ValueError("No media found at that URL")
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise ValueError("Playlist has no playable entries")
        info = entries[0]
    return {
        "title": info.get("title") or "Untitled",
        "duration": float(info.get("duration") or 0),
        "uploader": info.get("uploader"),
        "thumbnail": info.get("thumbnail"),
        "is_live": bool(info.get("is_live")),
        "extractor": info.get("extractor_key"),
    }


def download_source(url: str, dest_dir: Path, progress_cb: Callable[[float, str], None] | None = None) -> tuple[Path, dict]:
    import yt_dlp

    dest_dir.mkdir(parents=True, exist_ok=True)
    template = str(dest_dir / "source.%(ext)s")

    def hook(d: dict) -> None:
        if not progress_cb:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            parts: list[str] = []
            if total and done:
                frac = min(0.99, done / total)
                parts.append(f"downloading {done / total * 100:.0f}%")
            else:
                frac = 0.3
                parts.append("downloading")
            if done:
                parts.append(f"{done / 1e9:.1f} GB")
            speed = (d.get("_speed_str") or "").strip()
            if speed:
                parts.append(speed)
            eta = (d.get("_eta_str") or "").strip()
            if eta and eta not in {"Unknown", "N/A", "--:--"}:
                parts.append(f"~{eta} left")
            progress_cb(frac, " · ".join(parts))
        elif d.get("status") == "finished":
            progress_cb(0.99, "merging audio and video")

    opts = {
        "outtmpl": template,
        "format": "bv*[height<=2160][ext=mp4]+ba[ext=m4a]/bv*[height<=2160]+ba/b[height<=2160]/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [hook],
        "retries": 3,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
        "restrictfilenames": False,
        "overwrites": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
    if not info:
        raise RuntimeError("download produced no media")
    if info.get("_type") == "playlist" and info.get("entries"):
        info = info["entries"][0]
    files = sorted(dest_dir.glob("source.*"), key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        raise RuntimeError("download finished but no file was written")
    return files[0], info


def run_ingest(job: dict) -> None:
    pid = job["project_id"]
    project = db.get("projects", pid)
    if not project:
        raise RuntimeError("project vanished")
    paths = paths_for(pid)
    db.update("projects", pid, {"status": "ingesting", "error": None})
    payload = job.get("payload") or {}
    progress_cb = lambda f, m: _progress(job["id"], f * 0.92, m)  # noqa: E731

    if project["source_type"] == "file":
        src = payload.get("temp_path") or project["source_path"]
        if not src or not Path(src).exists():
            raise RuntimeError("uploaded file not found on disk")
        ext = Path(src).suffix.lower() or ".mp4"
        target = paths["root"] / f"source{ext}"
        if Path(src).resolve() != target.resolve():
            shutil.move(src, target)
        media_path = target
        _progress(job["id"], 0.9, "imported file")
    else:
        db.update("projects", pid, {"status": "ingesting"})
        media_path, info = download_source(project["source_url"], paths["root"], progress_cb)
        if info.get("title"):
            db.update("projects", pid, {"title": info.get("title")})

    _progress(job["id"], 0.93, "probing media")
    info = probe_media(media_path)
    if not info["has_video"]:
        raise RuntimeError("That file has no video stream")
    if info["duration"] <= 0 and info["has_video"]:
        raise RuntimeError("Could not determine the video duration")
    thumb = paths["root"] / "thumb.jpg"
    try:
        thumbnail(media_path, min(info["duration"] * 0.1, 20.0), thumb)
    except FFmpegError:
        thumb = None

    settings = normalise_settings(project.get("settings") or {})
    region = settings.get("facecam_region")
    if (project["mode"] in {"stream"} or settings.get("layout") in {"facecam_top", "facecam_bottom", "facecam"}) and not region:
        detected = faces.detect_facecam_region(media_path, info["duration"])
        if detected:
            settings["facecam_region"] = detected

    db.update("projects", pid, {
        "source_path": str(media_path),
        "title": project["title"] or media_path.stem,
        "duration": info["duration"],
        "width": info["width"],
        "height": info["height"],
        "fps": info["fps"],
        "has_audio": 1 if info["has_audio"] else 0,
        "thumb": str(thumb) if thumb else None,
        "settings": settings,
        "status": "created",
    })
    _progress(job["id"], 1.0, "ready")

    # Kick off analysis the moment the source is ready. This keeps the pipeline
    # moving even if the browser tab that created the project was closed, so a
    # long download can never leave a project sitting at status=created.
    if payload.get("auto_analyze", True) and not _cancelled(job["id"]):
        existing = db.query_one(
            "SELECT id FROM jobs WHERE project_id=? AND kind='analyze' AND status IN ('queued','running')",
            (pid,),
        )
        if not existing:
            enqueue("analyze", pid, {"settings": settings}, "starting analysis")


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def run_analyze(job: dict) -> None:
    pid = job["project_id"]
    project = db.get("projects", pid)
    if not project or not project.get("source_path"):
        raise RuntimeError("project has no source media")
    payload = job.get("payload") or {}
    settings = merge_clip_settings(project.get("settings") or {}, payload.get("settings"))
    db.update("projects", pid, {"status": "analyzing", "settings": settings, "error": None})
    # Clear previous AI clips so re-analysis is deterministic.
    db.execute("DELETE FROM clips WHERE project_id=? AND source='ai'", (pid,))
    paths = paths_for(pid)
    source = Path(project["source_path"])
    info = probe_media(source)

    from .config import DATA_DIR
    wav = DATA_DIR / "tmp" / f"{pid}.wav"
    _progress(job["id"], 0.02, "extracting audio")
    if info["has_audio"]:
        extract_audio(source, wav)
    _progress(job["id"], 0.06, "reading audio energy")
    energy, block = analysis.energy_profile(source)

    words: list[dict] = []
    segments: list[dict] = []
    transcript_error = None
    cached = None
    if not payload.get("force_transcribe") and paths["transcript"].exists():
        # Re-analysis with the same speech model should not redo an hour of
        # transcription for the same audio.
        try:
            data = json.loads(paths["transcript"].read_text(encoding="utf-8"))
            if data.get("model") == settings["whisper_model"] and data.get("words"):
                cached = data
        except Exception:
            cached = None
    if cached:
        words = cached.get("words") or []
        segments = cached.get("segments") or []
        _progress(job["id"], 0.9, "using saved transcript")
    if info["has_audio"] and cached:
        pass
    elif info["has_audio"]:
        if payload.get("skip_transcription"):
            transcript_error = "transcription disabled for this job"
        else:
            checkpoint = paths["work"] / "transcribe.partial.json"
            try:
                _progress(job["id"], 0.08, f"loading whisper {settings['whisper_model']}")
                result = transcribe.transcribe(
                    wav,
                    model_size=settings["whisper_model"],
                    language=settings.get("language", "auto"),
                    progress_cb=lambda f, m: _progress(job["id"], 0.08 + f * 0.82, m or "transcribing"),
                    should_cancel=lambda: _cancelled(job["id"]),
                    duration=info["duration"],
                    checkpoint=checkpoint,
                )
                words = result["words"]
                segments = result["segments"]
                paths["transcript"].write_text(json.dumps(result), encoding="utf-8")
                db.update("projects", pid, {"transcript_path": str(paths["transcript"])})
                try:
                    checkpoint.unlink(missing_ok=True)
                except OSError:
                    pass
            except RuntimeError as exc:
                if "canceled" in str(exc).lower():
                    raise
                transcript_error = str(exc)
            except Exception as exc:  # noqa: BLE001
                transcript_error = f"{exc.__class__.__name__}: {exc}"
                print(f"[analyze] transcription failed for {pid}: {transcript_error}")

    _progress(job["id"], 0.92, "ranking highlights")
    clips = analysis.analyze(words, segments, info["duration"], energy, block, settings)
    if not clips:
        raise RuntimeError("No clips could be generated — the video may be too short or silent")

    created = []
    for c in clips:
        clip_id = db.new_id()
        db.insert("clips", {
            "id": clip_id,
            "project_id": pid,
            "idx": c["idx"],
            "title": c["title"],
            "text": c["text"],
            "start": c["start"],
            "end": c["end"],
            "score": c["score"],
            "reason": c["reason"],
            "status": "pending",
            "progress": 0,
            "source": "ai",
            "settings": {},
            "created_at": db.now(),
            "updated_at": db.now(),
        })
        created.append(clip_id)

    note = ""
    if transcript_error:
        note = f" (no transcript: {transcript_error[:160]})"
    db.update("projects", pid, {"status": "ready", "error": None})
    _progress(job["id"], 0.96, "queuing renders")
    for clip_id in created:
        enqueue("render", pid, {"clip_id": clip_id}, "waiting to render")
    if wav.exists():
        try:
            wav.unlink()
        except OSError:
            pass
    _progress(job["id"], 1.0, f"{len(created)} clips generated{note}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def run_render(job: dict) -> None:
    payload = job.get("payload") or {}
    clip_id = payload.get("clip_id")
    clip = db.get("clips", clip_id) if clip_id else None
    if not clip:
        raise RuntimeError("clip not found")
    project = db.get("projects", clip["project_id"])
    if not project:
        raise RuntimeError("project source is missing")
    clip_source = clip.get("source_path")
    source = Path(clip_source) if clip_source else Path(project.get("source_path") or "")
    if not source or not source.exists():
        raise RuntimeError("original source file is missing; footage is never deleted automatically")

    free = disk_free_gb()
    if free and free < 1.0:
        raise RuntimeError(f"only {free} GB free — free up disk space and retry")

    settings = merge_clip_settings(project.get("settings") or {}, clip.get("settings") or {})
    start, end = float(clip["start"]), float(clip["end"])
    info = probe_media(source)
    if not clip_source and project.get("duration"):
        start = max(0.0, min(start, float(project["duration"]) - 1))
        end = min(float(project["duration"]), max(end, start + 1))
    paths = paths_for(clip["project_id"])
    words = _clip_words(clip, paths)

    ext = {"mp4": ".mp4", "mp4_hevc": ".mp4", "mov": ".mov"}.get(settings.get("format", "mp4"), ".mp4")
    out = paths["clips"] / f"{clip['idx'] + 1:02d}_{safe_filename(clip['title'])[:40]}_{clip_id[:6]}{ext}"

    db.update("clips", clip_id, {"status": "rendering", "progress": 0, "error": None})
    _progress(job["id"], 0.02, "preparing")

    def cb(frac: float, message: str) -> None:
        _progress(job["id"], 0.05 + frac * 0.9, message)
        db.update("clips", clip_id, {"progress": round(frac * 100, 1)})

    try:
        render.render_clip(
            source, start, end, settings, out,
            words=words, progress_cb=cb, should_cancel=lambda: _cancelled(job["id"]),
            info=info, workdir=paths["work"],
        )
    except FFmpegError:
        db.update("clips", clip_id, {"status": "failed", "error": "render failed"})
        raise

    thumb = None
    try:
        thumb = thumbnail(out, min(1.5, (end - start) / 3), paths["thumbs"] / f"{clip_id}.jpg")
    except FFmpegError:
        thumb = None
    db.update("clips", clip_id, {
        "status": "done",
        "progress": 100,
        "path": str(out),
        "thumb": str(thumb) if thumb else None,
        "error": None,
        "settings": settings if not clip.get("settings") else clip.get("settings"),
    })
    _refresh_project_status(clip["project_id"])


def run_upload(job: dict) -> None:
    """Stream a finished clip to YouTube. Bandwidth work, never CPU work."""
    video_id = youtube.upload_clip(
        job,
        progress_cb=lambda frac, message: _progress(job["id"], frac, message),
        should_cancel=lambda: _cancelled(job["id"]),
    )
    payload = dict(job.get("payload") or {})
    payload.update({"video_id": video_id, "url": f"https://youtu.be/{video_id}"})
    if payload.get("clip_id"):
        db.update("clips", payload["clip_id"], {
            "youtube_id": video_id,
            "youtube_at": db.now(),
            "youtube_account": str(payload.get("account") or "") or None,
        })
    db.update("jobs", job["id"], {"payload": payload, "message": "uploaded to YouTube"})


def _clip_words(clip: dict, paths: dict[str, Path]) -> list[dict]:
    sidecar = paths["clips"] / f"{clip['id']}.words.json"
    if clip.get("source_path") and sidecar.exists():
        try:
            return json.loads(sidecar.read_text(encoding="utf-8"))
        except Exception:
            return []
    return load_transcript(clip["project_id"]).get("words", [])


def _refresh_project_status(pid: str) -> None:
    rows = db.query("SELECT status FROM clips WHERE project_id=?", (pid,))
    if not rows:
        return
    statuses = {r["status"] for r in rows}
    if statuses <= {"done"}:
        db.update("projects", pid, {"status": "done"})
    elif statuses & {"pending", "rendering"}:
        db.update("projects", pid, {"status": "rendering"})
    elif statuses == {"failed"}:
        db.update("projects", pid, {"status": "failed"})


def queue_all_renders(pid: str) -> int:
    clips = db.query(
        "SELECT id FROM clips WHERE project_id=? AND status IN ('pending','failed') ORDER BY idx", (pid,)
    )
    for c in clips:
        enqueue("render", pid, {"clip_id": c["id"]}, "queued by user")
    return len(clips)


def regenerate_clip(clip_id: str) -> dict:
    """Pick the next-best unused moment for this clip slot and re-render it."""
    clip = db.get("clips", clip_id)
    if not clip:
        raise ValueError("clip not found")
    pid = clip["project_id"]
    project = db.get("projects", pid)
    if not project:
        raise ValueError("project not found")
    transcript = load_transcript(pid)
    words = transcript.get("words", [])
    settings = merge_clip_settings(project.get("settings") or {}, clip.get("settings") or {})
    if not words:
        raise ValueError("no transcript available for this project")
    energy, block = analysis.energy_profile(project["source_path"])
    candidates = analysis.analyze(
        words, transcript.get("segments", []), float(project.get("duration") or 0), energy, block,
        {**settings, "clip_count": 60, "allow_overlap": True, "highlight_sensitivity": 90},
    )
    others = db.query("SELECT start, end FROM clips WHERE project_id=? AND id!=?", (pid, clip_id))
    for cand in candidates:
        conflict = any(min(cand["end"], o["end"]) - max(cand["start"], o["start"]) > 0.5 for o in others)
        same = abs(cand["start"] - clip["start"]) < 0.5
        if conflict or same:
            continue
        db.update("clips", clip_id, {
            "start": cand["start"], "end": cand["end"], "title": cand["title"],
            "text": cand["text"], "score": cand["score"], "reason": cand["reason"],
            "status": "pending", "progress": 0, "error": None,
        })
        enqueue("render", pid, {"clip_id": clip_id}, "regenerating")
        return db.get("clips", clip_id) or clip
    raise ValueError("no alternative moment found for this clip")
