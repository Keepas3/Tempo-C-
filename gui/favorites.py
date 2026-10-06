"""Favorites: games the user has starred. Owned entirely by the GUI -- the CLI
never reads or writes this table, same boundary notes.py, bookmarks.py and
analysis_cache.py already draw for GUI-only data.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS favorite_games (
    game_id INTEGER PRIMARY KEY,
    added_at TEXT NOT NULL
);
"""


class Favorites:
    """Bound to one profile's db file, so each profile's favorites are
    isolated by construction (schema self-heals per file)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.executescript(_SCHEMA)
        return conn

    def get_all(self) -> set[int]:
        """Every favorited game id at once, for rendering the whole archive
        tree / filtering with one query instead of one per row."""
        conn = self._connect()
        try:
            return {row[0] for row in conn.execute("SELECT game_id FROM favorite_games;")}
        finally:
            conn.close()

    def is_favorite(self, game_id: int) -> bool:
        conn = self._connect()
        try:
            return conn.execute("SELECT 1 FROM favorite_games WHERE game_id = ?;", (game_id,)).fetchone() is not None
        finally:
            conn.close()

    def set_favorite(self, game_id: int, favorite: bool) -> None:
        conn = self._connect()
        try:
            if favorite:
                conn.execute(
                    "INSERT OR IGNORE INTO favorite_games (game_id, added_at) VALUES (?, ?);",
                    (game_id, datetime.now(timezone.utc).isoformat()),
                )
            else:
                conn.execute("DELETE FROM favorite_games WHERE game_id = ?;", (game_id,))
            conn.commit()
        finally:
            conn.close()

    def toggle(self, game_id: int) -> bool:
        """Flips the star and returns the new state."""
        new_state = not self.is_favorite(game_id)
        self.set_favorite(game_id, new_state)
        return new_state
