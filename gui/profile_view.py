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
from board_widget import BOARD_SIZE, BoardWidget
from bookmarks import Bookmark, Bookmarks
from command_panel import CommandPanel
from db_reader import DbReader, game_summary_to_row
import engine as engine_module
from engine import EngineManager, LIVE_EVAL_TIERS
from engine_download import start_engine_download_flow
from eval_bar import EvalBar
from explorer_panel import ExplorerPanel
from fetch_worker import FetchWorker
from game_browser import GameBrowser
from profiles import ProfileRecord
from tempo_cli import TempoCli

# How long to wait after the board stops changing before firing a live-eval
# request -- rapid clicking/navigation shouldn't launch an engine call per
# click (see the busy/pending guard in _run_live_eval for the other half of
# this: unlike the live-query filter, overlapping calls on the same engine
# subprocess aren't safe, so a debounce alone isn't enough here).
LIVE_EVAL_DEBOUNCE_MS = 300
# After this many consecutive engine failures, live eval turns itself off
# rather than continuing to retry a broken engine on every position change.
LIVE_EVAL_MAX_FAILURES = 3

# How long to wait after the board stops changing before firing a live-query
# request -- rapid clicking/navigation would otherwise launch a subprocess
# (find_games_by_move_prefix does a full games+moves table scan) on every
# single click.
LIVE_QUERY_DEBOUNCE_MS = 200
# Same debounce idea, for the board-driven opening explorer panel.
LIVE_EXPLORER_DEBOUNCE_MS = 200

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
        self.explorer_panel = ExplorerPanel()

        self.browser.game_selected.connect(self.load_game)
        self.browser.game_selected.connect(self._on_browser_game_selected)
        self.command_panel.game_requested.connect(self.load_game)
        self.command_panel.move_requested.connect(self._on_move_requested)
        self.command_panel.archive_updated.connect(self.browser.refresh)
        self.board.position_changed.connect(self._update_nav_buttons)
        self.board.position_changed.connect(self._on_position_changed_for_live_query)
        self.board.position_changed.connect(self._on_position_changed_for_live_eval)
        self.board.position_changed.connect(self._on_position_changed_for_live_explorer)
        self.explorer_panel.enabled_changed.connect(self._on_live_explorer_toggled)
        self.explorer_panel.color_changed.connect(lambda _c: self._live_explorer_timer.start())
        self.explorer_panel.move_clicked.connect(self._on_explorer_move_clicked)

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

        # Live opening explorer: same debounce/worker/generation-counter
        # pattern as live-query above, but rendering into self.explorer_panel
        # instead of the archive browser.
        self._live_explorer_generation = 0
        self._live_explorer_worker: FetchWorker | None = None
        self._live_explorer_timer = QTimer(self)
        self._live_explorer_timer.setSingleShot(True)
        self._live_explorer_timer.setInterval(LIVE_EXPLORER_DEBOUNCE_MS)
        self._live_explorer_timer.timeout.connect(self._run_live_explorer)

        # Live engine eval: same debounce idea as live-query, but a
        # chess.engine.SimpleEngine process isn't safe for overlapping
        # calls, so this also needs a busy/pending guard -- a trigger that
        # arrives while a call is already in flight just marks "pending"
        # rather than starting a second concurrent call; when the in-flight
        # call finishes, a pending trigger immediately re-runs against
        # whatever the board's position actually is by then (not a stashed
        # stale one), so a burst of rapid navigation collapses to exactly
        # one more analysis of the final position.
        self._live_eval_generation = 0
        self._live_eval_busy = False
        self._live_eval_pending = False
        self._live_eval_worker: FetchWorker | None = None
        self._live_eval_timer = QTimer(self)
        self._live_eval_timer.setSingleShot(True)
        self._live_eval_timer.setInterval(LIVE_EVAL_DEBOUNCE_MS)
        self._live_eval_timer.timeout.connect(self._run_live_eval)
        # Safety net: if the engine keeps failing (e.g. its process was
        # killed by antivirus, or repeatedly crashes), stop retrying on
        # every position change and turn the feature off with a clear
        # message instead of leaving it silently failing forever.
        self._live_eval_failure_count = 0

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

        # Game list on top, opening explorer below -- both are board-position-
        # driven archive views, sharing the right-hand column.
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.addWidget(browser_container)
        right_splitter.addWidget(self.explorer_panel)
        right_splitter.setStretchFactor(0, 2)
        right_splitter.setStretchFactor(1, 1)

        center = self._build_center_panel()

        splitter = QSplitter()
        splitter.addWidget(self.command_panel)
        splitter.addWidget(center)
        splitter.addWidget(right_splitter)
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
        self.flip_btn.clicked.connect(self._sync_eval_bar_orientation)
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
        # doubles as the row for live-eval controls. Not a first-run modal
        # -- the download only happens if/when the user clicks the button.
        self.engine_status_label = QLabel()
        self.download_engine_btn = QPushButton("Download Stockfish")
        self.download_engine_btn.clicked.connect(self._on_download_engine_clicked)
        self.live_eval_checkbox = QCheckBox("Live engine eval")
        self.live_eval_checkbox.setChecked(False)  # opt-in, CPU-consuming, unlike the archive-panel checkboxes
        self.live_eval_checkbox.toggled.connect(self._on_live_eval_toggled)
        self.eval_tier_combo = QComboBox()
        for label, depth in LIVE_EVAL_TIERS:
            self.eval_tier_combo.addItem(label, depth)
        self.eval_tier_combo.setCurrentIndex(1)  # "Balanced"
        self.eval_label = QLabel("")

        # Multiple lines: when on, each live-eval request asks the engine
        # for its top 3 candidate moves (MultiPV=3) instead of just the
        # best one, so you can compare the 2nd/3rd-best alternatives too.
        self.multipv_checkbox = QCheckBox("Multiple lines (top 3)")
        self.multipv_checkbox.setChecked(False)
        self.multipv_checkbox.toggled.connect(self._on_multipv_toggled)
        self.multipv_lines_label = QLabel("")

        # Visual white/black advantage bar next to the board -- mirrors
        # eval_label's number, not a separate data source. Sized to match
        # the board's own height so it reads as one unit alongside it.
        self.eval_bar = EvalBar(BOARD_SIZE)
        self.eval_bar.set_orientation(self.board.orientation == chess.WHITE)

        engine_row = QHBoxLayout()
        engine_row.addWidget(self.engine_status_label)
        engine_row.addWidget(self.download_engine_btn)
        engine_row.addWidget(self.live_eval_checkbox)
        engine_row.addWidget(self.eval_tier_combo)
        engine_row.addStretch(1)

        multipv_row = QHBoxLayout()
        multipv_row.addWidget(self.multipv_checkbox)
        multipv_row.addWidget(self.multipv_lines_label)
        multipv_row.addStretch(1)

        self._refresh_engine_status_row()

        # The eval number sits right above its bar, not off in engine_row,
        # so the score and the bar it describes read as one readout.
        self.eval_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        eval_column = QVBoxLayout()
        eval_column.addWidget(self.eval_label)
        eval_column.addWidget(self.eval_bar)

        board_row = QHBoxLayout()
        board_row.addStretch(1)
        board_row.addLayout(eval_column)
        board_row.addWidget(self.board)
        board_row.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addLayout(board_row)
        layout.addLayout(nav_row)
        layout.addLayout(bookmark_row)
        layout.addLayout(engine_row)
        layout.addLayout(multipv_row)
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
        self._sync_eval_bar_orientation()
        self.explorer_panel.set_color(detail.your_color)
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

    def _sync_eval_bar_orientation(self) -> None:
        self.eval_bar.set_orientation(self.board.orientation == chess.WHITE)

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

    # --- Live board-driven opening explorer ---------------------------------

    def _on_position_changed_for_live_explorer(self) -> None:
        if self.explorer_panel.is_enabled():
            self._live_explorer_timer.start()  # restarts the debounce window

    def _on_live_explorer_toggled(self, checked: bool) -> None:
        if checked:
            self._live_explorer_timer.start()

    def _run_live_explorer(self) -> None:
        if not self.explorer_panel.is_enabled():
            return
        self._live_explorer_generation += 1
        generation = self._live_explorer_generation
        sequence = self.board.current_san_sequence()
        color = self.explorer_panel.selected_color()

        worker = FetchWorker(lambda: self.cli.explorer(color, sequence))
        worker.succeeded.connect(lambda data: self._on_live_explorer_succeeded(generation, data))
        worker.failed.connect(lambda msg: self._on_live_explorer_failed(generation, msg))
        self._live_explorer_worker = worker
        worker.start()

    def _on_live_explorer_succeeded(self, generation: int, data: dict) -> None:
        if generation != self._live_explorer_generation:
            return  # a newer position change has since superseded this query
        self.explorer_panel.render(data["replies"])

    def _on_live_explorer_failed(self, generation: int, message: str) -> None:
        if generation != self._live_explorer_generation:
            return
        self.explorer_panel.render_error(message)

    def _on_explorer_move_clicked(self, san: str) -> None:
        # push_san's own position_changed emit drives the next refresh.
        self.board.push_san(san)

    # --- Engine (Stockfish) analysis -----------------------------------

    def _refresh_engine_status_row(self) -> None:
        if engine_module.is_available():
            version = engine_module.engine_version() or "engine"
            self.engine_status_label.setText(f"Engine: {version} ready")
            self.download_engine_btn.hide()
            self.live_eval_checkbox.show()
            self.eval_tier_combo.show()
            self.eval_label.show()
            self.eval_bar.show()
            self.multipv_checkbox.show()
            self.multipv_lines_label.show()
        else:
            self.engine_status_label.setText("Engine: not installed")
            self.download_engine_btn.show()
            self.live_eval_checkbox.hide()
            self.eval_tier_combo.hide()
            self.eval_label.hide()
            self.eval_bar.hide()
            self.multipv_checkbox.hide()
            self.multipv_lines_label.hide()

    def _on_download_engine_clicked(self) -> None:
        def on_complete(succeeded: bool) -> None:
            if succeeded:
                self._refresh_engine_status_row()

        start_engine_download_flow(self, on_complete)

    def _on_live_eval_toggled(self, checked: bool) -> None:
        if checked:
            self._live_eval_failure_count = 0  # fresh start each time it's turned on
            self._live_eval_timer.start()
        else:
            self.board.set_best_move_arrow(None)
            self.board.set_secondary_move_arrows([])
            self.eval_label.setText("")
            self.eval_bar.clear()
            self.multipv_lines_label.setText("")

    def _on_multipv_toggled(self, checked: bool) -> None:
        self.multipv_lines_label.setText("")
        self.board.set_secondary_move_arrows([])
        if self.live_eval_checkbox.isChecked():
            self._live_eval_timer.start()  # re-run against the current position with the new MultiPV setting

    def _on_position_changed_for_live_eval(self) -> None:
        if self.live_eval_checkbox.isChecked():
            self.board.set_best_move_arrow(None)  # clear the stale arrows while the new position is pending
            self.board.set_secondary_move_arrows([])
            self._live_eval_timer.start()

    def _run_live_eval(self) -> None:
        if not self.live_eval_checkbox.isChecked():
            return
        if self._live_eval_busy:
            self._live_eval_pending = True
            return

        self._live_eval_busy = True
        self._live_eval_generation += 1
        generation = self._live_eval_generation
        fen = self.board.fen()
        depth = self.eval_tier_combo.currentData()
        multipv = 3 if self.multipv_checkbox.isChecked() else None

        def line_from_info(info: dict) -> dict:
            score = info["score"].white()
            pv = info.get("pv", [])
            return {"cp": score.score(), "mate": score.mate(), "best_move_uci": pv[0].uci() if pv else None}

        def analyze() -> dict:
            live_engine = self.engine.ensure_live_engine()
            board = chess.Board(fen)
            result = live_engine.analyse(board, chess.engine.Limit(depth=depth), multipv=multipv)
            # analyse() returns a single info dict normally, but a list of
            # up to `multipv` info dicts (ranked best-first) when multipv is
            # set -- normalize both shapes into "lines" here so the rest of
            # the pipeline only has one shape to deal with.
            lines = [line_from_info(i) for i in result] if multipv else [line_from_info(result)]
            top = lines[0]
            return {
                "cp": top["cp"], "mate": top["mate"], "best_move_uci": top["best_move_uci"],
                "fen": fen, "lines": lines if multipv else None,
            }

        worker = FetchWorker(analyze)
        worker.succeeded.connect(lambda data: self._on_live_eval_succeeded(generation, data))
        worker.failed.connect(lambda msg: self._on_live_eval_failed(generation, msg))
        self._live_eval_worker = worker
        worker.start()

    def _on_live_eval_done(self) -> None:
        self._live_eval_busy = False
        if self._live_eval_pending:
            self._live_eval_pending = False
            self._run_live_eval()  # re-reads the board's current position, not a stashed one

    def _on_live_eval_succeeded(self, generation: int, data: dict) -> None:
        self._on_live_eval_done()
        self._live_eval_failure_count = 0
        if generation != self._live_eval_generation:
            return  # a newer position change has since superseded this result
        if data["mate"] is not None:
            self.eval_label.setText(f"M{data['mate']}" if data["mate"] > 0 else f"-M{abs(data['mate'])}")
        elif data["cp"] is not None:
            self.eval_label.setText(f"{data['cp'] / 100.0:+.2f}")
        else:
            self.eval_label.setText("--")
        self.eval_bar.set_eval(data["cp"], data["mate"])
        if data["best_move_uci"] and self.board.fen() == data["fen"]:
            # Only draw the arrow if the board hasn't moved on again since
            # this (now-current-generation) result was requested.
            self.board.set_best_move_arrow(chess.Move.from_uci(data["best_move_uci"]))
        self._render_multipv_lines(data.get("lines"), data["fen"])

    def _render_multipv_lines(self, lines: list[dict] | None, fen: str) -> None:
        if not lines or self.board.fen() != fen:
            self.multipv_lines_label.setText("")
            self.board.set_secondary_move_arrows([])
            return
        rows = []
        for i, line in enumerate(lines, start=1):
            if line["mate"] is not None:
                score_str = f"M{line['mate']}" if line["mate"] > 0 else f"-M{abs(line['mate'])}"
            elif line["cp"] is not None:
                score_str = f"{line['cp'] / 100.0:+.2f}"
            else:
                score_str = "--"
            move_str = ""
            if line["best_move_uci"]:
                move = chess.Move.from_uci(line["best_move_uci"])
                move_str = chess.Board(fen).san(move)
            rows.append(f"{i}. {score_str}  {move_str}")
        self.multipv_lines_label.setText("   ".join(rows))
        # Faded arrows for the 2nd/3rd-best lines (index 0 is the best move,
        # already drawn by set_best_move_arrow in _on_live_eval_succeeded).
        secondary_moves = [
            chess.Move.from_uci(line["best_move_uci"])
            for line in lines[1:3] if line["best_move_uci"]
        ]
        self.board.set_secondary_move_arrows(secondary_moves)

    def _on_live_eval_failed(self, generation: int, message: str) -> None:
        self._on_live_eval_done()
        self._live_eval_failure_count += 1
        if generation != self._live_eval_generation:
            return
        self.eval_label.setText("(engine error)")
        self.eval_bar.clear()
        self.multipv_lines_label.setText("")
        self.board.set_secondary_move_arrows([])
        if self._live_eval_failure_count >= LIVE_EVAL_MAX_FAILURES:
            self.live_eval_checkbox.setChecked(False)  # also clears the arrow/label via _on_live_eval_toggled
            self.status_label.setText(
                f"Live engine eval disabled after {LIVE_EVAL_MAX_FAILURES} repeated errors: {message}")

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
