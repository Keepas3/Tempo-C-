"""Left-panel command panel: a scrolling output history above an input box.
Slash commands mirror the CLI 1:1 via tempo_cli.py. Plain input (no leading
/) is a placeholder for the deferred NLP phase.

Output is rendered as HTML (Qt's rich-text subset) instead of plain wrapped
text -- tables for anything tabular (games, openings, replies), colored
section headers, and a muted command echo -- so results stay scannable
instead of turning into run-on wrapped paragraphs in a narrow panel.
"""
from __future__ import annotations

import calendar
import html
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import quote, unquote

from PySide6.QtCore import QEvent, QObject, QRect, Qt, Signal
from PySide6.QtGui import QFont, QMouseEvent, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressDialog,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import opening_moves
import llm_settings
import markdown_lite
from analysis_cache import AnalysisCache
from bookmarks import Bookmarks
from command_popup import COMMANDS, CommandPopup, GameTypePopup, OpeningPopup
from db_reader import DbReader
from engine import BATCH_DEPTH, BATCH_SETTING_KEY, ENGINE_ID, EngineManager
from engine import engine_version as current_engine_version
from engine import is_available as engine_is_available
from engine_batch_worker import EngineBatchWorker
from fetch_worker import FetchWorker
from llm_tools import build_tools
from llm_worker import LlmWorker
from review_data import build_basic_review_payload, build_stockfish_review_payload
from tempo_cli import TempoCli, TempoCliError
from colors import DRAW_COLOR, ERROR_COLOR, HEADER_COLOR, LOSS_COLOR, MUTED_COLOR, WIN_COLOR
DEFAULT_OPENINGS_SHOWN = 5
DEFAULT_GAMES_SHOWN = 10

WELCOME_TEXT = f'<span style="color:{MUTED_COLOR}">Type <code>/</code> to see commands, or <code>/help</code> any time.</span>'

HELP_TEXT = (
    f'<b style="color:{HEADER_COLOR}">Commands</b>'
    '<table cellpadding="3" style="width:100%">'
    + "".join(
        f'<tr><td><code>{html.escape(cmd)} {html.escape(args)}</code></td><td>{html.escape(desc)}</td></tr>'
        for cmd, args, desc in COMMANDS
    )
    + "</table>"
)


def _esc(s) -> str:
    return html.escape(str(s))


def _result_color(result: str) -> str:
    return {"Win": WIN_COLOR, "Loss": LOSS_COLOR, "Draw": DRAW_COLOR}.get(result, MUTED_COLOR)


def _game_link(game_id: int, inner_html: str) -> str:
    """Wraps cell content so clicking it loads that game onto the board (via
    the "game:<id>" anchor scheme handled in _on_anchor_clicked) -- used for
    every game reference shown in chat output (/list, /opening, /stats'
    "Most recent" rows), not just the dedicated /show and browser click."""
    return f'<a href="game:{game_id}" style="color:inherit; text-decoration:none;">{inner_html}</a>'


def _section(title: str) -> str:
    return f'<div style="margin-top:12px;"><b style="color:{HEADER_COLOR}; font-size:11pt;">{_esc(title)}</b></div>'


def _win_rate_span(wins: int, games: int) -> str:
    if not games:
        return '<span style="color:{c}">n/a</span>'.format(c=MUTED_COLOR)
    pct = 100.0 * wins / games
    color = WIN_COLOR if pct >= 55 else (LOSS_COLOR if pct < 45 else DRAW_COLOR)
    return f'<span style="color:{color}"><b>{pct:.0f}%</b></span>'


def _month_year(pgn_date: str) -> str:
    try:
        year, month, _day = pgn_date.split(".")
        return f"{calendar.month_name[int(month)]} {year}"
    except (ValueError, IndexError, KeyError):
        return pgn_date


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f'<th align="left" style="color:{MUTED_COLOR}; border-bottom:1px solid #555; padding:4px 14px 6px 0;">{_esc(h)}</th>' for h in headers)
    body = ""
    for row in rows:
        cells = "".join(f'<td style="padding:4px 14px 4px 0;">{cell}</td>' for cell in row)
        body += f"<tr>{cells}</tr>"
    return f'<table cellspacing="0" style="width:100%; margin-top:6px;"><tr>{head}</tr>{body}</table>'


class CommandPanel(QWidget):
    # Emitted when a game should be loaded onto the board (from /show or /review).
    game_requested = Signal(int)
    # Emitted after a fetch adds at least one new game, so the browser can refresh.
    archive_updated = Signal()
    # Emitted when a move is clicked in a /review table, so the board can
    # jump straight to the position right after that move.
    move_requested = Signal(int, int)  # game_id, ply
    # Emitted with the rendered /review HTML instead of printing it into the
    # chat -- ProfileView shows it in a dedicated panel under the board.
    # Second arg is the ply to scroll/highlight to, or -1 for none.
    review_ready = Signal(str, int)

    def __init__(
        self, db: DbReader, cli: TempoCli, cache: AnalysisCache, engine: EngineManager,
        bookmarks: Bookmarks, get_current_fen: Callable[[], str], parent=None,
    ):
        super().__init__(parent)
        self.db = db
        self.cli = cli
        self.cache = cache
        self.engine = engine
        self.bookmarks = bookmarks
        self.get_current_fen = get_current_fen
        self._review_worker: EngineBatchWorker | None = None
        self._review_progress: QProgressDialog | None = None
        # Workers that have been asked to cancel but haven't emitted
        # QThread's built-in `finished` signal yet -- kept referenced here
        # until they actually stop, so overwriting self._review_worker with
        # a newer one never orphans a still-running QThread. Destroying a
        # QThread wrapper while its underlying OS thread is still alive is
        # a Qt fatal error (hard-aborts the whole process, not a catchable
        # Python exception) -- this is exactly what used to happen if you
        # selected a second game's review while the first was still
        # analyzing.
        self._retiring_review_workers: list[EngineBatchWorker] = []

        # Same worker-lifecycle pattern as the review worker above, for the
        # LLM assistant's tool-use loop.
        self._llm_worker: LlmWorker | None = None
        self._retiring_llm_workers: list[LlmWorker] = []
        # Text-only turns, in-memory for this tab's session only -- intra-
        # turn tool_use/tool_result traffic is never persisted here, only
        # the final question/answer text, so history stays small.
        self._llm_history: list[dict] = []
        self._llm_start: QTextCursor | None = None
        self._llm_end: QTextCursor | None = None

        # The data behind whatever review is currently shown in ProfileView's
        # panel, kept around so set_review_ply can cheaply re-render with a
        # different highlighted move as the board's position changes --
        # no re-analysis, just re-formatting the same already-computed data.
        self._last_review_data: dict | None = None
        self._last_review_game_id: int | None = None

        self.output = QTextBrowser()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Segoe UI", 11))
        self.output.document().setDocumentMargin(12)  # breathing room from the panel edge, instead of text touching it
        self.output.setOpenLinks(False)  # we handle anchor clicks ourselves (expand/collapse), not real navigation
        self.output.anchorClicked.connect(self._on_anchor_clicked)

        self.input = QLineEdit()
        self.input.setMinimumHeight(28)  # matches the slightly larger chat font -- a cramped input looked out of place under it
        self.input.setPlaceholderText("Type / for commands...")
        self.input.returnPressed.connect(self._on_submit)
        self.input.installEventFilter(self)

        self.popup = CommandPopup(self.input)
        self.popup.command_chosen.connect(self._apply_popup_command)

        self.opening_popup = OpeningPopup(self.input)
        self.opening_popup.command_chosen.connect(self._apply_opening_selection)

        self.game_type_popup = GameTypePopup(self.input)
        self.game_type_popup.command_chosen.connect(self._sync_stats_input_from_popup)

        # Always-visible (not a popup like GameTypePopup) since this needs to
        # persist across multiple /stats and /opening calls rather than reset
        # after each submit -- it's a standing filter, not a one-shot command
        # argument. Per-tab: each CommandPanel/profile gets its own, matching
        # the app's existing complete per-tab isolation.
        self.range_combo = QComboBox()
        for label, days in [("Last 14 days", 14), ("Last 30 days", 30), ("Last 60 days", 60),
                             ("Last 90 days", 90), ("Last 180 days", 180), ("All time", None)]:
            self.range_combo.addItem(label, userData=days)
        self.range_combo.setCurrentIndex(3)  # "Last 90 days" default
        self.range_combo.currentIndexChanged.connect(self._on_range_changed)

        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Range:"))
        range_row.addWidget(self.range_combo)
        range_row.addStretch(1)

        self.last_fetch_label = QLabel("Last fetched: ...")
        self.last_fetch_label.setStyleSheet(f"color: {MUTED_COLOR};")
        last_fetch_row = QHBoxLayout()
        last_fetch_row.addWidget(self.last_fetch_label)
        last_fetch_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.output, stretch=1)
        layout.addLayout(range_row)
        layout.addLayout(last_fetch_row)
        layout.addWidget(self.input)

        self.input.textChanged.connect(self._on_input_changed)

        # Current date-range filter (days, or None for "all time"), and the
        # last /opening query typed, so changing the range can re-run
        # whatever /stats and/or /opening results are currently on screen.
        self._range_days: int | None = 90
        self._last_opening_query: str | None = None

        # Last /stats result and which opening rows are expanded, so a click
        # can re-render just that block in place (see _replace_stats_block).
        self._stats_data: dict | None = None
        self._stats_filter: list[str] = []
        self._expanded_openings: set[str] = set()
        self._stats_start: QTextCursor | None = None
        self._stats_end: QTextCursor | None = None
        # Recent-games lookups are fetched lazily (only when a row is first
        # expanded) and cached here so re-collapsing/re-expanding is instant.
        self._opening_games_cache: dict[tuple[str, str], list[dict]] = {}
        # Long opening lists start truncated (see DEFAULT_OPENINGS_SHOWN);
        # "white"/"black" here track whether each section has been expanded
        # to show the rest.
        self._show_all_openings: dict[str, bool] = {"white": False, "black": False}

        # Last /opening result, truncated by default (see DEFAULT_GAMES_SHOWN)
        # with a "show more" toggle -- only the most recent /opening call is
        # interactively toggleable, matching how /stats works (older results
        # already printed to the log stay as they were rendered).
        self._opening_results: list[dict] | None = None
        self._opening_results_show_all = False
        self._opening_results_start: QTextCursor | None = None
        self._opening_results_end: QTextCursor | None = None

        # /explorer repertoire browser: which color and how deep into the
        # move tree the current block shows, so drilling into a move or
        # clicking a breadcrumb can re-query and replace the block in place
        # (same pattern as _stats_start/_end above).
        self._explorer_color: str | None = None
        self._explorer_sequence: list[str] = []
        self._explorer_replies: list[dict] | None = None
        self._explorer_start: QTextCursor | None = None
        self._explorer_end: QTextCursor | None = None

        self._print(WELCOME_TEXT)
        self._refresh_last_fetch_row()

        # Clicking anywhere else in the app (board, browser, output pane,
        # another tab) dismisses whichever popup is open -- installed on the
        # whole application (not just self.input) so it also catches clicks
        # outside this panel entirely, not just clicks on other widgets
        # within it. All popups exist by this point in __init__, so there's
        # no ordering hazard like the one guarded against below for
        # self.input's own filter installation (constructed earlier).
        QApplication.instance().installEventFilter(self)

    def _format_last_fetch(self, entry: dict | None) -> str:
        if entry is None:
            return "never"
        raw = entry.get("last_fetched_at", "")
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            return dt.astimezone().strftime("%b %d, %I:%M %p").replace(" 0", " ")
        except ValueError:
            return raw or "never"

    def _refresh_last_fetch_row(self) -> None:
        try:
            data = self.cli.last_fetch_status()
        except TempoCliError:
            self.last_fetch_label.setText("Last fetched: unavailable")
            return
        cc = self._format_last_fetch(data.get("chesscom"))
        lc = self._format_last_fetch(data.get("lichess"))
        self.last_fetch_label.setText(f"Last fetched — chess.com: {cc}, lichess: {lc}")

    def _print(self, html_fragment: str) -> None:
        self.output.append(html_fragment)

    def _on_anchor_clicked(self, url) -> None:
        text = url.toString()
        if text.startswith("opening:"):
            name = unquote(text[len("opening:"):])
            if name in self._expanded_openings:
                self._expanded_openings.discard(name)
            else:
                self._expanded_openings.add(name)
            self._replace_stats_block()
        elif text.startswith("game:"):
            try:
                game_id = int(text[len("game:"):])
            except ValueError:
                return
            self.game_requested.emit(game_id)
        elif text.startswith("ply:"):
            try:
                game_id_str, ply_str = text[len("ply:"):].split(":")
                self.move_requested.emit(int(game_id_str), int(ply_str))
            except ValueError:
                return
        elif text.startswith("showmore:"):
            color_key = text[len("showmore:"):]
            self._show_all_openings[color_key] = not self._show_all_openings.get(color_key, False)
            self._replace_stats_block()
        elif text == "showmoregames":
            self._opening_results_show_all = not self._opening_results_show_all
            self._replace_opening_results_block()
        elif text.startswith("explorer:"):
            color, _sep, seq_text = text[len("explorer:"):].partition(":")
            sequence = unquote(seq_text).split() if seq_text else []
            self._refresh_explorer_data(color, sequence)
            self._replace_explorer_block()

    def _print_error(self, message: str) -> None:
        self._print(f'<span style="color:{ERROR_COLOR}">[Error] {_esc(message)}</span>')

    # --- slash-command / opening-name popups --------------------------------

    def _on_input_changed(self, text: str) -> None:
        if self._stats_query_text(text) is not None:
            self.popup.hide()
            self.opening_popup.hide()
            self._show_popup(self.game_type_popup, "")
            return
        self.game_type_popup.hide()

        opening_filter = self._opening_query_text(text)
        if opening_filter is not None:
            self.popup.hide()
            self.opening_popup.set_openings(self.db.list_opening_names())
            self._show_popup(self.opening_popup, opening_filter)
            return
        self.opening_popup.hide()

        if not text.startswith("/") or not self._still_choosing_command(text):
            self.popup.hide()
            return
        self._show_popup(self.popup, text)

    @staticmethod
    def _stats_query_text(text: str) -> str | None:
        """Returns "" while typing "/stats <type...>" (the game-type popup
        doesn't filter by what's typed -- it's a fixed multi-select list),
        or None if `text` isn't in that state."""
        if not text.lower().startswith("/stats"):
            return None
        rest = text[len("/stats"):]
        if not rest.startswith(" "):
            return None  # still typing "/stats" itself -- that's the command popup's territory
        return ""

    def _sync_stats_input_from_popup(self) -> None:
        """Keeps the input text in sync with the game-type popup's current
        checkbox selection after every click, so plain Enter submits with
        whatever's checked -- and so the selection is visible/editable as
        normal text, not hidden state only the popup knows about."""
        types = self.game_type_popup.selected_lower()
        text = "/stats " + " ".join(types) if types else "/stats "
        self.input.blockSignals(True)
        self.input.setText(text)
        self.input.setCursorPosition(len(text))
        self.input.blockSignals(False)

    def _show_popup(self, popup, filter_text: str) -> None:
        popup.setFixedWidth(max(self.input.width(), 260))
        has_matches = popup.refresh(filter_text)  # also sets the popup's fixed height for the current match count
        if has_matches:
            # Anchor above the input (it sits at the bottom of the panel, so
            # a downward popup would run off the panel or cover the output).
            top_left = self.input.mapToGlobal(self.input.rect().topLeft())
            popup.move(top_left.x(), top_left.y() - popup.height())
            popup.show()
        else:
            popup.hide()

    @staticmethod
    def _opening_query_text(text: str) -> str | None:
        """Returns the partial opening-name filter text while typing
        "/opening <name>" (possibly empty, right after "/opening "), or None
        if `text` isn't in that state."""
        if not text.lower().startswith("/opening"):
            return None
        rest = text[len("/opening"):]
        if not rest.startswith(" "):
            return None  # still typing "/opening" itself -- that's the command popup's territory
        return rest.lstrip(" ")

    def _apply_opening_selection(self, name: str) -> None:
        self.input.setText(f"/opening {name}")
        self._on_submit()

    @staticmethod
    def _still_choosing_command(text: str) -> bool:
        """True while the typed text could still be extending toward a full
        command *name* (e.g. "/fe", "/fetch", "/fetch "->"/fetch chesscom").
        False once a trailing space follows something that's already a
        complete command name with no longer sibling to keep matching, i.e.
        the user has moved on to typing that command's arguments."""
        stripped = text.rstrip(" ")
        lower = stripped.lower()
        has_trailing_space = text.endswith(" ")
        return any(
            cmd.lower().startswith(lower) and (not has_trailing_space or len(cmd) > len(stripped))
            for cmd, _args, _desc in COMMANDS
        )

    def _apply_popup_command(self, command: str) -> None:
        self.input.setText(command)
        self.input.setFocus()
        self.input.setCursorPosition(len(command))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            self._dismiss_popups_on_outside_click(event)
            return False  # never consume the click -- other widgets still need it

        # Check the event type/source *before* touching self.popup/opening_popup:
        # this filter is installed on self.input, and constructing a child
        # widget parented to it (the popups themselves, in __init__) raises
        # ChildEvents through here too -- accessing those attributes
        # unconditionally would run before they're assigned during __init__.
        if obj is not self.input or event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(obj, event)

        if self.popup.isVisible():
            active_popup, apply_fn = self.popup, self._apply_popup_command
        elif self.opening_popup.isVisible():
            active_popup, apply_fn = self.opening_popup, self._apply_opening_selection
        elif self.game_type_popup.isVisible():
            # No apply_fn: selection happens via clicks (checkboxes), not
            # Enter, so Enter should fall through to a normal submit instead
            # of "choosing" whatever row happens to be highlighted.
            active_popup, apply_fn = self.game_type_popup, None
        else:
            active_popup, apply_fn = None, None

        if active_popup is not None:
            key = event.key()
            if key == Qt.Key.Key_Down:
                active_popup.move_selection(1)
                return True
            if key == Qt.Key.Key_Up:
                active_popup.move_selection(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab) and apply_fn is not None:
                value = active_popup.choose_current()
                if value is not None:
                    apply_fn(value)
                    return True
            elif key == Qt.Key.Key_Escape:
                active_popup.hide()
                return True
        return super().eventFilter(obj, event)

    def _dismiss_popups_on_outside_click(self, event: QMouseEvent) -> None:
        """Hides any visible popup when the user clicks anywhere that isn't
        the input box or the popup itself -- the popups deliberately never
        take keyboard focus (see _BasePopup's WA_ShowWithoutActivating), so
        there's no natural focus-out event to hide on; this is the click
        equivalent."""
        global_pos = event.globalPosition().toPoint()
        input_rect = QRect(self.input.mapToGlobal(self.input.rect().topLeft()), self.input.size())
        for popup in (self.popup, self.opening_popup, self.game_type_popup):
            if not popup.isVisible():
                continue
            if input_rect.contains(global_pos) or popup.geometry().contains(global_pos):
                continue
            popup.hide()

    def _on_submit(self) -> None:
        line = self.input.text().strip()
        self.input.clear()
        self.game_type_popup.hide()  # doesn't self-hide on Enter like the other popups (see eventFilter)
        if not line:
            return

        self._print(f'<hr style="border:none; border-top:1px solid #444; margin:10px 0 4px 0;">'
                    f'<span style="color:{MUTED_COLOR}">&gt; {_esc(line)}</span>')

        if not line.startswith("/"):
            if not llm_settings.is_available():
                self._print(
                    f'<span style="color:{MUTED_COLOR}">Claude isn\'t configured -- set the '
                    f'<code>ANTHROPIC_API_KEY</code> environment variable and restart the app to ask '
                    f'questions here. Try /help for the commands that work today.</span>'
                )
                return
            self._run_llm_query(line)
            return

        parts = line[1:].split()
        cmd, args = parts[0].lower() if parts else "", parts[1:]

        try:
            self._dispatch(cmd, args)
        except TempoCliError as e:
            self._print_error(str(e))
        except Exception as e:  # defensive: never let a bad command kill the GUI
            self._print_error(str(e))

    def _range_token(self) -> str:
        return f"{self._range_days}d" if self._range_days is not None else "all"

    def _refresh_stats_data(self, args: list[str]) -> None:
        self._stats_data = self.cli.stats(*args, self._range_token())
        self._stats_filter = list(args)
        self._expanded_openings = set()
        self._opening_games_cache = {}
        self._show_all_openings = {"white": False, "black": False}

    def _refresh_opening_data(self, query: str) -> None:
        self._last_opening_query = query
        data = self.cli.opening(query, self._range_token())
        self._opening_results = data["games"]
        self._opening_results_show_all = False

    def _on_range_changed(self) -> None:
        self._range_days = self.range_combo.currentData()
        if self._stats_data is not None:
            self._refresh_stats_data(self._stats_filter)
            self._replace_stats_block()
        if self._opening_results is not None and self._last_opening_query is not None:
            self._refresh_opening_data(self._last_opening_query)
            self._replace_opening_results_block()

    def _dispatch(self, cmd: str, args: list[str]) -> None:
        if cmd == "help" or cmd == "":
            self._print(HELP_TEXT)
        elif cmd == "list":
            limit = int(args[0]) if args else 20
            data = self.cli.list_games(limit)
            self._print(self._format_games(data["games"]))
        elif cmd == "stats":
            self._refresh_stats_data(args)
            self.game_type_popup.reset()  # next time the dropdown opens, start with nothing checked
            self._render_stats_block()
        elif cmd == "clear":
            self.output.clear()
            self._stats_start = None
            self._stats_end = None
            self._opening_results_start = None
            self._opening_results_end = None
            self._explorer_start = None
            self._explorer_end = None
            self._llm_start = None
            self._llm_end = None
            self._print(WELCOME_TEXT)  # clear wipes the whole document -- put the welcome hint back
        elif cmd == "opening":
            if not args:
                self._print_error("usage: /opening <name-or-ECO>")
                return
            self._refresh_opening_data(" ".join(args))
            self._render_opening_results_block()
        elif cmd == "moves":
            if not args:
                self._print_error("usage: /moves <san-sequence>, e.g. /moves e4 e5 Nf3")
                return
            data = self.cli.moves(args)
            self._print(self._format_replies(data["replies"]))
        elif cmd == "explorer":
            if not args or args[0].lower() not in ("white", "black"):
                self._print_error("usage: /explorer <white|black> [san-sequence...]")
                return
            self._refresh_explorer_data(args[0].lower(), args[1:])
            self._render_explorer_block()
        elif cmd == "show":
            if not args:
                self._print_error("usage: /show <id>")
                return
            game_id = int(args[0])
            data = self.cli.show(game_id)
            self._print(self._format_game_header(data))
            self.game_requested.emit(game_id)
        elif cmd == "review":
            if not args:
                self._print_error("usage: /review <id>")
                return
            game_id = int(args[0])
            # game_requested first, then _run_review -- ProfileView.load_game
            # (which game_requested triggers) clears any stale review panel
            # content from a previously-loaded game; doing it in this order
            # means that clear happens before this command's own review is
            # rendered, not after (which would immediately wipe it again).
            self.game_requested.emit(game_id)
            self._run_review(game_id)
        elif cmd == "fetch":
            self._dispatch_fetch(args)
        else:
            self._print_error(f"unknown command '/{cmd}'. Type /help for a list.")

    def display_selected_game(self, game_id: int, include_review: bool) -> None:
        """Prints /show (or /review, if `include_review`) info for a game
        selected by clicking it in the archive panel -- mirrors typing the
        command, but without re-emitting game_requested (the caller already
        loaded the game onto the board; re-emitting would loop back here)."""
        self._print(f'<hr style="border:none; border-top:1px solid #444; margin:10px 0 4px 0;">'
                    f'<span style="color:{MUTED_COLOR}">Selected game #{game_id} from archive</span>')
        if include_review:
            self._run_review(game_id)
            return
        try:
            data = self.cli.show(game_id)
            self._print(self._format_game_header(data))
        except TempoCliError as e:
            self._print_error(str(e))

    def _cancel_active_review(self) -> None:
        """Cancels any in-flight batch review before starting a new one, and
        closes its progress dialog immediately (rather than leaving two
        dialogs on screen). The old worker's thread may take a moment to
        actually stop -- see _retire_review_worker for how its QThread
        wrapper is kept alive (not orphaned) until it genuinely does."""
        if self._review_worker is not None:
            if self._review_worker.isRunning():
                self._review_worker.request_cancel()
            self._review_worker = None
        if self._review_progress is not None:
            self._review_progress.close()
            self._review_progress = None

    def _retire_review_worker(self, worker: EngineBatchWorker) -> None:
        """Connected to QThread's built-in `finished` signal (emitted once
        run() actually returns, however it exited) -- only now is it safe to
        drop the last Python reference to `worker`."""
        if worker in self._retiring_review_workers:
            self._retiring_review_workers.remove(worker)
        worker.deleteLater()

    def _cancel_active_llm_query(self) -> None:
        """Same pattern as _cancel_active_review, for the LLM assistant's
        tool-use loop."""
        if self._llm_worker is not None:
            if self._llm_worker.isRunning():
                self._llm_worker.request_cancel()
            self._llm_worker = None

    def _retire_llm_worker(self, worker: LlmWorker) -> None:
        if worker in self._retiring_llm_workers:
            self._retiring_llm_workers.remove(worker)
        worker.deleteLater()

    def cleanup(self) -> None:
        """Cancels and waits (briefly) for any in-flight/retiring review or
        LLM workers to actually stop -- called from ProfileView.cleanup() on
        app exit. Unlike normal interactive use, blocking briefly here is
        fine (and necessary): letting the process exit while a QThread is
        still running is the same fatal-abort hazard _cancel_active_review
        guards against during ordinary use."""
        self._cancel_active_review()
        self._cancel_active_llm_query()
        for worker in list(self._retiring_review_workers) + list(self._retiring_llm_workers):
            worker.request_cancel()
            worker.wait(2000)

    def _emit_review(self, data: dict, game_id: int) -> None:
        """Renders a /review table into the dedicated panel under the board
        (see review_ready, connected in ProfileView) instead of the chat --
        the chat just gets a short breadcrumb so it's clear the command did
        something. Stores `data` so set_review_ply can cheaply re-render
        with a different move highlighted as the board's position changes,
        without redoing any analysis/cache lookup."""
        self._last_review_data = data
        self._last_review_game_id = game_id
        # No highlight yet -- by the time this runs the board's already
        # been reset to the game's start (see the load-before-review
        # ordering note in _dispatch's "review" branch), so there's no
        # move played yet to highlight.
        html = self._format_review(data, game_id)
        self.review_ready.emit(html, -1)
        self._print(f'<span style="color:{MUTED_COLOR}">Move review for game #{game_id} shown below the board.</span>')

    def set_review_ply(self, game_id: int, ply: int) -> None:
        """Re-renders the currently-shown review with `ply` highlighted and
        scrolled into view -- called from ProfileView whenever the board's
        position changes while that game's review is on screen. No-op if
        the panel isn't currently showing `game_id`'s review (e.g. a
        different, unreviewed game is loaded, or no review has been run
        yet in this tab)."""
        if self._last_review_data is None or game_id != self._last_review_game_id:
            return
        html = self._format_review(self._last_review_data, game_id, current_ply=ply)
        self.review_ready.emit(html, ply)

    def clear_review_state(self) -> None:
        """Called from ProfileView.load_game() when switching to a
        different game, so a later set_review_ply() call for the old game
        (e.g. a stray queued signal) doesn't re-render stale data."""
        self._last_review_data = None
        self._last_review_game_id = None

    def _run_review(self, game_id: int) -> None:
        """Renders /review for `game_id`: instant if Stockfish isn't set
        up (today's basic evaluator, unchanged) or if a matching analysis
        is already cached; otherwise runs a background batch analysis with
        a real, cancellable progress dialog first."""
        if not engine_is_available():
            self._print_basic_review(game_id)
            return

        detail = self.db.load_game(game_id)
        if detail is None:
            self._print_error(f"no game with id {game_id}")
            return
        version = current_engine_version()
        total_plies = len(detail.moves)
        cache_rows = self.cache.get_full(game_id, total_plies, ENGINE_ID, version, BATCH_SETTING_KEY)
        if cache_rows is not None:
            payload = build_stockfish_review_payload(detail, cache_rows, version, BATCH_DEPTH)
            self._emit_review(payload, game_id)
            return

        # /review and the LLM assistant's run_batch_analysis tool both
        # write to the same engine_analysis cache and both spawn batch
        # engines -- never let them run concurrently in one tab.
        self._cancel_active_llm_query()
        self._cancel_active_review()

        missing = self.cache.get_missing_plies(game_id, total_plies, ENGINE_ID, version, BATCH_SETTING_KEY)
        sans = [m.san for m in detail.moves]

        progress = QProgressDialog(f"Analyzing game #{game_id} with {version}...", "Cancel", 0, len(missing), self)
        progress.setWindowTitle("Engine analysis")
        progress.setMinimumDuration(0)
        progress.setValue(0)

        worker = EngineBatchWorker(self.db.db_path, sans, self.engine, self.cache, game_id, version, missing, self)

        def on_progress(done: int, total: int) -> None:
            progress.setMaximum(total)
            progress.setValue(done)

        def render_from_cache() -> bool:
            rows = self.cache.get_full(game_id, total_plies, ENGINE_ID, version, BATCH_SETTING_KEY)
            if rows is None:
                return False
            payload = build_stockfish_review_payload(detail, rows, version, BATCH_DEPTH)
            self._emit_review(payload, game_id)
            return True

        def clear_review_worker_if_current() -> None:
            # A worker that finishes normally (not via _cancel_active_review)
            # was never otherwise cleared from self._review_worker -- left
            # as-is, that stale reference would eventually point at a
            # deleteLater()-reaped C++ object (once _retire_review_worker's
            # finished-signal handler runs), and the next /review's
            # _cancel_active_review() touching .isRunning() on it would hit
            # the same libshiboken "already deleted" crash this whole
            # worker-lifecycle scheme exists to prevent. Guarded by identity
            # so this never clobbers a newer worker that's since replaced it.
            if self._review_worker is worker:
                self._review_worker = None

        def on_succeeded() -> None:
            clear_review_worker_if_current()
            progress.close()
            if not render_from_cache():
                self._print_error("analysis completed but the cache read failed unexpectedly")

        def on_cancelled() -> None:
            clear_review_worker_if_current()
            progress.close()
            if not render_from_cache():
                self._print(f'<span style="color:{MUTED_COLOR}">Analysis cancelled -- showing basic evaluator.</span>')
                self._print_basic_review(game_id)

        def on_failed(message: str) -> None:
            clear_review_worker_if_current()
            progress.close()
            self._print_error(f"engine analysis failed: {message}")
            self._print_basic_review(game_id)

        progress.canceled.connect(worker.request_cancel)
        worker.progress.connect(on_progress)
        worker.succeeded.connect(on_succeeded)
        worker.cancelled.connect(on_cancelled)
        worker.failed.connect(on_failed)
        worker.finished.connect(lambda w=worker: self._retire_review_worker(w))
        self._retiring_review_workers.append(worker)
        self._review_progress = progress
        self._review_worker = worker
        worker.start()
        progress.show()

    def _print_basic_review(self, game_id: int) -> None:
        try:
            data = self.cli.review(game_id)
        except TempoCliError as e:
            self._print_error(str(e))
            return
        self._emit_review(build_basic_review_payload(data), game_id)

    # --- LLM assistant (free-text questions) --------------------------------

    def _run_llm_query(self, question: str) -> None:
        # run_batch_analysis (a tool the LLM can call) and /review both
        # write to the same engine_analysis cache and both spawn batch
        # engines -- never let them run concurrently in one tab.
        self._cancel_active_review()
        self._cancel_active_llm_query()

        self._llm_start, self._llm_end = self._insert_tracked_block(
            f'<span style="color:{MUTED_COLOR}">Thinking...</span>'
        )

        worker = LlmWorker(
            llm_settings.api_key(), build_tools(),
            self.db, self.cli, self.cache, self.engine, self.bookmarks, self.get_current_fen,
            self._llm_history, question, self,
        )
        worker.status.connect(self._on_llm_status)
        worker.answer_chunk.connect(self._on_llm_answer_chunk)
        worker.succeeded.connect(lambda text, w=worker: self._on_llm_succeeded(w, question, text))
        worker.failed.connect(lambda msg, w=worker: self._on_llm_failed(w, msg))
        worker.cancelled.connect(lambda w=worker: self._on_llm_cancelled(w))
        worker.finished.connect(lambda w=worker: self._retire_llm_worker(w))
        self._retiring_llm_workers.append(worker)
        self._llm_worker = worker
        worker.start()

    def _clear_llm_worker_if_current(self, worker: LlmWorker) -> None:
        # Same fix as clear_review_worker_if_current in _run_review: a
        # worker that finishes normally was never otherwise cleared from
        # self._llm_worker, which would eventually dangle once
        # _retire_llm_worker's deleteLater() is processed -- guarded by
        # identity so a newer worker that's since replaced it is untouched.
        if self._llm_worker is worker:
            self._llm_worker = None

    def _replace_llm_block(self, html_fragment: str) -> None:
        if self._llm_start is None or self._llm_end is None:
            return
        self._llm_end = self._replace_tracked_block(self._llm_start, self._llm_end, html_fragment)

    def _on_llm_status(self, text: str) -> None:
        self._replace_llm_block(f'<span style="color:{MUTED_COLOR}">{_esc(text)}</span>')

    def _on_llm_answer_chunk(self, text: str) -> None:
        self._replace_llm_block(self._format_llm_answer(text))

    def _on_llm_succeeded(self, worker: LlmWorker, question: str, text: str) -> None:
        self._clear_llm_worker_if_current(worker)
        self._replace_llm_block(self._format_llm_answer(text))
        self._llm_history.append({"role": "user", "content": question})
        self._llm_history.append({"role": "assistant", "content": text})
        # Keep the last several exchanges only -- bounds how much history
        # (and therefore token cost) every future turn in this tab resends.
        self._llm_history = self._llm_history[-20:]

    def _on_llm_failed(self, worker: LlmWorker, message: str) -> None:
        self._clear_llm_worker_if_current(worker)
        self._replace_llm_block(f'<span style="color:{ERROR_COLOR}">[Error] {_esc(message)}</span>')

    def _on_llm_cancelled(self, worker: LlmWorker) -> None:
        self._clear_llm_worker_if_current(worker)
        self._replace_llm_block(f'<span style="color:{MUTED_COLOR}">Cancelled.</span>')

    def _format_llm_answer(self, text: str) -> str:
        return (
            f'<div style="margin-top:4px;"><b style="color:{HEADER_COLOR}">Claude</b></div>'
            f'<div>{markdown_lite.to_html(text)}</div>'
        )

    def _dispatch_fetch(self, args: list[str]) -> None:
        if len(args) < 2:
            self._print_error("usage: /fetch chesscom <user> [year month]  |  /fetch lichess <user> [days]")
            return
        site, username = args[0], args[1]

        if site == "chesscom":
            if len(args) == 4:
                year, month = int(args[2]), int(args[3])
                fetch_fn = lambda: self.cli.fetch_chesscom(username, year, month)
            elif len(args) == 2:
                fetch_fn = lambda: self.cli.fetch_chesscom(username)
            else:
                self._print_error("usage: /fetch chesscom <user> [year month] (both or neither)")
                return
        elif site == "lichess":
            days = int(args[2]) if len(args) >= 3 else None
            fetch_fn = lambda: self.cli.fetch_lichess(username, days)
        else:
            self._print_error(f"unknown fetch site '{site}' (expected chesscom or lichess)")
            return

        self._print(f'<span style="color:{MUTED_COLOR}">Fetching from {_esc(site)} for {_esc(username)}... '
                    f'(this can take a few seconds)</span>')

        # Run off the GUI thread -- with multiple profile tabs live at once,
        # a blocking fetch here would freeze every tab, not just this one.
        self._fetch_worker = FetchWorker(fetch_fn)
        self._fetch_worker.succeeded.connect(self._on_fetch_succeeded)
        self._fetch_worker.failed.connect(self._print_error)
        self._fetch_worker.start()

    def _on_fetch_succeeded(self, data: dict) -> None:
        self._print(self._format_fetch_results(data["results"]))
        self._refresh_last_fetch_row()

    def _insert_tracked_block(self, html: str) -> tuple[QTextCursor, QTextCursor]:
        """Inserts `html` as a distinct block and returns (start, end)
        cursors bounding exactly that block, so a later click (expand/
        collapse, show more) can replace just it in place via
        _replace_tracked_block instead of re-appending a whole new copy."""
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.output.document().isEmpty():
            cursor.insertBlock()
        start = QTextCursor(cursor)
        # Without this, a cursor sitting exactly at a future insertion point
        # drifts forward with that insertion by default, so on the next
        # replace the "start" marker would have silently slid past the block
        # it's supposed to bound -- breaking in-place replacement.
        start.setKeepPositionOnInsert(True)
        cursor.insertHtml(html)
        end = QTextCursor(cursor)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()
        return start, end

    def _replace_tracked_block(self, start: QTextCursor, end: QTextCursor, html: str) -> QTextCursor:
        """Replaces the [start, end) region (from a prior _insert_tracked_block)
        with `html`, returning the new end cursor to keep tracking with."""
        # Editing the document (even via a cursor that isn't the widget's
        # "active" one) can make QTextBrowser auto-scroll -- observed
        # jumping all the way to the top on a toggle click. Capture and
        # restore the scrollbar position explicitly rather than relying on
        # cursor-follow behavior, so expanding/collapsing a row stays put.
        scrollbar = self.output.verticalScrollBar()
        scroll_pos = scrollbar.value()

        # Build the selection from raw integer positions rather than copying
        # `start` directly: keepPositionOnInsert only pins its position(),
        # not its anchor(), so after the first insertion at that spot the
        # copied cursor's anchor silently drifts forward too -- producing a
        # phantom zero-length selection instead of the real start..end range.
        cursor = self.output.textCursor()
        cursor.setPosition(start.position())
        cursor.setPosition(end.position(), QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertHtml(html)

        scrollbar.setValue(scroll_pos)
        return QTextCursor(cursor)

    def _render_stats_block(self) -> None:
        self._stats_start, self._stats_end = self._insert_tracked_block(self._format_stats(self._stats_data))

    def _replace_stats_block(self) -> None:
        if self._stats_start is None or self._stats_end is None or self._stats_data is None:
            return
        self._stats_end = self._replace_tracked_block(self._stats_start, self._stats_end, self._format_stats(self._stats_data))

    def _opening_range_banner(self) -> str:
        if self._range_days is None:
            return ""
        return f'<div style="color:{HEADER_COLOR}">Range: last {self._range_days} days</div>'

    def _render_opening_results_block(self) -> None:
        html = self._opening_range_banner() + self._format_games(
            self._opening_results, truncate=DEFAULT_GAMES_SHOWN, show_all=self._opening_results_show_all)
        self._opening_results_start, self._opening_results_end = self._insert_tracked_block(html)

    def _replace_opening_results_block(self) -> None:
        if self._opening_results_start is None or self._opening_results_end is None or self._opening_results is None:
            return
        html = self._opening_range_banner() + self._format_games(
            self._opening_results, truncate=DEFAULT_GAMES_SHOWN, show_all=self._opening_results_show_all)
        self._opening_results_end = self._replace_tracked_block(self._opening_results_start, self._opening_results_end, html)

    def _refresh_explorer_data(self, color: str, sequence: list[str]) -> None:
        self._explorer_color = color
        self._explorer_sequence = list(sequence)
        self._explorer_replies = self.cli.explorer(color, sequence)["replies"]

    def _render_explorer_block(self) -> None:
        self._explorer_start, self._explorer_end = self._insert_tracked_block(self._format_explorer())

    def _replace_explorer_block(self) -> None:
        if self._explorer_start is None or self._explorer_end is None or self._explorer_replies is None:
            return
        self._explorer_end = self._replace_tracked_block(self._explorer_start, self._explorer_end, self._format_explorer())

    def _recent_games_for(self, name: str, color_key: str) -> list[dict]:
        # find_games_by_exact_opening (the C++ side of opening_exact) has no
        # color filter -- it matches by opening name alone and returns the
        # most recent N regardless of color. Fetching a wider batch (15,
        # not just the 3 we'll actually show) and filtering to color_key
        # here in Python is what keeps a White-section row's examples from
        # ever showing a game the user actually played as Black (or vice
        # versa), without needing a C++ change. Cached by (name, color_key)
        # -- not name alone -- so the White and Black sections never share
        # a cache entry when they list the same opening name.
        cache_key = (name, color_key)
        if cache_key not in self._opening_games_cache:
            try:
                games = self.cli.opening_exact(name, 15, self._range_token())["games"]
                self._opening_games_cache[cache_key] = [g for g in games if g["your_color"] == color_key][:3]
            except TempoCliError:
                self._opening_games_cache[cache_key] = []
        return self._opening_games_cache[cache_key]

    def _openings_table(self, openings: list[dict], color_key: str) -> str:
        # Long lists (plus each row's own optional expansion) can make /stats
        # very tall, so only a handful show by default -- the rest are one
        # click away via the "Show N more" row, rather than always dumping
        # everything and making every /stats call a wall of scrolling.
        show_all = self._show_all_openings.get(color_key, False)
        visible = openings if show_all else openings[:DEFAULT_OPENINGS_SHOWN]

        head = (f'<tr><th align="left" style="color:{MUTED_COLOR}; border-bottom:1px solid #555; padding:4px 14px 6px 0;">Opening</th>'
                f'<th align="left" style="color:{MUTED_COLOR}; border-bottom:1px solid #555; padding:4px 14px 6px 0;">Games</th>'
                f'<th align="left" style="color:{MUTED_COLOR}; border-bottom:1px solid #555; padding:4px 14px 6px 0;">Win rate</th></tr>')
        rows = []
        for o in visible:
            name = o["opening"]
            expanded = name in self._expanded_openings
            arrow = "&#9662;" if expanded else "&#9656;"  # ▾ / ▸
            href = "opening:" + quote(name)
            link = f'<a href="{href}" style="color:inherit; text-decoration:none;">{arrow} {_esc(name)}</a>'
            rows.append(f'<tr><td style="padding:4px 14px 4px 0;">{link}</td>'
                        f'<td style="padding:4px 14px 4px 0;">{o["games"]}</td>'
                        f'<td style="padding:4px 14px 4px 0;">{_win_rate_span(o["wins"], o["games"])}</td></tr>')
            if expanded:
                moves = opening_moves.get_moves(name)
                if moves:
                    detail = f'<code>{_esc(moves)}</code>'
                else:
                    detail = f'<span style="color:{MUTED_COLOR}">move order not found</span>'

                recent = self._recent_games_for(name, color_key)
                if recent:
                    recent_lines = []
                    for g in recent:
                        line = (
                            f'<span style="color:{MUTED_COLOR}">{_esc(g["date"])}</span>  '
                            f'<span style="color:{_result_color(g["result"])}"><b>{_esc(g["result"])}</b></span>  '
                            f'<span style="color:{MUTED_COLOR}">{_esc(g["time_category"])}</span>'
                        )
                        recent_lines.append(_game_link(g["id"], line))
                    detail += f'<div style="margin-top:4px;">{"<br>".join(recent_lines)}</div>'

                rows.append(f'<tr><td colspan="3" style="padding:0 8px 8px 22px; color:{MUTED_COLOR};">{detail}</td></tr>')

        if len(openings) > DEFAULT_OPENINGS_SHOWN:
            href = "showmore:" + color_key
            if show_all:
                toggle_label = "Show fewer &#9652;"
            else:
                remaining = len(openings) - DEFAULT_OPENINGS_SHOWN
                toggle_label = f"Show {remaining} more opening{'s' if remaining != 1 else ''} &#9662;"
            toggle_link = f'<a href="{href}" style="color:{HEADER_COLOR}; text-decoration:none;">{toggle_label}</a>'
            rows.append(f'<tr><td colspan="3" style="padding:6px 8px 2px 0;">{toggle_link}</td></tr>')

        return f'<table cellspacing="0" style="width:100%; margin-top:4px;">{head}{"".join(rows)}</table>'

    def _format_fetch_results(self, results: list[dict]) -> str:
        rows = []
        total_added = 0
        for r in results:
            if not r["ok"]:
                rows.append([f'<span style="color:{ERROR_COLOR}">{_esc(r["source"])}</span>',
                             f'<span style="color:{ERROR_COLOR}">{_esc(r["error"])}</span>', "", ""])
            else:
                rows.append([_esc(r["source"]), str(r["parsed"]),
                             f'<b style="color:{WIN_COLOR}">{r["added"]}</b>' if r["added"] else "0",
                             str(r["skipped"])])
                total_added += r["added"]
        if total_added > 0:
            self.archive_updated.emit()
        return _table(["Source", "Parsed", "Added", "Already had"], rows)

    # --- formatting -------------------------------------------------------

    def _format_games(self, games: list[dict], truncate: int | None = None, show_all: bool = False) -> str:
        if not games:
            return f'<span style="color:{MUTED_COLOR}">No games found.</span>'

        visible = games if (truncate is None or show_all) else games[:truncate]

        header = f'<b>{len(games)} game(s)</b> <span style="color:{MUTED_COLOR}">(click a row to load it on the board)</span>'
        rows = []
        for g in visible:
            result_html = f'<span style="color:{_result_color(g["result"])}"><b>{_esc(g["result"])}</b></span>'
            opponent_html = f'{_esc(g["opponent"])} <span style="color:{MUTED_COLOR}">({_esc(g["your_color"])})</span>'
            opening_html = _esc(g["opening"]) or '<span style="color:#666">-</span>'
            cells = [f'#{g["id"]}', _esc(g["date"]), opponent_html, result_html, _esc(g["site"]), opening_html]
            rows.append([_game_link(g["id"], cell) for cell in cells])
        html = header + _table(["#", "Date", "Opponent", "Result", "Site", "Opening"], rows)

        if truncate is not None and len(games) > truncate:
            if show_all:
                toggle_label = "Show fewer &#9652;"
            else:
                remaining = len(games) - truncate
                toggle_label = f"Show {remaining} more game{'s' if remaining != 1 else ''} &#9662;"
            html += (f'<div style="margin-top:6px;">'
                     f'<a href="showmoregames" style="color:{HEADER_COLOR}; text-decoration:none;">{toggle_label}</a></div>')

        return html

    def _format_stats(self, s: dict) -> str:
        total = s["wins"] + s["losses"] + s["draws"]
        parts = []

        if self._stats_filter or self._range_days is not None:
            filter_parts = []
            if self._stats_filter:
                filter_parts.append(", ".join(t.capitalize() for t in self._stats_filter))
            if self._range_days is not None:
                filter_parts.append(f"last {self._range_days} days")
            parts.append(f'<div style="color:{HEADER_COLOR}">Filtered to: {_esc(", ".join(filter_parts))}</div>')

        if s.get("earliest_date") and s.get("latest_date"):
            span = f"{_month_year(s['earliest_date'])} &ndash; {_month_year(s['latest_date'])}"
            parts.append(f'<div style="color:{MUTED_COLOR}">Covers {span} &middot; {total} game(s)</div>')

        parts.append(_section("Overall"))
        overall_rows = [[
            f'<span style="color:{WIN_COLOR}">{s["wins"]}W</span> '
            f'<span style="color:{LOSS_COLOR}">{s["losses"]}L</span> '
            f'<span style="color:{DRAW_COLOR}">{s["draws"]}D</span>',
            _win_rate_span(s["wins"], total),
        ]]
        parts.append(_table(["Record", "Win rate"], overall_rows))

        color_rows = [
            ["As White",
             f'{s["wins_white"]}W {s["losses_white"]}L {s["draws_white"]}D',
             _win_rate_span(s["wins_white"], s["wins_white"] + s["losses_white"] + s["draws_white"])],
            ["As Black",
             f'{s["wins_black"]}W {s["losses_black"]}L {s["draws_black"]}D',
             _win_rate_span(s["wins_black"], s["wins_black"] + s["losses_black"] + s["draws_black"])],
        ]
        parts.append(_table(["", "Record", "Win rate"], color_rows))

        if s["avg_seconds_per_move"] >= 0:
            parts.append(_section("Time management"))
            overall_time_row = [["Overall", str(total), f'{s["avg_seconds_per_move"]:.1f}', str(s["time_trouble_moves"])]]
            by_tc_rows = [
                [t["category"], str(t["games"]),
                 f'{t["avg_seconds_per_move"]:.1f}' if t["avg_seconds_per_move"] >= 0 else '<span style="color:{}">n/a</span>'.format(MUTED_COLOR),
                 str(t["time_trouble_moves"])]
                for t in s.get("by_time_control", [])
            ]
            parts.append(_table(["Time control", "Games", "Avg. sec/move", "Moves under 10s"],
                                 overall_time_row + by_tc_rows))

        if s["top_openings_white"]:
            parts.append(_section("Openings you play (White)"))
            parts.append(self._openings_table(s["top_openings_white"], "white"))

        if s["top_openings_black"]:
            parts.append(_section("Openings faced (Black)"))
            parts.append(self._openings_table(s["top_openings_black"], "black"))

        return "".join(parts)

    def _format_replies(self, replies: list[dict]) -> str:
        if not replies:
            return f'<span style="color:{MUTED_COLOR}">No games reached that position.</span>'
        rows = []
        for r in replies:
            games = r["wins"] + r["losses"] + r["draws"]
            rows.append([f'<b>{_esc(r["san"])}</b>', str(r["count"]),
                         f'{r["wins"]}W {r["losses"]}L {r["draws"]}D', _win_rate_span(r["wins"], games)])
        return _table(["Move", "Games", "Record", "Win rate"], rows)

    _EXPLORER_BAR_WIDTH = 180  # px -- fixed rather than percentage widths, since QTextBrowser's table layout
                               # doesn't reliably honor percentage-width cells the way real HTML/CSS does.

    def _explorer_result_bar(self, wins: int, losses: int, draws: int) -> str:
        total = wins + losses + draws
        if not total:
            return ""
        win_w = round(self._EXPLORER_BAR_WIDTH * wins / total)
        loss_w = round(self._EXPLORER_BAR_WIDTH * losses / total)
        draw_w = max(0, self._EXPLORER_BAR_WIDTH - win_w - loss_w)

        def seg(width: int, color: str, pct: float) -> str:
            if width <= 0:
                return ""
            label = f"{pct:.0f}%" if pct >= 8 else ""  # skip the label on slivers too narrow to hold text
            return (f'<td style="background:{color}; width:{width}px; color:#111; font-size:9px; '
                    f'text-align:center; padding:1px 0;">{label}</td>')

        win_pct, draw_pct, loss_pct = (100.0 * n / total for n in (wins, draws, losses))
        segs = seg(win_w, WIN_COLOR, win_pct) + seg(draw_w, DRAW_COLOR, draw_pct) + seg(loss_w, LOSS_COLOR, loss_pct)
        return f'<table cellspacing="0" cellpadding="0" style="width:{self._EXPLORER_BAR_WIDTH}px;"><tr>{segs}</tr></table>'

    @staticmethod
    def _explorer_move_label(san: str, ply_index: int) -> str:
        # Move numbers only precede White's moves (even ply), matching how
        # /moves and /show already display SAN sequences elsewhere.
        prefix = f"{ply_index // 2 + 1}." if ply_index % 2 == 0 else ""
        return prefix + san

    def _format_explorer(self) -> str:
        color = self._explorer_color
        sequence = self._explorer_sequence
        replies = self._explorer_replies or []

        color_label = "White" if color == "white" else "Black"
        parts = [f'<div><b style="color:{HEADER_COLOR}">Repertoire explorer</b> '
                 f'<span style="color:{MUTED_COLOR}">-- your moves as {color_label}</span></div>']

        crumbs = [f'<a href="explorer:{color}:" style="color:{HEADER_COLOR}; text-decoration:none;">Start</a>']
        for i, san in enumerate(sequence):
            prefix = sequence[: i + 1]
            href = "explorer:" + color + ":" + quote(" ".join(prefix))
            label = self._explorer_move_label(san, i)
            crumbs.append(f'<a href="{href}" style="color:{HEADER_COLOR}; text-decoration:none;">{_esc(label)}</a>')
        parts.append(f'<div style="margin:4px 0;">{" &rsaquo; ".join(crumbs)}</div>')

        if not replies:
            parts.append(f'<span style="color:{MUTED_COLOR}">No games reached this position.</span>')
            return "".join(parts)

        total_games = sum(r["count"] for r in replies)
        rows = []
        for r in replies:
            freq_pct = 100.0 * r["count"] / total_games if total_games else 0.0
            next_seq = sequence + [r["san"]]
            href = "explorer:" + color + ":" + quote(" ".join(next_seq))
            label = self._explorer_move_label(r["san"], len(sequence))
            link = f'<a href="{href}" style="color:inherit; text-decoration:none;"><b>{_esc(label)}</b></a>'
            rows.append([
                link,
                f'{freq_pct:.0f}% <span style="color:{MUTED_COLOR}">({r["count"]})</span>',
                self._explorer_result_bar(r["wins"], r["losses"], r["draws"]),
            ])
        parts.append(_table(["Move", "Played", "Result"], rows))
        parts.append(f'<div style="color:{MUTED_COLOR}; margin-top:2px;">{total_games} game(s) reached this position '
                      f'<span style="color:{MUTED_COLOR}">(click a move to drill in, or a breadcrumb to jump back)</span></div>')
        return "".join(parts)

    def _format_game_header(self, g: dict) -> str:
        result_html = f'<span style="color:{_result_color(g.get("result", ""))}"><b>{_esc(g.get("result", ""))}</b></span>' \
            if g.get("result") in ("Win", "Loss", "Draw") else _esc(g.get("result", ""))
        header = (f'<b>{_esc(g["white"])} vs {_esc(g["black"])}</b>  '
                  f'<span style="color:{MUTED_COLOR}">({_esc(g["date"])})</span>  {result_html}  '
                  f'<span style="color:{MUTED_COLOR}">[{_esc(g.get("site", "Unknown"))}]</span>')
        if g.get("opening"):
            header += f'<br><span style="color:{MUTED_COLOR}">Opening:</span> {_esc(g["opening"])} ({_esc(g.get("eco", ""))})'
        return header

    _SEVERITY_COLORS = {"blunder": ERROR_COLOR, "mistake": "#e0a030", "inaccuracy": "#d4c840"}
    _SEVERITY_LABELS = {"blunder": "Blunder", "mistake": "Mistake", "inaccuracy": "Inaccuracy"}

    def _format_review(self, data: dict, game_id: int, current_ply: int = -1) -> str:
        g, evals = data["game"], data["evals"]
        mates = data.get("mates", {})
        best_moves = data.get("best_moves", {})
        severities = data.get("severities", {})
        engine_label = data.get("engine_label")

        def move_link(ply: int) -> str:
            # ply here is the position AFTER this move is played (1-based);
            # idx is the 0-based index used by g["moves"]/evals/mates/etc.
            idx = ply - 1
            san = _esc(g["moves"][idx]["san"])
            if idx in mates:
                mate_n = mates[idx]
                ev_text = f"M{mate_n}" if mate_n > 0 else f"-M{abs(mate_n)}"
            else:
                ev_text = f"{evals[idx] / 100.0:+.2f}"
            ev = f'<span style="color:{MUTED_COLOR}">[{ev_text}]</span>'
            link = (f'<a href="ply:{game_id}:{ply}" style="color:{HEADER_COLOR}; text-decoration:none;">{san}</a> {ev}')
            severity = severities.get(idx)
            if severity:
                link += (f' <span style="color:{self._SEVERITY_COLORS[severity]}">'
                         f'{self._SEVERITY_LABELS[severity]}</span>')
            best = best_moves.get(idx)
            if best:
                link += (f'<br><span style="color:{MUTED_COLOR}; font-size:smaller;">'
                         f'engine likes: {_esc(best)}</span>')
            # A named anchor at every ply (not just the highlighted one) so
            # ProfileView can always scrollToAnchor() the current move into
            # view, whichever it ends up being. The highlight itself is
            # just a background pill around the link+eval -- Qt's rich-text
            # subset has no real glow/box-shadow, so a solid contrasting
            # background is the closest "stands out" effect available.
            anchor = f'<a name="ply-{ply}"></a>'
            if ply == current_ply:
                return f'{anchor}<span style="background-color:#2d4a6b; border:1px solid {HEADER_COLOR}; border-radius:4px; padding:2px 5px;">{link}</span>'
            return anchor + link

        rows = []
        for i in range(0, len(g["moves"]), 2):
            move_no = i // 2 + 1
            white_move = move_link(i + 1)
            black_move = move_link(i + 2) if i + 1 < len(g["moves"]) else ""
            rows.append([str(move_no), white_move, black_move])

        banner = f'<div style="color:{MUTED_COLOR}">Evaluated with: {_esc(engine_label)}</div>' if engine_label else ""
        return self._format_game_header(g) + banner + _table(["#", "White", "Black"], rows)
