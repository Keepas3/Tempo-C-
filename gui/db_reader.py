"""Read-only direct SQLite access for simple schema-level data: the game
browser listing (grouped by year/month) and loading one game's moves. No
business logic lives here (opening matching, time-control rules, etc. all
stay in the C++ side and are reached via tempo_cli.py) -- this module only
mirrors what Archive::list_games/load_game already do.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "tempo_archive.db"


@dataclass
class GameRow:
    id: int
    date: str
    year: int
    month: int
    white: str
    black: str
    your_color: str
    result: str          # "Win" / "Loss" / "Draw" / "?", relative to your_color
    opening: str
    site: str
    time_label: str       # e.g. "2 min+1", "5 min", "Daily"
    time_category: str    # e.g. "Bullet", "Blitz", "Rapid", "Classical", "Daily", "Unknown"
    time_seconds: int     # base time in seconds, for numeric sorting (see classify_time_control)

    @property
    def opponent(self) -> str:
        return self.black if self.your_color == "white" else self.white


def _platform_label(site: str) -> str:
    # Mirrors db.h's platform_label(): chess.com's Site tag is already clean
    # ("Chess.com"), but lichess's is a per-game URL, so normalize both to a
    # short, consistent badge.
    lower = (site or "").lower()
    if "chess.com" in lower:
        return "Chess.com"
    if "lichess" in lower:
        return "Lichess"
    return site or "Unknown"


def result_relative_to(result: str, your_color: str) -> str:
    """Mirrors db.h's result_relative_to(): maps a raw PGN result ("1-0",
    "0-1", "1/2-1/2") to "Win"/"Loss"/"Draw" from your side of the board."""
    if result == "1/2-1/2":
        return "Draw"
    if your_color not in ("white", "black"):
        return "?"
    white_won = result == "1-0"
    black_won = result == "0-1"
    if not white_won and not black_won:
        return "?"
    you_won = white_won if your_color == "white" else black_won
    return "Win" if you_won else "Loss"


# Sentinel base-seconds values for categories with no single numeric
# duration, used only for sorting the Time column: Daily sorts as "longest"
# (it's the slowest format in practice), Unknown sorts last regardless of
# direction by living beyond every real value.
DAILY_SORT_SECONDS = 10**8
UNKNOWN_SORT_SECONDS = -1


def classify_time_control(tc: str) -> tuple[str, str, int]:
    """Mirrors db.h's classify_time_control() bucketing. Returns
    (time_label, category, sort_seconds), e.g. ("5 min", "Blitz", 300),
    ("3 min+2", "Blitz", 180), ("", "Daily", DAILY_SORT_SECONDS)."""
    if not tc:
        return "", "Unknown", UNKNOWN_SORT_SECONDS
    if "/" in tc:
        return "", "Daily", DAILY_SORT_SECONDS

    base_str, _, inc_str = tc.partition("+")
    try:
        base_seconds = int(base_str)
    except ValueError:
        return "", "Unknown", UNKNOWN_SORT_SECONDS

    if base_seconds < 180:
        category = "Bullet"
    elif base_seconds < 600:
        category = "Blitz"
    elif base_seconds < 1800:
        category = "Rapid"
    else:
        category = "Classical"

    if base_seconds % 60 == 0:
        time_label = f"{base_seconds // 60} min"
    else:
        time_label = f"{base_seconds}s"
    if inc_str:
        time_label += f"+{inc_str}"

    return time_label, category, base_seconds


@dataclass
class MoveRow:
    san: str
    clock_seconds: int | None


@dataclass
class GameDetail:
    id: int
    event: str
    site: str
    date: str
    white: str
    black: str
    result: str
    eco: str
    opening: str
    time_control: str
    your_color: str
    moves: list[MoveRow] = field(default_factory=list)


def _connect() -> sqlite3.Connection:
    # Read-only: the GUI never writes to the games/moves tables (that's the
    # CLI's job via import/fetch); "mode=ro" makes that explicit and safe
    # even if the CLI is writing concurrently.
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def games_by_year_month() -> dict[int, dict[int, list[GameRow]]]:
    """Returns {year: {month: [GameRow, ...]}}, newest first within each month."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT id, date, white, black, your_color, result, opening, site, time_control "
            "FROM games ORDER BY date DESC, id DESC;"
        ).fetchall()
    finally:
        conn.close()

    tree: dict[int, dict[int, list[GameRow]]] = {}
    for id_, date, white, black, your_color, result, opening, site, time_control in rows:
        year, month = _parse_year_month(date)
        time_label, time_category, time_seconds = classify_time_control(time_control)
        game = GameRow(
            id_, date, year, month, white, black, your_color,
            result_relative_to(result, your_color), opening,
            _platform_label(site), time_label, time_category, time_seconds,
        )
        tree.setdefault(year, {}).setdefault(month, []).append(game)
    return tree


def load_game(game_id: int) -> GameDetail | None:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, event, site, date, white, black, result, eco, opening, time_control, your_color "
            "FROM games WHERE id = ?;",
            (game_id,),
        ).fetchone()
        if row is None:
            return None
        detail = GameDetail(*row)
        detail.site = _platform_label(detail.site)

        move_rows = conn.execute(
            "SELECT san, clock_seconds FROM moves WHERE game_id = ? ORDER BY ply;",
            (game_id,),
        ).fetchall()
        detail.moves = [MoveRow(san, clock) for san, clock in move_rows]
        return detail
    finally:
        conn.close()


def list_opening_names() -> list[tuple[str, int]]:
    """Every distinct opening name actually present in the archive, with how
    many games carry it, most-played first. Simple DISTINCT+COUNT -- no
    opening-matching heuristics involved, so this stays a direct read here
    rather than going through tempo_cli.py."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT opening, COUNT(*) as n FROM games WHERE opening != '' "
            "GROUP BY opening ORDER BY n DESC, opening ASC;"
        ).fetchall()
        return [(opening, count) for opening, count in rows]
    finally:
        conn.close()


def _parse_year_month(pgn_date: str) -> tuple[int, int]:
    try:
        year_str, month_str, _day_str = pgn_date.split(".")
        return int(year_str), int(month_str)
    except (ValueError, AttributeError):
        return 0, 0  # "Unknown" bucket, sorts first
