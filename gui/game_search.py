"""Multi-criteria game search over the archive: opponent/color/result/
opening/date-range/time-category, all filtered together. Nothing today
answers "find my games as black against the Sicilian where I lost" --
db_reader.py's own docstring scopes it to schema-mirroring only ("no
business logic lives here"), so this lives in its own module rather than
growing DbReader past that stated boundary.

Filters in plain Python over DbReader.games_by_year_month()'s already-
loaded rows (a few hundred lightweight GameRow objects for a personal
archive -- trivial in memory) rather than adding new SQL, since the result
only ever needs to be capped and sorted, not indexed.
"""
from __future__ import annotations

from dataclasses import dataclass

from db_reader import DbReader, GameRow


@dataclass
class GameSearchFilter:
    opponent: str | None = None       # substring match, case-insensitive
    color: str | None = None          # "white" | "black"
    result: str | None = None         # "Win" | "Loss" | "Draw"
    opening: str | None = None        # substring match, case-insensitive
    date_from: str | None = None      # "YYYY.MM.DD", inclusive
    date_to: str | None = None        # "YYYY.MM.DD", inclusive
    time_category: str | None = None  # "Bullet" | "Blitz" | "Rapid" | "Classical" | "Daily"
    limit: int = 25


def _matches(row: GameRow, filt: GameSearchFilter) -> bool:
    if filt.opponent and filt.opponent.lower() not in row.opponent.lower():
        return False
    if filt.color and row.your_color != filt.color:
        return False
    if filt.result and row.result != filt.result:
        return False
    if filt.opening and filt.opening.lower() not in row.opening.lower():
        return False
    if filt.date_from and row.date < filt.date_from:
        return False
    if filt.date_to and row.date > filt.date_to:
        return False
    if filt.time_category and row.time_category != filt.time_category:
        return False
    return True


def search_games(db: DbReader, filt: GameSearchFilter) -> tuple[list[GameRow], int]:
    """Returns (matching rows capped at filt.limit, total_matches_before_cap),
    newest first. The caller can compare len(rows) to total_matches to know
    whether the result was truncated."""
    tree = db.games_by_year_month()
    matches: list[GameRow] = []
    for year in sorted(tree, reverse=True):
        for month in sorted(tree[year], reverse=True):
            for row in tree[year][month]:
                if _matches(row, filt):
                    matches.append(row)
    total = len(matches)
    return matches[: filt.limit], total


def game_row_to_compact_dict(row: GameRow) -> dict:
    """id/date/opponent/result/opening/time_category only -- no movetext,
    keeps search results cheap regardless of how many are returned."""
    return {
        "id": row.id,
        "date": row.date,
        "opponent": row.opponent,
        "your_color": row.your_color,
        "result": row.result,
        "opening": row.opening,
        "time_category": row.time_category,
    }
