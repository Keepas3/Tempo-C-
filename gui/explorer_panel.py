"""Board-driven opening explorer box: shows archive move stats (frequency +
win/draw/loss) for whatever position the board is currently on, refreshed by
ProfileView as the board moves (see profile_view.py's _run_live_explorer).
Read-only display + its own toggle/color controls -- the actual query logic
lives in ProfileView, mirroring how GameBrowser is a dumb display driven by
ProfileView's live-query wiring.
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

HEADER_COLOR = "#7fb3ff"
MUTED_COLOR = "#888888"
WIN_COLOR = "#5cb85c"
LOSS_COLOR = "#e57373"
DRAW_COLOR = "#b0b0b0"
ERROR_COLOR = "#e57373"

BAR_WIDTH = 140  # px -- fixed rather than percentage, QTextBrowser's table layout doesn't honor percentage widths


def _esc(s) -> str:
    return html.escape(str(s))


def _move_label(san: str, ply_index: int) -> str:
    # Move numbers only precede White's moves (even ply), matching how the
    # rest of the app (CommandPanel's /moves, /explorer) displays SAN.
    prefix = f"{ply_index // 2 + 1}." if ply_index % 2 == 0 else ""
    return prefix + san


def _result_bar(wins: int, losses: int, draws: int) -> str:
    total = wins + losses + draws
    if not total:
        return ""
    win_w = round(BAR_WIDTH * wins / total)
    loss_w = round(BAR_WIDTH * losses / total)
    draw_w = max(0, BAR_WIDTH - win_w - loss_w)

    def seg(width: int, color: str, pct: float) -> str:
        if width <= 0:
            return ""
        label = f"{pct:.0f}%" if pct >= 8 else ""  # skip the label on slivers too narrow to hold text
        return (f'<td style="background:{color}; width:{width}px; color:#111; font-size:9px; '
                f'text-align:center; padding:1px 0;">{label}</td>')

    win_pct, draw_pct, loss_pct = (100.0 * n / total for n in (wins, draws, losses))
    segs = seg(win_w, WIN_COLOR, win_pct) + seg(draw_w, DRAW_COLOR, draw_pct) + seg(loss_w, LOSS_COLOR, loss_pct)
    return f'<table cellspacing="0" cellpadding="0" style="width:{BAR_WIDTH}px;"><tr>{segs}</tr></table>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f'<th align="left" style="color:{MUTED_COLOR}; border-bottom:1px solid #555; '
                    f'padding:3px 8px 3px 0;">{_esc(h)}</th>' for h in headers)
    body = "".join(
        "<tr>" + "".join(f'<td style="padding:2px 8px 2px 0;">{cell}</td>' for cell in row) + "</tr>"
        for row in rows
    )
    return f'<table cellspacing="0" style="width:100%; margin-top:4px;"><tr>{head}</tr>{body}</table>'


class ExplorerPanel(QWidget):
    # Whether the "Show opening explorer" checkbox is checked.
    enabled_changed = Signal(bool)
    # "white" or "black", whenever the radio toggle changes by user click.
    color_changed = Signal(str)
    # SAN of a clicked move row -- ProfileView plays it on the board.
    move_clicked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.checkbox = QCheckBox("Show opening explorer")
        self.checkbox.toggled.connect(self.enabled_changed)
        self.checkbox.toggled.connect(self._on_enabled_toggled)

        self.white_radio = QRadioButton("White")
        self.black_radio = QRadioButton("Black")
        self.white_radio.setChecked(True)
        self._color_group = QButtonGroup(self)
        self._color_group.addButton(self.white_radio)
        self._color_group.addButton(self.black_radio)
        self.white_radio.toggled.connect(self._on_radio_toggled)

        controls = QHBoxLayout()
        controls.addWidget(self.checkbox)
        controls.addWidget(QLabel("as:"))
        controls.addWidget(self.white_radio)
        controls.addWidget(self.black_radio)
        controls.addStretch(1)

        self.output = QTextBrowser()
        self.output.setOpenLinks(False)
        self.output.anchorClicked.connect(self._on_anchor_clicked)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        layout.addWidget(self.output, stretch=1)

        self.render_placeholder("Explorer off")

    def _on_enabled_toggled(self, checked: bool) -> None:
        self.white_radio.setEnabled(checked)
        self.black_radio.setEnabled(checked)
        if not checked:
            self.render_placeholder("Explorer off")

    def _on_radio_toggled(self, _checked: bool) -> None:
        # Connected only to white_radio.toggled -- fires exactly once per
        # click (on either radio), since only one button's checked state
        # actually changes per click in a QButtonGroup.
        self.color_changed.emit(self.selected_color())

    def _on_anchor_clicked(self, url) -> None:
        text = url.toString()
        if text.startswith("play:"):
            self.move_clicked.emit(text[len("play:"):])

    def is_enabled(self) -> bool:
        return self.checkbox.isChecked()

    def selected_color(self) -> str:
        return "white" if self.white_radio.isChecked() else "black"

    def set_color(self, color: str) -> None:
        """Programmatic sync (e.g. to a newly loaded game's your_color) --
        does not emit color_changed, since the caller is about to trigger
        its own refresh anyway."""
        radio = self.black_radio if color == "black" else self.white_radio
        radio.blockSignals(True)
        radio.setChecked(True)
        radio.blockSignals(False)

    def render(self, replies: list[dict]) -> None:
        if not replies:
            self.output.setHtml(f'<span style="color:{MUTED_COLOR}">No games reached this position.</span>')
            return

        total_games = sum(r["count"] for r in replies)
        rows = []
        for r in replies:
            freq_pct = 100.0 * r["count"] / total_games if total_games else 0.0
            link = f'<a href="play:{_esc(r["san"])}" style="color:inherit; text-decoration:none;"><b>{_esc(r["san"])}</b></a>'
            rows.append([
                link,
                f'{freq_pct:.0f}% <span style="color:{MUTED_COLOR}">({r["count"]})</span>',
                _result_bar(r["wins"], r["losses"], r["draws"]),
            ])
        html_out = (_table(["Move", "Played", "Result"], rows)
                    + f'<div style="color:{MUTED_COLOR}; margin-top:2px;">{total_games} game(s) reached this position '
                      f'<span style="color:{MUTED_COLOR}">(click a move to play it)</span></div>')
        self.output.setHtml(html_out)

    def render_placeholder(self, text: str) -> None:
        self.output.setHtml(f'<span style="color:{MUTED_COLOR}">{_esc(text)}</span>')

    def render_error(self, message: str) -> None:
        self.output.setHtml(f'<span style="color:{ERROR_COLOR}">Explorer failed: {_esc(message)}</span>')
