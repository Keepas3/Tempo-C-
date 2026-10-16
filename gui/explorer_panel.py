"""Board-driven opening explorer box: shows archive move stats (frequency +
win/draw/loss) for whatever position the board is currently on, refreshed by
ProfileView as the board moves (see profile_view.py's _run_live_explorer).
Read-only display + its own toggle/color controls -- the actual query logic
lives in ProfileView, mirroring how GameBrowser is a dumb display driven by
ProfileView's live-query wiring.
"""
from __future__ import annotations

import html

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from colors import DRAW_COLOR, ERROR_COLOR, LOSS_COLOR, MUTED_COLOR, WIN_COLOR

BAR_WIDTH = 140  # px -- fixed rather than percentage, QTextBrowser's table layout doesn't honor percentage widths


def _esc(s) -> str:
    return html.escape(str(s))


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


# Archive::opponent_replies (C++) refuses sequences longer than this (SQLite's join limit); past it
# there are no meaningful opening statistics anyway, so the panel just says so instead of erroring.
EXPLORER_MAX_PLIES = 40


class ExplorerPanel(QWidget):
    # Whether the "Show opening explorer" checkbox is checked.
    enabled_changed = Signal(bool)
    # "white" or "black", whenever the radio toggle changes by user click.
    color_changed = Signal(str)
    # SAN of a clicked move row -- ProfileView plays it on the board.
    move_clicked = Signal(str)
    # Whose games to explore: "" = this profile's own, otherwise another profile's id.
    source_changed = Signal(str)

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

        # "Games from": this profile's own archive, or another profile's (e.g. an
        # opponent you are preparing for) -- the White/Black radios then mean the
        # color THEY played. Hidden until there is another profile to pick.
        self.source_label = QLabel("from:")
        self.source_combo = QComboBox()
        self.source_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.source_combo.addItem("My games", "")
        self.source_combo.currentIndexChanged.connect(lambda _i: self.source_changed.emit(self.selected_source()))
        self.source_label.hide()
        self.source_combo.hide()

        controls = QHBoxLayout()
        controls.addWidget(self.checkbox)
        controls.addWidget(QLabel("as:"))
        controls.addWidget(self.white_radio)
        controls.addWidget(self.black_radio)
        controls.addSpacing(10)
        controls.addWidget(self.source_label)
        controls.addWidget(self.source_combo)
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

    def selected_source(self) -> str:
        return self.source_combo.currentData() or ""

    def selected_source_label(self) -> str:
        return self.source_combo.currentText() if self.selected_source() else ""

    def set_sources(self, others: list[tuple[str, str]]) -> None:
        """Replaces the list of other profiles (id, label) to pick from. Keeps the current
        choice if it still exists; otherwise falls back to "My games" and says so (e.g. the
        selected profile was just deleted)."""
        previous = self.selected_source()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("My games", "")
        for profile_id, label in others:
            self.source_combo.addItem(label, profile_id)
        index = self.source_combo.findData(previous)
        self.source_combo.setCurrentIndex(index if index >= 0 else 0)
        self.source_combo.blockSignals(False)
        visible = bool(others)
        self.source_label.setVisible(visible)
        self.source_combo.setVisible(visible)
        if self.selected_source() != previous:
            self.source_changed.emit(self.selected_source())

    def set_source(self, profile_id: str) -> None:
        """Programmatic switch (e.g. from a /scout line link); emits source_changed."""
        index = self.source_combo.findData(profile_id)
        if index >= 0:
            self.source_combo.setCurrentIndex(index)

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

    def render(self, replies: list[dict], source_label: str = "") -> None:
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
        who = (f'<div style="color:{MUTED_COLOR}; margin-top:2px;">Games from <b>{_esc(source_label)}</b></div>'
               if source_label else "")
        html_out = (who + _table(["Move", "Played", "Result"], rows)
                    + f'<div style="color:{MUTED_COLOR}; margin-top:2px;">{total_games} game(s) reached this position '
                      f'<span style="color:{MUTED_COLOR}">(click a move to play it)</span></div>')
        self.output.setHtml(html_out)

    def render_placeholder(self, text: str) -> None:
        self.output.setHtml(f'<span style="color:{MUTED_COLOR}">{_esc(text)}</span>')

    def render_error(self, message: str) -> None:
        self.output.setHtml(f'<span style="color:{ERROR_COLOR}">Explorer failed: {_esc(message)}</span>')
