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
from favorites import Favorites
from notes import Notes


@dataclass
class GameSearchFilter:
    opponent: str | None = None       # substring match, case-insensitive
    color: str | None = None          # "white" | "black"
    result: str | None = None         # "Win" | "Loss" | "Draw"
    opening: str | None = None        # substring match, case-insensitive
    date_from: str | None = None      # "YYYY.MM.DD", inclusive
    date_to: str | None = None        # "YYYY.MM.DD", inclusive
    time_category: str | None = None  # "Bullet" | "Blitz" | "Rapid" | "Classical" | "Daily"
    text: str | None = None           # free-text search, case-insensitive: matches opponent, opening, OR the game's own note
    favorites_only: bool = False      # only starred games (needs search_games' `favorites` set)
    limit: int = 25


def _matches(row: GameRow, filt: GameSearchFilter, game_notes: dict[int, str] | None = None,
             favorite_ids: set[int] | None = None) -> bool:
    if filt.favorites_only and row.id not in (favorite_ids or ()):
        return False
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
    if filt.text:
        needle = filt.text.lower()
        note_text = (game_notes or {}).get(row.id, "")
        if (needle not in row.opponent.lower() and needle not in row.opening.lower()
                and needle not in note_text.lower()):
            return False
    return True


def search_games(db: DbReader, filt: GameSearchFilter, notes: Notes | None = None,
                 favorites: Favorites | None = None) -> tuple[list[GameRow], int]:
    """Returns (matching rows capped at filt.limit, total_matches_before_cap),
    newest first. The caller can compare len(rows) to total_matches to know
    whether the result was truncated. `notes`, if given, makes the `text`
    filter also match against each game's own note (one query up front for
    all of them, rather than one per game)."""
    tree = db.games_by_year_month()
    game_notes = notes.get_all_game_notes() if notes else {}
    favorite_ids = favorites.get_all() if (favorites and filt.favorites_only) else None
    matches: list[GameRow] = []
    for year in sorted(tree, reverse=True):
        for month in sorted(tree[year], reverse=True):
            for row in tree[year][month]:
                if _matches(row, filt, game_notes, favorite_ids):
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
