"""Claude tool-use surface for the chat panel's LLM assistant: tool schemas,
the system prompt, and a ToolExecutor that dispatches tool calls into the
existing data layer (TempoCli, DbReader, AnalysisCache, Bookmarks,
EngineManager) plus one new piece (game_search.py).

Every tool returns a small, pre-aggregated dict -- aggregation happens here
in Python, results are hard-capped, and nothing dumps a raw per-ply array
or a bulk game listing into the model's context. That discipline (narrow
first with cheap metadata tools, only then spend on movetext/engine time)
is enforced two ways: the SYSTEM_PROMPT asks for it, and ToolExecutor
enforces the hard caps itself regardless of what the model requests, since
a schema's `maxItems` alone is only a hint the model could ignore.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

import chess
import chess.engine

import llm_settings
from analysis_cache import AnalysisCache
from bookmarks import Bookmarks
import engine as engine_module
from engine import BATCH_DEPTH, BATCH_SETTING_KEY, ENGINE_ID, EngineManager
from engine_analysis_core import analyze_missing_plies
from db_reader import DbReader
from game_search import GameSearchFilter, game_row_to_compact_dict, search_games as _filter_games
from review_data import build_stockfish_review_payload
from tempo_cli import TempoCli, TempoCliError

SYSTEM_PROMPT = """You are the chat-panel assistant inside Tempo, a personal \
chess game archive app. You can answer general chess knowledge questions \
(openings theory, endgame technique, tactics, strategy) directly from what \
you know -- those need no tools at all.

For anything about the user's own games, stats, or playstyle, use the \
tools rather than guessing. Follow this discipline, in order:

1. Narrow first. Use get_archive_stats, search_games, and/or lookup_opening \
to find the small set of games actually relevant to the question (usually \
fewer than 10) before touching movetext or engine analysis. If a question \
is too broad to narrow cheaply (e.g. "find every mistake I've ever made"), \
ask a clarifying question instead (time control? a date range? a specific \
opening or opponent?) rather than trying to scan the whole archive.
2. Before running any Stockfish analysis, call check_analysis_coverage on \
your narrowed game list -- most of the archive has never been analyzed, \
and re-analyzing already-cached games wastes time for no benefit.
3. Only call run_batch_analysis on games that check_analysis_coverage \
showed are missing data, and only for games you've already established \
matter to the question. It's capped at 6 games per call by design -- pick \
the most relevant ones, don't try to analyze broadly.
4. Use summarize_cached_analysis to turn cached per-game analysis into \
blunder/mistake/inaccuracy patterns -- never ask for or reconstruct raw \
per-ply evaluation data yourself.
5. get_game_moves returns one game's full move list -- use it only for a \
specific game you already know matters (e.g. to quote a concrete line), \
never in a loop over many games.

Keep answers concise: light markdown (bold, short bullet lists) is fine, \
but summarize rather than dumping tables of raw numbers. If your analysis \
only covers some of the user's relevant games (because deeper coverage \
would need analyzing more than is practical), say so plainly rather than \
implying the answer is exhaustive."""


@dataclass
class ToolContext:
    db: DbReader
    cli: TempoCli
    cache: AnalysisCache
    engine: EngineManager
    bookmarks: Bookmarks
    get_current_fen: Callable[[], str]
    on_status: Callable[[str], None]


def _phase_for_ply(ply: int) -> str:
    # Simple ply-based bucketing (not material-based) -- good enough for a
    # "where do your mistakes cluster" summary without replaying every game
    # to count material at each position.
    if ply < 20:
        return "opening"
    if ply < 60:
        return "middlegame"
    return "endgame"


def _range_token(range_days: int | None) -> str:
    return f"{range_days}d" if range_days else "all"


def _error(message: str) -> dict:
    return {"_is_error": True, "error": message}


# --- Tool schemas ---------------------------------------------------------

_BASE_TOOLS: list[dict] = [
    {
        "name": "get_archive_stats",
        "description": "Aggregate win/loss/draw record and top openings, optionally filtered by game type and/or a recent date range. Cheap -- already computed server-side.",
        "input_schema": {
            "type": "object",
            "properties": {
                "game_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["bullet", "blitz", "rapid", "classical", "daily"]},
                    "description": "Restrict to these time-control categories. Omit for all.",
                },
                "range_days": {"type": "integer", "description": "Only include games from the last N days. Omit for the whole archive."},
            },
        },
    },
    {
        "name": "search_games",
        "description": "Find a small set of specific games matching criteria (opponent, color, result, opening, date range, time control). This is the primary tool for narrowing down to relevant games before deeper analysis -- always prefer this over scanning broadly.",
        "input_schema": {
            "type": "object",
            "properties": {
                "opponent": {"type": "string", "description": "Substring match on opponent's username."},
                "color": {"type": "string", "enum": ["white", "black"]},
                "result": {"type": "string", "enum": ["Win", "Loss", "Draw"]},
                "opening": {"type": "string", "description": "Substring match on opening name."},
                "date_from": {"type": "string", "description": "YYYY.MM.DD, inclusive."},
                "date_to": {"type": "string", "description": "YYYY.MM.DD, inclusive."},
                "time_category": {"type": "string", "enum": ["Bullet", "Blitz", "Rapid", "Classical", "Daily"]},
                "limit": {"type": "integer", "description": f"Max results (server-capped at {llm_settings.MAX_SEARCH_RESULTS} regardless of this value)."},
            },
        },
    },
    {
        "name": "lookup_opening",
        "description": "Win/loss record and matching games for an opening, by name or ECO code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Opening name (or substring) or ECO code."},
                "range_days": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_game_summary",
        "description": "One game's header info (players, date, result, opening, site) -- no movetext.",
        "input_schema": {
            "type": "object",
            "properties": {"game_id": {"type": "integer"}},
            "required": ["game_id"],
        },
    },
    {
        "name": "get_game_moves",
        "description": "One specific game's full move list (SAN). Only call this for a single game you've already determined matters -- never loop it over many games.",
        "input_schema": {
            "type": "object",
            "properties": {"game_id": {"type": "integer"}},
            "required": ["game_id"],
        },
    },
    {
        "name": "check_analysis_coverage",
        "description": f"Checks which of up to {llm_settings.MAX_COVERAGE_CHECK_GAMES} given game ids already have full Stockfish analysis cached -- no engine calls, just a cache lookup. Always call this before run_batch_analysis.",
        "input_schema": {
            "type": "object",
            "properties": {
                "game_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": llm_settings.MAX_COVERAGE_CHECK_GAMES},
            },
            "required": ["game_ids"],
        },
    },
    {
        "name": "run_batch_analysis",
        "description": f"Runs real Stockfish analysis on specific games not yet cached, storing per-ply results. Capped at {llm_settings.MAX_BATCH_ANALYSIS_GAMES} game ids per call -- rejected if exceeded. This spends real time/CPU, so only call it for games you've already narrowed to and confirmed (via check_analysis_coverage) are missing. Returns only per-game status, not eval numbers -- call summarize_cached_analysis afterward for the actual findings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "game_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": llm_settings.MAX_BATCH_ANALYSIS_GAMES},
            },
            "required": ["game_ids"],
        },
    },
    {
        "name": "summarize_cached_analysis",
        "description": f"Aggregates already-cached Stockfish analysis across up to {llm_settings.MAX_SUMMARIZE_GAMES} games into blunder/mistake/inaccuracy counts by game phase, plus a handful of concrete example moves. Only reads what's already cached -- never returns raw per-ply data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "game_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": llm_settings.MAX_SUMMARIZE_GAMES},
            },
            "required": ["game_ids"],
        },
    },
    {
        "name": "get_repertoire_stats",
        "description": "Win/loss/draw record for each reply played after a given move sequence (opening repertoire explorer), restricted to games where the user played the given color.",
        "input_schema": {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": ["white", "black"]},
                "sequence": {"type": "array", "items": {"type": "string"}, "description": "SAN moves so far, e.g. [\"e4\", \"e5\", \"Nf3\"]. Omit for the first move."},
            },
            "required": ["color"],
        },
    },
    {
        "name": "list_bookmarks",
        "description": "The user's saved bookmarked positions (FEN + note, optionally linked to a source game/ply).",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": f"Max results (server-capped at {llm_settings.MAX_BOOKMARKS})."}},
        },
    },
]

_BEST_MOVE_TOOL = {
    "name": "get_best_move_in_current_position",
    "description": "Runs Stockfish on whatever position is currently on the board right now (use when the user refers to \"this position\" / \"the board\" / \"here\"). No parameters -- always evaluates the live board.",
    "input_schema": {"type": "object", "properties": {}},
}


def build_tools() -> list[dict]:
    """Includes get_best_move_in_current_position only when Stockfish is
    actually installed, so the schema never advertises a tool that would
    just fail -- and Claude never wastes a call finding that out."""
    tools = list(_BASE_TOOLS)
    if engine_module.is_available():
        tools.append(_BEST_MOVE_TOOL)
    return tools


# --- Executor --------------------------------------------------------------

class ToolExecutor:
    def __init__(self, ctx: ToolContext) -> None:
        self.ctx = ctx

    def execute(self, name: str, tool_input: dict) -> dict:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return _error(f"unknown tool '{name}'")
        try:
            return handler(tool_input)
        except TempoCliError as e:
            return _error(str(e))
        except RuntimeError as e:  # e.g. "Stockfish is not installed"
            return _error(str(e))
        except sqlite3.Error as e:
            return _error(f"database error: {e}")
        except Exception as e:  # defensive: a tool failure must never kill the tool-loop
            return _error(f"{type(e).__name__}: {e}")

    # --- handlers ---

    def _tool_get_archive_stats(self, inp: dict) -> dict:
        game_types = inp.get("game_types") or []
        data = self.ctx.cli.stats(*game_types, _range_token(inp.get("range_days")))
        data["top_openings_white"] = (data.get("top_openings_white") or [])[:8]
        data["top_openings_black"] = (data.get("top_openings_black") or [])[:8]
        return data

    def _tool_search_games(self, inp: dict) -> dict:
        limit = min(int(inp.get("limit", llm_settings.MAX_SEARCH_RESULTS)), llm_settings.MAX_SEARCH_RESULTS)
        filt = GameSearchFilter(
            opponent=inp.get("opponent"), color=inp.get("color"), result=inp.get("result"),
            opening=inp.get("opening"), date_from=inp.get("date_from"), date_to=inp.get("date_to"),
            time_category=inp.get("time_category"), limit=limit,
        )
        rows, total = _filter_games(self.ctx.db, filt)
        return {
            "games": [game_row_to_compact_dict(r) for r in rows],
            "total_matches": total,
            "truncated": total > len(rows),
        }

    def _tool_lookup_opening(self, inp: dict) -> dict:
        data = self.ctx.cli.opening(inp["query"], _range_token(inp.get("range_days")))
        games = data.get("games") or []
        return {"games": games[:10], "total": len(games)}

    def _tool_get_game_summary(self, inp: dict) -> dict:
        return self.ctx.cli.show(int(inp["game_id"]))

    def _tool_get_game_moves(self, inp: dict) -> dict:
        game_id = int(inp["game_id"])
        detail = self.ctx.db.load_game(game_id)
        if detail is None:
            return _error(f"no game with id {game_id}")
        return {
            "game_id": game_id, "your_color": detail.your_color, "result": detail.result,
            "moves": [m.san for m in detail.moves],
        }

    def _tool_check_analysis_coverage(self, inp: dict) -> dict:
        game_ids = inp.get("game_ids") or []
        if len(game_ids) > llm_settings.MAX_COVERAGE_CHECK_GAMES:
            return _error(f"too many game_ids ({len(game_ids)}) -- max {llm_settings.MAX_COVERAGE_CHECK_GAMES} per call")
        version = engine_module.engine_version()
        if version is None:
            return {"games": [], "note": "Stockfish has no recorded version -- nothing has been analyzed yet."}
        results = []
        for game_id in game_ids:
            detail = self.ctx.db.load_game(game_id)
            if detail is None:
                results.append({"game_id": game_id, "error": "not found"})
                continue
            total_plies = len(detail.moves)
            missing = self.ctx.cache.get_missing_plies(game_id, total_plies, ENGINE_ID, version, BATCH_SETTING_KEY)
            results.append({
                "game_id": game_id, "total_plies": total_plies,
                "cached_plies": total_plies - len(missing), "fully_cached": len(missing) == 0,
            })
        return {"games": results}

    def _tool_run_batch_analysis(self, inp: dict) -> dict:
        game_ids = inp.get("game_ids") or []
        if not game_ids:
            return _error("game_ids must be non-empty")
        if len(game_ids) > llm_settings.MAX_BATCH_ANALYSIS_GAMES:
            return _error(f"too many game_ids ({len(game_ids)}) -- max {llm_settings.MAX_BATCH_ANALYSIS_GAMES} per call")

        version = engine_module.engine_version()
        if version is None:
            return _error("Stockfish is not installed")

        engine = self.ctx.engine.spawn_batch_engine()
        results = []
        try:
            for i, game_id in enumerate(game_ids, start=1):
                detail = self.ctx.db.load_game(game_id)
                if detail is None:
                    results.append({"game_id": game_id, "status": "error", "error": "not found"})
                    continue
                total_plies = len(detail.moves)
                missing = self.ctx.cache.get_missing_plies(game_id, total_plies, ENGINE_ID, version, BATCH_SETTING_KEY)
                if not missing:
                    results.append({"game_id": game_id, "analyzed_plies": 0, "already_cached_plies": total_plies, "status": "ok"})
                    continue
                self.ctx.on_status(f"Analyzing game #{game_id} ({i}/{len(game_ids)} games)...")
                sans = [m.san for m in detail.moves]

                def on_progress(done: int, total: int, gid=game_id, idx=i) -> None:
                    self.ctx.on_status(f"Analyzing game #{gid} ({idx}/{len(game_ids)} games, ply {done}/{total})...")

                analyze_missing_plies(
                    engine, sans, missing, self.ctx.cache, game_id, version, BATCH_SETTING_KEY, BATCH_DEPTH,
                    on_progress=on_progress,
                )
                results.append({
                    "game_id": game_id, "analyzed_plies": len(missing),
                    "already_cached_plies": total_plies - len(missing), "status": "ok",
                })
        finally:
            try:
                engine.quit()
            except Exception:
                pass
        return {"games": results}

    def _tool_summarize_cached_analysis(self, inp: dict) -> dict:
        game_ids = inp.get("game_ids") or []
        if len(game_ids) > llm_settings.MAX_SUMMARIZE_GAMES:
            return _error(f"too many game_ids ({len(game_ids)}) -- max {llm_settings.MAX_SUMMARIZE_GAMES} per call")

        version = engine_module.engine_version()
        if version is None:
            return {
                "summary": {"blunders": 0, "mistakes": 0, "inaccuracies": 0, "by_phase": {"opening": 0, "middlegame": 0, "endgame": 0}},
                "examples": [], "skipped_uncached_game_ids": list(game_ids),
            }

        counts = {"blunders": 0, "mistakes": 0, "inaccuracies": 0}
        by_phase = {"opening": 0, "middlegame": 0, "endgame": 0}
        examples: list[dict] = []
        skipped: list[int] = []
        plural = {"blunder": "blunders", "mistake": "mistakes", "inaccuracy": "inaccuracies"}

        for game_id in game_ids:
            detail = self.ctx.db.load_game(game_id)
            if detail is None:
                skipped.append(game_id)
                continue
            total_plies = len(detail.moves)
            rows = self.ctx.cache.get_full(game_id, total_plies, ENGINE_ID, version, BATCH_SETTING_KEY)
            if rows is None:
                skipped.append(game_id)
                continue
            payload = build_stockfish_review_payload(detail, rows, version, BATCH_DEPTH)
            for ply, severity in payload["severities"].items():
                counts[plural[severity]] += 1
                by_phase[_phase_for_ply(ply)] += 1
                if len(examples) < 5:
                    examples.append({"game_id": game_id, "ply": ply, "san": detail.moves[ply].san, "severity": severity})

        return {"summary": {**counts, "by_phase": by_phase}, "examples": examples, "skipped_uncached_game_ids": skipped}

    def _tool_get_repertoire_stats(self, inp: dict) -> dict:
        return self.ctx.cli.explorer(inp["color"], inp.get("sequence") or [])

    def _tool_list_bookmarks(self, inp: dict) -> dict:
        limit = min(int(inp.get("limit", llm_settings.MAX_BOOKMARKS)), llm_settings.MAX_BOOKMARKS)
        marks = self.ctx.bookmarks.list_all()[:limit]
        return {"bookmarks": [
            {"id": b.id, "fen": b.fen, "note": b.note, "source_game_id": b.source_game_id, "source_ply": b.source_ply}
            for b in marks
        ]}

    def _tool_get_best_move_in_current_position(self, inp: dict) -> dict:
        fen = self.ctx.get_current_fen()
        engine = self.ctx.engine.spawn_batch_engine()
        try:
            board = chess.Board(fen)
            info = engine.analyse(board, chess.engine.Limit(depth=BATCH_DEPTH))
            score = info["score"].white()
            pv = info.get("pv", [])
            best_move_san = board.san(pv[0]) if pv else None
            return {"fen": fen, "best_move_san": best_move_san, "eval_cp": score.score(), "eval_mate": score.mate()}
        finally:
            try:
                engine.quit()
            except Exception:
                pass
