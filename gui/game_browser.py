"""Right-panel game browser: a tree grouped Year -> Month -> individual
games. Selecting a game emits its id so the rest of the UI can load it.
Column headers are clickable to sort games within each month by that
column, using domain-specific ordering (numeric time, category rank) rather
than Qt's default alphabetical text sort.
"""
from __future__ import annotations

import calendar

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHeaderView, QTreeWidget, QTreeWidgetItem

import db_reader

GAME_ID_ROLE = 1000
COLUMNS = ["Game", "Time", "Type", "Site", "Result"]

TYPE_ORDER = {"Bullet": 0, "Blitz": 1, "Rapid": 2, "Classical": 3, "Daily": 4, "Unknown": 5}
RESULT_ORDER = {"Win": 0, "Draw": 1, "Loss": 2, "?": 3}

# Which direction each column starts in the first time it's clicked (i.e.
# when switching to it from a different column). Game defaults to
# descending (most recent first, matching the initial unsorted load); the
# rest default to ascending (lowest time / earliest category / A-Z / best
# result first).
DEFAULT_ASCENDING = {0: False, 1: True, 2: True, 3: True, 4: True}


def _abbrev_date(date: str) -> str:
    """"2026.09.05" -> "26.09.05" -- saves horizontal space in the row title;
    the full year is already shown once on the tree's Year grouping node."""
    parts = date.split(".")
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[0][2:]}.{parts[1]}.{parts[2]}"
    return date


def _sort_key(column: int, game) -> object:
    if column == 1:
        return game.time_seconds
    if column == 2:
        return TYPE_ORDER.get(game.time_category, len(TYPE_ORDER))
    if column == 3:
        return game.site
    if column == 4:
        return RESULT_ORDER.get(game.result, len(RESULT_ORDER))
    return game.date  # column 0 (or anything unrecognized): "YYYY.MM.DD" sorts correctly as text


class GameBrowser(QTreeWidget):
    game_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items_by_id: dict[int, QTreeWidgetItem] = {}
        self._tree_data: dict[int, dict[int, list]] = {}
        self._sort_column = 0
        self._sort_ascending = False  # matches the initial most-recent-first load

        self.setHeaderLabels(COLUMNS)
        # Column 0 (the game title) stretches to fill whatever space is left
        # after the fixed-width columns, instead of a fixed width that
        # truncates opponent names.
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.setColumnWidth(1, 70)
        self.setColumnWidth(2, 70)
        self.setColumnWidth(3, 80)
        self.setColumnWidth(4, 60)
        self.header().setSortIndicatorShown(True)
        self.header().sectionClicked.connect(self._on_header_clicked)
        self.itemClicked.connect(self._on_item_clicked)
        self.refresh()

    def refresh(self) -> None:
        self._tree_data = db_reader.games_by_year_month()
        self._rebuild(preserve_state=False)

    def _on_header_clicked(self, column: int) -> None:
        if column == self._sort_column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = DEFAULT_ASCENDING.get(column, True)
        self._rebuild(preserve_state=True)

    def _rebuild(self, preserve_state: bool) -> None:
        expanded_years, expanded_months, current_id = set(), set(), None
        if preserve_state:
            current_item = self.currentItem()
            if current_item is not None:
                current_id = current_item.data(0, GAME_ID_ROLE)
            for i in range(self.topLevelItemCount()):
                year_item = self.topLevelItem(i)
                if year_item.isExpanded():
                    expanded_years.add(year_item.text(0))
                for j in range(year_item.childCount()):
                    month_item = year_item.child(j)
                    if month_item.isExpanded():
                        expanded_months.add((year_item.text(0), month_item.text(0)))

        self.clear()
        self._items_by_id = {}
        reverse = not self._sort_ascending

        for year in sorted(self._tree_data, reverse=True):
            year_label = str(year) if year else "Unknown date"
            year_item = QTreeWidgetItem([year_label])
            self.addTopLevelItem(year_item)
            months = self._tree_data[year]
            for month in sorted(months, reverse=True):
                month_name = calendar.month_name[month] if 1 <= month <= 12 else "Unknown"
                month_label = f"{month_name} ({len(months[month])})"
                month_item = QTreeWidgetItem([month_label])
                year_item.addChild(month_item)

                games_sorted = sorted(months[month], key=lambda g: _sort_key(self._sort_column, g), reverse=reverse)
                for game in games_sorted:
                    label = f"{_abbrev_date(game.date)}  vs {game.opponent}  ({game.your_color})"
                    full_label = f"{game.date}  vs {game.opponent}  ({game.your_color})"
                    game_item = QTreeWidgetItem([label, game.time_label, game.time_category, game.site, game.result])
                    game_item.setData(0, GAME_ID_ROLE, game.id)
                    game_item.setToolTip(0, full_label)  # full year on hover
                    month_item.addChild(game_item)
                    self._items_by_id[game.id] = game_item

                if preserve_state and (year_label, month_label) in expanded_months:
                    month_item.setExpanded(True)
            if preserve_state and year_label in expanded_years:
                year_item.setExpanded(True)

        if not preserve_state:
            self.collapseAll()

        order = Qt.SortOrder.AscendingOrder if self._sort_ascending else Qt.SortOrder.DescendingOrder
        self.header().setSortIndicator(self._sort_column, order)

        if current_id is not None:
            item = self._items_by_id.get(current_id)
            if item is not None:
                self.setCurrentItem(item)

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
