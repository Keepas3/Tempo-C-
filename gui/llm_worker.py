"""Runs Claude's tool-use loop off the GUI thread. Modeled on
EngineBatchWorker's progress/succeeded/failed/cancelled signal shape and
the same cooperative-cancellation pattern (checked between stream chunks
and between tool-loop iterations, not preemptive).

The tool-use loop itself: send the conversation + tool schemas, stream the
response; if the model asks for tool calls (stop_reason == "tool_use"),
run them via ToolExecutor and feed the results back as a new user turn;
repeat until the model returns a plain text answer or the iteration cap is
hit. Bounded by llm_settings.MAX_TOOL_ITERATIONS so a confused loop can't
run forever.
"""
from __future__ import annotations

import json
from typing import Callable

from PySide6.QtCore import QThread, Signal

import llm_settings
from analysis_cache import AnalysisCache
from bookmarks import Bookmarks
from db_reader import DbReader
from engine import EngineManager
from llm_tools import SYSTEM_PROMPT, ToolContext, ToolExecutor
from tempo_cli import TempoCli

_STATUS_TEXT = {
    "get_archive_stats": "Checking your stats...",
    "search_games": "Searching your games...",
    "lookup_opening": "Looking up opening record...",
    "get_game_summary": "Looking up game details...",
    "get_game_moves": "Reading game moves...",
    "check_analysis_coverage": "Checking what's already analyzed...",
    "run_batch_analysis": "Analyzing games with Stockfish...",
    "summarize_cached_analysis": "Summarizing engine analysis...",
    "get_repertoire_stats": "Checking your repertoire...",
    "list_bookmarks": "Checking bookmarks...",
    "get_best_move_in_current_position": "Analyzing the current position...",
}


class LlmWorker(QThread):
    status = Signal(str)        # short progress text, e.g. "Searching your games..."
    answer_chunk = Signal(str)  # cumulative answer text so far (throttled, not per-token)
    succeeded = Signal(str)     # final answer text
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self, api_key: str, tools: list[dict],
        db: DbReader, cli: TempoCli, cache: AnalysisCache, engine: EngineManager,
        bookmarks: Bookmarks, get_current_fen: Callable[[], str],
        history: list[dict], user_message: str, parent=None,
    ) -> None:
        super().__init__(parent)
        self._api_key = api_key
        self._tools = tools
        self._history = list(history)
        self._user_message = user_message
        self._cancel_requested = False
        # ToolContext.on_status is wired to self.status.emit here (not
        # passed in from outside) specifically so tool handlers that report
        # fine-grained progress (e.g. run_batch_analysis, mid-game) can call
        # it directly from this worker's own thread and have it land back
        # on the GUI thread the same safe, already-proven way every other
        # signal here does -- calling a GUI-thread slot directly from a tool
        # handler running on this thread would violate Qt's thread affinity.
        ctx = ToolContext(
            db=db, cli=cli, cache=cache, engine=engine, bookmarks=bookmarks,
            get_current_fen=get_current_fen, on_status=self.status.emit,
        )
        self._executor = ToolExecutor(ctx)

    def request_cancel(self) -> None:
        # Cooperative, not preemptive -- checked between stream chunks and
        # between tool-loop iterations, matching EngineBatchWorker's
        # request_cancel. Worst case one in-flight API call finishes before
        # the loop actually stops.
        self._cancel_requested = True

    def run(self) -> None:
        import anthropic  # imported here, not at module scope, so this file can be imported even before `anthropic` is installed

        client = anthropic.Anthropic(api_key=self._api_key)
        messages: list[dict] = self._history + [{"role": "user", "content": self._user_message}]

        for _ in range(llm_settings.MAX_TOOL_ITERATIONS):
            if self._cancel_requested:
                self.cancelled.emit()
                return

            try:
                with client.messages.stream(
                    model=llm_settings.MODEL_ID,
                    max_tokens=llm_settings.MAX_OUTPUT_TOKENS,
                    system=SYSTEM_PROMPT,
                    tools=self._tools,
                    messages=messages,
                    cache_control={"type": "ephemeral"},
                    output_config={"effort": "medium"},
                ) as stream:
                    buffer = ""
                    last_emit_len = 0
                    for text in stream.text_stream:
                        if self._cancel_requested:
                            break
                        buffer += text
                        # Throttled: re-rendering the chat's HTML block on
                        # every single token would be wasteful cross-thread
                        # signal/UI churn for no visible benefit.
                        if len(buffer) - last_emit_len >= 80:
                            self.answer_chunk.emit(buffer)
                            last_emit_len = len(buffer)
                    if self._cancel_requested:
                        stream.close()
                        self.cancelled.emit()
                        return
                    final = stream.get_final_message()
            except Exception as e:
                self.failed.emit(str(e))
                return

            messages.append({"role": "assistant", "content": final.content})

            tool_use_blocks = [b for b in final.content if b.type == "tool_use"]
            if not tool_use_blocks:
                text = "".join(b.text for b in final.content if b.type == "text")
                self.succeeded.emit(text)
                return

            tool_results = []
            for block in tool_use_blocks:
                if self._cancel_requested:
                    self.cancelled.emit()
                    return
                self.status.emit(_STATUS_TEXT.get(block.name, f"Using {block.name}..."))
                result = self._executor.execute(block.name, block.input)
                is_error = bool(result.pop("_is_error", False))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": tool_results})

        self.failed.emit("Reached the tool-call limit without a final answer -- try narrowing your question.")
