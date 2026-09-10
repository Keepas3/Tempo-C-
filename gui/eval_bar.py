"""A vertical white/black advantage bar shown beside the board -- the same
lichess/chess.com-style readout, driven directly by live-eval results (no
separate label widget). The score is drawn inside the bar itself (not as a
sibling QLabel stacked above it) specifically so the bar's own height stays
exactly equal to the board's -- a label above it would push the bar down,
misaligning its top edge from the board's. Flips which end is white/black
whenever the board's orientation flips, so its bottom always matches
whichever side is drawn at the bottom of the board.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

BAR_WIDTH = 24
_LABEL_HEIGHT = 16

_WHITE_COLOR = QColor("#f0f0f0")
_BLACK_COLOR = QColor("#3a3a3a")
_BORDER_COLOR = QColor("#888888")
_MIDLINE_COLOR = QColor("#999999")


def _white_win_fraction(cp: int | None, mate: int | None) -> float:
    """Converts a White-POV centipawn/mate score into the 0..1 fraction of
    the bar that should read as White's, via the same logistic curve
    lichess uses for its own eval bar (Win% = 50 + 50*(2/(1+exp(-0.00368208*cp))-1))."""
    if mate is not None:
        return 1.0 if mate > 0 else 0.0
    if cp is None:
        return 0.5
    return 1.0 / (1.0 + math.exp(-0.00368208 * cp))


def _format_score(cp: int | None, mate: int | None) -> str:
    if mate is not None:
        return f"M{mate}" if mate > 0 else f"-M{abs(mate)}"
    if cp is not None:
        return f"{cp / 100.0:+.2f}"
    return "--"


class EvalBar(QWidget):
    def __init__(self, height: int, parent=None):
        super().__init__(parent)
        self.setFixedSize(BAR_WIDTH, height)
        self._white_at_bottom = True
        self._white_fraction = 0.5
        self._label_text = ""

    def set_height(self, height: int) -> None:
        """Keeps this bar exactly as tall as the board -- called whenever
        the board's own size changes (see ProfileView's board-size toggle)."""
        if height == self.height():
            return
        self.setFixedSize(BAR_WIDTH, height)
        self.update()

    def set_orientation(self, white_at_bottom: bool) -> None:
        if white_at_bottom == self._white_at_bottom:
            return
        self._white_at_bottom = white_at_bottom
        self.update()

    def set_eval(self, cp: int | None, mate: int | None) -> None:
        self._white_fraction = _white_win_fraction(cp, mate)
        self._label_text = _format_score(cp, mate)
        self.update()

    def clear(self) -> None:
        self._white_fraction = 0.5
        self._label_text = ""
        self.update()

    def show_error(self) -> None:
        self._white_fraction = 0.5
        self._label_text = "err"
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w, h = self.width(), self.height()

        white_px = round(h * self._white_fraction)
        black_px = h - white_px

        if self._white_at_bottom:
            painter.fillRect(0, 0, w, black_px, _BLACK_COLOR)
            painter.fillRect(0, black_px, w, white_px, _WHITE_COLOR)
        else:
            painter.fillRect(0, 0, w, white_px, _WHITE_COLOR)
            painter.fillRect(0, white_px, w, black_px, _BLACK_COLOR)

        painter.setPen(_MIDLINE_COLOR)
        painter.drawLine(0, h // 2, w, h // 2)

        painter.setPen(_BORDER_COLOR)
        painter.drawRect(0, 0, w - 1, h - 1)

        if self._label_text:
            # Always drawn at the very top of the bar, in whichever color
            # contrasts against that region (top is black when white sits
            # at the bottom, and vice versa) -- keeping it at a fixed spot
            # (rather than following the leading edge) is what lets the bar
            # stay a clean, fixed-height rectangle with no separate label
            # widget pushing its top edge out of line with the board's.
            top_is_black = self._white_at_bottom
            text_color = _WHITE_COLOR if top_is_black else _BLACK_COLOR
            font = QFont(painter.font())
            font.setPointSize(7)
            painter.setFont(font)
            painter.setPen(text_color)
            painter.drawText(QRect(0, 2, w, _LABEL_HEIGHT), Qt.AlignmentFlag.AlignHCenter, self._label_text)
