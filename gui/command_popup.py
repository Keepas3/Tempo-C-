"""Popups anchored to the command panel's input box: the slash-command list
(shown as soon as "/" is typed) and the opening-name picker (shown while
typing "/opening ..."), Discord/Slack-style, instead of requiring the exact
command syntax or opening name to be typed from memory.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem

COMMANDS = [
    ("/list", "[n]", "List the n most recent games (default 20)"),
    ("/show", "<id>", "Show a game's info and load it on the board"),
    ("/review", "<id>", "Replay a game with eval annotations, loaded on the board"),
    ("/stats", "[type...]", "Win rate, opening, and time-management stats (optionally filtered by game type)"),
    ("/opening", "<query>", "Win/loss record for an opening (name substring or ECO code)"),
    ("/moves", "<sequence>", "What was played after a SAN sequence, e.g. /moves e4 e5 Nf3"),
    ("/fetch chesscom", "<user> [year month]", "Fetch games from chess.com"),
    ("/fetch lichess", "<user> [days]", "Fetch games from lichess"),
    ("/clear", "", "Clear the chat history"),
    ("/help", "", "Show this list"),
]

GAME_TYPES = ["Bullet", "Blitz", "Rapid", "Classical", "Daily"]

ITEM_VERTICAL_PADDING = 10  # matches the 4px+4px item padding below plus a little breathing room
MAX_VISIBLE_ROWS = 7
DATA_ROLE = Qt.ItemDataRole.UserRole


class _BasePopup(QListWidget):
    """Shared plumbing for an overlay list anchored above the command input:
    window flags that keep it visible without stealing keyboard focus (see
    the comment below), styling, and arrow-key/selection helpers. Subclasses
    just provide their own refresh() to populate rows."""

    # Declared once here (not repeated per subclass) -- PySide6's meta-object
    # system doesn't handle the same signal name being declared separately
    # in sibling subclasses of a common base.
    command_chosen = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        # Qt.Popup does an OS-level keyboard grab (that's how it auto-closes
        # on outside clicks) -- which stole every keystroke from the input
        # box the moment this became visible, regardless of the widget's own
        # Qt-level focus policy. Qt.Tool + WA_ShowWithoutActivating is the
        # correct combination for an overlay that stays visible and
        # clickable but never takes keyboard focus away from the real input.
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                             | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setUniformItemSizes(True)
        self.setStyleSheet(
            "QListWidget { background: #2b2b2b; color: #ddd; border: 1px solid #555; }"
            "QListWidget::item { padding: 4px 8px; }"
            "QListWidget::item:selected { background: #3a5f8a; }"
        )
        self.itemClicked.connect(self._on_item_clicked)

    def _set_rows(self, labels: list[str], data: list[object], lines_per_item: int) -> bool:
        """Populates the list and sizes both the items and the widget to fit
        `lines_per_item` real font lines each -- a flat guessed pixel
        constant clipped multi-line items before. Returns True if non-empty."""
        line_height = self.fontMetrics().lineSpacing()
        item_height = lines_per_item * line_height + ITEM_VERTICAL_PADDING

        self.clear()
        for label, value in zip(labels, data):
            item = QListWidgetItem(label)
            item.setData(DATA_ROLE, value)
            item.setSizeHint(QSize(0, item_height))
            self.addItem(item)

        if labels:
            self.setCurrentRow(0)
            rows = min(len(labels), MAX_VISIBLE_ROWS)
            self.setFixedHeight(rows * item_height + 4)
        return bool(labels)

    def move_selection(self, delta: int) -> None:
        if self.count() == 0:
            return
        row = (self.currentRow() + delta) % self.count()
        self.setCurrentRow(row)

    def choose_current(self) -> object | None:
        item = self.currentItem()
        if item is None:
            return None
        value = item.data(DATA_ROLE)
        self.hide()
        return value

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        self.command_chosen.emit(item.data(DATA_ROLE))
        self.hide()


class CommandPopup(_BasePopup):
    def refresh(self, filter_text: str) -> bool:
        """Repopulates the list for the current input text (which starts
        with "/"). Returns True if there's at least one match (and the
        caller should show the popup), False otherwise (caller should hide)."""
        needle = filter_text.strip().lower()
        matches = [c for c in COMMANDS if c[0].lower().startswith(needle) or needle in c[0].lower()]
        labels = [f"{cmd} {args}".strip() + f"\n{desc}" for cmd, args, desc in matches]
        data = [cmd + " " for cmd, _args, _desc in matches]
        return self._set_rows(labels, data, lines_per_item=2)


class OpeningPopup(_BasePopup):
    """Lists the opening names actually present in the archive (most-played
    first), filtered by substring as you type after "/opening "."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._openings: list[tuple[str, int]] = []  # (name, game count)

    def set_openings(self, openings: list[tuple[str, int]]) -> None:
        self._openings = openings

    def refresh(self, filter_text: str) -> bool:
        needle = filter_text.strip().lower()
        matches = [o for o in self._openings if needle in o[0].lower()] if needle else list(self._openings)
        labels = [f"{name}  ({count} game{'s' if count != 1 else ''})" for name, count in matches]
        data = [name for name, _count in matches]
        return self._set_rows(labels, data, lines_per_item=1)


class GameTypePopup(_BasePopup):
    """Multi-select list of time-control categories for /stats <type...>.
    Clicking toggles a checkmark and keeps the popup open (unlike the other
    popups, which close on selection) -- command_chosen fires after every
    toggle so the caller can resync the input text with the current
    selection. Enter is handled specially by the caller too: for this popup
    it submits the command rather than "choosing" a highlighted row, since
    selection already happens via clicks, not Enter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected: set[str] = set()

    def refresh(self, _filter_text: str = "") -> bool:
        # Fixed list of 5 -- no substring filtering, always show all.
        labels = [self._label_for(t) for t in GAME_TYPES]
        return self._set_rows(labels, list(GAME_TYPES), lines_per_item=1)

    def selected_lower(self) -> list[str]:
        return [t.lower() for t in GAME_TYPES if t in self._selected]

    def reset(self) -> None:
        self._selected = set()

    @staticmethod
    def _label_for_selected(game_type: str, selected: bool) -> str:
        # Actual Unicode box-drawing characters, not HTML entities: this text
        # goes into a plain QListWidgetItem, which (unlike the HTML output
        # pane elsewhere in the app) doesn't render markup/entities at all.
        mark = "☑" if selected else "☐"  # checked / unchecked box
        return f"{mark} {game_type}"

    def _label_for(self, game_type: str) -> str:
        return self._label_for_selected(game_type, game_type in self._selected)

    def _on_item_clicked(self, item: QListWidgetItem) -> None:
        value = item.data(DATA_ROLE)
        if value in self._selected:
            self._selected.discard(value)
        else:
            self._selected.add(value)
        # Update the clicked item's own text in place rather than calling
        # refresh() (clear() + rebuild every row): doing that synchronously
        # from inside an itemClicked handler is a Qt reentrancy hazard --
        # clearing the list while it's still finishing this very click can
        # make Qt re-fire itemClicked against the rebuilt list, silently
        # toggling the selection right back off.
        item.setText(self._label_for_selected(value, value in self._selected))
        self.command_chosen.emit(value)  # deliberately doesn't hide()
