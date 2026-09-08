"""Per-position Stockfish analysis cache: a game's engine evaluation is
expensive in wall-clock TIME (running a real search across every ply) but
cheap in storage (a few numbers per ply), so results are cached here to
avoid re-running the engine every time an already-analyzed game is
reviewed. Owned entirely by the GUI -- the CLI never reads or writes this
table (mirrors bookmarks.py's pattern exactly), and it never touches the
C++-owned games/moves tables or the vestigial, always-NULL moves.eval_cp
column.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS engine_analysis (
    game_id INTEGER NOT NULL,
    ply INTEGER NOT NULL,
    engine_id TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    setting_key TEXT NOT NULL,
    score_cp INTEGER,
    score_mate INTEGER,
    best_move_uci TEXT,
    pv TEXT,
    analyzed_at TEXT NOT NULL,
    PRIMARY KEY (game_id, ply, engine_id, setting_key)
);
"""


@dataclass
class AnalysisRow:
    ply: int
    score_cp: int | None
    score_mate: int | None
    best_move_uci: str | None
    pv: str | None


class AnalysisCache:
    """Bound to one profile's db file, structurally identical to
    bookmarks.py's Bookmarks class. engine_version and setting_key are
    part of the lookup/primary key so a deeper re-analysis or a Stockfish
    upgrade never collides with (or silently reuses) a stale shallower/
    older cached run -- both simply coexist, and a lookup for the current
    settings just misses and re-analyzes if there's no exact match."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute(_SCHEMA)
        return conn

    def store_ply(
        self, game_id: int, ply: int, engine_id: str, engine_version: str, setting_key: str,
        score_cp: int | None, score_mate: int | None, best_move_uci: str | None, pv: str | None,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO engine_analysis "
                "(game_id, ply, engine_id, engine_version, setting_key, score_cp, score_mate, "
                " best_move_uci, pv, analyzed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                (game_id, ply, engine_id, engine_version, setting_key, score_cp, score_mate,
                 best_move_uci, pv, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()

    def get_full(
        self, game_id: int, total_plies: int, engine_id: str, engine_version: str, setting_key: str,
    ) -> list[AnalysisRow] | None:
        """Returns all `total_plies` rows in ply order, or None if any ply
        is missing (a partial/no cache -- caller should run analysis)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT ply, score_cp, score_mate, best_move_uci, pv FROM engine_analysis "
                "WHERE game_id = ? AND engine_id = ? AND engine_version = ? AND setting_key = ? "
                "ORDER BY ply;",
                (game_id, engine_id, engine_version, setting_key),
            ).fetchall()
        finally:
            conn.close()
        if len(rows) != total_plies:
            return None
        result = [AnalysisRow(*row) for row in rows]
        if [r.ply for r in result] != list(range(total_plies)):
            return None
        return result

    def get_missing_plies(
        self, game_id: int, total_plies: int, engine_id: str, engine_version: str, setting_key: str,
    ) -> list[int]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT ply FROM engine_analysis "
                "WHERE game_id = ? AND engine_id = ? AND engine_version = ? AND setting_key = ?;",
                (game_id, engine_id, engine_version, setting_key),
            ).fetchall()
        finally:
            conn.close()
        have = {row[0] for row in rows}
        return [ply for ply in range(total_plies) if ply not in have]
