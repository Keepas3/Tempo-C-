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
from urllib.parse import quote, unquote

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QApplication, QLineEdit, QTextBrowser, QVBoxLayout, QWidget

import db_reader
import opening_moves
import tempo_cli
from command_popup import COMMANDS, CommandPopup, OpeningPopup

HEADER_COLOR = "#7fb3ff"
MUTED_COLOR = "#888888"
WIN_COLOR = "#5cb85c"
LOSS_COLOR = "#e57373"
DRAW_COLOR = "#b0b0b0"
ERROR_COLOR = "#e57373"
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
    head = "".join(f'<th align="left" style="color:{MUTED_COLOR}; border-bottom:1px solid #555; padding:3px 8px 3px 0;">{_esc(h)}</th>' for h in headers)
    body = ""
    for row in rows:
        cells = "".join(f'<td style="padding:2px 8px 2px 0;">{cell}</td>' for cell in row)
        body += f"<tr>{cells}</tr>"
    return f'<table cellspacing="0" style="width:100%; margin-top:4px;"><tr>{head}</tr>{body}</table>'


class CommandPanel(QWidget):
    # Emitted when a game should be loaded onto the board (from /show or /review).
    game_requested = Signal(int)
    # Emitted after a fetch adds at least one new game, so the browser can refresh.
    archive_updated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.output = QTextBrowser()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Segoe UI", 10))
        self.output.setOpenLinks(False)  # we handle anchor clicks ourselves (expand/collapse), not real navigation
        self.output.anchorClicked.connect(self._on_anchor_clicked)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type / for commands...")
        self.input.returnPressed.connect(self._on_submit)
        self.input.installEventFilter(self)

        self.popup = CommandPopup(self.input)
        self.popup.command_chosen.connect(self._apply_popup_command)

        self.opening_popup = OpeningPopup(self.input)
        self.opening_popup.command_chosen.connect(self._apply_opening_selection)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.output, stretch=1)
        layout.addWidget(self.input)

        self.input.textChanged.connect(self._on_input_changed)

        # Last /stats result and which opening rows are expanded, so a click
        # can re-render just that block in place (see _replace_stats_block).
        self._stats_data: dict | None = None
        self._expanded_openings: set[str] = set()
        self._stats_start: QTextCursor | None = None
        self._stats_end: QTextCursor | None = None
        # Recent-games lookups are fetched lazily (only when a row is first
        # expanded) and cached here so re-collapsing/re-expanding is instant.
        self._opening_games_cache: dict[str, list[dict]] = {}
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

        self._print(WELCOME_TEXT)

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
        elif text.startswith("showmore:"):
            color_key = text[len("showmore:"):]
            self._show_all_openings[color_key] = not self._show_all_openings.get(color_key, False)
            self._replace_stats_block()
        elif text == "showmoregames":
            self._opening_results_show_all = not self._opening_results_show_all
            self._replace_opening_results_block()

    def _print_error(self, message: str) -> None:
        self._print(f'<span style="color:{ERROR_COLOR}">[Error] {_esc(message)}</span>')

    # --- slash-command / opening-name popups --------------------------------

    def _on_input_changed(self, text: str) -> None:
        opening_filter = self._opening_query_text(text)
        if opening_filter is not None:
            self.popup.hide()
            self.opening_popup.set_openings(db_reader.list_opening_names())
            self._show_popup(self.opening_popup, opening_filter)
            return
        self.opening_popup.hide()

        if not text.startswith("/") or not self._still_choosing_command(text):
            self.popup.hide()
            return
        self._show_popup(self.popup, text)

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
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
                value = active_popup.choose_current()
                if value is not None:
                    apply_fn(value)
                    return True
            elif key == Qt.Key.Key_Escape:
                active_popup.hide()
                return True
        return super().eventFilter(obj, event)

    def _on_submit(self) -> None:
        line = self.input.text().strip()
        self.input.clear()
        if not line:
            return

        self._print(f'<hr style="border:none; border-top:1px solid #444; margin:10px 0 4px 0;">'
                    f'<span style="color:{MUTED_COLOR}">&gt; {_esc(line)}</span>')

        if not line.startswith("/"):
            self._print(
                f'<span style="color:{MUTED_COLOR}">Natural-language questions aren\'t available yet '
                f'-- try /help for the commands that work today.</span>'
            )
            return

        parts = line[1:].split()
        cmd, args = parts[0].lower() if parts else "", parts[1:]

        try:
            self._dispatch(cmd, args)
        except tempo_cli.TempoCliError as e:
            self._print_error(str(e))
        except Exception as e:  # defensive: never let a bad command kill the GUI
            self._print_error(str(e))

    def _dispatch(self, cmd: str, args: list[str]) -> None:
        if cmd == "help" or cmd == "":
            self._print(HELP_TEXT)
        elif cmd == "list":
            limit = int(args[0]) if args else 20
            data = tempo_cli.list_games(limit)
            self._print(self._format_games(data["games"]))
        elif cmd == "stats":
            self._stats_data = tempo_cli.stats()
            self._expanded_openings = set()
            self._opening_games_cache = {}
            self._show_all_openings = {"white": False, "black": False}
            self._render_stats_block()
        elif cmd == "clear":
            self.output.clear()
            self._stats_start = None
            self._stats_end = None
            self._opening_results_start = None
            self._opening_results_end = None
        elif cmd == "opening":
            if not args:
                self._print_error("usage: /opening <name-or-ECO>")
                return
            data = tempo_cli.opening(" ".join(args))
            self._opening_results = data["games"]
            self._opening_results_show_all = False
            self._render_opening_results_block()
        elif cmd == "moves":
            if not args:
                self._print_error("usage: /moves <san-sequence>, e.g. /moves e4 e5 Nf3")
                return
            data = tempo_cli.moves(args)
            self._print(self._format_replies(data["replies"]))
        elif cmd == "show":
            if not args:
                self._print_error("usage: /show <id>")
                return
            game_id = int(args[0])
            data = tempo_cli.show(game_id)
            self._print(self._format_game_header(data))
            self.game_requested.emit(game_id)
        elif cmd == "review":
            if not args:
                self._print_error("usage: /review <id>")
                return
            game_id = int(args[0])
            data = tempo_cli.review(game_id)
            self._print(self._format_review(data))
            self.game_requested.emit(game_id)
        elif cmd == "fetch":
            self._dispatch_fetch(args)
        else:
            self._print_error(f"unknown command '/{cmd}'. Type /help for a list.")

    def _dispatch_fetch(self, args: list[str]) -> None:
        if len(args) < 2:
            self._print_error("usage: /fetch chesscom <user> [year month]  |  /fetch lichess <user> [days]")
            return
        site, username = args[0], args[1]

        self._print(f'<span style="color:{MUTED_COLOR}">Fetching from {_esc(site)} for {_esc(username)}... '
                    f'(this can take a few seconds)</span>')
        QApplication.processEvents()  # paint the message above before the blocking network call

        if site == "chesscom":
            if len(args) == 4:
                data = tempo_cli.fetch_chesscom(username, int(args[2]), int(args[3]))
            elif len(args) == 2:
                data = tempo_cli.fetch_chesscom(username)
            else:
                self._print_error("usage: /fetch chesscom <user> [year month] (both or neither)")
                return
        elif site == "lichess":
            days = int(args[2]) if len(args) >= 3 else 90
            data = tempo_cli.fetch_lichess(username, days)
        else:
            self._print_error(f"unknown fetch site '{site}' (expected chesscom or lichess)")
            return

        self._print(self._format_fetch_results(data["results"]))

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
        return QTextCursor(cursor)

    def _render_stats_block(self) -> None:
        self._stats_start, self._stats_end = self._insert_tracked_block(self._format_stats(self._stats_data))

    def _replace_stats_block(self) -> None:
        if self._stats_start is None or self._stats_end is None or self._stats_data is None:
            return
        self._stats_end = self._replace_tracked_block(self._stats_start, self._stats_end, self._format_stats(self._stats_data))

    def _render_opening_results_block(self) -> None:
        html = self._format_games(self._opening_results, truncate=DEFAULT_GAMES_SHOWN, show_all=self._opening_results_show_all)
        self._opening_results_start, self._opening_results_end = self._insert_tracked_block(html)

    def _replace_opening_results_block(self) -> None:
        if self._opening_results_start is None or self._opening_results_end is None or self._opening_results is None:
            return
        html = self._format_games(self._opening_results, truncate=DEFAULT_GAMES_SHOWN, show_all=self._opening_results_show_all)
        self._opening_results_end = self._replace_tracked_block(self._opening_results_start, self._opening_results_end, html)

    def _recent_games_for(self, name: str) -> list[dict]:
        if name not in self._opening_games_cache:
            try:
                self._opening_games_cache[name] = tempo_cli.opening_exact(name, 3)["games"]
            except tempo_cli.TempoCliError:
                self._opening_games_cache[name] = []
        return self._opening_games_cache[name]

    def _openings_table(self, openings: list[dict], color_key: str) -> str:
        # Long lists (plus each row's own optional expansion) can make /stats
        # very tall, so only a handful show by default -- the rest are one
        # click away via the "Show N more" row, rather than always dumping
        # everything and making every /stats call a wall of scrolling.
        show_all = self._show_all_openings.get(color_key, False)
        visible = openings if show_all else openings[:DEFAULT_OPENINGS_SHOWN]

        head = (f'<tr><th align="left" style="color:{MUTED_COLOR}; border-bottom:1px solid #555; padding:3px 8px 3px 0;">Opening</th>'
                f'<th align="left" style="color:{MUTED_COLOR}; border-bottom:1px solid #555; padding:3px 8px 3px 0;">Games</th>'
                f'<th align="left" style="color:{MUTED_COLOR}; border-bottom:1px solid #555; padding:3px 8px 3px 0;">Win rate</th></tr>')
        rows = []
        for o in visible:
            name = o["opening"]
            expanded = name in self._expanded_openings
            arrow = "&#9662;" if expanded else "&#9656;"  # ▾ / ▸
            href = "opening:" + quote(name)
            link = f'<a href="{href}" style="color:inherit; text-decoration:none;">{arrow} {_esc(name)}</a>'
            rows.append(f'<tr><td style="padding:2px 8px 2px 0;">{link}</td>'
                        f'<td style="padding:2px 8px 2px 0;">{o["games"]}</td>'
                        f'<td style="padding:2px 8px 2px 0;">{_win_rate_span(o["wins"], o["games"])}</td></tr>')
            if expanded:
                moves = opening_moves.get_moves(name)
                if moves:
                    detail = f'<code>{_esc(moves)}</code>'
                else:
                    detail = f'<span style="color:{MUTED_COLOR}">move order not found</span>'

                recent = self._recent_games_for(name)
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

    def _format_game_header(self, g: dict) -> str:
        result_html = f'<span style="color:{_result_color(g.get("result", ""))}"><b>{_esc(g.get("result", ""))}</b></span>' \
            if g.get("result") in ("Win", "Loss", "Draw") else _esc(g.get("result", ""))
        header = (f'<b>{_esc(g["white"])} vs {_esc(g["black"])}</b>  '
                  f'<span style="color:{MUTED_COLOR}">({_esc(g["date"])})</span>  {result_html}  '
                  f'<span style="color:{MUTED_COLOR}">[{_esc(g.get("site", "Unknown"))}]</span>')
        if g.get("opening"):
            header += f'<br><span style="color:{MUTED_COLOR}">Opening:</span> {_esc(g["opening"])} ({_esc(g.get("eco", ""))})'
        return header

    def _format_review(self, data: dict) -> str:
        g, evals = data["game"], data["evals"]
        rows = []
        for i in range(0, len(g["moves"]), 2):
            move_no = i // 2 + 1
            white_move = f'{_esc(g["moves"][i]["san"])} <span style="color:{MUTED_COLOR}">[{evals[i] / 100.0:+.2f}]</span>'
            if i + 1 < len(g["moves"]):
                black_move = f'{_esc(g["moves"][i + 1]["san"])} <span style="color:{MUTED_COLOR}">[{evals[i + 1] / 100.0:+.2f}]</span>'
            else:
                black_move = ""
            rows.append([str(move_no), white_move, black_move])
        return self._format_game_header(g) + _table(["#", "White", "Black"], rows)
