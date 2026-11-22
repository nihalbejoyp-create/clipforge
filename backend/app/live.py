"""Real-time livestream clipping: capture, rolling analysis, auto clip creation."""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
import traceback
from pathlib import Path

import numpy as np

from . import analysis, db, pipeline, transcribe
from .clip_settings import normalise_settings
from .config import FFMPEG, LIVE_DIR, disk_free_gb, ffmpeg_capabilities
from .media import probe_media, run, run_bytes

SEGMENT_RE = re.compile(r"^#EXTINF:([0-9.]+),\s*$")
DIRECT_SCHEMES = ("rtmp://", "rtmps://", "rtsp://", "srt://", "udp://", "tcp://", "http://", "https://")
SESSIONS: dict[str, "LiveSession"] = {}
_lock = threading.Lock()


def _is_direct(url: str) -> bool:
    low = url.lower()
    if not low.startswith(DIRECT_SCHEMES):
        return False
    return any(low.split("?")[0].endswith(ext) for ext in (".m3u8", ".flv", ".ts", ".mp4", ".mkv"))


def resolve_stream(url: str) -> tuple[str, dict]:
    """Turn a page URL into a playable stream URL, or pass direct inputs through."""
    if _is_direct(url):
        return url, {"title": "", "is_live": True, "headers": {}}
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 25,
            "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("no stream found at that URL")
    candidate = info.get("url")
    if not candidate:
        formats = [f for f in (info.get("formats") or []) if f.get("url")]
        hls = [f for f in formats if str(f.get("protocol", "")).startswith("m3u8")]
        progressive = [f for f in formats if f.get("vcodec") not in (None, "none")]
        pool = hls or progressive
        if not pool:
            raise RuntimeError("stream has no playable format")
        pool.sort(key=lambda f: (f.get("height") or 0), reverse=True)
        candidate = pool[0]["url"]
    return candidate, {
        "title": info.get("title") or "",
        "is_live": bool(info.get("is_live")),
        "headers": info.get("http_headers") or {},
        "extractor": info.get("extractor_key"),
    }


class LiveSession:
    def __init__(self, row: dict):
        self.id = row["id"]
        self.url = row["url"]
        self.project_id = row.get("project_id") or ""
        self.settings = normalise_settings(row.get("settings") or {})
        self.root = LIVE_DIR / self.id
        self.seg_dir = self.root / "segments"
        self.tmp_dir = self.root / "tmp"
        for p in (self.root, self.seg_dir, self.tmp_dir):
            p.mkdir(parents=True, exist_ok=True)
        self.playlist = self.root / "index.m3u8"
        self.log_path = self.root / "ffmpeg.log"
        self.stop_event = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.lock = threading.RLock()
        self.segments: list[dict] = []
        self.total_duration = 0.0
        self.next_number = 0
        self.energy: list[tuple[float, float]] = []
        self.pending_segments: list[dict] = []
        self.words: list[dict] = []
        self.transcribed_until = 0.0
        self.highlights = 0
        self.clips = 0
        self.last_clip_end = 0.0
        self.status = "starting"
        self.error = ""
        self.probed = False
        self.last_data_at = 0.0
        self.stream_title = row.get("title") or ""
        self.threads: list[threading.Thread] = []
        self._load_existing()

    # ---------------------------------------------------------------- state
    def _load_existing(self) -> None:
        """Restore the captured-segment index so nothing is lost across restarts."""
        index_file = self.root / "segments.json"
        entries: list[tuple[str, float]] = []
        if index_file.exists():
            try:
                for item in json.loads(index_file.read_text(encoding="utf-8")):
                    entries.append((item["name"], float(item["duration"])))
            except Exception:
                entries = []
        if not entries:
            entries = self._parse_playlist()
        for name, dur in entries:
            path = self.seg_dir / name
            if not path.exists():
                continue
            self.segments.append({
                "name": name, "path": str(path), "start": round(self.total_duration, 3),
                "duration": float(dur),
            })
            self.total_duration += float(dur)
        numbers = []
        for seg in self.segments:
            digits = re.sub(r"\D", "", seg["name"])
            if digits.isdigit():
                numbers.append(int(digits))
        self.next_number = (max(numbers) + 1) if numbers else len(self.segments)
        if self.segments:
            self.transcribed_until = 0.0
        if self.project_id:
            row = db.query_one("SELECT COUNT(*) AS n FROM clips WHERE project_id=?", (self.project_id,))
            self.clips = int(row["n"]) if row else 0

    def _persist_index(self) -> None:
        index_file = self.root / "segments.json"
        payload = [{"name": s["name"], "duration": s["duration"], "start": s["start"]}
                   for s in self.segments]
        tmp = index_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(index_file)

    def _parse_playlist(self) -> list[tuple[str, float]]:
        try:
            text = self.playlist.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return []
        out: list[tuple[str, float]] = []
        pending: float | None = None
        for line in text.splitlines():
            line = line.strip()
            m = SEGMENT_RE.match(line)
            if m:
                pending = float(m.group(1))
                continue
            if line and not line.startswith("#") and pending is not None:
                out.append((line, pending))
                pending = None
        return out

    def snapshot(self) -> dict:
        row = db.get("live_sessions", self.id) or {}
        with self.lock:
            now = self.total_duration
        return {
            **row,
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "duration": round(now, 1),
            "segments": len(self.segments),
            "highlights": self.highlights or int(row.get("highlights") or 0),
            "clips": self.clips,
            "disk_free_gb": disk_free_gb(),
            "project_id": self.project_id,
            "buffer_seconds": int(self.settings.get("live_buffer_seconds") or 240),
            "energy_points": len(self.energy),
            "transcript_words": len(self.words),
            "settings": self.settings,
        }

    def _set_status(self, status: str, error: str | None = None) -> None:
        self.status = status
        fields: dict = {"status": status, "last_segment_at": time.time()}
        if error is not None:
            self.error = error
            fields["error"] = error
        db.update("live_sessions", self.id, fields)

    def _log(self, message: str) -> None:
        print(f"[live {self.id}] {message}")

    # ---------------------------------------------------------------- capture
    def start(self) -> None:
        with _lock:
            SESSIONS[self.id] = self
        self._set_status("starting")
        for target, name in ((self._supervise, "capture"), (self._analysis_loop, "analysis")):
            t = threading.Thread(target=target, name=f"live-{name}-{self.id}", daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self, reason: str = "stopped") -> None:
        self.stop_event.set()
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=6)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with _lock:
            SESSIONS.pop(self.id, None)
        try:
            self._persist_index()
        except OSError:
            pass
        self.status = "stopped"
        db.update("live_sessions", self.id, {
            "status": "stopped", "stopped_at": time.time(),
            "duration": round(self.total_duration, 1),
        })
        self._log(reason)

    def _capture_cmd(self, input_url: str, headers: dict) -> list[str]:
        caps = ffmpeg_capabilities()
        res_cap = int(self.settings.get("resolution") or 1080)
        cmd = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "warning", "-y"]
        if input_url.startswith(("http://", "https://")):
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                    "-rw_timeout", "20000000"]
        if headers:
            hdr = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k.lower() != "host")
            if hdr:
                cmd += ["-headers", hdr]
        cmd += ["-i", input_url, "-map", "0:v:0?", "-map", "0:a:0?"]
        if caps["encoders"].get("h264_videotoolbox"):
            cmd += ["-c:v", "h264_videotoolbox", "-b:v", "6M", "-maxrate", "8M",
                    "-bufsize", "12M", "-allow_sw", "1"]
        else:
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"]
        cmd += [
            "-vf", f"scale=-2:min(ih\\,{res_cap})",
            "-pix_fmt", "yuv420p", "-g", "48", "-keyint_min", "48", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-f", "hls", "-hls_time", "2", "-hls_list_size", "0", "-hls_flags", "omit_endlist",
            "-hls_segment_type", "mpegts", "-start_number", str(self.next_number),
            "-hls_segment_filename", str(self.seg_dir / "seg_%06d.ts"),
            str(self.playlist),
        ]
        return cmd

    def _supervise(self) -> None:
        backoff = 2.0
        while not self.stop_event.is_set():
            try:
                url, meta = resolve_stream(self.url)
                if meta.get("title") and not self.stream_title:
                    self.stream_title = meta["title"]
                    db.update("live_sessions", self.id, {"title": meta["title"]})
                cmd = self._capture_cmd(url, meta.get("headers") or {})
                self._log("starting capture")
                with open(self.log_path, "ab") as log:
                    log.write(b"\n--- capture start ---\n")
                    log.flush()
                    self.proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL)
                started = time.time()
                last_count = len(self.segments)
                while not self.stop_event.is_set():
                    time.sleep(1.0)
                    proc = self.proc
                    if proc is not None and proc.poll() is not None:
                        raise RuntimeError(f"capture process exited (code {proc.returncode})")
                    self._ingest_playlist()
                    if len(self.segments) > last_count:
                        last_count = len(self.segments)
                        self.last_data_at = time.time()
                        if self.status != "live":
                            self._set_status("live")
                            if self.error:
                                self.error = ""
                                db.update("live_sessions", self.id, {"error": None})
                            backoff = 2.0
                    elif time.time() - max(self.last_data_at, started) > 30:
                        raise RuntimeError("no new stream data for 30s")
                    if disk_free_gb() < 4.0:
                        raise RuntimeError(f"disk almost full ({disk_free_gb()} GB free) — capture stopped")
                break
            except Exception as exc:  # noqa: BLE001
                if self.stop_event.is_set():
                    break
                message = str(exc)[:500]
                self._set_status("reconnecting", message)
                self._log(f"reconnecting after: {message}")
                proc = self.proc
                if proc and proc.poll() is None:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                time.sleep(backoff)
                backoff = min(30.0, backoff * 1.8)

    def _ingest_playlist(self) -> list[dict]:
        entries = self._parse_playlist()
        if not entries:
            return []
        known = {s["name"] for s in self.segments}
        new: list[dict] = []
        with self.lock:
            for name, dur in entries:
                if name in known:
                    continue
                path = self.seg_dir / name
                if not path.exists() or path.stat().st_size < 1024:
                    continue
                seg = {"name": name, "path": str(path),
                       "start": round(self.total_duration, 3), "duration": float(dur)}
                self.segments.append(seg)
                self.total_duration += float(dur)
                digits = re.sub(r"\D", "", name)
                if digits.isdigit():
                    self.next_number = max(self.next_number, int(digits) + 1)
                new.append(seg)
                self.pending_segments.append(seg)
                known.add(name)
            if new:
                self.last_data_at = time.time()
                db.update("live_sessions", self.id, {"last_segment_at": time.time()})
                try:
                    self._persist_index()
                except OSError:
                    pass
        return new

    # ---------------------------------------------------------------- analysis
    def _analysis_loop(self) -> None:
        while not self.stop_event.is_set():
            time.sleep(1.5)
            try:
                self._ingest_playlist()
                with self.lock:
                    fresh = self.pending_segments[:]
                    self.pending_segments.clear()
                for seg in fresh:
                    self._add_energy(seg)
                if not self.probed and self.segments:
                    self._probe_stream()
                self._maybe_transcribe(force=False)
                self._score_and_clip()
            except Exception:
                self._log(traceback.format_exc())
        try:
            self._maybe_transcribe(force=True)
        except Exception:
            pass

    def _probe_stream(self) -> None:
        try:
            info = probe_media(self.segments[-1]["path"])
        except Exception:
            return
        self.probed = True
        db.update("live_sessions", self.id, {
            "width": info["width"], "height": info["height"], "fps": info["fps"],
            "has_audio": 1 if info["has_audio"] else 0,
        })

    def _add_energy(self, seg: dict) -> None:
        try:
            proc = run_bytes([
                FFMPEG, "-hide_banner", "-nostdin", "-v", "error", "-i", seg["path"],
                "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-",
            ], timeout=90)
            raw = proc.stdout or b""
        except Exception as exc:  # noqa: BLE001
            self._log(f"energy decode failed: {exc}")
            return
        if not raw:
            return
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        block = 4000  # 0.5 s at 8 kHz
        n = len(samples) // block
        if n == 0:
            return
        cols = samples[: n * block].reshape(n, block)
        rms = np.sqrt(np.mean(np.square(cols), axis=1) + 1e-12)
        dbs = 20.0 * np.log10(np.maximum(rms, 1e-6))
        with self.lock:
            for i, val in enumerate(dbs):
                self.energy.append((seg["start"] + i * 0.5, float(val)))
            cutoff = self.total_duration - 900
            self.energy = [e for e in self.energy if e[0] >= cutoff]

    def _maybe_transcribe(self, force: bool) -> None:
        if not self.segments:
            return
        untranscribed = [s for s in self.segments
                         if s["start"] + s["duration"] > self.transcribed_until + 0.4]
        if not untranscribed:
            return
        chunk_dur = sum(s["duration"] for s in untranscribed)
        if chunk_dur < (3 if force else 16):
            return
        chunk = untranscribed[:32]  # ~64 s per pass
        chunk_start = chunk[0]["start"]
        total = sum(s["duration"] for s in chunk)
        list_file = self.tmp_dir / f"chunk_{int(chunk_start * 1000)}.txt"
        list_file.write_text(
            "\n".join(f"file '{Path(s['path']).as_posix()}'" for s in chunk), encoding="utf-8"
        )
        wav = self.tmp_dir / "chunk.wav"
        res = run([
            FFMPEG, "-hide_banner", "-nostdin", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-vn", "-ac", "1", "-ar", "16000", str(wav),
        ], timeout=240)
        if res.returncode != 0 or not wav.exists():
            self.transcribed_until = chunk[-1]["start"] + chunk[-1]["duration"]
            return
        try:
            result = transcribe.transcribe(
                wav,
                model_size=self.settings.get("whisper_model", "base"),
                language=self.settings.get("language", "auto"),
                duration=total,
            )
        except Exception as exc:
            self._log(f"live transcription failed: {exc}")
            self.transcribed_until = chunk[-1]["start"] + chunk[-1]["duration"]
            wav.unlink(missing_ok=True)
            list_file.unlink(missing_ok=True)
            return
        with self.lock:
            for w in result.get("words", []):
                self.words.append({
                    "start": round(w["start"] + chunk_start, 3),
                    "end": round(w["end"] + chunk_start, 3),
                    "word": w["word"],
                })
            cutoff = self.total_duration - 600
            self.words = [w for w in self.words if w["end"] >= cutoff]
        self.transcribed_until = chunk[-1]["start"] + chunk[-1]["duration"]
        wav.unlink(missing_ok=True)
        list_file.unlink(missing_ok=True)

    def _score_and_clip(self) -> None:
        settings = self.settings
        limit = None if settings.get("live_unlimited") else int(settings.get("clip_count") or 5)
        if limit is not None and self.clips >= limit:
            return
        now = self.total_duration
        if now < 12:
            return
        duration = float(settings.get("clip_duration") or 30)
        if now - self.last_clip_end < max(6.0, duration * 0.55):
            return
        window = float(settings.get("live_analysis_window") or 30)
        with self.lock:
            recent = [e for e in self.energy if e[0] >= now - window]
            baseline = [e[1] for e in self.energy[:-10]] or [e[1] for e in self.energy]
            words = [w for w in self.words if w["end"] >= now - window]
        if len(recent) < 6 or not baseline:
            return
        vals = np.array([e[1] for e in recent], dtype=np.float32)
        base = np.array(baseline, dtype=np.float32)
        mu, sigma = float(base.mean()), float(base.std() + 1e-3)
        spike = float(vals[-10:].mean())
        z = (spike - mu) / max(sigma, 1.5)
        peak_idx = int(np.argmax(vals))
        hook = sum(1 for w in words if w["word"].lower().strip(".,!?") in analysis.HOOK_WORDS)
        rate = len(words) / max(1.0, window / 10.0)
        text = " ".join(w["word"] for w in words)

        sensitivity = float(settings.get("live_sensitivity", 55)) / 100.0
        threshold = 1.55 - 1.0 * sensitivity
        score = 0.62 * z + 0.22 * min(3.0, hook / 3.0) + 0.16 * min(2.0, rate / 4.0)
        self.last_clip_end = now
        if score < threshold:
            return
        if words and len(_words_set(text)) < 3:
            return

        peak_time = recent[peak_idx][0]
        pre = float(settings.get("live_pre_roll") or 0.55)
        start = max(0.0, peak_time - duration * pre)
        end = min(now, start + duration)
        if end - start < max(5.0, duration * 0.45):
            return
        self._create_clip(start, end, min(100.0, 45 + score * 12), text)

    def _create_clip(self, start: float, end: float, score: float, text: str) -> None:
        self.highlights += 1
        self.clips += 1
        clip_id = db.new_id()
        db.insert("clips", {
            "id": clip_id,
            "project_id": self.project_id,
            "idx": self.clips - 1,
            "title": text.strip()[:70] or f"Live moment at {_clock(start)}",
            "text": text[:2000],
            "start": 0.0,
            "end": round(end - start, 2),
            "score": round(score, 1),
            "reason": f"live detection · {_clock(start)}",
            "status": "pending",
            "progress": 0,
            "source": "live",
            "settings": {},
            "created_at": db.now(),
            "updated_at": db.now(),
        })
        db.update("live_sessions", self.id, {"highlights": self.highlights, "clips": self.clips})
        self.last_clip_end = max(self.last_clip_end, end - 2)
        threading.Thread(target=self._materialise, args=(clip_id, start, end, 0),
                         name=f"live-clip-{clip_id}", daemon=True).start()

    def _materialise(self, clip_id: str, start: float, end: float, attempt: int) -> None:
        """Wait for the post-roll to be captured, then remux the covering segments."""
        wait = max(0.0, end - self.total_duration) + 2.5
        if wait > 0 and not self.stop_event.is_set():
            time.sleep(min(wait, 90))
        if attempt > 8:
            db.update("clips", clip_id, {"status": "failed",
                                         "error": "the post-roll footage for this clip was not captured"})
            return
        with self.lock:
            covered = [s for s in self.segments
                       if s["start"] + s["duration"] > start and s["start"] < end]
        if not covered:
            db.update("clips", clip_id, {"status": "failed", "error": "no footage available for this moment"})
            return
        if covered[-1]["start"] + covered[-1]["duration"] < end - 0.75:
            if self.status == "live" or time.time() - self.last_data_at < 20:
                threading.Thread(target=self._materialise, args=(clip_id, start, end, attempt + 1),
                                 daemon=True).start()
                return
        first = covered[0]
        list_file = self.tmp_dir / f"{clip_id}.txt"
        list_file.write_text(
            "\n".join(f"file '{Path(s['path']).as_posix()}'" for s in covered), encoding="utf-8"
        )
        proj = pipeline.project_paths(self.project_id)
        proj["clips"].mkdir(parents=True, exist_ok=True)
        out_src = proj["clips"] / f"{clip_id}_live.mp4"
        res = run([
            FFMPEG, "-hide_banner", "-nostdin", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy", "-movflags", "+faststart", str(out_src),
        ], timeout=900)
        list_file.unlink(missing_ok=True)
        if res.returncode != 0 or not out_src.exists():
            db.update("clips", clip_id, {
                "status": "failed",
                "error": f"could not extract clip footage: {res.stderr.strip()[-300:]}",
            })
            return
        offset = first["start"]
        end_of_source = covered[-1]["start"] + covered[-1]["duration"]
        with self.lock:
            local_words = [
                {"start": round(w["start"] - offset, 3), "end": round(w["end"] - offset, 3),
                 "word": w["word"]}
                for w in self.words if w["end"] >= start - 1 and w["start"] <= end + 1
            ]
        sidecar = proj["clips"] / f"{clip_id}.words.json"
        sidecar.write_text(json.dumps(local_words), encoding="utf-8")
        db.update("clips", clip_id, {
            "source_path": str(out_src),
            "start": round(max(0.0, start - offset), 3),
            "end": round(max(1.0, min(end, end_of_source) - offset), 3),
            "status": "pending",
            "error": None,
        })
        pipeline.enqueue("render", self.project_id, {"clip_id": clip_id}, "live clip queued")

    # ---------------------------------------------------------------- preview
    def preview_playlist(self) -> str:
        with self.lock:
            segs = self.segments[-8:]
            total = len(self.segments)
        header = ["#EXTM3U", "#EXT-X-VERSION:3"]
        if not segs:
            header += ["#EXT-X-TARGETDURATION:2", "#EXT-X-MEDIA-SEQUENCE:0"]
            return "\n".join(header) + "\n"
        header.append(f"#EXT-X-TARGETDURATION:{max(2, int(max(s['duration'] for s in segs)) + 1)}")
        header.append(f"#EXT-X-MEDIA-SEQUENCE:{max(0, total - len(segs))}")
        for s in segs:
            header.append(f"#EXTINF:{s['duration']:.3f},")
            header.append(f"segments/{s['name']}")
        return "\n".join(header) + "\n"


def _words_set(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", text.lower()) if len(w) > 3}


def _clock(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------------------
# Lifecycle helpers used by the API
# ---------------------------------------------------------------------------

def start_session(url: str, settings: dict, title: str = "") -> dict:
    url = (url or "").strip()
    if not url:
        raise ValueError("stream URL is required")
    if not url.lower().startswith(DIRECT_SCHEMES):
        raise ValueError("unsupported stream URL scheme")
    resolved_url, meta = resolve_stream(url)
    settings = normalise_settings(settings)
    sid = db.new_id()
    pid = db.new_id()
    (LIVE_DIR / sid / "segments").mkdir(parents=True, exist_ok=True)
    project_name = title or meta.get("title") or f"Live stream {sid[:6]}"
    db.insert("projects", {
        "id": pid, "name": project_name, "mode": "live", "source_type": "url",
        "source_url": url, "title": project_name, "duration": 0, "width": 0, "height": 0,
        "fps": 0, "has_audio": 1, "status": "created", "settings": settings,
        "created_at": db.now(), "updated_at": db.now(),
    })
    db.insert("live_sessions", {
        "id": sid, "url": url, "title": project_name, "status": "starting",
        "started_at": db.now(), "settings": settings, "highlights": 0, "clips": 0,
        "project_id": pid, "log": "",
    })
    row = db.get("live_sessions", sid) or {}
    session = LiveSession(row)
    session.stream_title = project_name
    session.remote_url = resolved_url  # type: ignore[attr-defined]
    session.start()
    return session.snapshot()


def get_session(sid: str) -> LiveSession | None:
    with _lock:
        return SESSIONS.get(sid)


def stop_session(sid: str) -> bool:
    session = get_session(sid)
    if not session:
        row = db.get("live_sessions", sid)
        if row and row["status"] in {"live", "reconnecting", "starting"}:
            db.update("live_sessions", sid, {"status": "stopped", "stopped_at": db.now()})
            return True
        return False
    session.stop()
    return True


def list_sessions() -> list[dict]:
    rows = db.query("SELECT * FROM live_sessions ORDER BY started_at DESC LIMIT 50")
    out = []
    for row in rows:
        session = get_session(row["id"])
        if session:
            out.append(session.snapshot())
        else:
            out.append({**row, "duration": round(row.get("duration") or 0, 1),
                        "segments": 0, "disk_free_gb": disk_free_gb()})
    return out


def restore_sessions() -> None:
    """Reconnect sessions that were live when the app last stopped."""
    rows = db.query("SELECT * FROM live_sessions WHERE status IN ('live','reconnecting','starting')")
    for row in rows:
        try:
            session = LiveSession(row)
            session.error = "reconnecting after app restart"
            session.start()
        except Exception as exc:  # noqa: BLE001
            db.update("live_sessions", row["id"], {"status": "failed", "error": str(exc)[:500]})
