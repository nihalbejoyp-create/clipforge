"""SQLite persistence layer (no ORM, plain sqlite3 with WAL)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .config import DB_PATH, ensure_dirs

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,               -- video | stream | live
    source_type TEXT NOT NULL,        -- url | file
    source_url TEXT,
    source_path TEXT,
    title TEXT,
    duration REAL DEFAULT 0,
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    fps REAL DEFAULT 0,
    has_audio INTEGER DEFAULT 1,
    thumb TEXT,
    status TEXT DEFAULT 'created',    -- created | ingesting | analyzing | ready | rendering | done | failed
    settings TEXT DEFAULT '{}',
    transcript_path TEXT,
    error TEXT,
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    idx INTEGER DEFAULT 0,
    title TEXT DEFAULT '',
    text TEXT DEFAULT '',
    start REAL DEFAULT 0,
    end REAL DEFAULT 0,
    score REAL DEFAULT 0,
    reason TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',    -- pending | rendering | done | failed
    progress REAL DEFAULT 0,
    source_path TEXT,                -- per-clip source (live segment materialisation)
    path TEXT,
    thumb TEXT,
    error TEXT,
    settings TEXT DEFAULT '{}',
    source TEXT DEFAULT 'ai',         -- ai | live | manual
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    kind TEXT NOT NULL,               -- ingest | analyze | render | export
    status TEXT DEFAULT 'queued',     -- queued | running | done | failed | canceled
    progress REAL DEFAULT 0,
    message TEXT DEFAULT '',
    error TEXT,
    payload TEXT DEFAULT '{}',
    created_at REAL,
    updated_at REAL
);

CREATE TABLE IF NOT EXISTS live_sessions (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT DEFAULT '',
    status TEXT DEFAULT 'starting',   -- starting | live | reconnecting | stopped | failed
    started_at REAL,
    stopped_at REAL,
    settings TEXT DEFAULT '{}',
    width INTEGER DEFAULT 0,
    height INTEGER DEFAULT 0,
    fps REAL DEFAULT 0,
    has_audio INTEGER DEFAULT 1,
    highlights INTEGER DEFAULT 0,
    clips INTEGER DEFAULT 0,
    error TEXT,
    log TEXT DEFAULT '',
    project_id TEXT,
    duration REAL DEFAULT 0,
    last_segment_at REAL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_clips_project ON clips(project_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def connection() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            ensure_dirs()
            _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.executescript(SCHEMA)
            _conn.commit()
        return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the first release (idempotent)."""
    wanted = {
        "clips": {
            "progress": "REAL DEFAULT 0",
            "source_path": "TEXT",
            "youtube_id": "TEXT",
            "youtube_at": "REAL",
            "youtube_account": "TEXT",
        },
        "live_sessions": {"project_id": "TEXT", "duration": "REAL DEFAULT 0"},
    }
    for table, columns in wanted.items():
        try:
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.Error:
            continue
        for column, ddl in columns.items():
            if column not in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                except sqlite3.Error:
                    pass
    conn.commit()


def init_db() -> None:
    connection()
    with _lock:
        _migrate(connection())
    # Anything that was mid-flight when the process died is resumable.
    with _lock:
        conn = connection()
        conn.execute("UPDATE jobs SET status='queued', message='resuming after restart' WHERE status='running'")
        conn.execute("UPDATE clips SET status='pending' WHERE status='rendering'")
        conn.commit()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> float:
    return time.time()


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for key in ("settings", "payload"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except Exception:
                d[key] = {}
    return d


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _lock:
        cur = connection().execute(sql, tuple(params))
        return [_row_to_dict(r) for r in cur.fetchall()]  # type: ignore[misc]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    with _lock:
        cur = connection().execute(sql, tuple(params))
        return _row_to_dict(cur.fetchone())


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    with _lock:
        conn = connection()
        conn.execute(sql, tuple(params))
        conn.commit()


def insert(table: str, data: dict) -> dict:
    data = dict(data)
    if "settings" in data and not isinstance(data["settings"], str):
        data["settings"] = json.dumps(data["settings"])
    if "payload" in data and not isinstance(data["payload"], str):
        data["payload"] = json.dumps(data["payload"])
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    with _lock:
        conn = connection()
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", tuple(data.values()))
        conn.commit()
    return data


def update(table: str, row_id: str, data: dict) -> None:
    data = dict(data)
    for key in ("settings", "payload"):
        if key in data and not isinstance(data[key], str):
            data[key] = json.dumps(data[key])
    if not data:
        return
    if "updated_at" not in data and table in {"projects", "clips", "jobs"}:
        data["updated_at"] = now()
    sets = ", ".join(f"{k}=?" for k in data)
    with _lock:
        conn = connection()
        conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*data.values(), row_id))
        conn.commit()


def get(table: str, row_id: str) -> dict | None:
    return query_one(f"SELECT * FROM {table} WHERE id=?", (row_id,))


def delete(table: str, row_id: str) -> None:
    execute(f"DELETE FROM {table} WHERE id=?", (row_id,))


def kv_get(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM kv WHERE key=?", (key,))
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return default


def kv_set(key: str, value: Any) -> None:
    with _lock:
        conn = connection()
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
        conn.commit()


def project_dir(project_id: str) -> Path:
    from .config import PROJECTS_DIR

    return PROJECTS_DIR / project_id


def live_dir(session_id: str) -> Path:
    from .config import LIVE_DIR

    return LIVE_DIR / session_id


def safe_unlink(path: str | Path | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass
