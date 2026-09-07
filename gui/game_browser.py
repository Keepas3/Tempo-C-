"""Right-panel game browser: a tree grouped Year -> Month -> individual
games. Selecting a game emits its id so the rest of the UI can load it.
"""
from __future__ import annotations

import calendar

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

import db_reader

GAME_ID_ROLE = 1000
COLUMNS = ["Game", "Time", "Type", "Site", "Result"]


def _abbrev_date(date: str) -> str:
    """"2026.09.05" -> "26.09.05" -- saves horizontal space in the row title;
    the full year is already shown once on the tree's Year grouping node."""
    parts = date.split(".")
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[0][2:]}.{parts[1]}.{parts[2]}"
    return date


class GameBrowser(QTreeWidget):
    game_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items_by_id: dict[int, QTreeWidgetItem] = {}
        self.setHeaderLabels(COLUMNS)
        # Column 0 (the game title) stretches to fill whatever space is left
        # after the fixed-width columns, instead of a fixed width that
        # truncates opponent names.
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(1, 70)
        self.setColumnWidth(2, 70)
        self.setColumnWidth(3, 80)
        self.setColumnWidth(4, 60)
        self.itemClicked.connect(self._on_item_clicked)
        self.refresh()

    def refresh(self) -> None:
        self.clear()
        self._items_by_id = {}
        tree = db_reader.games_by_year_month()
        for year in sorted(tree, reverse=True):
            year_item = QTreeWidgetItem([str(year) if year else "Unknown date"])
            self.addTopLevelItem(year_item)
            months = tree[year]
            for month in sorted(months, reverse=True):
                month_name = calendar.month_name[month] if 1 <= month <= 12 else "Unknown"
                month_item = QTreeWidgetItem([f"{month_name} ({len(months[month])})"])
                year_item.addChild(month_item)
                for game in months[month]:
                    label = f"{_abbrev_date(game.date)}  vs {game.opponent}  ({game.your_color})"
                    full_label = f"{game.date}  vs {game.opponent}  ({game.your_color})"
                    game_item = QTreeWidgetItem([label, game.time_label, game.time_category, game.site, game.result])
                    game_item.setData(0, GAME_ID_ROLE, game.id)
                    game_item.setToolTip(0, full_label)  # full year on hover
                    month_item.addChild(game_item)
                    self._items_by_id[game.id] = game_item
            year_item.setExpanded(False)
        self.collapseAll()

    def select_game(self, game_id: int) -> None:
        """Expands to and highlights the row for `game_id`, e.g. after it was
        loaded from a chat command or a clicked game link rather than by
        clicking the row here directly -- keeps the browser in sync with
        whatever's actually shown on the board."""
        item = self._items_by_id.get(game_id)
        if item is None:
            return
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.setCurrentItem(item)
        self.scrollToItem(item)

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        game_id = item.data(0, GAME_ID_ROLE)
        if game_id is not None:
            self.game_selected.emit(game_id)
