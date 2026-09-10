"""Runs a full-game Stockfish analysis off the GUI thread, one position at
a time, reporting real incremental progress and supporting cooperative
cancellation -- unlike FetchWorker (fetch_worker.py), which only emits
succeeded/failed once at the very end with no progress signal, this is
built specifically for a run that can take up to a minute and where the
user should be able to see it happening and cancel it.
"""
from __future__ import annotations

from pathlib import Path

import chess.engine
from PySide6.QtCore import QThread, Signal

from analysis_cache import AnalysisCache
from engine import BATCH_DEPTH, BATCH_SETTING_KEY, EngineManager
from engine_analysis_core import analyze_missing_plies


class EngineBatchWorker(QThread):
    progress = Signal(int, int)  # done, total
    succeeded = Signal()  # all requested plies are now in the cache
    failed = Signal(str)
    cancelled = Signal()  # distinct from failed -- no error dialog on a deliberate cancel

    def __init__(
        self, db_path: Path, sans: list[str], engine_manager: EngineManager,
        cache: AnalysisCache, game_id: int, engine_version: str, plies_to_analyze: list[int],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._sans = sans
        self._engine_manager = engine_manager
        self._cache = cache
        self._game_id = game_id
        self._engine_version = engine_version
        self._plies_to_analyze = plies_to_analyze
        self._cancel_requested = False

    def request_cancel(self) -> None:
        # Cooperative, not preemptive -- checked between positions, so
        # worst-case cancel latency is roughly one position's analysis
        # budget (a few hundred ms at batch depth). Acceptable.
        self._cancel_requested = True

    def run(self) -> None:
        try:
            engine = self._engine_manager.spawn_batch_engine()
        except Exception as e:
            self.failed.emit(str(e))
            return

        try:
            completed = analyze_missing_plies(
                engine, self._sans, self._plies_to_analyze, self._cache, self._game_id,
                self._engine_version, BATCH_SETTING_KEY, BATCH_DEPTH,
                on_progress=self.progress.emit,
                should_cancel=lambda: self._cancel_requested,
            )
            if completed:
                self.succeeded.emit()
            else:
                self.cancelled.emit()
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            try:
                engine.quit()
            except Exception:
                pass
