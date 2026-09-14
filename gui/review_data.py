"""Normalizes /review data from either source (today's basic C++ evaluator,
or a Stockfish-backed cache read) into one common shape that
command_panel.py's _format_review renders -- so the rendering code doesn't
need to know or care which source produced it.

Normalized shape:
{
  "game": {white, black, date, result, site, opening, eco, moves: [{san}]},
  "evals": [cp ints or None, White POV, one per ply -- same contract as the
            C++ side's evaluate_game(): evals[i] is the eval AFTER move i.
            None means "not evaluated yet" (only for source == "pending")],
  "mates": {ply: mate_n},          # only present for plies with a forced mate
  "best_moves": {ply: uci string}, # engine's preferred move INSTEAD of the one actually played at `ply` (never present for ply 0 -- there's no analyzed position before the game's first move to draw it from)
  "severities": {ply: "blunder"|"mistake"|"inaccuracy"},
  "source": "stockfish" | "basic" | "pending",
  "engine_label": str | None,      # "Evaluated with: ..." banner text
  "status_text": str | None,       # only for "pending" -- shown instead of engine_label while a batch analysis is still running
  "accuracy": {"white": float|None, "black": float|None} | None,
              # lichess-style per-color game accuracy %. The whole key is
              # None (not per-color) for "basic"/"pending" sources, which
              # have no per-ply Stockfish data to derive it from.
}
"""
from __future__ import annotations

from analysis_cache import AnalysisRow
from db_reader import GameDetail
from win_probability import classify_delta, game_accuracy, mate_to_cp_equivalent, move_accuracy_from_cp


def _game_dict_from_cli(cli_game: dict) -> dict:
    # Already the right shape -- to_json(Game) in the C++ side.
    return cli_game


def _game_dict_from_detail(detail: GameDetail) -> dict:
    return {
        "white": detail.white, "black": detail.black, "date": detail.date,
        "result": detail.result, "site": detail.site, "opening": detail.opening,
        "eco": detail.eco, "moves": [{"san": m.san} for m in detail.moves],
    }


def build_basic_review_payload(cli_review_data: dict) -> dict:
    """Wraps today's tempo.exe --json review output unchanged -- the basic
    fallback path stays pixel-for-pixel identical to current behavior."""
    return {
        "game": _game_dict_from_cli(cli_review_data["game"]),
        "evals": cli_review_data["evals"],
        "mates": {},
        "best_moves": {},
        "severities": {},
        "source": "basic",
        "engine_label": "basic evaluator (no search) -- download Stockfish above the board for real analysis",
        "accuracy": None,
    }


def build_moves_only_payload(game_detail: GameDetail, status_text: str) -> dict:
    """Just the recorded move list, no evaluation at all yet -- shown
    immediately so /review never makes you wait to see at least the moves
    while a Stockfish batch analysis (which can take a while for an
    uncached game) runs in the background. `_emit_review` later replaces
    this same chat block in place with build_stockfish_review_payload's
    output once that analysis actually completes."""
    return {
        "game": _game_dict_from_detail(game_detail),
        "evals": [None] * len(game_detail.moves),
        "mates": {},
        "best_moves": {},
        "severities": {},
        "source": "pending",
        "engine_label": None,
        "status_text": status_text,
        "accuracy": None,
    }


def build_stockfish_review_payload(
    game_detail: GameDetail, cache_rows: list[AnalysisRow], engine_version: str, depth: int,
) -> dict:
    evals: list[int] = []
    mates: dict[int, int] = {}
    best_moves: dict[int, str] = {}
    for row in cache_rows:
        if row.score_mate is not None:
            mates[row.ply] = row.score_mate
            evals.append(mate_to_cp_equivalent(row.score_mate))
        else:
            evals.append(row.score_cp if row.score_cp is not None else 0)
        if row.best_move_uci:
            # row.best_move_uci is the engine's top choice IN THE POSITION
            # AFTER row.ply -- i.e. its suggestion for whichever move comes
            # next (row.ply + 1), not for row.ply itself. Keying by
            # row.ply + 1 here means best_moves[idx] then means "what the
            # engine would have played instead of the move actually made at
            # idx" -- an alternative to that move, not a preview of the
            # opponent's reply to it.
            best_moves[row.ply + 1] = row.best_move_uci

    severities = _classify_severities(cache_rows)
    accuracy = _compute_accuracy(cache_rows)

    return {
        "game": _game_dict_from_detail(game_detail),
        "evals": evals,
        "mates": mates,
        "best_moves": best_moves,
        "severities": severities,
        "source": "stockfish",
        "engine_label": f"{engine_version} (depth {depth})",
        "accuracy": accuracy,
    }


def _classify_severities(cache_rows: list[AnalysisRow]) -> dict[int, str]:
    """Grades every ply (both colors -- unlike analysis.h::find_blunders,
    which only ever looked at the archive owner's own moves) by lichess-
    style win-probability drop instead of a flat centipawn threshold, so
    the review table can flag the opponent's mistakes too, not just yours."""
    severities: dict[int, str] = {}
    by_ply = {row.ply: row for row in cache_rows}
    for row in cache_rows:
        mover_is_white = row.ply % 2 == 0
        before = by_ply.get(row.ply - 1)
        before_cp = before.score_cp if before else 0
        before_mate = before.score_mate if before else None
        severity = classify_delta(before_cp, before_mate, row.score_cp, row.score_mate, mover_is_white)
        if severity:
            severities[row.ply] = severity
    return severities


def _compute_accuracy(cache_rows: list[AnalysisRow]) -> dict[str, float | None]:
    """Per-color lichess-style game accuracy, computed once here (rather
    than in command_panel.py's rendering code) so re-rendering the table
    on every board-position change -- CommandPanel.set_review_ply, for the
    move-highlight feature -- never redoes this work; it just reads the
    number back out of the payload that's already been built."""
    by_ply = {row.ply: row for row in cache_rows}
    win_pcts: dict[str, list[float]] = {"white": [], "black": []}
    accuracies: dict[str, list[float]] = {"white": [], "black": []}

    for row in cache_rows:
        mover_is_white = row.ply % 2 == 0
        before = by_ply.get(row.ply - 1)
        before_cp = before.score_cp if before else 0
        before_mate = before.score_mate if before else None
        result = move_accuracy_from_cp(before_cp, before_mate, row.score_cp, row.score_mate, mover_is_white)
        if result is None:
            continue
        before_pct, accuracy = result
        color = "white" if mover_is_white else "black"
        win_pcts[color].append(before_pct)
        accuracies[color].append(accuracy)

    return {
        "white": game_accuracy(win_pcts["white"], accuracies["white"]),
        "black": game_accuracy(win_pcts["black"], accuracies["black"]),
    }
