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

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLineEdit, QTextEdit, QVBoxLayout, QWidget

import tempo_cli

HEADER_COLOR = "#7fb3ff"
MUTED_COLOR = "#888888"
WIN_COLOR = "#5cb85c"
LOSS_COLOR = "#e57373"
DRAW_COLOR = "#b0b0b0"
ERROR_COLOR = "#e57373"

HELP_TEXT = """
<b style="color:{header}">Commands</b>
<table cellpadding="3" style="width:100%">
<tr><td><code>/list [n]</code></td><td>List the n most recent games (default 20)</td></tr>
<tr><td><code>/show &lt;id&gt;</code></td><td>Show a game's info and load it on the board</td></tr>
<tr><td><code>/review &lt;id&gt;</code></td><td>Replay a game with eval annotations, loaded on the board</td></tr>
<tr><td><code>/stats</code></td><td>Win rate, opening, and time-management stats</td></tr>
<tr><td><code>/opening &lt;query&gt;</code></td><td>Win/loss record for an opening (name substring or ECO code)</td></tr>
<tr><td><code>/moves &lt;sequence&gt;</code></td><td>What was played after a SAN sequence, e.g. <code>/moves e4 e5 Nf3</code></td></tr>
<tr><td><code>/fetch chesscom &lt;user&gt; [year month]</code></td><td>Fetch games from chess.com</td></tr>
<tr><td><code>/fetch lichess &lt;user&gt; [days]</code></td><td>Fetch games from lichess</td></tr>
<tr><td><code>/help</code></td><td>Show this help</td></tr>
</table>
""".format(header=HEADER_COLOR)


def _esc(s) -> str:
    return html.escape(str(s))


def _result_color(result: str) -> str:
    return {"Win": WIN_COLOR, "Loss": LOSS_COLOR, "Draw": DRAW_COLOR}.get(result, MUTED_COLOR)


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

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Segoe UI", 10))

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type /help for commands...")
        self.input.returnPressed.connect(self._on_submit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.output, stretch=1)
        layout.addWidget(self.input)

        self._print(HELP_TEXT)

    def _print(self, html_fragment: str) -> None:
        self.output.append(html_fragment)

    def _print_error(self, message: str) -> None:
        self._print(f'<span style="color:{ERROR_COLOR}">[Error] {_esc(message)}</span>')

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
            self._print(self._format_stats(tempo_cli.stats()))
        elif cmd == "opening":
            if not args:
                self._print_error("usage: /opening <name-or-ECO>")
                return
            data = tempo_cli.opening(" ".join(args))
            self._print(self._format_games(data["games"]))
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

    def _format_games(self, games: list[dict]) -> str:
        if not games:
            return f'<span style="color:{MUTED_COLOR}">No games found.</span>'
        header = f'<b>{len(games)} game(s)</b>'
        rows = []
        for g in games:
            result_html = f'<span style="color:{_result_color(g["result"])}"><b>{_esc(g["result"])}</b></span>'
            rows.append([
                f'#{g["id"]}', _esc(g["date"]),
                f'{_esc(g["opponent"])} <span style="color:{MUTED_COLOR}">({_esc(g["your_color"])})</span>',
                result_html, _esc(g["site"]), _esc(g["opening"]) or '<span style="color:#666">-</span>',
            ])
        return header + _table(["#", "Date", "Opponent", "Result", "Site", "Opening"], rows)

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
            parts.append(_table(["Avg. sec/move", "Moves under 10s"],
                                 [[f'{s["avg_seconds_per_move"]:.1f}', str(s["time_trouble_moves"])]]))

        if s["top_openings_white"]:
            parts.append(_section("Openings you play (White)"))
            rows = [[_esc(o["opening"]), str(o["games"]), _win_rate_span(o["wins"], o["games"])]
                    for o in s["top_openings_white"][:10]]
            parts.append(_table(["Opening", "Games", "Win rate"], rows))

        if s["top_openings_black"]:
            parts.append(_section("Openings faced (Black)"))
            rows = [[_esc(o["opening"]), str(o["games"]), _win_rate_span(o["wins"], o["games"])]
                    for o in s["top_openings_black"][:10]]
            parts.append(_table(["Opening", "Games", "Win rate"], rows))

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
