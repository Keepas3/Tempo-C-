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

# Lichess's public per-move accuracy formula (AccuracyPercent.scala) --
# chess.com has never published theirs, so this (already-open, and already
# the source of _WIN_PERCENT_K above) is what "accuracy %" means here.
_ACCURACY_A = 103.1668
_ACCURACY_B = -0.04354
_ACCURACY_C = -3.1669

# Game-level accuracy aggregation (also lichess's public algorithm): a
# color's per-move accuracies are combined via a blend of a volatility-
# weighted mean and a harmonic mean, rather than a plain average -- the
# harmonic mean is what keeps one real blunder from being diluted away by
# a long run of otherwise-fine moves.
_MIN_WINDOW = 2
_MAX_WINDOW = 8
_MIN_VOLATILITY = 0.5
_MAX_VOLATILITY = 12.0


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


def move_accuracy_percent(win_percent_before: float, win_percent_after: float) -> float:
    """Lichess's per-move accuracy formula. Both args are already in the
    MOVER's own win% POV (not White's) -- see move_accuracy_from_cp, which
    does that mirroring before calling this. Clamped to [0, 100]."""
    drop = win_percent_before - win_percent_after
    accuracy = _ACCURACY_A * math.exp(_ACCURACY_B * drop) + _ACCURACY_C
    return max(0.0, min(100.0, accuracy))


def move_accuracy_from_cp(
    before_cp: int | None, before_mate: int | None,
    after_cp: int | None, after_mate: int | None,
    mover_is_white: bool,
) -> tuple[float, float] | None:
    """Same shape as classify_delta (White-POV cp/mate pairs in, mover-POV
    mirroring handled here) but for accuracy instead of severity. Returns
    (mover's own before-move win%, this move's accuracy%), or None if
    either side's eval is missing. The win% is returned alongside the
    accuracy because game_accuracy() needs the raw before-move win%
    trajectory (not just the accuracy scores) to compute each window's
    volatility."""
    before = _effective_cp(before_cp, before_mate)
    after = _effective_cp(after_cp, after_mate)
    if before is None or after is None:
        return None

    before_white_pct = win_percent(before)
    after_white_pct = win_percent(after)
    if mover_is_white:
        before_pct, after_pct = before_white_pct, after_white_pct
    else:
        before_pct, after_pct = 100 - before_white_pct, 100 - after_white_pct

    return before_pct, move_accuracy_percent(before_pct, after_pct)


def game_accuracy(win_percents: list[float], accuracies: list[float]) -> float | None:
    """Lichess-style overall accuracy for one color across a game: the
    average of a volatility-weighted mean and a harmonic mean of that
    color's per-move accuracy scores. `win_percents`/`accuracies` are
    parallel, one entry per move that color made, in game order --
    win_percents[i] is that color's own win% in the position BEFORE their
    i-th move (the same `before_pct` move_accuracy_from_cp returns for
    that move). Returns None if the color made no moves.

    The windowing here (same-size window for every move, clamped to stay
    inside the list) is a principled approximation of lila's own edge-case
    handling for the first few moves before a full window exists, not a
    byte-for-byte reproduction of its Scala implementation -- the formula
    and the window/volatility clamp constants are what's faithful to the
    public algorithm."""
    n = len(accuracies)
    if n == 0:
        return None

    window = max(_MIN_WINDOW, min(_MAX_WINDOW, n // 10))
    weights: list[float] = []
    for i in range(n):
        start = max(0, min(i, n - window)) if n >= window else 0
        end = start + window if n >= window else n
        segment = win_percents[start:end]
        mean = sum(segment) / len(segment)
        variance = sum((x - mean) ** 2 for x in segment) / len(segment)
        stdev = math.sqrt(variance)
        weights.append(max(_MIN_VOLATILITY, min(_MAX_VOLATILITY, stdev)))

    weighted_mean = sum(a * w for a, w in zip(accuracies, weights)) / sum(weights)
    # Harmonic mean: floor each term just above zero so a single 0%-
    # accuracy move can't divide by zero -- consistent with
    # move_accuracy_percent's own [0, 100] clamp.
    harmonic_mean = n / sum(1.0 / max(a, 1e-6) for a in accuracies)

    return (weighted_mean + harmonic_mean) / 2
