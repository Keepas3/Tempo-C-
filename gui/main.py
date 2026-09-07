"""Tempo GUI entry point: command panel (left) | board (center) | game
browser (right), plus bookmarking. See ../.claude-plan (or the repo's
conversation history) for the design rationale.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import bookmarks
import db_reader
from board_widget import BoardWidget
from command_panel import CommandPanel
from game_browser import GameBrowser

BOOKMARK_ID_ROLE = 1000


class BookmarksDialog(QDialog):
    def __init__(self, parent, on_select):
        super().__init__(parent)
        self.setWindowTitle("Bookmarks")
        self.resize(500, 400)
        self._on_select = on_select

        self.list_widget = QListWidget()
        for b in bookmarks.list_all():
            label = b.note or "(no note)"
            if b.source_game_id is not None:
                label = f"{label}  [game #{b.source_game_id}, ply {b.source_ply}]"
            item = QListWidgetItem(f"{label}   -- {b.created_at}")
            item.setData(BOOKMARK_ID_ROLE, b)
            self.list_widget.addItem(item)
        self.list_widget.itemDoubleClicked.connect(self._on_double_click)

        delete_btn = QPushButton("Delete selected")
        delete_btn.clicked.connect(self._on_delete)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Double-click a bookmark to load it on the board."))
        layout.addWidget(self.list_widget)
        layout.addWidget(delete_btn)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        bookmark = item.data(BOOKMARK_ID_ROLE)
        self._on_select(bookmark)
        self.accept()

    def _on_delete(self) -> None:
        item = self.list_widget.currentItem()
        if item is None:
            return
        bookmark = item.data(BOOKMARK_ID_ROLE)
        bookmarks.delete(bookmark.id)
        self.list_widget.takeItem(self.list_widget.row(item))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tempo - Game Archive")
        self.current_game_id: int | None = None

        self.command_panel = CommandPanel()
        self.board = BoardWidget()
        self.browser = GameBrowser()

        self.browser.game_selected.connect(self.load_game)
        self.command_panel.game_requested.connect(self.load_game)
        self.command_panel.archive_updated.connect(self.browser.refresh)
        self.board.position_changed.connect(self._update_nav_buttons)

        center = self._build_center_panel()

        splitter = QSplitter()
        splitter.addWidget(self.command_panel)
        splitter.addWidget(center)
        splitter.addWidget(self.browser)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
        self.setCentralWidget(splitter)
        self.resize(1700, 800)
        splitter.setSizes([320, 620, 620])

        # Left/Right navigate the board like the Prev/Next buttons, from
        # anywhere in the window -- except while actually typing/editing
        # text (the command input), where the arrow keys should just move
        # the text cursor as normal. An application-level filter is used
        # (rather than overriding keyPressEvent) so this works regardless of
        # which panel currently has focus -- the browser tree and output
        # pane would otherwise consume Left/Right themselves for their own
        # navigation before a window-level handler ever saw the event.
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress and not isinstance(QApplication.focusWidget(), QLineEdit):
            if event.key() == Qt.Key.Key_Left:
                self.board.prev_ply()
                return True
            if event.key() == Qt.Key.Key_Right:
                self.board.next_ply()
                return True
        return super().eventFilter(obj, event)

    def _build_center_panel(self) -> QWidget:
        self.prev_btn = QPushButton("< Prev")
        self.next_btn = QPushButton("Next >")
        self.mainline_btn = QPushButton("Return to mainline")
        self.bookmark_btn = QPushButton("Bookmark position")
        self.view_bookmarks_btn = QPushButton("View bookmarks")

        self.prev_btn.clicked.connect(self.board.prev_ply)
        self.next_btn.clicked.connect(self.board.next_ply)
        self.mainline_btn.clicked.connect(self.board.return_to_mainline)
        self.bookmark_btn.clicked.connect(self._on_bookmark)
        self.view_bookmarks_btn.clicked.connect(self._on_view_bookmarks)

        nav_row = QHBoxLayout()
        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.next_btn)
        nav_row.addWidget(self.mainline_btn)

        bookmark_row = QHBoxLayout()
        bookmark_row.addWidget(self.bookmark_btn)
        bookmark_row.addWidget(self.view_bookmarks_btn)

        self.status_label = QLabel("No game loaded.")

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.board, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(nav_row)
        layout.addLayout(bookmark_row)
        layout.addStretch(1)

        container = QWidget()
        container.setLayout(layout)
        return container

    def load_game(self, game_id: int) -> None:
        detail = db_reader.load_game(game_id)
        if detail is None:
            self.status_label.setText(f"No game with id {game_id}")
            return
        self.current_game_id = game_id
        sans = [m.san for m in detail.moves]
        self.board.load_game(sans)
        self.status_label.setText(
            f"#{game_id}  {detail.white} vs {detail.black}  ({detail.date}, {detail.result})  [{detail.site}]"
        )
        self._update_nav_buttons()
        self.browser.select_game(game_id)  # keep the browser's selection in sync regardless of how the game was loaded

    def _update_nav_buttons(self) -> None:
        on_main = self.board.on_mainline
        self.prev_btn.setEnabled(on_main and self.board.mainline_ply > 0)
        self.next_btn.setEnabled(on_main and self.board.mainline_ply < len(self.board.mainline_sans))
        self.mainline_btn.setEnabled(not on_main)

    def _on_bookmark(self) -> None:
        note, ok = QInputDialog.getText(self, "Bookmark position", "Note (optional):")
        if not ok:
            return
        source_ply = self.board.mainline_ply if self.board.on_mainline else None
        bookmarks.add(
            fen=self.board.fen(),
            note=note,
            source_game_id=self.current_game_id,
            source_ply=source_ply,
        )
        self.status_label.setText("Position bookmarked.")

    def _on_view_bookmarks(self) -> None:
        dialog = BookmarksDialog(self, self._load_bookmark)
        dialog.exec()

    def _load_bookmark(self, bookmark: bookmarks.Bookmark) -> None:
        if bookmark.source_game_id is not None and bookmark.source_ply is not None:
            self.load_game(bookmark.source_game_id)
            self.board.set_ply(bookmark.source_ply)
        else:
            self.board.mainline_sans = []
            self.board.board.set_fen(bookmark.fen)
            self.board.on_mainline = False
            self.board.mainline_ply = 0
            self.board._last_move = None
            self.board.selected_square = None
            self.board._render()
            self.current_game_id = None
            self.status_label.setText(f"Loaded bookmark: {bookmark.note or '(no note)'}")
        self._update_nav_buttons()


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
