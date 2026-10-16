"""Availability check + tunable constants for the Claude chat-panel
assistant. The API key comes from the ANTHROPIC_API_KEY environment variable,
or failing that a one-line `.env` file (gui/.env or the project root's .env,
both gitignored) -- read only, never written by this app. Unlike engine.py's
engine.json or profiles.py's profiles.json, which store non-secret settings
and so follow the plaintext-JSON-in-gui/data/ convention, a real secret is
never saved by the app itself.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_KEY_NAME = "ANTHROPIC_API_KEY"
_ENV_FILES = (Path(__file__).resolve().parent / ".env", Path(__file__).resolve().parent.parent / ".env")

MODEL_ID = "claude-sonnet-5"
FAST_MODEL_ID = "claude-haiku-4-5-20251001"


@dataclass(frozen=True)
class AiTier:
    """One speed/detail preset for the assistant: how deep Stockfish searches,
    how many games one weakness question may analyze, which Claude model
    answers, and the knobs that bound token use."""
    key: str
    label: str
    depth: int            # Stockfish search depth for any analysis the assistant runs
    max_games: int        # games analyze_weaknesses may cover in one question
    model: str
    effort: str | None    # Claude "effort" level; None = don't send (Haiku doesn't take it)
    max_output_tokens: int
    history_messages: int  # past chat messages re-sent with every question (even number)
    max_tool_iterations: int
    blurb: str


# Depths are tuned from measured timings (about 8 parallel engines on one game of ~90 plies):
# depth 12 ~ 3s, 15 ~ 8s, 18 ~ 19s per game.
AI_TIERS: dict[str, AiTier] = {
    "fast": AiTier("fast", "Fast", depth=12, max_games=6, model=FAST_MODEL_ID, effort=None,
                   max_output_tokens=1024, history_messages=6, max_tool_iterations=5,
                   blurb="quickest and cheapest: shallow analysis, up to 6 games, lighter Claude model"),
    "balanced": AiTier("balanced", "Balanced", depth=15, max_games=10, model=MODEL_ID, effort="low",
                       max_output_tokens=1536, history_messages=10, max_tool_iterations=8,
                       blurb="good default: moderate depth, up to 10 games"),
    "detailed": AiTier("detailed", "Detailed", depth=18, max_games=16, model=MODEL_ID, effort="medium",
                       max_output_tokens=2048, history_messages=20, max_tool_iterations=8,
                       blurb="deepest analysis, up to 16 games, most thorough answers (slowest, most tokens)"),
}
DEFAULT_TIER = "balanced"
_SETTINGS_PATH = Path(__file__).resolve().parent / "data" / "llm.json"


def load_tier() -> AiTier:
    """The saved speed/detail choice (a non-secret preference in gui/data/llm.json)."""
    try:
        key = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8")).get("tier", DEFAULT_TIER)
    except (OSError, ValueError, AttributeError):
        key = DEFAULT_TIER
    return AI_TIERS.get(key, AI_TIERS[DEFAULT_TIER])


def save_tier(key: str) -> None:
    if key not in AI_TIERS:
        return
    try:
        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_PATH.write_text(json.dumps({"tier": key}), encoding="utf-8")
    except OSError:
        pass  # a preference that fails to save just resets next launch


# Per-tool guardrails -- see llm_tools.py. (Loop length and output size are per speed tier, above.)
MAX_SEARCH_RESULTS = 25
MAX_BATCH_ANALYSIS_GAMES = 6
MAX_COVERAGE_CHECK_GAMES = 10
MAX_SUMMARIZE_GAMES = 20
MAX_BOOKMARKS = 20
MAX_SCOUT_OPENINGS = 8   # per color, in scout_profile's reply


def _key_from_env_files() -> str | None:
    """First ANTHROPIC_API_KEY=... found in gui/.env, then the project root's
    .env. Tolerates spaces around `=`, an optional `export `, surrounding
    quotes, a UTF-8 BOM and comment lines; nothing else is parsed."""
    for path in _ENV_FILES:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            name, sep, value = line.partition("=")
            if sep and name.strip() == _KEY_NAME:
                value = value.strip().strip("\"'").strip()
                if value:
                    return value
    return None


def api_key() -> str | None:
    """The real environment variable wins; a .env file is the fallback."""
    return os.environ.get(_KEY_NAME) or _key_from_env_files()


def is_available() -> bool:
    return bool(api_key())
