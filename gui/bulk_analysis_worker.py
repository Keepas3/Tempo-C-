"""Background Stockfish analysis of several games in a row (the /analyze
command), with real progress and cooperative cancel -- the same job the AI
assistant's weakness tool does mid-question, but done ahead of time so that
later questions find everything already cached and answer almost instantly.
"""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from analysis_cache import AnalysisCache
from db_reader import DbReader
from engine import ENGINE_ID, EngineManager, batch_worker_count, setting_key_for
from engine_analysis_core import analyze_missing_plies


class BulkAnalysisWorker(QThread):
    progress = Signal(int, int, int, int)  # positions_done, positions_total, game_number, game_count
    succeeded = Signal(dict)   # {"games", "games_analyzed", "games_cached", "positions"}
    cancelled = Signal(dict)   # same shape, for whatever finished before Cancel
    failed = Signal(str)

    def __init__(
        self, db: DbReader, cache: AnalysisCache, engine_manager: EngineManager,
        game_ids: list[int], depth: int, engine_version: str, parent=None,
    ) -> None:
        super().__init__(parent)
        self._db = db
        self._cache = cache
        self._engine_manager = engine_manager
        self._game_ids = game_ids
        self._depth = depth
        self._engine_version = engine_version
        self._cancel_requested = False

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        try:
            key = setting_key_for(self._depth)
            plan = []
            for game_id in self._game_ids:
                detail = self._db.load_game(game_id)
                if detail is None or not detail.moves:
                    continue
                missing = self._cache.get_missing_plies(game_id, len(detail.moves), ENGINE_ID, self._engine_version, key)
                plan.append((detail, missing))

            total = sum(len(missing) for _detail, missing in plan)
            summary = {"games": len(plan), "games_cached": sum(1 for _d, m in plan if not m),
                       "games_analyzed": 0, "positions": 0}
            done = 0
            for number, (detail, missing) in enumerate(plan, start=1):
                if not missing:
                    continue
                completed = analyze_missing_plies(
                    self._engine_manager.spawn_batch_engine, batch_worker_count(),
                    [m.san for m in detail.moves], missing, self._cache, detail.id,
                    self._engine_version, key, self._depth,
                    on_progress=lambda d, _t, base=done, n=number: self.progress.emit(base + d, total, n, len(plan)),
                    should_cancel=lambda: self._cancel_requested,
                )
                if not completed:
                    self.cancelled.emit(summary)
                    return
                done += len(missing)
                summary["games_analyzed"] += 1
                summary["positions"] = done
            self.succeeded.emit(summary)
        except Exception as e:  # never let a failed run die silently inside the thread
            self.failed.emit(str(e))
