"""SQLite storage. Projects hold an idea + script; segments hold one prompt each."""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .config import DATA

DB_PATH = DATA / "vidpipe.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    idea TEXT DEFAULT '',
    script TEXT DEFAULT '',
    beats TEXT DEFAULT '[]',
    outline TEXT DEFAULT '',
    final_video TEXT DEFAULT '',
    settings TEXT DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    beat TEXT DEFAULT '',
    prompt TEXT DEFAULT '',
    status TEXT DEFAULT 'empty',
    error TEXT DEFAULT '',
    comfy_prompt_id TEXT DEFAULT '',
    video TEXT DEFAULT '',
    last_frame TEXT DEFAULT '',
    UNIQUE (project_id, idx)
);
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    brief TEXT DEFAULT '',
    text TEXT DEFAULT '',
    status TEXT DEFAULT 'empty',
    error TEXT DEFAULT '',
    UNIQUE (project_id, idx)
);
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    local_path TEXT NOT NULL,
    comfy_name TEXT DEFAULT '',
    description TEXT DEFAULT '',
    created_at REAL NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "segment_count": 4,
    "duration": 15.0,
    "aspect_ratio": "16:9 (Widescreen)",
    "megapixels": 0.8,
    "steps": 10,
    "seed": -1,  # -1 = random per segment
    "filename_prefix": "video/vidpipe",
    "output_fps": 0,      # 0 = leave the workflow's own frame rate alone
    "ref_images": [],       # asset ids, in slot order
    "ref_audios": [],       # asset ids, in slot order
    "ref_image_count": 2,   # how many static image slots to switch on
    "ref_audio_count": 1,   # how many audio slots to switch on
    "continuation": True,   # append the previous clip's last frame as an extra reference
    "loras": [],          # [{"on": true, "lora": "name.safetensors", "strength": 1.0}]
    "story_mode": False,   # plan an outline, then write the script chapter by chapter
    "chapter_count": 4,
    "plan_beats": True,   # split the script into beats before writing prompts
    "send_full_script": True,  # include the whole script as context in stage 3
    "adopt_segment_count": True,  # if the segmenter returns a different count, use it
    "concat": True,       # stitch the finished clips into one file
    "crossfade_on": False,  # tick to overlap clips instead of cutting
    "crossfade": 0.5,     # seconds of overlap, used when crossfade_on
}


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


MIGRATIONS = [
    ("projects", "beats", "TEXT DEFAULT '[]'"),
    ("projects", "final_video", "TEXT DEFAULT ''"),
    ("segments", "beat", "TEXT DEFAULT ''"),
    ("assets", "description", "TEXT DEFAULT ''"),
    ("projects", "outline", "TEXT DEFAULT ''"),
]


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        for table, column, decl in MIGRATIONS:
            existing = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _row(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


# --- projects -------------------------------------------------------------

def create_project(name: str, idea: str = "") -> dict:
    now = time.time()
    with connect() as con:
        cur = con.execute(
            "INSERT INTO projects (name, idea, settings, created_at, updated_at)"
            " VALUES (?,?,?,?,?)",
            (name, idea, json.dumps(DEFAULT_SETTINGS), now, now),
        )
        pid = cur.lastrowid
    return get_project(pid)


def list_projects() -> list[dict]:
    with connect() as con:
        rows = con.execute(
            "SELECT id, name, updated_at FROM projects ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_project(pid: int) -> dict | None:
    with connect() as con:
        p = _row(con.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone())
        if p is None:
            return None
        settings = dict(DEFAULT_SETTINGS)
        settings.update(json.loads(p["settings"] or "{}"))
        p["settings"] = settings
        try:
            p["beats"] = json.loads(p["beats"] or "[]")
        except (json.JSONDecodeError, KeyError, TypeError):
            p["beats"] = []
        p["segments"] = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM segments WHERE project_id=? ORDER BY idx", (pid,)
            ).fetchall()
        ]
        p["chapters"] = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM chapters WHERE project_id=? ORDER BY idx", (pid,)
            ).fetchall()
        ]
    return p


def update_project(pid: int, **fields: Any) -> dict | None:
    allowed = {"name", "idea", "script", "settings", "beats", "final_video", "outline"}
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        vals.append(json.dumps(v) if k in ("settings", "beats") else v)
    if sets:
        sets.append("updated_at=?")
        vals.extend([time.time(), pid])
        with connect() as con:
            con.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id=?", vals)
    return get_project(pid)


def delete_project(pid: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM segments WHERE project_id=?", (pid,))
        con.execute("DELETE FROM chapters WHERE project_id=?", (pid,))
        con.execute("DELETE FROM projects WHERE id=?", (pid,))


# --- segments -------------------------------------------------------------

def ensure_segments(pid: int, count: int) -> list[dict]:
    with connect() as con:
        con.execute("DELETE FROM segments WHERE project_id=? AND idx>=?", (pid, count))
        for i in range(count):
            con.execute(
                "INSERT OR IGNORE INTO segments (project_id, idx) VALUES (?,?)", (pid, i)
            )
    return get_project(pid)["segments"]


def ensure_chapters(pid: int, count: int) -> list[dict]:
    with connect() as con:
        con.execute("DELETE FROM chapters WHERE project_id=? AND idx>=?", (pid, count))
        for i in range(count):
            con.execute(
                "INSERT OR IGNORE INTO chapters (project_id, idx) VALUES (?,?)", (pid, i)
            )
    return get_project(pid)["chapters"]


def update_chapter(pid: int, idx: int, **fields: Any) -> dict | None:
    allowed = {"brief", "text", "status", "error"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return get_chapter(pid, idx)
    vals.extend([pid, idx])
    with connect() as con:
        con.execute(
            f"UPDATE chapters SET {', '.join(sets)} WHERE project_id=? AND idx=?", vals
        )
    return get_chapter(pid, idx)


def get_chapter(pid: int, idx: int) -> dict | None:
    with connect() as con:
        return _row(con.execute(
            "SELECT * FROM chapters WHERE project_id=? AND idx=?", (pid, idx)).fetchone())


def update_segment(pid: int, idx: int, **fields: Any) -> dict | None:
    allowed = {"prompt", "status", "error", "comfy_prompt_id", "video",
               "last_frame", "beat"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return get_segment(pid, idx)
    vals.extend([pid, idx])
    with connect() as con:
        con.execute(
            f"UPDATE segments SET {', '.join(sets)} WHERE project_id=? AND idx=?", vals
        )
    return get_segment(pid, idx)


def get_segment(pid: int, idx: int) -> dict | None:
    with connect() as con:
        return _row(
            con.execute(
                "SELECT * FROM segments WHERE project_id=? AND idx=?", (pid, idx)
            ).fetchone()
        )


# --- assets ---------------------------------------------------------------

def add_asset(kind: str, label: str, local_path: str, comfy_name: str = "") -> dict:
    with connect() as con:
        cur = con.execute(
            "INSERT INTO assets (kind, label, local_path, comfy_name, created_at)"
            " VALUES (?,?,?,?,?)",
            (kind, label, local_path, comfy_name, time.time()),
        )
        aid = cur.lastrowid
    return get_asset(aid)


def get_asset(aid: int) -> dict | None:
    with connect() as con:
        return _row(con.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone())


def list_assets(kind: str | None = None) -> list[dict]:
    with connect() as con:
        if kind:
            rows = con.execute(
                "SELECT * FROM assets WHERE kind=? ORDER BY created_at", (kind,)
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM assets ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def update_asset(aid: int, **fields) -> dict | None:
    allowed = {"label", "description"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if sets:
        vals.append(aid)
        with connect() as con:
            con.execute(f"UPDATE assets SET {', '.join(sets)} WHERE id=?", vals)
    return get_asset(aid)


def delete_asset(aid: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM assets WHERE id=?", (aid,))
