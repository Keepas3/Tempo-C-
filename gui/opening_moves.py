"""Opening name -> defining move sequence lookup, for the /stats expandable
rows. Generated from lichess-org/chess-openings (a.tsv..e.tsv, MIT licensed):
for each unique opening name, the shortest (most general) line carrying that
exact name is kept as its "defining" move order -- the minimal sequence that
first reaches a position classified under that name, mirroring how ECO
codes themselves work.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "data" / "eco_openings.json"


@lru_cache(maxsize=1)
def _table() -> dict:
    try:
        with open(DATA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def get_moves(opening_name: str) -> str | None:
    """Returns the defining move sequence (e.g. "1. e4 e5 2. Nf3 Nc6 3. d4")
    for an opening name, or None if not found."""
    entry = _table().get(opening_name)
    return entry["moves"] if entry else None
