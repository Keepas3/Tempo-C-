"""The actual per-ply Stockfish analysis loop, extracted out of
engine_batch_worker.py so it can be called two ways: from EngineBatchWorker
(QThread, used by /review) and synchronously from the LLM assistant's
run_batch_analysis tool (which already runs off the GUI thread inside
LlmWorker, so it calls this directly rather than spinning up a nested
QThread).
"""
from __future__ import annotations

from typing import Callable

import chess
import chess.engine

from analysis_cache import AnalysisCache
from engine import ENGINE_ID


def analyze_missing_plies(
    engine: chess.engine.SimpleEngine, sans: list[str], plies_to_analyze: list[int],
    cache: AnalysisCache, game_id: int, engine_version: str, setting_key: str, depth: int,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> bool:
    """Analyzes `plies_to_analyze` (a subset of the game's plies) and caches
    each result as it completes -- a cancelled run keeps whatever finished.
    Returns False if cancelled partway through, True if it ran to completion.
    Caller owns the engine's lifecycle (spawn + quit)."""
    wanted = set(plies_to_analyze)
    total = len(plies_to_analyze)
    done = 0

    board = chess.Board()
    for ply, san in enumerate(sans):
        # Analyze AFTER pushing this ply's move, matching the existing
        # evals[] convention from analysis.h::evaluate_game (evals[i] = eval
        # after move i) that review_data.py already indexes by.
        board.push_san(san)
        if ply not in wanted:
            continue
        if should_cancel is not None and should_cancel():
            return False
        info = engine.analyse(board, chess.engine.Limit(depth=depth))
        score = info["score"].white()
        pv = info.get("pv", [])
        cache.store_ply(
            game_id, ply, ENGINE_ID, engine_version, setting_key,
            score.score(), score.mate(),
            pv[0].uci() if pv else None,
            " ".join(m.uci() for m in pv) if pv else None,
        )
        done += 1
        if on_progress is not None:
            on_progress(done, total)

    return True
