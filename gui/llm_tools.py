"""Claude tool-use surface for the chat panel's LLM assistant: tool schemas,
the system prompt, and a ToolExecutor that dispatches tool calls into the
existing data layer (TempoCli, DbReader, AnalysisCache, Bookmarks, Notes,
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
from dataclasses import dataclass, field
from typing import Callable

import chess
import chess.engine

import llm_settings
from analysis_cache import AnalysisCache
from bookmarks import Bookmarks
import engine as engine_module
from engine import ENGINE_ID, EngineManager, batch_worker_count, setting_key_for
from engine_analysis_core import analyze_missing_plies
from db_reader import DbReader
from game_search import GameSearchFilter, game_row_to_compact_dict, search_games as _filter_games
from notes import Notes
from profiles import ProfileRecord
from review_data import build_stockfish_review_payload
from tempo_cli import TempoCli, TempoCliError
from weakness_summary import phase_for_ply, summarize_weaknesses

SYSTEM_PROMPT = """You are the assistant inside Tempo, a personal chess game archive app. \
Answer general chess knowledge (theory, endgames, tactics, strategy) directly. For anything \
about the user's own games, stats or playstyle, use tools -- never guess.

Weaknesses / playstyle / "what should I improve" questions: call analyze_weaknesses ONCE \
(set color; add time_category/opening/date filters only if the user named them). It picks the \
recent games, runs Stockfish on any not analyzed yet, and returns aggregated findings. Reply \
with 2-4 concrete takeaways, name the games/moves that illustrate them, and say how many games \
it covered. Don't re-run it for the same question.

Specific questions: get_archive_stats, search_games, lookup_opening, get_game_summary, \
get_repertoire_stats, list_bookmarks, get_game_notes. get_game_moves is for one specific game \
only. Use check_analysis_coverage -> run_batch_analysis -> summarize_cached_analysis only for \
analysis of particular games you already identified (cached results are free to reuse).

Other profiles: the user can have other players' archives open (e.g. an opponent). To prepare \
for or beat someone, call list_profiles then scout_profile; use get_repertoire_stats with its \
`profile` argument to see what that player plays after a given sequence. These are read-only.

Be concise: light markdown, summarize rather than dump numbers, and say plainly when coverage \
is partial."""


@dataclass
class ToolContext:
    db: DbReader
    cli: TempoCli
    cache: AnalysisCache
    engine: EngineManager
    bookmarks: Bookmarks
    notes: Notes
    get_current_fen: Callable[[], str]
    on_status: Callable[[str], None]
    # Snapshot of the other profiles taken when the question was asked (read-only use
    # from this worker thread; the live list belongs to the GUI thread).
    other_profiles: list[ProfileRecord] = field(default_factory=list)
    # Speed/detail preset: Stockfish depth, games per weakness question, etc.
    tier: llm_settings.AiTier = field(default_factory=lambda: llm_settings.AI_TIERS[llm_settings.DEFAULT_TIER])
    # True once the user pressed Cancel -- long analysis checks it between positions.
    is_cancelled: Callable[[], bool] = field(default=lambda: False)


# How many recent matching games (as a multiple of the tier's game budget) a weakness question may
# choose from when preferring ones whose analysis is already saved.
RECENT_WINDOW_FACTOR = 3


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
        "name": "analyze_weaknesses",
        "description": "One-call weakness report: picks the user's most recent games (optionally filtered), runs Stockfish on any not analyzed yet (can take a while the first time; cached afterward), and returns aggregated findings: error rates, errors by game phase, by result and by opening, accuracy, and the costliest moves with better alternatives.",
        "input_schema": {
            "type": "object",
            "properties": {
                "color": {"type": "string", "enum": ["white", "black", "either"], "description": "Which side's games to study."},
                "time_category": {"type": "string", "enum": ["Bullet", "Blitz", "Rapid", "Classical", "Daily"]},
                "opening": {"type": "string", "description": "Substring match on opening name."},
                "date_from": {"type": "string", "description": "YYYY.MM.DD, inclusive."},
                "max_games": {"type": "integer", "description": "Games to study; capped by the user's speed setting. Omit to use the cap."},
            },
            "required": ["color"],
        },
    },
    {
        "name": "check_analysis_coverage",
        "description": f"Which of up to {llm_settings.MAX_COVERAGE_CHECK_GAMES} game ids already have Stockfish analysis cached (cache lookup only). Call before run_batch_analysis.",
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
        "description": f"Runs Stockfish on specific games not yet cached (max {llm_settings.MAX_BATCH_ANALYSIS_GAMES} per call; slow). Returns status only -- call summarize_cached_analysis for findings.",
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
        "description": f"Blunder/mistake/inaccuracy counts by phase, accuracy and example moves for up to {llm_settings.MAX_SUMMARIZE_GAMES} already-analyzed games (cache only).",
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
                "profile": {"type": "string", "description": "Name of another profile (see list_profiles) to explore THEIR games instead of the user's own; `color` is then the color that player had."},
            },
            "required": ["color"],
        },
    },
    {
        "name": "list_profiles",
        "description": "The other players' archives available to study (name and username). Call before scout_profile.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "scout_profile",
        "description": "Scouting summary of another profile's player: recent form, ratings, how often they open with each first move, their most common lines as White and as Black (vs 1.e4 and 1.d4), top openings with results per color, results by time control, and the user's own head-to-head record against them. Takes up to ~15 seconds on very large archives.",
        "input_schema": {
            "type": "object",
            "properties": {"profile": {"type": "string", "description": "Name, id or username of the profile (see list_profiles)."}},
            "required": ["profile"],
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
    {
        "name": "get_game_notes",
        "description": "The user's own saved notes on a game (an overall note) and on specific moves within it, if any exist.",
        "input_schema": {
            "type": "object",
            "properties": {"game_id": {"type": "integer"}},
            "required": ["game_id"],
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
        key = setting_key_for(self.ctx.tier.depth)
        if version is None:
            return {"games": [], "note": "Stockfish has no recorded version -- nothing has been analyzed yet."}
        results = []
        for game_id in game_ids:
            detail = self.ctx.db.load_game(game_id)
            if detail is None:
                results.append({"game_id": game_id, "error": "not found"})
                continue
            total_plies = len(detail.moves)
            missing = self.ctx.cache.get_missing_plies(game_id, total_plies, ENGINE_ID, version, key)
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
        depth = self.ctx.tier.depth
        key = setting_key_for(depth)

        results = []
        for i, game_id in enumerate(game_ids, start=1):
            detail = self.ctx.db.load_game(game_id)
            if detail is None:
                results.append({"game_id": game_id, "status": "error", "error": "not found"})
                continue
            total_plies = len(detail.moves)
            missing = self.ctx.cache.get_missing_plies(game_id, total_plies, ENGINE_ID, version, key)
            if not missing:
                results.append({"game_id": game_id, "analyzed_plies": 0, "already_cached_plies": total_plies, "status": "ok"})
                continue
            self.ctx.on_status(f"Analyzing game #{game_id} ({i}/{len(game_ids)} games)...")
            sans = [m.san for m in detail.moves]

            def on_progress(done: int, total: int, gid=game_id, idx=i) -> None:
                self.ctx.on_status(f"Analyzing game #{gid} ({idx}/{len(game_ids)} games, ply {done}/{total})...")

            analyze_missing_plies(
                self.ctx.engine.spawn_batch_engine, batch_worker_count(),
                sans, missing, self.ctx.cache, game_id, version, key, depth,
                on_progress=on_progress, should_cancel=self.ctx.is_cancelled,
            )
            results.append({
                "game_id": game_id, "analyzed_plies": len(missing),
                "already_cached_plies": total_plies - len(missing), "status": "ok",
            })
        return {"games": results}

    def _tool_analyze_weaknesses(self, inp: dict) -> dict:
        tier = self.ctx.tier
        version = engine_module.engine_version()
        if version is None:
            return _error("Stockfish is not installed -- click Download Stockfish under the board first")
        color = inp.get("color") or "either"
        limit = min(int(inp.get("max_games") or tier.max_games), tier.max_games)
        filt = GameSearchFilter(
            color=color if color in ("white", "black") else None, opening=inp.get("opening"),
            date_from=inp.get("date_from"), time_category=inp.get("time_category"), limit=limit * RECENT_WINDOW_FACTOR,
        )
        rows, total_matches = _filter_games(self.ctx.db, filt)
        rows = [r for r in rows if r.your_color in ("white", "black")]  # untagged games have no "your side"
        if not rows:
            return _error("no games match those filters")

        # Look at a window of the user's recent games (newest first), then spend the game budget on the ones
        # whose analysis is ALREADY saved before paying to analyze new ones -- so earlier /analyze runs and
        # earlier questions are reused instead of being ignored in favor of the very newest games. Games
        # still missing analysis fill whatever budget is left, newest first.
        key = setting_key_for(tier.depth)
        candidates = []
        for row in rows:
            detail = self.ctx.db.load_game(row.id)
            if detail is None or not detail.moves:
                continue
            missing = self.ctx.cache.get_missing_plies(row.id, len(detail.moves), ENGINE_ID, version, key)
            candidates.append((detail, missing))
        window_size = len(candidates)
        cached = [c for c in candidates if not c[1]]
        fresh = [c for c in candidates if c[1]]
        plan = (cached + fresh)[:limit]
        total_new = sum(len(m) for _d, m in plan)

        done_before = 0
        for index, (detail, missing) in enumerate(plan, start=1):
            if not missing:
                continue
            if self.ctx.is_cancelled():
                return _error("cancelled")
            sans = [m.san for m in detail.moves]

            def on_progress(done: int, _total: int, base=done_before, idx=index) -> None:
                self.ctx.on_status(f"Analyzing game {idx}/{len(plan)} (depth {tier.depth}) -- {base + done}/{total_new} positions")

            analyze_missing_plies(
                self.ctx.engine.spawn_batch_engine, batch_worker_count(), sans, missing, self.ctx.cache,
                detail.id, version, key, tier.depth, on_progress=on_progress, should_cancel=self.ctx.is_cancelled,
            )
            done_before += len(missing)
        if self.ctx.is_cancelled():
            return _error("cancelled")

        analyzed = []
        for detail, _missing in plan:
            cache_rows = self.ctx.cache.get_full(detail.id, len(detail.moves), ENGINE_ID, version, key)
            if cache_rows is None:
                continue
            analyzed.append((detail, build_stockfish_review_payload(detail, cache_rows, version, tier.depth)))

        summary = summarize_weaknesses(analyzed, color)
        summary.update({
            "color": color, "depth": tier.depth,
            "games_newly_analyzed": sum(1 for _d, m in plan if m),
            "games_already_cached": sum(1 for _d, m in plan if not m),
            "matching_games_in_archive": total_matches,
            "chosen_from_most_recent": window_size,
        })
        return summary

    def _tool_summarize_cached_analysis(self, inp: dict) -> dict:
        game_ids = inp.get("game_ids") or []
        if len(game_ids) > llm_settings.MAX_SUMMARIZE_GAMES:
            return _error(f"too many game_ids ({len(game_ids)}) -- max {llm_settings.MAX_SUMMARIZE_GAMES} per call")

        version = engine_module.engine_version()
        if version is None:
            return {
                "summary": {"blunders": 0, "mistakes": 0, "inaccuracies": 0, "by_phase": {"opening": 0, "middlegame": 0, "endgame": 0}, "average_accuracy": None},
                "accuracy_by_game": {}, "examples": [], "skipped_uncached_game_ids": list(game_ids),
            }

        depth = self.ctx.tier.depth
        key = setting_key_for(depth)
        counts = {"blunders": 0, "mistakes": 0, "inaccuracies": 0}
        by_phase = {"opening": 0, "middlegame": 0, "endgame": 0}
        examples: list[dict] = []
        skipped: list[int] = []
        plural = {"blunder": "blunders", "mistake": "mistakes", "inaccuracy": "inaccuracies"}
        accuracy_by_game: dict[int, dict] = {}
        your_accuracies: list[float] = []

        for game_id in game_ids:
            detail = self.ctx.db.load_game(game_id)
            if detail is None:
                skipped.append(game_id)
                continue
            total_plies = len(detail.moves)
            rows = self.ctx.cache.get_full(game_id, total_plies, ENGINE_ID, version, key)
            if rows is None:
                skipped.append(game_id)
                continue
            payload = build_stockfish_review_payload(detail, rows, version, depth)
            for ply, severity in payload["severities"].items():
                counts[plural[severity]] += 1
                by_phase[phase_for_ply(ply)] += 1
                if len(examples) < 5:
                    examples.append({"game_id": game_id, "ply": ply, "san": detail.moves[ply].san, "severity": severity})

            accuracy = payload.get("accuracy")
            if accuracy:
                accuracy_by_game[game_id] = accuracy
                your_accuracy = accuracy.get(detail.your_color)
                if your_accuracy is not None:
                    your_accuracies.append(your_accuracy)

        average_accuracy = sum(your_accuracies) / len(your_accuracies) if your_accuracies else None
        return {
            "summary": {**counts, "by_phase": by_phase, "average_accuracy": average_accuracy},
            "accuracy_by_game": accuracy_by_game,
            "examples": examples,
            "skipped_uncached_game_ids": skipped,
        }

    def _resolve_profile(self, query: str) -> ProfileRecord:
        needle = (query or "").strip().lower()
        for key in (lambda p: p.display_name, lambda p: p.id, lambda p: p.username):
            for p in self.ctx.other_profiles:
                if key(p).lower() == needle:
                    return p
        partial = [p for p in self.ctx.other_profiles if needle and needle in p.display_name.lower()]
        if len(partial) == 1:
            return partial[0]
        names = ", ".join(p.display_name for p in self.ctx.other_profiles) or "none"
        raise RuntimeError(f"no other profile matches '{query}' (available: {names})")

    def _tool_get_repertoire_stats(self, inp: dict) -> dict:
        cli = self.ctx.cli
        if inp.get("profile"):
            record = self._resolve_profile(inp["profile"])
            cli = TempoCli(record.resolved_db_path(), record.username)
        return cli.explorer(inp["color"], inp.get("sequence") or [])

    def _tool_list_profiles(self, inp: dict) -> dict:
        return {"profiles": [
            {"name": p.display_name, "username": p.username, "platform": p.platform} for p in self.ctx.other_profiles
        ]}

    def _tool_scout_profile(self, inp: dict) -> dict:
        record = self._resolve_profile(inp.get("profile", ""))
        self.ctx.on_status(f"Scouting {record.display_name}...")
        scout = TempoCli(record.resolved_db_path(), record.username).scout()
        if scout["tagged_games"] == 0:
            return _error(
                f"none of {record.display_name}'s {scout['total_games']} games are tagged as {record.username}'s "
                f"(most common player: {scout.get('suggested_username') or 'unknown'}); the user must right-click "
                f"the profile tab and choose 'Set account username...' first")

        cap = llm_settings.MAX_SCOUT_OPENINGS

        def line(prefix: list[str], steps: list[dict]) -> dict:
            return {"moves": prefix + [s["san"] for s in steps],
                    "share_at_each_step_pct": [round(100 * s["count"] / s["total"]) if s["total"] else 0 for s in steps]}

        def moves(replies: list[dict]) -> list[dict]:
            total = sum(r["count"] for r in replies) or 1
            return [{"san": r["san"], "played_pct": round(100 * r["count"] / total), "games": r["count"],
                     "score_pct": round(100 * (r["wins"] + 0.5 * r["draws"]) / r["count"]) if r["count"] else None}
                    for r in replies[:4]]

        def openings(items: list[dict]) -> list[dict]:
            return [{"opening": o["name"], "games": o["games"], "wins": o["wins"], "draws": o["draws"], "losses": o["losses"],
                     "score_pct": round(100 * (o["wins"] + 0.5 * o["draws"]) / o["games"])} for o in items[:cap]]

        try:
            h2h = self.ctx.cli.head_to_head(record.username)
            head_to_head = {"games": h2h["games"], "wins": h2h["wins"], "draws": h2h["draws"], "losses": h2h["losses"]}
        except TempoCliError:
            head_to_head = None

        return {
            "player": record.display_name, "username": scout["subject"],
            "games": scout["tagged_games"], "first_game": scout["earliest_date"], "last_game": scout["latest_date"],
            "recent_results_oldest_to_newest": scout["recent"],
            "ratings": {tc["name"]: tc["rating"] for tc in scout["by_time_control"] if tc["rating"] > 0},
            "by_time_control": [{"category": tc["name"], "games": tc["games"],
                                 "score_pct": round(100 * (tc["wins"] + 0.5 * tc["draws"]) / tc["games"])}
                                for tc in scout["by_time_control"] if tc["games"]],
            "first_moves_as_white": moves(scout["first_moves_white"]),
            "replies_as_black_to_e4": moves(scout["replies_vs_e4"]),
            "replies_as_black_to_d4": moves(scout["replies_vs_d4"]),
            "most_common_line_as_white": line([], scout["line_white"]),
            "most_common_line_as_black_vs_e4": line(["e4"], scout["line_black_vs_e4"]),
            "most_common_line_as_black_vs_d4": line(["d4"], scout["line_black_vs_d4"]),
            "top_openings_as_white": openings(scout["openings_white"]),
            "top_openings_as_black": openings(scout["openings_black"]),
            "your_head_to_head_vs_them": head_to_head,
        }

    def _tool_list_bookmarks(self, inp: dict) -> dict:
        limit = min(int(inp.get("limit", llm_settings.MAX_BOOKMARKS)), llm_settings.MAX_BOOKMARKS)
        marks = self.ctx.bookmarks.list_all()[:limit]
        return {"bookmarks": [
            {"id": b.id, "fen": b.fen, "note": b.note, "source_game_id": b.source_game_id, "source_ply": b.source_ply}
            for b in marks
        ]}

    def _tool_get_game_notes(self, inp: dict) -> dict:
        game_id = inp["game_id"]
        return {
            "game_note": self.ctx.notes.get_game_note(game_id),
            "move_notes": self.ctx.notes.get_move_notes(game_id),  # {ply (1-based): text}
        }

    def _tool_get_best_move_in_current_position(self, inp: dict) -> dict:
        fen = self.ctx.get_current_fen()
        engine = self.ctx.engine.spawn_batch_engine()
        try:
            board = chess.Board(fen)
            info = engine.analyse(board, chess.engine.Limit(depth=self.ctx.tier.depth))
            score = info["score"].white()
            pv = info.get("pv", [])
            best_move_san = board.san(pv[0]) if pv else None
            return {"fen": fen, "best_move_san": best_move_san, "eval_cp": score.score(), "eval_mate": score.mate()}
        finally:
            try:
                engine.quit()
            except Exception:
                pass
