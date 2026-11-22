"""ClipForge API server: FastAPI + static frontend + live HLS endpoints."""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import tempfile
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db, faces, live, metadata, pipeline, render, transcribe, youtube
from .clip_settings import (
    DEFAULT_SETTINGS, FORMATS, LAYOUTS, LAYOUT_LABELS, RESOLUTION_CHOICES, SUBTITLE_FONTS,
    SUBTITLE_PRESETS, normalise_settings,
)
from .config import (
    DATA_DIR, FONTS_DIR, FRONTEND_OUT, HOST, PORT, TEMP_DIR, VIDEO_EXTENSIONS,
    disk_free_gb, ensure_dirs, ffmpeg_capabilities, installed_fonts, machine_info,
)
from .media import FFmpegError, probe_media, safe_filename

PREVIEW_DIR = DATA_DIR / "previews"


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    db.init_db()
    pipeline.start_workers()
    try:
        live.restore_sessions()
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] live restore skipped: {exc}")
    print(f"ClipForge ready on http://{HOST}:{PORT}")
    yield
    pipeline.stop_workers()


app = FastAPI(title="ClipForge", version="1.0.0", lifespan=lifespan)
# Job/clip payloads are polled continuously; compressing them keeps the UI
# snappy even with a few hundred clips in the queue.
app.add_middleware(GZipMiddleware, minimum_size=800)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}",
                   "http://localhost:3311", "http://127.0.0.1:3311"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file(path: str | None, media_type: str | None = None) -> FileResponse:
    if not path or not Path(path).exists():
        raise HTTPException(404, "file not found")
    return FileResponse(path, media_type=media_type)


def _clip_or_404(clip_id: str) -> dict:
    clip = db.get("clips", clip_id)
    if not clip:
        raise HTTPException(404, "clip not found")
    return clip


def _check_account(account_id: str) -> None:
    """Reject a channel id that isn't connected, so a stale tab can't queue
    uploads that are guaranteed to fail later."""
    connected = {item["id"] for item in youtube.status()["accounts"]}
    if account_id and account_id not in connected:
        raise HTTPException(400, "that YouTube channel is no longer connected — pick another one")


def _project_or_404(pid: str) -> dict:
    project = db.get("projects", pid)
    if not project:
        raise HTTPException(404, "project not found")
    return project


def _clip_public(clip: dict) -> dict:
    out = dict(clip)
    out["settings"] = clip.get("settings") or {}
    out["url"] = f"/api/clips/{clip['id']}/video" if clip.get("path") else None
    out["thumb_url"] = f"/api/clips/{clip['id']}/thumb" if clip.get("thumb") else None
    out["download_url"] = f"/api/clips/{clip['id']}/download" if clip.get("path") else None
    out["youtube_url"] = f"https://youtu.be/{clip['youtube_id']}" if clip.get("youtube_id") else None
    out["duration"] = round(float(clip.get("end") or 0) - float(clip.get("start") or 0), 2)
    return out


def _project_public(project: dict) -> dict:
    out = dict(project)
    out["thumb_url"] = f"/api/projects/{project['id']}/thumb" if project.get("thumb") else None
    out["source_url_local"] = (
        f"/api/projects/{project['id']}/source"
        if project.get("source_path") and project.get("mode") != "live" else None
    )
    out["clip_count"] = db.query_one(
        "SELECT COUNT(*) AS n FROM clips WHERE project_id=?", (project["id"],)
    )["n"]
    out["done_count"] = db.query_one(
        "SELECT COUNT(*) AS n FROM clips WHERE project_id=? AND status='done'", (project["id"],)
    )["n"]
    return out


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "service": "clipforge", "version": "1.0.0"}


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> RedirectResponse:
    """Browsers ask for /favicon.ico; the app ships an SVG icon."""
    return RedirectResponse("/icon.svg", status_code=307)


@app.get("/api/system")
def system() -> dict:
    caps = ffmpeg_capabilities()
    fonts = installed_fonts()
    available_fonts = {f["family"].lower() for f in fonts}
    missing = [f for f in SUBTITLE_FONTS if f.lower() not in available_fonts]
    return {
        "machine": machine_info(),
        "ffmpeg": caps,
        "disk_free_gb": disk_free_gb(),
        "data_dir": str(DATA_DIR),
        "fonts": fonts,
        "missing_fonts": missing,
        "whisper": {"available": transcribe.available()},
        "opencv": faces.available(),
        "frontend_built": FRONTEND_OUT.exists(),
        "limits": {
            "clip_count": [1, 2, 5, 10, 20, 50, 100],
            "durations": [15, 30, 45, 60, 90, 120],
            "resolutions": RESOLUTION_CHOICES,
            "formats": FORMATS,
            "layouts": [{"id": l, "label": LAYOUT_LABELS[l]} for l in LAYOUTS],
            "subtitle_presets": SUBTITLE_PRESETS,
            "fonts": SUBTITLE_FONTS,
        },
        "defaults": DEFAULT_SETTINGS,
    }


@app.get("/api/fonts")
def fonts() -> dict:
    return {"available": installed_fonts(), "choices": SUBTITLE_FONTS}


@app.post("/api/validate-url")
def validate_url(body: dict = Body(...)) -> dict:
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url is required")
    try:
        info = pipeline.validate_url(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"could not read that link: {exc}") from exc
    return {"ok": True, **info}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    name = file.filename or "upload.mp4"
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_EXTENSIONS:
        raise HTTPException(400, f"unsupported format {ext or '(none)'} — use MP4, MOV, MKV or WebM")
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    dest = TEMP_DIR / f"{db.new_id()}{ext}"
    size = 0
    with open(dest, "wb") as out:
        while chunk := await file.read(1024 * 1024 * 4):
            out.write(chunk)
            size += len(chunk)
    if size < 1024:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "uploaded file is empty")
    try:
        info = probe_media(dest)
    except FFmpegError as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"that file can't be read as video: {exc}") from exc
    if not info["has_video"]:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "that file has no video stream")
    return {"ok": True, "temp_path": str(dest), "filename": name, "size": size, "probe": info}


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.post("/api/projects")
def create_project(body: dict = Body(...)) -> dict:
    name = (body.get("name") or "").strip() or "Untitled project"
    mode = body.get("mode") or "video"
    if mode not in {"video", "stream", "live"}:
        raise HTTPException(400, "mode must be video, stream or live")
    source_type = body.get("source_type") or "file"
    settings = normalise_settings(body.get("settings") or {})

    if mode == "live":
        try:
            session = live.start_session(body.get("url") or "", settings, name)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "live": session, "project_id": session.get("project_id")}

    pid = db.new_id()
    payload: dict = {}
    if source_type == "url":
        url = (body.get("url") or "").strip()
        if not url:
            raise HTTPException(400, "url is required")
        try:
            info = pipeline.validate_url(url)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if info.get("is_live"):
            raise HTTPException(400, "that link is a live stream — use Live Stream Clipping")
        if info.get("duration", 0) <= 0:
            raise HTTPException(400, "could not determine the video duration")
        name = name if body.get("name") else info.get("title") or name
    else:
        temp_path = body.get("temp_path") or body.get("local_path")
        if not temp_path or not Path(temp_path).exists():
            raise HTTPException(400, "file not found — upload a video first")
        ext = Path(temp_path).suffix.lower()
        if ext not in VIDEO_EXTENSIONS:
            raise HTTPException(400, f"unsupported video format {ext}")
        payload["temp_path"] = str(temp_path)
        if not body.get("name"):
            name = Path(temp_path).stem

    project = {
        "id": pid,
        "name": name,
        "mode": mode,
        "source_type": source_type,
        "source_url": body.get("url") if source_type == "url" else None,
        "source_path": None,
        "title": name,
        "duration": 0,
        "width": 0,
        "height": 0,
        "fps": 0,
        "has_audio": 1,
        "status": "created",
        "settings": settings,
        "created_at": db.now(),
        "updated_at": db.now(),
    }
    db.insert("projects", project)
    pipeline.paths_for(pid)
    job = pipeline.enqueue("ingest", pid, payload, "adding source")
    return {"ok": True, "project_id": pid, "job_id": job["id"]}


@app.get("/api/projects")
def list_projects() -> dict:
    rows = db.query(
        "SELECT * FROM projects WHERE mode!='live' OR source_path IS NOT NULL "
        "ORDER BY created_at DESC LIMIT 200"
    )
    return {"projects": [_project_public(r) for r in rows]}


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict:
    project = _project_or_404(pid)
    jobs = db.query("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 30", (pid,))
    return {"project": _project_public(project), "jobs": jobs}


@app.patch("/api/projects/{pid}")
def update_project(pid: str, body: dict = Body(...)) -> dict:
    project = _project_or_404(pid)
    fields: dict = {}
    if "name" in body:
        fields["name"] = (body.get("name") or "").strip()[:120] or project["name"]
    if "settings" in body:
        fields["settings"] = normalise_settings({**(project.get("settings") or {}), **(body["settings"] or {})})
    if fields:
        db.update("projects", pid, fields)
    return {"ok": True, "project": _project_public(_project_or_404(pid))}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str, delete_files: bool = Query(False)) -> dict:
    project = _project_or_404(pid)
    for job in db.query("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')", (pid,)):
        db.update("jobs", job["id"], {"status": "canceled"})
    db.execute("DELETE FROM clips WHERE project_id=?", (pid,))
    db.execute("DELETE FROM jobs WHERE project_id=?", (pid,))
    db.delete("projects", pid)
    if project.get("mode") == "live":
        for row in db.query("SELECT id FROM live_sessions WHERE project_id=?", (pid,)):
            live.stop_session(row["id"])
            db.delete("live_sessions", row["id"])
            if delete_files:
                shutil.rmtree(Path(DATA_DIR) / "live" / row["id"], ignore_errors=True)
    if delete_files:
        shutil.rmtree(pipeline.project_paths(pid)["root"], ignore_errors=True)
    return {"ok": True}


@app.post("/api/projects/{pid}/analyze")
def analyze_project(pid: str, body: dict = Body(default={})) -> dict:
    project = _project_or_404(pid)
    # Analysis is started by the ingest job itself; if a caller asks again while
    # it is queued or running, hand back that job instead of failing or
    # creating a duplicate run.
    existing = db.query_one(
        "SELECT id FROM jobs WHERE project_id=? AND kind='analyze' AND status IN ('queued','running')",
        (pid,),
    )
    if existing:
        return {"ok": True, "job_id": existing["id"], "already_started": True}
    if project["status"] in {"ingesting"}:
        raise HTTPException(409, "this project is already importing its source")
    if not project.get("source_path"):
        raise HTTPException(400, "source media is not ready yet")
    settings = normalise_settings({**(project.get("settings") or {}), **(body.get("settings") or {})})
    if body.get("settings", {}).get("subtitle"):
        settings["subtitle"] = {**(project.get("settings") or {}).get("subtitle", {}),
                                **body["settings"]["subtitle"]}
    db.update("projects", pid, {"settings": settings})
    job = pipeline.enqueue("analyze", pid, {"settings": settings}, "analyzing")
    return {"ok": True, "job_id": job["id"]}


@app.get("/api/projects/{pid}/clips")
def project_clips(pid: str) -> dict:
    _project_or_404(pid)
    rows = db.query("SELECT * FROM clips WHERE project_id=? ORDER BY idx, start", (pid,))
    return {"clips": [_clip_public(c) for c in rows]}


@app.get("/api/projects/{pid}/jobs")
def project_jobs(pid: str) -> dict:
    return {"jobs": db.query("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 50", (pid,))}


@app.get("/api/projects/{pid}/transcript")
def project_transcript(pid: str) -> dict:
    _project_or_404(pid)
    data = pipeline.load_transcript(pid)
    return {
        "words": data.get("words", [])[:4000],
        "segments": data.get("segments", [])[:1200],
        "language": data.get("language"),
    }


@app.get("/api/projects/{pid}/thumb")
def project_thumb(pid: str) -> FileResponse:
    project = _project_or_404(pid)
    return _file(project.get("thumb"), "image/jpeg")


@app.get("/api/projects/{pid}/source")
def project_source(pid: str) -> FileResponse:
    project = _project_or_404(pid)
    path = project.get("source_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "source media not available")
    return FileResponse(path, media_type=mimetypes.guess_type(path)[0] or "video/mp4")


@app.post("/api/projects/{pid}/export-all")
def export_all(pid: str) -> dict:
    _project_or_404(pid)
    count = pipeline.queue_all_renders(pid)
    return {"ok": True, "queued": count}


@app.get("/api/projects/{pid}/download-all")
def download_all(pid: str) -> FileResponse:
    project = _project_or_404(pid)
    clips = db.query("SELECT * FROM clips WHERE project_id=? AND status='done' AND path IS NOT NULL ORDER BY idx", (pid,))
    if not clips:
        raise HTTPException(404, "no finished clips yet")
    tmp = Path(tempfile.mkstemp(suffix=".zip", dir=str(TEMP_DIR))[1])
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
        for clip in clips:
            path = Path(clip["path"])
            if path.exists():
                zf.write(path, arcname=path.name)
        info_txt = f"{project['name']}\n{len(clips)} clips\nGenerated with ClipForge\n"
        zf.writestr("clips.txt", info_txt)
    return FileResponse(tmp, media_type="application/zip",
                        filename=f"{safe_filename(project['name'])}_clips.zip",
                        background=_Cleanup(tmp))


class _Cleanup:
    def __init__(self, path: Path):
        self.path = path

    async def __call__(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Clips
# ---------------------------------------------------------------------------

@app.get("/api/clips")
def all_clips(status: str | None = Query(None)) -> dict:
    sql = ("SELECT c.*, p.name AS project_name, p.mode AS project_mode FROM clips c "
           "LEFT JOIN projects p ON p.id=c.project_id")
    params: list = []
    if status:
        sql += " WHERE c.status=?"
        params.append(status)
    sql += " ORDER BY c.updated_at DESC LIMIT 500"
    rows = db.query(sql, params)
    return {"clips": [{**_clip_public(r), "project_name": r.get("project_name"),
                       "project_mode": r.get("project_mode")} for r in rows]}


@app.get("/api/clips/{clip_id}")
def get_clip(clip_id: str) -> dict:
    clip = _clip_or_404(clip_id)
    project = db.get("projects", clip["project_id"]) or {}
    return {"clip": _clip_public(clip), "project": _project_public(project) if project else None}


@app.patch("/api/clips/{clip_id}")
def patch_clip(clip_id: str, body: dict = Body(...)) -> dict:
    clip = _clip_or_404(clip_id)
    project = db.get("projects", clip["project_id"]) or {}
    fields: dict = {}
    if "title" in body:
        fields["title"] = (body.get("title") or "").strip()[:140] or clip["title"]
    start = clip["start"]
    end = clip["end"]
    if body.get("start") is not None:
        start = float(body["start"])
    if body.get("end") is not None:
        end = float(body["end"])
    if end - start < 1.0:
        raise HTTPException(400, "clip must be at least 1 second long")
    limit = None
    if not clip.get("source_path") and project.get("duration"):
        limit = float(project["duration"])
    if start < 0 or (limit and end > limit + 0.5):
        raise HTTPException(400, "trim is outside the available footage")
    fields["start"] = round(start, 3)
    fields["end"] = round(end, 3)
    if "settings" in body and isinstance(body["settings"], dict):
        merged = dict(clip.get("settings") or {})
        sub = body["settings"].pop("subtitle", None)
        if isinstance(sub, dict):
            merged["subtitle"] = {**(merged.get("subtitle") or {}), **sub}
        merged.update(body["settings"])
        fields["settings"] = merged
    fields.setdefault("status", "pending")
    db.update("clips", clip_id, fields)
    if body.get("render", True):
        db.update("clips", clip_id, {"status": "pending", "progress": 0, "error": None})
        pipeline.enqueue("render", clip["project_id"], {"clip_id": clip_id}, "re-rendering")
    return {"ok": True, "clip": _clip_public(_clip_or_404(clip_id))}


@app.post("/api/clips/{clip_id}/render")
def render_clip(clip_id: str) -> dict:
    clip = _clip_or_404(clip_id)
    db.update("clips", clip_id, {"status": "pending", "progress": 0, "error": None})
    job = pipeline.enqueue("render", clip["project_id"], {"clip_id": clip_id}, "queued")
    return {"ok": True, "job_id": job["id"]}


@app.post("/api/clips/{clip_id}/regenerate")
def regenerate(clip_id: str) -> dict:
    try:
        clip = pipeline.regenerate_clip(clip_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "clip": _clip_public(clip)}


@app.get("/api/clips/{clip_id}/video")
def clip_video(clip_id: str) -> FileResponse:
    clip = _clip_or_404(clip_id)
    path = clip.get("path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "clip not rendered yet")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/clips/{clip_id}/thumb")
def clip_thumb(clip_id: str) -> FileResponse:
    clip = _clip_or_404(clip_id)
    return _file(clip.get("thumb"), "image/jpeg")


@app.post("/api/clips/{clip_id}/youtube")
def clip_youtube(clip_id: str, body: dict = Body(default={})) -> dict:
    clip = _clip_or_404(clip_id)
    if not clip.get("path") or not Path(clip["path"]).exists():
        raise HTTPException(400, "render this clip before uploading it")
    if not youtube.status()["connected"]:
        raise HTTPException(400, "connect your YouTube channel in Settings first")
    privacy = body.get("privacy")
    account = str(body.get("account") or "")
    _check_account(account)
    job = pipeline.enqueue("upload", clip["project_id"], {
        "clip_id": clip_id,
        "title": (body.get("title") or clip.get("title") or "").strip()[:100],
        "description": (body.get("description") or clip.get("text") or "").strip()[:4900],
        "privacy": privacy if privacy in youtube.PRIVACY else "private",
        "account": account or youtube.status()["active_account"],
    }, "uploading to YouTube")
    return {"ok": True, "job_id": job["id"], "job": job}


@app.post("/api/youtube/prepare")
def youtube_prepare(body: dict = Body(default={})) -> dict:
    """Draft titles, descriptions and tags for a batch of clips, for review."""
    ids = [str(cid) for cid in (body.get("clip_ids") or []) if cid]
    if not ids:
        raise HTTPException(400, "pick at least one clip")
    clips = [clip for clip in (db.get("clips", cid) for cid in ids) if clip]
    projects: dict[str, dict] = {}
    items = []
    for clip in sorted(clips, key=lambda c: (c.get("project_id") or "", c.get("idx") or 0)):
        pid = clip.get("project_id") or ""
        if pid not in projects:
            projects[pid] = db.get("projects", pid) or {}
        items.append({
            "clip_id": clip["id"],
            "project_id": pid,
            "idx": clip.get("idx") or 0,
            "duration": round(float(clip.get("end") or 0) - float(clip.get("start") or 0), 2),
            "thumb_url": f"/api/clips/{clip['id']}/thumb" if clip.get("thumb") else None,
            "status": clip.get("status"),
            "rendered": bool(clip.get("path") and Path(clip["path"]).exists()),
            "youtube_url": f"https://youtu.be/{clip['youtube_id']}" if clip.get("youtube_id") else None,
            "youtube_account": clip.get("youtube_account"),
            **metadata.draft(clip, projects[pid]),
        })
    return {"items": items, "connected": youtube.status()["connected"]}


@app.post("/api/youtube/bulk")
def youtube_bulk(body: dict = Body(default={})) -> dict:
    """Queue one upload job per clip so a whole batch posts without babysitting."""
    items = body.get("items") or []
    if not items:
        raise HTTPException(400, "nothing to upload")
    if not youtube.status()["connected"]:
        raise HTTPException(400, "connect your YouTube channel in Settings first")
    account = str(body.get("account") or "")
    _check_account(account)
    account = account or youtube.status()["active_account"]
    privacy = body.get("privacy")
    privacy = privacy if privacy in youtube.PRIVACY else "private"
    job_ids: list[str] = []
    skipped: list[dict] = []
    for item in items:
        clip = db.get("clips", str(item.get("clip_id") or ""))
        if not clip:
            skipped.append({"clip_id": item.get("clip_id"), "reason": "clip not found"})
            continue
        if not clip.get("path") or not Path(clip["path"]).exists():
            skipped.append({"clip_id": clip["id"], "reason": "not rendered yet"})
            continue
        job = pipeline.enqueue("upload", clip["project_id"], {
            "clip_id": clip["id"],
            "title": (item.get("title") or clip.get("title") or "").strip()[:100],
            "description": (item.get("description") or clip.get("text") or "").strip()[:4900],
            "tags": metadata.clean_tags(item.get("tags")),
            "privacy": privacy,
            "account": account,
        }, "uploading to YouTube")
        job_ids.append(job["id"])
    return {
        "ok": True,
        "queued": len(job_ids),
        "job_ids": job_ids,
        "skipped": skipped,
        "privacy": privacy,
        "account": account,
    }


@app.get("/api/clips/{clip_id}/download")
def clip_download(clip_id: str) -> FileResponse:
    clip = _clip_or_404(clip_id)
    path = clip.get("path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "clip not rendered yet")
    ext = Path(path).suffix
    return FileResponse(path, media_type="video/mp4",
                        filename=f"{safe_filename(clip['title'])}{ext}")


# ---------------------------------------------------------------------------
# Subtitle preview
# ---------------------------------------------------------------------------

@app.post("/api/preview/subtitle")
def subtitle_preview(body: dict = Body(default={})) -> dict:
    settings = normalise_settings(body.get("settings") or {})
    if isinstance(body.get("settings"), dict) and body["settings"].get("subtitle"):
        settings["subtitle"] = {**DEFAULT_SETTINGS["subtitle"], **body["settings"]["subtitle"]}
    out = PREVIEW_DIR / f"subs_{db.new_id()}.mp4"
    try:
        render.render_subtitle_preview(settings, out)
    except FFmpegError as exc:
        raise HTTPException(500, f"could not render preview: {exc}") from exc
    return {"ok": True, "url": f"/api/previews/{out.name}"}


@app.get("/api/previews/{name}")
def preview_file(name: str) -> FileResponse:
    path = PREVIEW_DIR / Path(name).name
    return _file(str(path), "video/mp4")


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------

@app.get("/api/youtube/status")
def youtube_status() -> dict:
    return youtube.status()


@app.post("/api/youtube/client")
def youtube_client(body: dict = Body(...)) -> dict:
    try:
        # ``blob`` is the downloaded client_secret_*.json pasted whole; the two
        # fields stay for anyone typing them in by hand.
        return youtube.save_client(body.get("client_id"), body.get("client_secret"), body.get("blob"))
    except youtube.YouTubeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/youtube/connect")
def youtube_connect() -> dict:
    try:
        return {"ok": True, "url": youtube.auth_url()}
    except youtube.YouTubeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/youtube/callback")
def youtube_callback(code: str = "", state: str = "", error: str = "") -> HTMLResponse:
    if error or not code:
        hint = youtube.oauth_error_hint(error)
        return HTMLResponse(youtube.page(
            "Google blocked the sign-in" if error == "access_denied" else "Sign-in cancelled",
            hint or "Google did not authorise the app, so nothing changed. You can close this tab and try again.",
        ), status_code=400)
    try:
        channel = youtube.complete(code, state)
    except youtube.YouTubeError as exc:
        return HTMLResponse(youtube.page("Could not connect", str(exc)), status_code=400)
    return HTMLResponse(youtube.page(
        "YouTube connected",
        "ClipForge can now upload renders to this channel. Close this tab and pick a clip.",
        channel=channel,
    ))


@app.post("/api/youtube/disconnect")
def youtube_disconnect(body: dict = Body(default={})) -> dict:
    """Disconnect one channel (``account_id``), or the default one."""
    return youtube.disconnect(str(body.get("account_id") or "") or None)


@app.post("/api/youtube/account")
def youtube_account(body: dict = Body(default={})) -> dict:
    """Choose which connected channel one-click uploads post to."""
    try:
        return youtube.set_active(str(body.get("account_id") or ""))
    except youtube.YouTubeError as exc:
        raise HTTPException(400, str(exc)) from exc


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
def jobs(limit: int = Query(40, le=200)) -> dict:
    return {"jobs": db.query("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = db.get("jobs", job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {"job": job}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    job = db.get("jobs", job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job["status"] in {"queued", "running"}:
        db.update("jobs", job_id, {"status": "canceled", "message": "canceled"})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------

@app.get("/api/live")
def live_list() -> dict:
    return {"sessions": live.list_sessions()}


@app.post("/api/live")
def live_start(body: dict = Body(...)) -> dict:
    url = (body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "stream URL is required")
    try:
        session = live.start_session(url, body.get("settings") or {}, body.get("title") or "")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "session": session}


@app.get("/api/live/{sid}")
def live_detail(sid: str) -> dict:
    session = live.get_session(sid)
    if session:
        data = session.snapshot()
    else:
        row = db.get("live_sessions", sid)
        if not row:
            raise HTTPException(404, "session not found")
        data = {**row, "duration": 0, "segments": 0, "disk_free_gb": disk_free_gb()}
    pid = data.get("project_id")
    clips = []
    if pid:
        clips = [_clip_public(c) for c in
                 db.query("SELECT * FROM clips WHERE project_id=? ORDER BY created_at DESC", (pid,))]
    data["clips_list"] = clips
    data["jobs"] = db.query(
        "SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 20", (pid,)
    ) if pid else []
    return {"session": data}


@app.post("/api/live/{sid}/stop")
def live_stop(sid: str) -> dict:
    ok = live.stop_session(sid)
    if not ok:
        raise HTTPException(404, "live session is not running")
    return {"ok": True}


@app.get("/api/live/{sid}/playlist.m3u8")
def live_playlist(sid: str) -> Response:
    session = live.get_session(sid)
    if session:
        body = session.preview_playlist()
    else:
        path = Path(DATA_DIR) / "live" / sid / "index.m3u8"
        if not path.exists():
            raise HTTPException(404, "no captured segments for this session")
        body = path.read_text(encoding="utf-8", errors="ignore")
    return Response(body, media_type="application/vnd.apple.mpegurl",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/live/{sid}/segments/{name}")
def live_segment(sid: str, name: str) -> FileResponse:
    safe = Path(name).name
    path = Path(DATA_DIR) / "live" / sid / "segments" / safe
    if not path.exists():
        # capture may still be writing the first segments
        raise HTTPException(404, "segment not ready")
    return FileResponse(path, media_type="video/mp2t")


# ---------------------------------------------------------------------------
# Frontend (static export) — must be mounted last
# ---------------------------------------------------------------------------

if (FRONTEND_OUT / "index.html").exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_OUT), html=True), name="frontend")
else:
    @app.get("/", response_class=HTMLResponse)
    def placeholder() -> str:
        return (
            "<html><body style='font-family:-apple-system;background:#0b0d12;color:#e8ecf4;padding:48px'>"
            "<h1>ClipForge backend is running</h1>"
            "<p>The frontend has not been built yet. Run <code>npm run build</code> in "
            "<code>frontend/</code>, or use <code>START.command</code> which does it for you.</p>"
            f"<p>API docs: <a style='color:#7aa2ff' href='/docs'>/docs</a></p></body></html>"
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
