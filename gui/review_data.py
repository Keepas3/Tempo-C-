"""Normalizes /review data from either source (today's basic C++ evaluator,
or a Stockfish-backed cache read) into one common shape that
command_panel.py's _format_review renders -- so the rendering code doesn't
need to know or care which source produced it.

Normalized shape:
{
  "game": {white, black, date, result, site, opening, eco, moves: [{san}]},
  "evals": [cp ints, White POV, one per ply -- same contract as the C++
            side's evaluate_game(): evals[i] is the eval AFTER move i],
  "mates": {ply: mate_n},          # only present for plies with a forced mate
  "best_moves": {ply: uci string}, # engine's top continuation after this move
  "severities": {ply: "blunder"|"mistake"|"inaccuracy"},
  "source": "stockfish" | "basic",
  "engine_label": str,
}
"""
from __future__ import annotations

from analysis_cache import AnalysisRow
from db_reader import GameDetail
from win_probability import classify_delta, mate_to_cp_equivalent


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
            best_moves[row.ply] = row.best_move_uci

    severities = _classify_severities(cache_rows, game_detail.your_color)

    return {
        "game": _game_dict_from_detail(game_detail),
        "evals": evals,
        "mates": mates,
        "best_moves": best_moves,
        "severities": severities,
        "source": "stockfish",
        "engine_label": f"{engine_version} (depth {depth})",
    }


def _classify_severities(cache_rows: list[AnalysisRow], your_color: str) -> dict[int, str]:
    """Mirrors analysis.h::find_blunders's restriction to the user's own
    plies, but grades each move by lichess-style win-probability drop
    instead of a flat centipawn threshold."""
    severities: dict[int, str] = {}
    by_ply = {row.ply: row for row in cache_rows}
    for row in cache_rows:
        mover_is_white = row.ply % 2 == 0
        mover_color = "white" if mover_is_white else "black"
        if mover_color != your_color:
            continue
        before = by_ply.get(row.ply - 1)
        before_cp = before.score_cp if before else 0
        before_mate = before.score_mate if before else None
        severity = classify_delta(before_cp, before_mate, row.score_cp, row.score_mate, mover_is_white)
        if severity:
            severities[row.ply] = severity
    return severities
