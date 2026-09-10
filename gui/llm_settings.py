"""Availability check + tunable constants for the Claude chat-panel
assistant. The API key is read from the environment only (ANTHROPIC_API_KEY)
and never written to disk by this app -- unlike engine.py's engine.json or
profiles.py's profiles.json, both of which store non-secret settings and so
follow this app's usual plaintext-JSON-in-gui/data/ convention, a real
secret doesn't.
"""
from __future__ import annotations

import os

MODEL_ID = "claude-sonnet-5"

# Tool-loop guardrails -- see llm_tools.py/llm_worker.py. Kept together here
# so every cap the assistant is bound by is visible in one place.
MAX_TOOL_ITERATIONS = 8
MAX_OUTPUT_TOKENS = 2048
MAX_SEARCH_RESULTS = 25
MAX_BATCH_ANALYSIS_GAMES = 6
MAX_COVERAGE_CHECK_GAMES = 10
MAX_SUMMARIZE_GAMES = 20
MAX_BOOKMARKS = 20


def is_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")
