"""Turns a batch of analyzed games into one compact "where do I lose points?"
summary for the AI assistant -- all aggregation happens here in Python so the
model sees ~1k tokens of pre-digested statistics instead of per-move data.

Only the user's own moves count (an opponent's blunder isn't the user's
weakness). Severity is lichess's win-probability-drop grading from
win_probability.py -- the same one /review uses.
"""
from __future__ import annotations

import chess

from db_reader import GameDetail
from win_probability import win_percent

SEVERITIES = ("blunder", "mistake", "inaccuracy")
PLURAL = {"blunder": "blunders", "mistake": "mistakes", "inaccuracy": "inaccuracies"}


def phase_for_ply(ply: int) -> str:
    # Simple ply-based bucketing (not material-based) -- good enough for a
    # "where do your mistakes cluster" summary without replaying every game.
    if ply < 20:
        return "opening"
    if ply < 60:
        return "middlegame"
    return "endgame"


def _win_pct_lost(evals: list[int], ply: int) -> float:
    """How many points of the mover's own win probability the move at `ply`
    cost, from the White-POV evals after each ply (position before ply 0 is
    the start position, evaluated as equal)."""
    before = win_percent(evals[ply - 1] if ply > 0 else 0)
    after = win_percent(evals[ply])
    return (before - after) if ply % 2 == 0 else (after - before)


def _better_move_san(detail: GameDetail, ply: int, best_uci: str | None) -> str | None:
    if not best_uci:
        return None
    board = chess.Board()
    try:
        for move in detail.moves[:ply]:
            board.push_san(move.san)
        san = board.san(chess.Move.from_uci(best_uci))
    except ValueError:  # illegal/garbled uci or an unparseable stored move: just skip the hint
        return None
    return san if san != detail.moves[ply].san else None


def _move_label(ply: int, san: str) -> str:
    return f"{ply // 2 + 1}{'.' if ply % 2 == 0 else '...'}{san}"


def summarize_weaknesses(games: list[tuple[GameDetail, dict]], color: str) -> dict:
    """`games` pairs each GameDetail with its review payload (see
    review_data.build_stockfish_review_payload). `color` is "white", "black"
    or "either" (each game then uses the color the user actually played)."""
    totals = {s: 0 for s in SEVERITIES}
    phase_errors = {"opening": 0, "middlegame": 0, "endgame": 0}
    phase_moves = {"opening": 0, "middlegame": 0, "endgame": 0}
    by_result: dict[str, dict] = {}
    by_opening: dict[str, dict] = {}
    worst: list[dict] = []
    accuracies: list[float] = []
    your_moves = 0

    for detail, payload in games:
        parity = 0 if detail.your_color == "white" else 1
        severities: dict[int, str] = payload["severities"]
        evals: list[int] = payload["evals"]
        result = {"1-0": "white", "0-1": "black"}.get(detail.result)
        outcome = "draw" if detail.result == "1/2-1/2" else (
            "win" if result == detail.your_color else "loss" if result else "unknown")

        game_errors = 0
        for ply in range(len(detail.moves)):
            if ply % 2 != parity:
                continue
            your_moves += 1
            phase_moves[phase_for_ply(ply)] += 1
            severity = severities.get(ply)
            if severity is None:
                continue
            game_errors += 1
            totals[severity] += 1
            phase_errors[phase_for_ply(ply)] += 1
            if severity != "inaccuracy" and ply < len(evals):
                worst.append({
                    "game_id": detail.id, "ply": ply, "severity": severity,
                    "win_pct_lost": round(_win_pct_lost(evals, ply)),
                    "opening": detail.opening,
                })

        accuracy = payload.get("accuracy") or {}
        if accuracy.get(detail.your_color) is not None:
            accuracies.append(accuracy[detail.your_color])

        bucket = by_result.setdefault(outcome, {"games": 0, "errors": 0})
        bucket["games"] += 1
        bucket["errors"] += game_errors
        opening = detail.opening or "Unknown"
        bucket = by_opening.setdefault(opening, {"games": 0, "errors": 0})
        bucket["games"] += 1
        bucket["errors"] += game_errors

    payload_by_id = {d.id: p for d, p in games}
    detail_by_id = {d.id: d for d, _p in games}
    worst.sort(key=lambda w: w["win_pct_lost"], reverse=True)
    worst_moves = []
    for w in worst[:5]:
        detail = detail_by_id[w["game_id"]]
        best = payload_by_id[w["game_id"]]["best_moves"].get(w["ply"])
        entry = {"game_id": w["game_id"], "move": _move_label(w["ply"], detail.moves[w["ply"]].san),
                 "severity": w["severity"], "win_pct_lost": w["win_pct_lost"], "opening": w["opening"]}
        better = _better_move_san(detail, w["ply"], best)
        if better:
            entry["better"] = better
        worst_moves.append(entry)

    n_games = len(games)
    per_100 = (lambda n: round(100 * n / your_moves, 1)) if your_moves else (lambda n: 0.0)
    openings = sorted(
        ({"opening": name, "games": b["games"], "errors_per_game": round(b["errors"] / b["games"], 1)}
         for name, b in by_opening.items() if b["games"] >= 2),
        key=lambda o: o["errors_per_game"], reverse=True)[:4]

    return {
        "games": n_games,
        "your_moves": your_moves,
        "avg_accuracy": round(sum(accuracies) / len(accuracies), 1) if accuracies else None,
        "errors_per_100_moves": {PLURAL[s]: per_100(totals[s]) for s in SEVERITIES},
        "errors_by_phase": {
            phase: {"errors": phase_errors[phase], "per_100_moves": per_100_phase(phase_errors[phase], phase_moves[phase])}
            for phase in phase_errors
        },
        "errors_per_game_by_result": {
            outcome: round(b["errors"] / b["games"], 1) for outcome, b in by_result.items()
        },
        "openings_with_most_errors": openings,
        "worst_moves": worst_moves,
    }


def per_100_phase(errors: int, moves: int) -> float:
    return round(100 * errors / moves, 1) if moves else 0.0
