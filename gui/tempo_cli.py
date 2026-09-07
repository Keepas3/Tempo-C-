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


def run(*args: str, timeout: int = 30) -> dict:
    """Runs `tempo.exe --json <args...>`, returns the parsed JSON object.
    Raises TempoCliError on any failure (missing exe, bad exit, bad JSON,
    or an {"error": ...} response)."""
    if not TEMPO_EXE.exists():
        raise TempoCliError(f"tempo.exe not found at {TEMPO_EXE} (build it first)")

    try:
        proc = subprocess.run(
            [str(TEMPO_EXE), "--json", *args],
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


def stats() -> dict:
    return run("stats")


def opening(query: str) -> dict:
    return run("opening", query)


def opening_exact(name: str, limit: int = 3) -> dict:
    return run("opening_exact", name, str(limit))


def moves(sequence: list[str]) -> dict:
    return run("moves", *sequence)


def list_games(limit: int = 20) -> dict:
    return run("list", str(limit))


def show(game_id: int) -> dict:
    return run("show", str(game_id))


def review(game_id: int) -> dict:
    return run("review", str(game_id))


def fetch_chesscom(username: str, year: int | None = None, month: int | None = None) -> dict:
    args = ["fetch", "chesscom", username]
    if year is not None and month is not None:
        args += [str(year), str(month)]
    return run(*args, timeout=90)


def fetch_lichess(username: str, days: int = 90) -> dict:
    return run("fetch", "lichess", username, str(days), timeout=90)
