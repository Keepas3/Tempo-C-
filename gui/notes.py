"""Notes: free-text annotations the user attaches to a whole game or to a
specific move within it. Owned entirely by the GUI -- the CLI never reads or
writes these tables, same boundary bookmarks.py and analysis_cache.py both
already draw for GUI-only data.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS game_notes (
    game_id INTEGER PRIMARY KEY,
    note TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS move_notes (
    game_id INTEGER NOT NULL,
    ply INTEGER NOT NULL,
    note TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (game_id, ply)
);
"""


class Notes:
    """Bound to one profile's db file -- notes live in the same file as that
    profile's games, so each profile's notes are isolated by construction
    (schema self-heals per file via CREATE TABLE IF NOT EXISTS). `ply` for
    move notes is 1-based, matching the "ply:<game_id>:<ply>" scheme already
    used for jump-to-move chat links."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.executescript(_SCHEMA)
        return conn

    def get_game_note(self, game_id: int) -> str | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT note FROM game_notes WHERE game_id = ?;", (game_id,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def set_game_note(self, game_id: int, text: str) -> None:
        """Empty/whitespace-only text deletes the note instead of storing an
        empty string -- lets "/note <id>" with no trailing text act as
        "clear this note" without a separate delete command."""
        text = text.strip()
        conn = self._connect()
        try:
            if not text:
                conn.execute("DELETE FROM game_notes WHERE game_id = ?;", (game_id,))
            else:
                conn.execute(
                    "INSERT INTO game_notes (game_id, note, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(game_id) DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at;",
                    (game_id, text, datetime.now(timezone.utc).isoformat()),
                )
            conn.commit()
        finally:
            conn.close()

    def get_move_notes(self, game_id: int) -> dict[int, str]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT ply, note FROM move_notes WHERE game_id = ?;", (game_id,)).fetchall()
            return {ply: note for ply, note in rows}
        finally:
            conn.close()

    def set_move_note(self, game_id: int, ply: int, text: str) -> None:
        text = text.strip()
        conn = self._connect()
        try:
            if not text:
                conn.execute("DELETE FROM move_notes WHERE game_id = ? AND ply = ?;", (game_id, ply))
            else:
                conn.execute(
                    "INSERT INTO move_notes (game_id, ply, note, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(game_id, ply) DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at;",
                    (game_id, ply, text, datetime.now(timezone.utc).isoformat()),
                )
            conn.commit()
        finally:
            conn.close()
