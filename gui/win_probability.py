"""Lichess-style win-probability-delta move classification, used to grade
Stockfish-backed /review results. Deliberately not a flat centipawn-loss
threshold (e.g. the existing C++ find_blunders in analysis.h uses a flat
150cp cutoff) -- a fixed cp swing means very different things near equality
vs. in an already-decided position, so lichess grades moves by how much
win probability they cost instead.
"""
from __future__ import annotations

import math

# Lichess's own calibrated constant for converting centipawns to an
# expected win percentage (fit against real games between ~2300-rated
# players). See lila's AccuracyPercent.scala / Advice.scala.
_WIN_PERCENT_K = -0.00368208

# Mover's-POV win% drop thresholds for each severity tag.
_BLUNDER_THRESHOLD = 30.0
_MISTAKE_THRESHOLD = 20.0
_INACCURACY_THRESHOLD = 10.0


def win_percent(cp: int) -> float:
    """Centipawns (White POV) -> expected win percentage for White (0-100)."""
    return 50 + 50 * (2 / (1 + math.exp(_WIN_PERCENT_K * cp)) - 1)


def mate_to_cp_equivalent(mate_n: int) -> int:
    """Approximates a forced-mate score as an extreme centipawn value so it
    can still be fed through win_percent() -- the true win% at a forced
    mate is 100/0, but an extreme finite value keeps the same monotonic
    "further/faster mate is more decisive" ordering without the formula
    blowing up. Not a reproduction of lichess's own internal mate handling,
    just a reasonable approximation for this app's purposes."""
    sign = 1 if mate_n > 0 else -1
    return sign * (1000 - min(abs(mate_n), 10) * 50)


def _effective_cp(score_cp: int | None, score_mate: int | None) -> int | None:
    if score_mate is not None:
        return mate_to_cp_equivalent(score_mate)
    return score_cp


def classify_delta(
    before_cp: int | None, before_mate: int | None,
    after_cp: int | None, after_mate: int | None,
    mover_is_white: bool,
) -> str | None:
    """Classifies the move that changed the position from (before) to
    (after), both given as White-POV cp/mate pairs (one of cp/mate should
    be set, not both). Returns "blunder"/"mistake"/"inaccuracy"/None,
    based on the drop in the MOVER's own win probability."""
    before = _effective_cp(before_cp, before_mate)
    after = _effective_cp(after_cp, after_mate)
    if before is None or after is None:
        return None

    before_white_pct = win_percent(before)
    after_white_pct = win_percent(after)
    if mover_is_white:
        drop = before_white_pct - after_white_pct
    else:
        # Mirror to the mover's own perspective: their win% is 100 - White's.
        drop = (100 - before_white_pct) - (100 - after_white_pct)

    if drop >= _BLUNDER_THRESHOLD:
        return "blunder"
    if drop >= _MISTAKE_THRESHOLD:
        return "mistake"
    if drop >= _INACCURACY_THRESHOLD:
        return "inaccuracy"
    return None
