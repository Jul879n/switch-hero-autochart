"""SQLite de trabajos (/data/app.db)."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "/data"))
SONGS = DATA / "songs"
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """CREATE TABLE IF NOT EXISTS jobs(
 id TEXT PRIMARY KEY,
 status TEXT NOT NULL CHECK (status IN ('queued','running','done','error')),
 stage TEXT DEFAULT '', progress INTEGER DEFAULT 0,
 title TEXT DEFAULT '', artist TEXT DEFAULT '', src_kind TEXT DEFAULT '',
 src TEXT DEFAULT '', error TEXT DEFAULT '', created_at REAL, folder TEXT DEFAULT '',
 user_id INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE, name TEXT DEFAULT '',
 pw_hash TEXT DEFAULT '', role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin','user')),
 active INTEGER NOT NULL DEFAULT 1, created_at REAL, must_change INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS playlists(
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, spotify_id TEXT NOT NULL, name TEXT DEFAULT '',
 tracks TEXT NOT NULL DEFAULT '[]', updated REAL, UNIQUE(user_id, spotify_id));
CREATE TABLE IF NOT EXISTS invites(
 token_hash TEXT PRIMARY KEY, email TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(
 token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS spotify_tokens(
 user_id INTEGER PRIMARY KEY, access TEXT NOT NULL, refresh TEXT NOT NULL, expires REAL NOT NULL)"""


def init() -> None:
    global _conn
    DATA.mkdir(parents=True, exist_ok=True)
    SONGS.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(DATA / "app.db", check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _conn.executescript(SCHEMA)
    cols = [r[1] for r in _conn.execute('PRAGMA table_info(jobs)')]
    ucols = [r[1] for r in _conn.execute('PRAGMA table_info(users)')]
    if 'must_change' not in ucols:
        _conn.execute('ALTER TABLE users ADD COLUMN must_change INTEGER NOT NULL DEFAULT 0')
    if 'share' not in ucols:
        _conn.execute('ALTER TABLE users ADD COLUMN share INTEGER NOT NULL DEFAULT 1')
    if 'updated' not in cols:
        _conn.execute('ALTER TABLE jobs ADD COLUMN updated REAL DEFAULT 0')
    if 'sent' not in cols:
        _conn.execute('ALTER TABLE jobs ADD COLUMN sent INTEGER NOT NULL DEFAULT 0')
    if 'user_id' not in cols:
        _conn.execute('ALTER TABLE jobs ADD COLUMN user_id INTEGER DEFAULT 0')
    _conn.commit()


def _q(sql: str, args=()):
    with _lock:
        assert _conn is not None, "storage.init() no llamado"
        cur = _conn.execute(sql, args)
        _conn.commit()
        return cur


def create(src_kind: str, src: str, title: str = "", artist: str = "", user_id: int = 0) -> str:
    jid = uuid.uuid4().hex[:12]
    _q("INSERT INTO jobs(id,status,src_kind,src,title,artist,created_at,user_id) VALUES(?,?,?,?,?,?,?,?)",
       (jid, "queued", src_kind, src, title, artist, time.time(), user_id))
    return jid


def get(jid: str):
    r = _q("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    return dict(r) if r else None


def list_jobs(status: str | None = None, user_id: int | None = None) -> list:
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if user_id is not None:
        where.append("user_id=?")
        args.append(user_id)
    sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC"
    return [dict(r) for r in _q(sql, tuple(args)).fetchall()]


def update(jid: str, **fields) -> None:
    if not fields:
        return
    fields["updated"] = time.time()          # último avance: la web avisa si un trabajo lleva rato sin moverse
    cols = ", ".join(f"{k}=?" for k in fields)
    _q(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), jid))


def delete(jid: str) -> None:
    _q("DELETE FROM jobs WHERE id=?", (jid,))


def next_queued():
    r = _q("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1").fetchone()
    return dict(r) if r else None


def requeue_running() -> None:
    _q("UPDATE jobs SET status='queued', stage='', progress=0 WHERE status='running'")


def job_dir(jid: str) -> Path:
    return SONGS / jid
