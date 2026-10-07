"""Thin subprocess wrapper around `tempo.exe --json <command> [args...]`.

Everything with real query logic behind it (opening matching, time-control
bucketing, move-prefix search, win/loss relative to color) goes through this
instead of being re-derived in Python, so there's exactly one place that
logic lives.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPO_EXE = PROJECT_ROOT / "tempo.exe"


class TempoCliError(Exception):
    pass


class FetchCancelled(TempoCliError):
    pass


PROGRESS_PREFIX = "TEMPO_PROGRESS\t"


def _parse_progress(line: str) -> tuple[int, int, int, str] | None:
    """"TEMPO_PROGRESS<TAB>done<TAB>total<TAB>added<TAB>label" ->
    (done, total, added, label); `total` of 0 means "unknown"."""
    if not line.startswith(PROGRESS_PREFIX):
        return None
    parts = line.rstrip("\r\n").split("\t", 4)
    if len(parts) != 5:
        return None
    try:
        return int(parts[1]), int(parts[2]), int(parts[3]), parts[4]
    except ValueError:
        return None


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

    def run_streaming(
        self, *args: str,
        on_progress: Callable[[int, int, int, str], None] | None = None,
        cancel_event: threading.Event | None = None,
        timeout: int = 600,
    ) -> dict:
        """Like run(), but reads tempo.exe's output live so progress lines
        (see main.cpp's emit_progress) reach `on_progress` as they happen
        instead of only after the process exits. stderr is merged into
        stdout (one pipe -- no risk of deadlocking on a full second pipe);
        the final JSON document is the last line that parses as one. Setting
        `cancel_event` kills the process and raises FetchCancelled."""
        if not TEMPO_EXE.exists():
            raise TempoCliError(f"tempo.exe not found at {TEMPO_EXE} (build it first)")

        proc = subprocess.Popen(
            [str(TEMPO_EXE), "--db", str(self.db_path), "--user", self.username, "--json", *args],
            cwd=PROJECT_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        deadline = time.monotonic() + timeout
        finished = threading.Event()
        timed_out = False

        def watchdog() -> None:
            nonlocal timed_out
            while not finished.wait(0.2):
                if cancel_event is not None and cancel_event.is_set():
                    proc.kill()
                    return
                if time.monotonic() > deadline:
                    timed_out = True
                    proc.kill()
                    return

        threading.Thread(target=watchdog, daemon=True).start()
        result_line = None
        other_output: list[str] = []
        try:
            for line in proc.stdout:
                progress = _parse_progress(line)
                if progress is not None:
                    if on_progress is not None:
                        on_progress(*progress)
                elif line.strip().startswith("{"):
                    result_line = line.strip()
                elif line.strip():
                    other_output.append(line.strip())
            proc.wait()
        finally:
            finished.set()
            if proc.poll() is None:
                proc.kill()

        if cancel_event is not None and cancel_event.is_set():
            raise FetchCancelled("cancelled")
        if timed_out:
            raise TempoCliError("tempo.exe timed out")
        if result_line is None:
            raise TempoCliError(" ".join(other_output) or "tempo.exe produced no output")
        try:
            data = json.loads(result_line)
        except json.JSONDecodeError as e:
            raise TempoCliError(f"could not parse tempo.exe output: {result_line!r}") from e
        if isinstance(data, dict) and "error" in data:
            raise TempoCliError(data["error"])
        return data

    def stats(self, *category_filter: str) -> dict:
        return self.run("stats", *category_filter)

    def rating(self, *category_filter: str) -> dict:
        return self.run("rating", *category_filter)

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

    def show(self, game_id: int) -> dict:
        return self.run("show", str(game_id))

    def review(self, game_id: int) -> dict:
        return self.run("review", str(game_id))

    def fetch_chesscom(self, username: str, full: bool = False, on_progress=None, cancel_event=None) -> dict:
        args = ["fetch", "chesscom", username] + (["full"] if full else [])
        if on_progress is not None or cancel_event is not None:
            return self.run_streaming(*args, on_progress=on_progress, cancel_event=cancel_event, timeout=1800 if full else 300)
        return self.run(*args, timeout=600 if full else 90)

    def fetch_lichess(self, username: str, full: bool = False, on_progress=None, cancel_event=None) -> dict:
        args = ["fetch", "lichess", username] + (["full"] if full else [])
        if on_progress is not None or cancel_event is not None:
            return self.run_streaming(*args, on_progress=on_progress, cancel_event=cancel_event, timeout=1800 if full else 300)
        return self.run(*args, timeout=600 if full else 90)

    def last_fetch_status(self) -> dict:
        return self.run("last_fetch")
