"""Thin subprocess wrapper around `tempo.exe --json <command> [args...]`.

Everything with real query logic behind it (opening matching, time-control
bucketing, move-prefix search, win/loss relative to color) goes through this
instead of being re-derived in Python, so there's exactly one place that
logic lives.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPO_EXE = PROJECT_ROOT / "tempo.exe"


class TempoCliError(Exception):
    pass


class TempoCli:
    """Bound to one profile's db file + username -- each profile tab
    constructs its own instance so simultaneous profiles never share (or
    race on) a single db path/username. `username` is threaded through as
    `--user` on every call, so games imported/fetched via this instance are
    tagged with the correct win/loss perspective for that profile."""

    def __init__(self, db_path: Path, username: str) -> None:
        self.db_path = db_path
        self.username = username

    def run(self, *args: str, timeout: int = 30) -> dict:
        """Runs `tempo.exe --db <self.db_path> --user <self.username> --json
        <args...>`, returns the parsed JSON object. Raises TempoCliError on
        any failure (missing exe, bad exit, bad JSON, or an {"error": ...}
        response)."""
        if not TEMPO_EXE.exists():
            raise TempoCliError(f"tempo.exe not found at {TEMPO_EXE} (build it first)")

        try:
            proc = subprocess.run(
                [str(TEMPO_EXE), "--db", str(self.db_path), "--user", self.username, "--json", *args],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise TempoCliError("tempo.exe timed out") from e

        output = proc.stdout.strip()
        if not output:
            raise TempoCliError(proc.stderr.strip() or "tempo.exe produced no output")

        try:
            data = json.loads(output)
        except json.JSONDecodeError as e:
            raise TempoCliError(f"could not parse tempo.exe output: {output!r}") from e

        if isinstance(data, dict) and "error" in data:
            raise TempoCliError(data["error"])

        return data

    def stats(self, *category_filter: str) -> dict:
        return self.run("stats", *category_filter)

    def opening(self, query: str, range_token: str = "all") -> dict:
        return self.run("opening", query, range_token)

    def opening_exact(self, name: str, limit: int = 3, range_token: str = "all") -> dict:
        return self.run("opening_exact", name, str(limit), range_token)

    def moves(self, sequence: list[str]) -> dict:
        return self.run("moves", *sequence)

    def explorer(self, color: str, sequence: list[str]) -> dict:
        return self.run("explorer", color, *sequence)

    def games_by_move_prefix(self, sequence: list[str]) -> dict:
        return self.run("moves_games", *sequence)

    def list_games(self, limit: int = 20) -> dict:
        return self.run("list", str(limit))

    def show(self, game_id: int) -> dict:
        return self.run("show", str(game_id))

    def review(self, game_id: int) -> dict:
        return self.run("review", str(game_id))

    def fetch_chesscom(self, username: str, year: int | None = None, month: int | None = None) -> dict:
        args = ["fetch", "chesscom", username]
        if year is not None and month is not None:
            args += [str(year), str(month)]
        return self.run(*args, timeout=90)

    def fetch_lichess(self, username: str, days: int | None = None) -> dict:
        args = ["fetch", "lichess", username]
        if days is not None:
            args.append(str(days))
        return self.run(*args, timeout=90)

    def last_fetch_status(self) -> dict:
        return self.run("last_fetch")
