"""Bookmarks: save a position (real in-game or a branched analysis line) for
later review. Owned entirely by the GUI -- the CLI never reads or writes
this table, so schema creation happens here, not in the C++ side.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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


class Bookmarks:
    """Bound to one profile's db file -- bookmarks live in the same file as
    that profile's games, so each profile's bookmarks are isolated by
    construction (schema self-heals per file via CREATE TABLE IF NOT EXISTS)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute(_SCHEMA)
        return conn

    def add(self, fen: str, note: str = "", source_game_id: int | None = None, source_ply: int | None = None) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO bookmarks (fen, note, source_game_id, source_ply, created_at) VALUES (?, ?, ?, ?, ?);",
                (fen, note or None, source_game_id, source_ply, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def list_all(self) -> list[Bookmark]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, fen, note, source_game_id, source_ply, created_at "
                "FROM bookmarks ORDER BY id DESC;"
            ).fetchall()
            return [Bookmark(*row) for row in rows]
        finally:
            conn.close()

    def delete(self, bookmark_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM bookmarks WHERE id = ?;", (bookmark_id,))
            conn.commit()
        finally:
            conn.close()
