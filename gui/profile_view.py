"""One profile's full board + command panel + game browser + bookmarks, as
a single reusable widget. The outer MainWindow (gui/main.py) hosts one of
these per profile inside a QTabWidget -- this is a structural extraction of
what used to be MainWindow's whole body, unchanged in behavior.
"""
from __future__ import annotations

import chess
import chess.engine
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from analysis_cache import AnalysisCache
from board_widget import BoardWidget
from bookmarks import Bookmark, Bookmarks
from command_panel import CommandPanel
from db_reader import DbReader, game_summary_to_row
import engine as engine_module
from engine import EngineManager, LIVE_EVAL_TIERS
from engine_download import start_engine_download_flow
from fetch_worker import FetchWorker
from game_browser import GameBrowser
from profiles import ProfileRecord
from tempo_cli import TempoCli

# How long to wait after the board stops changing before firing a live-query
# request -- rapid clicking/navigation would otherwise launch a subprocess
# (find_games_by_move_prefix does a full games+moves table scan) on every
# single click.
LIVE_QUERY_DEBOUNCE_MS = 200

BOOKMARK_ID_ROLE = 1000


class BookmarksDialog(QDialog):
    def __init__(self, parent, bookmarks: Bookmarks, on_select):
        super().__init__(parent)
        self.setWindowTitle("Bookmarks")
        self.resize(500, 400)
        self._bookmarks = bookmarks
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
        self._bookmarks.delete(bookmark.id)
        self.list_widget.takeItem(self.list_widget.row(item))


class ProfileView(QWidget):
    """Board + game browser + command panel for one profile, each bound to
    that profile's own db file (and username, for the command panel's CLI
    calls)."""

    def __init__(self, profile: ProfileRecord, parent=None):
        super().__init__(parent)
        self.profile = profile
        self.current_game_id: int | None = None

        db_path = profile.resolved_db_path()
        self.db = DbReader(db_path)
        self.cli = TempoCli(db_path, profile.username)
        self.bookmarks = Bookmarks(db_path)
        self.cache = AnalysisCache(db_path)
        self.engine = EngineManager()

        self.command_panel = CommandPanel(self.db, self.cli, self.cache, self.engine)
        self.board = BoardWidget()
        self.browser = GameBrowser(self.db)

        self.browser.game_selected.connect(self.load_game)
        self.browser.game_selected.connect(self._on_browser_game_selected)
        self.command_panel.game_requested.connect(self.load_game)
        self.command_panel.move_requested.connect(self._on_move_requested)
        self.command_panel.archive_updated.connect(self.browser.refresh)
        self.board.position_changed.connect(self._update_nav_buttons)
        self.board.position_changed.connect(self._on_position_changed_for_live_query)
        self.board.position_changed.connect(self._on_position_changed_clears_eval)

        # Live board-query filter: debounced so rapid navigation doesn't fire
        # a query per click, and threaded (via FetchWorker) so the scan
        # doesn't block the GUI. A monotonic generation counter discards a
        # slow query's result if a newer position change has since started
        # a fresher one.
        self._live_query_generation = 0
        self._live_query_worker: FetchWorker | None = None
        self._live_query_timer = QTimer(self)
        self._live_query_timer.setSingleShot(True)
        self._live_query_timer.setInterval(LIVE_QUERY_DEBOUNCE_MS)
        self._live_query_timer.timeout.connect(self._run_live_query)

        self.live_query_checkbox = QCheckBox("Live query by board")
        self.live_query_checkbox.toggled.connect(self.browser.set_live_query_enabled)
        self.live_query_checkbox.toggled.connect(self._on_live_query_toggled)

        # On-demand engine analysis: runs once per "Analyze position" click
        # rather than continuously on every board change -- deliberately
        # not auto-triggered, so the engine only ever runs when you
        # actually ask it to (lighter on CPU/memory, and much less surface
        # area for anything engine-related to go wrong unattended). A
        # generation counter still guards against a slow analysis result
        # arriving after you've since moved on to a different position.
        self._live_eval_generation = 0
        self._live_eval_busy = False
        self._live_eval_worker: FetchWorker | None = None

        # When checked, selecting a game in the archive also prints /review's
        # eval-annotated move table (not just /show's header) into the chat.
        # Unchecked by default -- the review table is the heavier of the two.
        self.show_review_checkbox = QCheckBox("Also show move review on select")

        browser_controls = QHBoxLayout()
        browser_controls.addWidget(self.live_query_checkbox)
        browser_controls.addWidget(self.show_review_checkbox)
        browser_controls.addStretch(1)

        browser_container = QWidget()
        browser_layout = QVBoxLayout(browser_container)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_layout.addLayout(browser_controls)
        browser_layout.addWidget(self.browser)

        center = self._build_center_panel()

        splitter = QSplitter()
        splitter.addWidget(self.command_panel)
        splitter.addWidget(center)
        splitter.addWidget(browser_container)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        splitter.setSizes([320, 620, 620])

    def _build_center_panel(self) -> QWidget:
        self.prev_btn = QPushButton("< Prev")
        self.next_btn = QPushButton("Next >")
        self.mainline_btn = QPushButton("Return to mainline")
        self.flip_btn = QPushButton("Flip board")
        self.bookmark_btn = QPushButton("Bookmark position")
        self.view_bookmarks_btn = QPushButton("View bookmarks")

        self.prev_btn.clicked.connect(self.board.prev_ply)
        self.next_btn.clicked.connect(self.board.next_ply)
        self.mainline_btn.clicked.connect(self.board.return_to_mainline)
        self.flip_btn.clicked.connect(self.board.flip)
        self.bookmark_btn.clicked.connect(self._on_bookmark)
        self.view_bookmarks_btn.clicked.connect(self._on_view_bookmarks)

        nav_row = QHBoxLayout()
        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.next_btn)
        nav_row.addWidget(self.mainline_btn)
        nav_row.addWidget(self.flip_btn)

        bookmark_row = QHBoxLayout()
        bookmark_row.addWidget(self.bookmark_btn)
        bookmark_row.addWidget(self.view_bookmarks_btn)

        self.status_label = QLabel("No game loaded.")

        # Engine status row: shows install state and, once installed,
        # doubles as the row for on-demand analysis controls. Not a
        # first-run modal -- the download only happens if/when the user
        # clicks the button. Analysis itself is on-demand (one click =
        # one evaluation of the current position), not continuous --
        # deliberately lighter-weight than auto-analyzing on every move.
        self.engine_status_label = QLabel()
        self.download_engine_btn = QPushButton("Download Stockfish")
        self.download_engine_btn.clicked.connect(self._on_download_engine_clicked)
        self.analyze_btn = QPushButton("Analyze position")
        self.analyze_btn.clicked.connect(self._run_analysis)
        self.eval_tier_combo = QComboBox()
        for label, depth in LIVE_EVAL_TIERS:
            self.eval_tier_combo.addItem(label, depth)
        self.eval_tier_combo.setCurrentIndex(1)  # "Balanced"
        self.eval_label = QLabel("")

        engine_row = QHBoxLayout()
        engine_row.addWidget(self.engine_status_label)
        engine_row.addWidget(self.download_engine_btn)
        engine_row.addWidget(self.analyze_btn)
        engine_row.addWidget(self.eval_tier_combo)
        engine_row.addWidget(self.eval_label)
        engine_row.addStretch(1)
        self._refresh_engine_status_row()

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addWidget(self.board, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(nav_row)
        layout.addLayout(bookmark_row)
        layout.addLayout(engine_row)
        layout.addStretch(1)

        container = QWidget()
        container.setLayout(layout)
        return container

    def prev_ply(self) -> None:
        self.board.prev_ply()

    def next_ply(self) -> None:
        self.board.next_ply()

    def go_to_start(self) -> None:
        # set_ply(0), not return_to_mainline() -- this jumps to the true
        # start of the game (ply 0) even from a branched sideline, rather
        # than back to the branch point.
        self.board.set_ply(0)

    def load_game(self, game_id: int) -> None:
        detail = self.db.load_game(game_id)
        if detail is None:
            self.status_label.setText(f"No game with id {game_id}")
            return
        self.current_game_id = game_id
        sans = [m.san for m in detail.moves]
        self.board.load_game(sans)
        # Auto-orient to your own side -- white stays the default view
        # (unchanged for white games); black games flip so you're always
        # looking at the board from the side you actually played.
        self.board.set_orientation(chess.BLACK if detail.your_color == "black" else chess.WHITE)
        self.status_label.setText(
            f"#{game_id}  {detail.white} vs {detail.black}  ({detail.date}, {detail.result})  [{detail.site}]"
        )
        self._update_nav_buttons()
        self.browser.select_game(game_id)  # keep the browser's selection in sync regardless of how the game was loaded

    def _on_browser_game_selected(self, game_id: int) -> None:
        # Only for clicks in the archive panel -- /show and /review typed in
        # the chat already print their own info before calling load_game,
        # so this must not also fire for that path (it would double-print).
        self.command_panel.display_selected_game(game_id, self.show_review_checkbox.isChecked())

    def _on_move_requested(self, game_id: int, ply: int) -> None:
        # Clicking a move in a /review table -- load that game first if it
        # isn't already the one on the board, then jump straight to the
        # position right after that move.
        if self.current_game_id != game_id:
            self.load_game(game_id)
        self.board.set_ply(ply)

    def _update_nav_buttons(self) -> None:
        on_main = self.board.on_mainline
        self.prev_btn.setEnabled(on_main and self.board.mainline_ply > 0)
        self.next_btn.setEnabled(on_main and self.board.mainline_ply < len(self.board.mainline_sans))
        self.mainline_btn.setEnabled(not on_main)

    # --- Live board-query archive filter -----------------------------------

    def _on_position_changed_for_live_query(self) -> None:
        if self.live_query_checkbox.isChecked():
            self._live_query_timer.start()  # restarts the debounce window

    def _on_live_query_toggled(self, checked: bool) -> None:
        if checked:
            self._live_query_timer.start()

    def _run_live_query(self) -> None:
        if not self.live_query_checkbox.isChecked():
            return
        self._live_query_generation += 1
        generation = self._live_query_generation
        sequence = self.board.current_san_sequence()

        worker = FetchWorker(lambda: self.cli.games_by_move_prefix(sequence))
        worker.succeeded.connect(lambda data: self._on_live_query_succeeded(generation, data))
        worker.failed.connect(lambda msg: self._on_live_query_failed(generation, msg))
        self._live_query_worker = worker
        worker.start()

    def _on_live_query_succeeded(self, generation: int, data: dict) -> None:
        if generation != self._live_query_generation:
            return  # a newer position change has since superseded this query
        games = [game_summary_to_row(g) for g in data["games"]]
        self.browser.show_filtered(games)

    def _on_live_query_failed(self, generation: int, message: str) -> None:
        if generation != self._live_query_generation:
            return
        self.status_label.setText(f"Live query failed: {message}")

    # --- Engine (Stockfish) analysis -----------------------------------

    def _refresh_engine_status_row(self) -> None:
        if engine_module.is_available():
            version = engine_module.engine_version() or "engine"
            self.engine_status_label.setText(f"Engine: {version} ready")
            self.download_engine_btn.hide()
            self.analyze_btn.show()
            self.eval_tier_combo.show()
            self.eval_label.show()
        else:
            self.engine_status_label.setText("Engine: not installed")
            self.download_engine_btn.show()
            self.analyze_btn.hide()
            self.eval_tier_combo.hide()
            self.eval_label.hide()

    def _on_download_engine_clicked(self) -> None:
        def on_complete(succeeded: bool) -> None:
            if succeeded:
                self._refresh_engine_status_row()

        start_engine_download_flow(self, on_complete)

    def _on_position_changed_clears_eval(self) -> None:
        # The old eval/arrow describe a position you've since moved away
        # from -- clear them rather than leaving stale numbers on screen.
        # Deliberately does NOT start a new analysis: that only happens on
        # an explicit "Analyze position" click (see _run_analysis).
        self.board.set_best_move_arrow(None)
        self.eval_label.setText("")

    def _run_analysis(self) -> None:
        """Analyzes the CURRENT board position once, on explicit request --
        not re-triggered automatically on future position changes. Lighter
        on CPU/memory than continuously re-analyzing on every move, and
        much less surface area for anything engine-related to go wrong
        unattended."""
        if self._live_eval_busy:
            return  # a click while one is already running is just ignored (the button is disabled meanwhile)

        self._live_eval_busy = True
        self.analyze_btn.setEnabled(False)
        self._live_eval_generation += 1
        generation = self._live_eval_generation
        fen = self.board.fen()
        depth = self.eval_tier_combo.currentData()
        self.eval_label.setText("Analyzing...")

        def analyze() -> dict:
            live_engine = self.engine.ensure_live_engine()
            board = chess.Board(fen)
            info = live_engine.analyse(board, chess.engine.Limit(depth=depth))
            score = info["score"].white()
            pv = info.get("pv", [])
            return {
                "cp": score.score(), "mate": score.mate(),
                "best_move_uci": pv[0].uci() if pv else None,
                "fen": fen,
            }

        worker = FetchWorker(analyze)
        worker.succeeded.connect(lambda data: self._on_analysis_succeeded(generation, data))
        worker.failed.connect(lambda msg: self._on_analysis_failed(generation, msg))
        self._live_eval_worker = worker
        worker.start()

    def _on_analysis_done(self) -> None:
        self._live_eval_busy = False
        self.analyze_btn.setEnabled(True)

    def _on_analysis_succeeded(self, generation: int, data: dict) -> None:
        self._on_analysis_done()
        if generation != self._live_eval_generation:
            return  # you've since moved on to a different position -- discard this stale result
        if data["mate"] is not None:
            self.eval_label.setText(f"M{data['mate']}" if data["mate"] > 0 else f"-M{abs(data['mate'])}")
        elif data["cp"] is not None:
            self.eval_label.setText(f"{data['cp'] / 100.0:+.2f}")
        else:
            self.eval_label.setText("--")
        if data["best_move_uci"] and self.board.fen() == data["fen"]:
            # Only draw the arrow if the board hasn't moved on again since
            # this (now-current-generation) result was requested.
            self.board.set_best_move_arrow(chess.Move.from_uci(data["best_move_uci"]))

    def _on_analysis_failed(self, generation: int, message: str) -> None:
        self._on_analysis_done()
        if generation != self._live_eval_generation:
            return
        self.eval_label.setText("(engine error)")
        self.status_label.setText(f"Analysis failed: {message}")

    def cleanup(self) -> None:
        """Terminates any live Stockfish subprocess(es) owned by this
        profile tab -- called from MainWindow.closeEvent so no stockfish.exe
        processes are left orphaned when the app exits. Also cancels/waits
        for any in-flight batch review worker first -- letting the process
        exit while a QThread is still running is the same fatal-abort
        hazard as overwriting it mid-run (see CommandPanel.cleanup)."""
        self.command_panel.cleanup()
        self.engine.quit_all()

    def _on_bookmark(self) -> None:
        note, ok = QInputDialog.getText(self, "Bookmark position", "Note (optional):")
        if not ok:
            return
        source_ply = self.board.mainline_ply if self.board.on_mainline else None
        self.bookmarks.add(
            fen=self.board.fen(),
            note=note,
            source_game_id=self.current_game_id,
            source_ply=source_ply,
        )
        self.status_label.setText("Position bookmarked.")

    def _on_view_bookmarks(self) -> None:
        dialog = BookmarksDialog(self, self.bookmarks, self._load_bookmark)
        dialog.exec()

    def _load_bookmark(self, bookmark: Bookmark) -> None:
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
