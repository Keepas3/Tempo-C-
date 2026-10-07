"""Right-panel game browser: a tree grouped Year -> Month -> individual
games. Selecting a game emits its id so the rest of the UI can load it.
Column headers are clickable to sort games within each month by that
column, using domain-specific ordering (numeric time, category rank) rather
than Qt's default alphabetical text sort.
"""
from __future__ import annotations

import calendar

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QKeyEvent
from PySide6.QtWidgets import QHeaderView, QMenu, QTreeWidget, QTreeWidgetItem

from colors import DRAW_COLOR, LOSS_COLOR, MUTED_COLOR, TEXT_COLOR, WIN_COLOR
from db_reader import DbReader
from favorites import Favorites
from notes import Notes

GAME_ID_ROLE = 1000
COLUMNS = ["Game", "★", "Color", "Time", "Type", "Site", "Result", "Elo", "Opening", "Notes"]
(GAME_COLUMN, FAV_COLUMN, COLOR_COLUMN, TIME_COLUMN, TYPE_COLUMN, SITE_COLUMN,
 RESULT_COLUMN, ELO_COLUMN, OPENING_COLUMN, NOTES_COLUMN) = range(len(COLUMNS))
STAR_ON, STAR_OFF = "★", "☆"
STAR_COLOR = "#f5c518"
NOTE_TRUNCATE_CHARS = 28  # keeps the column narrow -- the full text is always in the tooltip

_RESULT_COLOR = {"Win": WIN_COLOR, "Loss": LOSS_COLOR, "Draw": DRAW_COLOR}

COLOR_ORDER = {"white": 0, "black": 1}
TYPE_ORDER = {"Bullet": 0, "Blitz": 1, "Rapid": 2, "Classical": 3, "Daily": 4, "Unknown": 5}
RESULT_ORDER = {"Win": 0, "Draw": 1, "Loss": 2, "?": 3}

# Which direction each column starts in the first time it's clicked (i.e.
# when switching to it from a different column). Game defaults to
# descending (most recent first, matching the initial unsorted load); the
# rest default to ascending (White before Black / lowest time / earliest
# category / A-Z / best result first). Elo defaults descending (highest
# rating first), matching how chess sites usually show it.
DEFAULT_ASCENDING = {GAME_COLUMN: False, FAV_COLUMN: True, COLOR_COLUMN: True, TIME_COLUMN: True, TYPE_COLUMN: True,
                     SITE_COLUMN: True, RESULT_COLUMN: True, ELO_COLUMN: False, OPENING_COLUMN: True, NOTES_COLUMN: True}


def _truncate_note(note: str) -> str:
    note = " ".join(note.split())  # collapse embedded newlines/extra whitespace to keep the row single-line
    if len(note) <= NOTE_TRUNCATE_CHARS:
        return note
    return note[:NOTE_TRUNCATE_CHARS - 1].rstrip() + "…"


def _day_only(date: str) -> str:
    """"2026.09.05" -> "05" -- the year and month are already shown once each
    on the tree's Year/Month grouping nodes, so repeating them on every row
    underneath is redundant; only the day actually varies row to row."""
    parts = date.split(".")
    if len(parts) == 3:
        return parts[2]
    return date


def _sort_key(column: int, game, notes_by_id: dict[int, str], favorite_ids: set[int]) -> object:
    if column == FAV_COLUMN:
        return 0 if game.id in favorite_ids else 1  # ascending = starred first
    if column == COLOR_COLUMN:
        return COLOR_ORDER.get(game.your_color, len(COLOR_ORDER))
    if column == TIME_COLUMN:
        return game.time_seconds
    if column == TYPE_COLUMN:
        return TYPE_ORDER.get(game.time_category, len(TYPE_ORDER))
    if column == SITE_COLUMN:
        return game.site
    if column == RESULT_COLUMN:
        return RESULT_ORDER.get(game.result, len(RESULT_ORDER))
    if column == ELO_COLUMN:
        return game.your_elo if game.your_elo is not None else 0
    if column == OPENING_COLUMN:
        return game.opening.lower()
    if column == NOTES_COLUMN:
        return notes_by_id.get(game.id, "")
    # column 0 (or anything unrecognized): "YYYY.MM.DD" then "HH:MM:SS" both sort correctly as text
    return (game.date, game.utc_time)


class GameBrowser(QTreeWidget):
    game_selected = Signal(int)
    # Up/Down, while a game row (not a Year/Month group node) is current,
    # jump the already-loaded board straight to that game's first/last
    # position -- see keyPressEvent. Carries the game id (like game_selected)
    # so a listener could double-check it matches whatever's actually loaded,
    # even though in practice the two never desync: this widget only ever
    # changes which row is "current" via a mouse click (which itself loads
    # that game), never via keyboard navigation, since Up/Down are fully
    # repurposed here instead of left as default row-to-row navigation.
    jump_to_start_requested = Signal(int)
    jump_to_end_requested = Signal(int)
    # (game_id, is_favorite) after a star is toggled from this widget.
    favorite_toggled = Signal(int, bool)

    def __init__(self, db: DbReader, notes: Notes, favorites: Favorites, parent=None):
        super().__init__(parent)
        self.db = db
        self.notes = notes
        self.favorites = favorites
        self._items_by_id: dict[int, QTreeWidgetItem] = {}
        self._tree_data: dict[int, dict[int, list]] = {}
        self._sort_column = 0
        self._sort_ascending = False  # matches the initial most-recent-first load
        self._live_query_enabled = False

        self.setHeaderLabels(COLUMNS)
        self.setAlternatingRowColors(True)  # zebra striping -- a dense table of near-identical rows is otherwise easy to lose your place in
        self.setUniformRowHeights(True)
        self.setStyleSheet("QTreeWidget::item { padding: 3px 0; }")  # a bit less cramped than the default row height
        # Every column has a fixed, user-resizable width -- the Game column
        # used to Stretch, which with nine other columns squeezed it down to
        # a sliver (cutting off month labels and opponent names). Widths are
        # sized so the whole row roughly fits the archive panel; anything
        # beyond that scrolls horizontally instead of cutting the Game
        # column short.
        self.setIndentation(14)  # default (20px x2 levels) wastes space the Game column needs
        self.header().setStretchLastSection(False)
        self.header().setMinimumSectionSize(24)
        for column, width in {
            GAME_COLUMN: 190, FAV_COLUMN: 28, COLOR_COLUMN: 48, TIME_COLUMN: 62, TYPE_COLUMN: 56,
            SITE_COLUMN: 72, RESULT_COLUMN: 48, ELO_COLUMN: 40, OPENING_COLUMN: 125, NOTES_COLUMN: 90,
        }.items():
            self.header().setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.setColumnWidth(column, width)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.header().setSortIndicatorShown(True)
        # Without this, sectionClicked never fires -- clicking a header does
        # nothing (the header just looks clickable because of the sort
        # indicator styling, but clicks aren't actually routed anywhere).
        self.header().setSectionsClickable(True)
        self.header().sectionClicked.connect(self._on_header_clicked)
        self.itemClicked.connect(self._on_item_clicked)
        self.refresh()

    def refresh(self) -> None:
        self._tree_data = self.db.games_by_year_month()
        self._rebuild(preserve_state=False)

    def set_live_query_enabled(self, enabled: bool) -> None:
        self._live_query_enabled = enabled
        if not enabled:
            self.refresh()  # revert to the exact normal, unfiltered view

    def show_filtered(self, games: list) -> None:
        """Renders `games` (GameRow objects matching the current board
        position) as the live-filtered view, reusing the same tree-building
        shape as refresh()/games_by_year_month(). Only meaningful while
        live query is enabled -- the caller is responsible for that gate."""
        tree: dict[int, dict[int, list]] = {}
        for game in games:
            tree.setdefault(game.year, {}).setdefault(game.month, []).append(game)
        self._tree_data = tree
        self._rebuild(preserve_state=True)

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
        # One query for every game's note rather than one per row -- the
        # tree can easily have hundreds of games.
        notes_by_id = self.notes.get_all_game_notes()
        favorite_ids = self.favorites.get_all()

        for year in sorted(self._tree_data, reverse=True):
            year_label = str(year) if year else "Unknown date"
            year_item = QTreeWidgetItem([year_label])
            year_item.setForeground(0, QBrush(QColor(TEXT_COLOR)))
            self.addTopLevelItem(year_item)
            months = self._tree_data[year]
            for month in sorted(months, reverse=True):
                # Abbreviated ("Sep" not "September") so the game count after
                # it never gets Qt-elided off a narrow tree column -- with
                # the full name, a long month (September, November, ...)
                # could push "(546)" past the visible width and get cut to
                # "September ..." with the count invisible.
                month_name = calendar.month_abbr[month] if 1 <= month <= 12 else "Unknown"
                month_label = f"{month_name} ({len(months[month])})"
                month_item = QTreeWidgetItem([month_label])
                month_item.setForeground(0, QBrush(QColor(TEXT_COLOR)))
                if 1 <= month <= 12:
                    month_item.setToolTip(0, f"{calendar.month_name[month]} {year}")  # full name on hover
                year_item.addChild(month_item)

                games_sorted = sorted(months[month], key=lambda g: _sort_key(self._sort_column, g, notes_by_id, favorite_ids), reverse=reverse)
                if self._sort_column == ELO_COLUMN:
                    # Second, stable pass: push games with no rating data to
                    # the end regardless of sort direction, rather than
                    # having them land wherever their placeholder 0 key
                    # happened to sort in either direction.
                    games_sorted.sort(key=lambda g: g.your_elo is None)
                elif self._sort_column == OPENING_COLUMN:
                    # Games with no recorded opening sort to the end in either direction.
                    games_sorted.sort(key=lambda g: not g.opening)
                elif self._sort_column == NOTES_COLUMN:
                    # Same idea: games with no note at all sort to the end
                    # regardless of direction, rather than empty strings
                    # winning every ascending sort by virtue of being "".
                    games_sorted.sort(key=lambda g: g.id not in notes_by_id)
                for game in games_sorted:
                    is_fav = game.id in favorite_ids
                    label = f"{_day_only(game.date)}  vs {game.opponent}"
                    time_part = f" {game.utc_time[:5]} UTC" if game.utc_time else ""
                    full_label = f"{game.date}{time_part}  vs {game.opponent}"
                    color_display = game.your_color.capitalize()
                    elo_display = str(game.your_elo) if game.your_elo is not None else ""
                    note = notes_by_id.get(game.id, "")
                    notes_display = _truncate_note(note)
                    game_item = QTreeWidgetItem(
                        [label, STAR_ON if is_fav else STAR_OFF, color_display, game.time_label, game.time_category, game.site, game.result,
                         elo_display, game.opening, notes_display])
                    game_item.setData(0, GAME_ID_ROLE, game.id)
                    game_item.setToolTip(0, full_label)  # full date on hover
                    if game.opening:
                        game_item.setToolTip(OPENING_COLUMN, game.opening)  # full name on hover, since the cell elides
                    if note:
                        game_item.setToolTip(NOTES_COLUMN, note)  # full text on hover, since the cell itself is truncated
                    # Qt's default item-text color resolves to black here (no
                    # palette is set; the app just inherits Windows' dark
                    # mode), which is unreadable against the dark background
                    # -- give every column an explicit, visible color instead
                    # of leaving it to that default. Result keeps its own
                    # win/loss/draw color instead of the plain default.
                    for col in range(len(COLUMNS)):
                        if col not in (RESULT_COLUMN, FAV_COLUMN):
                            game_item.setForeground(col, QBrush(QColor(TEXT_COLOR)))
                    game_item.setForeground(FAV_COLUMN, QBrush(QColor(STAR_COLOR if is_fav else MUTED_COLOR)))
                    game_item.setTextAlignment(FAV_COLUMN, Qt.AlignmentFlag.AlignCenter)
                    game_item.setToolTip(FAV_COLUMN, "Click to toggle favorite")
                    result_color = _RESULT_COLOR.get(game.result, MUTED_COLOR)
                    game_item.setForeground(RESULT_COLUMN, QBrush(QColor(result_color)))
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

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        game_id = item.data(0, GAME_ID_ROLE)
        if game_id is None:
            return
        if column == FAV_COLUMN:
            # Starring is its own action -- it must not also load the game
            # onto the board / print its info into the chat.
            self.toggle_favorite(game_id)
            return
        self.game_selected.emit(game_id)

    def toggle_favorite(self, game_id: int) -> None:
        self.set_favorite(game_id, not self.favorites.is_favorite(game_id))

    def set_favorite(self, game_id: int, favorite: bool) -> None:
        self.favorites.set_favorite(game_id, favorite)
        self._apply_star(game_id, favorite)
        self.favorite_toggled.emit(game_id, favorite)

    def _apply_star(self, game_id: int, favorite: bool) -> None:
        """Updates just this row's star in place (no full rebuild, so
        expansion/scroll/selection are untouched)."""
        item = self._items_by_id.get(game_id)
        if item is None:
            return
        item.setText(FAV_COLUMN, STAR_ON if favorite else STAR_OFF)
        item.setForeground(FAV_COLUMN, QBrush(QColor(STAR_COLOR if favorite else MUTED_COLOR)))

    def sync_favorite(self, game_id: int) -> None:
        """Re-reads one game's favorite state from the db, for when it was
        changed from somewhere other than this widget (e.g. the board's
        favorite button)."""
        self._apply_star(game_id, self.favorites.is_favorite(game_id))

    def _on_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        game_id = item.data(0, GAME_ID_ROLE) if item is not None else None
        if game_id is None:
            return
        menu = QMenu(self)
        is_fav = self.favorites.is_favorite(game_id)
        action = menu.addAction("Remove from favorites" if is_fav else "Add to favorites")
        if menu.exec(self.viewport().mapToGlobal(pos)) is action:
            self.set_favorite(game_id, not is_fav)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # Up/Down are repurposed here (jump to the current game's first/last
        # position) instead of Qt's default row-to-row selection movement --
        # only while a game row is current, so Up/Down on a Year/Month group
        # node still does normal tree navigation (there's no "game" to jump
        # for yet at that level).
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            item = self.currentItem()
            game_id = item.data(0, GAME_ID_ROLE) if item is not None else None
            if game_id is not None:
                if event.key() == Qt.Key.Key_Up:
                    self.jump_to_start_requested.emit(game_id)
                else:
                    self.jump_to_end_requested.emit(game_id)
                event.accept()
                return
        super().keyPressEvent(event)
