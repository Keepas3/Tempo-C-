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


class GameBrowser(QTreeWidget):
    game_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
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
                    label = f"{game.date}  vs {game.opponent}  ({game.your_color})"
                    game_item = QTreeWidgetItem([label, game.time_label, game.time_category, game.site, game.result])
                    game_item.setData(0, GAME_ID_ROLE, game.id)
                    game_item.setToolTip(0, label)  # full text on hover, even if still tight
                    month_item.addChild(game_item)
            year_item.setExpanded(False)
        self.collapseAll()

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        game_id = item.data(0, GAME_ID_ROLE)
        if game_id is not None:
            self.game_selected.emit(game_id)
