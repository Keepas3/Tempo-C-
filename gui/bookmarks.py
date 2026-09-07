"""Bookmarks: save a position (real in-game or a branched analysis line) for
later review. Owned entirely by the GUI -- the CLI never reads or writes
this table, so schema creation happens here, not in the C++ side.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from db_reader import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY,
    fen TEXT NOT NULL,
    note TEXT,
    source_game_id INTEGER,
    source_ply INTEGER,
    created_at TEXT NOT NULL
);
"""


@dataclass
class Bookmark:
    id: int
    fen: str
    note: str | None
    source_game_id: int | None
    source_ply: int | None
    created_at: str


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_SCHEMA)
    return conn


def add(fen: str, note: str = "", source_game_id: int | None = None, source_ply: int | None = None) -> int:
    conn = _connect()
    try:
        cur = conn.execute(
            "INSERT INTO bookmarks (fen, note, source_game_id, source_ply, created_at) VALUES (?, ?, ?, ?, ?);",
            (fen, note or None, source_game_id, source_ply, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_all() -> list[Bookmark]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, fen, note, source_game_id, source_ply, created_at "
            "FROM bookmarks ORDER BY id DESC;"
        ).fetchall()
        return [Bookmark(*row) for row in rows]
    finally:
        conn.close()


def delete(bookmark_id: int) -> None:
    conn = _connect()
    try:
        conn.execute("DELETE FROM bookmarks WHERE id = ?;", (bookmark_id,))
        conn.commit()
    finally:
        conn.close()
