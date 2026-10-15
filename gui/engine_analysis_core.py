"""The actual per-ply Stockfish analysis loop, extracted out of
engine_batch_worker.py so it can be called two ways: from EngineBatchWorker
(QThread, used by /review) and synchronously from the LLM assistant's
run_batch_analysis tool (which already runs off the GUI thread inside
LlmWorker, so it calls this directly rather than spinning up a nested
QThread).

Positions are analyzed by a small pool of single-threaded engines pulling
from a shared queue, not by one multi-threaded engine: at these depths
Stockfish's own multi-threading barely helps (measured: 15 threads in one
engine was no faster than 1), while independent engines on independent
positions scale almost linearly -- roughly 4-5x faster on the same hardware
at the same depth, so cached results stay identical in quality.
"""
from __future__ import annotations

import queue
import threading
from typing import Callable

import chess
import chess.engine

from analysis_cache import AnalysisCache
from engine import ENGINE_ID


def analyze_missing_plies(
    engine_factory: Callable[[], chess.engine.SimpleEngine], workers: int,
    sans: list[str], plies_to_analyze: list[int],
    cache: AnalysisCache, game_id: int, engine_version: str, setting_key: str, depth: int,
    on_progress: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> bool:
    """Analyzes `plies_to_analyze` (a subset of the game's plies) and caches
    each result as it completes -- a cancelled run keeps whatever finished.
    Returns False if cancelled partway through, True if it ran to completion;
    raises the first engine error if one occurred. `engine_factory` is called
    once per worker, and every engine it creates is quit before returning.
    `on_progress` and `should_cancel` are only ever called from the calling
    thread, never from a pool thread."""
    wanted = sorted(set(plies_to_analyze))
    total = len(wanted)
    if total == 0:
        return True

    # Analyze AFTER pushing this ply's move, matching the existing evals[]
    # convention from analysis.h::evaluate_game (evals[i] = eval after move i)
    # that review_data.py already indexes by.
    wanted_set = set(wanted)
    boards: dict[int, chess.Board] = {}
    board = chess.Board()
    for ply, san in enumerate(sans):
        board.push_san(san)
        if ply in wanted_set:
            boards[ply] = board.copy()

    pending: queue.Queue[int] = queue.Queue()
    for ply in wanted:
        pending.put(ply)
    finished: queue.Queue[int] = queue.Queue()  # one item per analyzed ply, for progress
    stop = threading.Event()
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            engine = engine_factory()
        except Exception as e:
            errors.append(e)
            stop.set()
            return
        try:
            while not stop.is_set():
                try:
                    ply = pending.get_nowait()
                except queue.Empty:
                    return
                info = engine.analyse(boards[ply], chess.engine.Limit(depth=depth))
                score = info["score"].white()
                pv = info.get("pv", [])
                cache.store_ply(
                    game_id, ply, ENGINE_ID, engine_version, setting_key,
                    score.score(), score.mate(),
                    pv[0].uci() if pv else None,
                    " ".join(m.uci() for m in pv) if pv else None,
                )
                finished.put(ply)
        except Exception as e:
            errors.append(e)
            stop.set()
        finally:
            try:
                engine.quit()
            except Exception:
                pass

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, min(workers, total)))]
    for t in threads:
        t.start()

    done = 0
    cancelled = False
    while done < total and not errors:
        if should_cancel is not None and should_cancel():
            cancelled = True
            break
        try:
            finished.get(timeout=0.1)
        except queue.Empty:
            if not any(t.is_alive() for t in threads):
                break
            continue
        done += 1
        if on_progress is not None:
            on_progress(done, total)

    stop.set()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    return not cancelled
