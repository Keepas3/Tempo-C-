"""Per-position Stockfish analysis cache: a game's engine evaluation is
expensive in wall-clock TIME (running a real search across every ply) but
cheap in storage (a few numbers per ply), so results are cached here to
avoid re-running the engine every time an already-analyzed game is
reviewed. Owned entirely by the GUI -- the CLI never reads or writes this
table (mirrors bookmarks.py's pattern exactly), and it never touches the
C++-owned games/moves tables.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from win_probability import MATE_NOW_CP

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


def _depth_of(setting_key: str) -> int | None:
    match = re.fullmatch(r"depth(\d+)", setting_key)
    return int(match.group(1)) if match else None


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

    def _best_rows(
        self, game_id: int, engine_id: str, engine_version: str, setting_key: str,
    ) -> dict[int, AnalysisRow]:
        """Per ply, the stored row to use for a request at `setting_key`
        ("depthN"): the DEEPEST row whose depth is >= N. A deeper analysis is
        always good enough for a shallower request, so analyzing at Detailed
        (depth 18) once also satisfies every Fast/Balanced question -- nothing
        is analyzed twice. A key that isn't "depthN" only matches exactly."""
        wanted = _depth_of(setting_key)
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT ply, score_cp, score_mate, best_move_uci, pv, setting_key FROM engine_analysis "
                "WHERE game_id = ? AND engine_id = ? AND engine_version = ?;",
                (game_id, engine_id, engine_version),
            ).fetchall()
        finally:
            conn.close()
        best: dict[int, tuple[int, AnalysisRow]] = {}
        for ply, cp, mate, best_move, pv, key in rows:
            if mate == 0:
                # Old rows from a game that ended in checkmate: "mate 0" carries no sign. The side to
                # move is the one mated, and ply parity says which that is (see engine_analysis_core).
                cp, mate = (MATE_NOW_CP if ply % 2 == 0 else -MATE_NOW_CP), None
            depth = _depth_of(key)
            if wanted is None or depth is None:
                if key != setting_key:
                    continue
                depth = wanted if wanted is not None else 0
            elif depth < wanted:
                continue
            if ply not in best or depth > best[ply][0]:
                best[ply] = (depth, AnalysisRow(ply, cp, mate, best_move, pv))
        return {ply: row for ply, (_depth, row) in best.items()}

    def get_full(
        self, game_id: int, total_plies: int, engine_id: str, engine_version: str, setting_key: str,
    ) -> list[AnalysisRow] | None:
        """Returns all `total_plies` rows in ply order, or None if any ply
        is missing (a partial/no cache -- caller should run analysis).
        Deeper-than-requested rows count (see _best_rows)."""
        rows = self._best_rows(game_id, engine_id, engine_version, setting_key)
        if any(ply not in rows for ply in range(total_plies)):
            return None
        return [rows[ply] for ply in range(total_plies)]

    def get_missing_plies(
        self, game_id: int, total_plies: int, engine_id: str, engine_version: str, setting_key: str,
    ) -> list[int]:
        have = self._best_rows(game_id, engine_id, engine_version, setting_key)
        return [ply for ply in range(total_plies) if ply not in have]
