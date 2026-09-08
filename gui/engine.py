"""Stockfish engine lifecycle: a global (shared across all profile tabs)
record of where the downloaded binary lives, plus per-tab management of
the actual running engine subprocess(es).

Global, not per-profile -- one download benefits every open tab, mirroring
how gui/data/profiles.json itself is already a single shared file. Each
profile tab still gets its own live-eval subprocess at runtime (see
EngineManager below); sharing the binary/settings doesn't compromise the
app's per-tab isolation, since isolation is about live processes and
per-profile cached analysis data, not the static executable.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import chess
import chess.engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = PROJECT_ROOT / "gui" / "engine"
ENGINE_EXE_PATH = ENGINE_DIR / "stockfish.exe"
ENGINE_SETTINGS_PATH = PROJECT_ROOT / "gui" / "data" / "engine.json"

ENGINE_ID = "stockfish"

LIVE_EVAL_TIERS = [("Fast", 14), ("Balanced", 16), ("Deep", 18)]
BATCH_SETTING_KEY = "depth18"
BATCH_DEPTH = 18


def _load_settings() -> dict | None:
    if not ENGINE_SETTINGS_PATH.exists():
        return None
    try:
        return json.loads(ENGINE_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_settings(exe_path: Path, release_tag: str, engine_version: str) -> None:
    ENGINE_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "exe_path": str(exe_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "release_tag": release_tag,
        "engine_version": engine_version,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    ENGINE_SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_available() -> bool:
    """Cheap check only -- file-exists, no subprocess spawn. Called on
    every /review and every live-eval interaction, so it must be instant."""
    settings = _load_settings()
    if settings is None:
        return False
    exe_path = PROJECT_ROOT / settings.get("exe_path", "")
    return exe_path.exists()


def engine_version() -> str | None:
    settings = _load_settings()
    return settings.get("engine_version") if settings else None


def resolved_exe_path() -> Path | None:
    settings = _load_settings()
    if settings is None:
        return None
    exe_path = PROJECT_ROOT / settings.get("exe_path", "")
    return exe_path if exe_path.exists() else None


def _is_alive(engine: chess.engine.SimpleEngine) -> bool:
    # SimpleEngine.returncode is a concurrent.futures.Future that resolves
    # once the underlying process has exited -- .done() is a cheap,
    # non-blocking way to check liveness without touching the pipe itself.
    return not engine.returncode.done()


class EngineManager:
    """Owns this profile tab's own engine subprocess(es). Two separate
    engines are used rather than one shared instance: chess.engine.
    SimpleEngine isn't safe for concurrent calls, and the live-eval engine
    (fast response, low thread count) and a batch-analysis engine (thorough,
    most-of-cores) legitimately want different option configs anyway."""

    def __init__(self) -> None:
        self._live_engine: chess.engine.SimpleEngine | None = None

    def ensure_live_engine(self) -> chess.engine.SimpleEngine:
        if self._live_engine is not None and not _is_alive(self._live_engine):
            # The process died unexpectedly (killed by antivirus flagging a
            # freshly-downloaded unsigned exe, OOM-killed, crashed, etc.) --
            # discard the dead handle instead of reusing it forever.
            # Repeatedly calling into a dead engine's pipe through
            # chess.engine's own background thread/event loop is exactly the
            # kind of thing that can destabilize the app rather than fail
            # cleanly, so this check runs before every use, not just once.
            self._live_engine = None
        if self._live_engine is None:
            exe_path = resolved_exe_path()
            if exe_path is None:
                raise RuntimeError("Stockfish is not installed")
            live_engine = chess.engine.SimpleEngine.popen_uci(str(exe_path))
            # Stockfish's own defaults (1 thread, 16MB hash) rather than the
            # earlier heavier config -- threading barely helps at the short
            # per-position budgets live eval uses, and keeping the resident
            # footprint small matters more here since this engine, unlike
            # the batch one, stays alive for as long as live eval is on.
            live_engine.configure({"Threads": 1, "Hash": 16})
            self._live_engine = live_engine
        return self._live_engine

    def spawn_batch_engine(self) -> chess.engine.SimpleEngine:
        """A fresh, ephemeral engine for one batch analysis run -- the
        caller is responsible for calling .quit() on it when done."""
        exe_path = resolved_exe_path()
        if exe_path is None:
            raise RuntimeError("Stockfish is not installed")
        engine = chess.engine.SimpleEngine.popen_uci(str(exe_path))
        threads = max(1, (os.cpu_count() or 2) - 1)
        engine.configure({"Threads": threads, "Hash": 256})
        return engine

    def quit_all(self) -> None:
        if self._live_engine is not None:
            try:
                self._live_engine.quit()
            except Exception:
                # Never let a shutdown-time engine error (dead process,
                # already-closed pipe, etc.) block app close or propagate
                # as an unhandled exception during teardown.
                pass
            self._live_engine = None
