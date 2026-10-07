"""One profile's full board + command panel + game browser + bookmarks, as
a single reusable widget. The outer MainWindow (gui/main.py) hosts one of
these per profile inside a QTabWidget -- this is a structural extraction of
what used to be MainWindow's whole body, unchanged in behavior.
"""
from __future__ import annotations

import html
from datetime import datetime

import chess
import chess.engine
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
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
from colors import HEADER_COLOR, LOSS_COLOR, MUTED_COLOR, TEXT_COLOR, WIN_COLOR
from command_panel import CommandPanel
from db_reader import DbReader, game_summary_to_row
import engine as engine_module
from engine import EngineManager, LIVE_EVAL_TIERS
from engine_download import start_engine_download_flow
from eval_bar import EvalBar
from explorer_panel import ExplorerPanel
from fetch_worker import FetchWorker
from game_browser import GameBrowser
from favorites import Favorites
from game_search import GameSearchFilter, search_games
from notes import Notes
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

# The "Larger board" toggle's enlarged size -- BOARD_SIZE (imported from
# board_widget) is the normal/default size.
LARGE_BOARD_SIZE = 640

BOOKMARK_ID_ROLE = 1000


def _format_bookmark_timestamp(created_at: str) -> str:
    """Bookmarks.add() stores this as datetime.now(timezone.utc).isoformat()
    -- parse that back into a compact, locale-local, human-readable stamp
    instead of showing the raw ISO string."""
    try:
        dt = datetime.fromisoformat(created_at)
        return dt.astimezone().strftime("%b %d, %Y %I:%M %p").replace(" 0", " ")
    except ValueError:
        return created_at


class _BookmarkRowWidget(QWidget):
    """One bookmark's row: the note as the prominent, word-wrapped line
    (previously squeezed onto the same single line as the game/ply/date
    metadata, which is why it was hard to read -- see the docked note
    below), with that metadata demoted to a second, smaller/muted line.

    A plain QListWidgetItem string can't word-wrap or use two visually
    distinct lines, so this is a real child widget instead (set via
    QListWidget.setItemWidget) -- which also means mouse events land on
    *this* widget first rather than reaching QListWidget's own selection/
    double-click handling, so both are re-implemented here explicitly
    (mousePressEvent selects the row, mouseDoubleClickEvent loads it)."""

    def __init__(self, list_widget: QListWidget, item: QListWidgetItem, bookmark: Bookmark, on_double_click):
        super().__init__()
        self._list_widget = list_widget
        self._item = item
        self.bookmark = bookmark
        self._on_double_click = on_double_click

        note_text = html.escape(bookmark.note) if bookmark.note else "No note"
        note_color = TEXT_COLOR if bookmark.note else MUTED_COLOR
        note_label = QLabel(f'<span style="color:{note_color};">{note_text}</span>')
        note_label.setWordWrap(True)
        note_label.setFont(QFont("Segoe UI", 11))

        meta_parts = []
        if bookmark.source_game_id is not None:
            meta_parts.append(f"Game #{bookmark.source_game_id}, ply {bookmark.source_ply}")
        meta_parts.append(_format_bookmark_timestamp(bookmark.created_at))
        meta_label = QLabel(f'<span style="color:{MUTED_COLOR};">{" &middot;&nbsp; ".join(meta_parts)}</span>')
        meta_label.setFont(QFont("Segoe UI", 9))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)
        layout.addWidget(note_label)
        layout.addWidget(meta_label)

    def mousePressEvent(self, event) -> None:
        self._list_widget.setCurrentItem(self._item)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self._on_double_click(self.bookmark)
        super().mouseDoubleClickEvent(event)


class BookmarksDialog(QDialog):
    def __init__(self, parent, bookmarks: Bookmarks, on_select):
        super().__init__(parent)
        self.setWindowTitle("Bookmarks")
        self.resize(560, 440)
        self._bookmarks = bookmarks
        self._on_select = on_select

        self.list_widget = QListWidget()
        self.list_widget.setAlternatingRowColors(True)  # matches the archive browser's zebra striping -- rows this dense need it
        self.list_widget.setSpacing(2)
        for b in bookmarks.list_all():
            item = QListWidgetItem()
            item.setData(BOOKMARK_ID_ROLE, b)
            self.list_widget.addItem(item)
            row_widget = _BookmarkRowWidget(self.list_widget, item, b, self._on_double_click)
            self.list_widget.setItemWidget(item, row_widget)
            item.setSizeHint(row_widget.sizeHint())

        delete_btn = QPushButton("Delete selected")
        delete_btn.clicked.connect(self._on_delete)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Double-click a bookmark to load it on the board."))
        layout.addWidget(self.list_widget)
        layout.addWidget(delete_btn)

    def _on_double_click(self, bookmark: Bookmark) -> None:
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
        self.notes = Notes(db_path)
        self.favorites = Favorites(db_path)
        self.cache = AnalysisCache(db_path)
        self.engine = EngineManager()

        self.command_panel = CommandPanel(
            self.db, self.cli, self.cache, self.engine, self.bookmarks, self.notes,
            get_current_fen=lambda: self.board.fen(),
        )
        self.board = BoardWidget()
        self.browser = GameBrowser(self.db, self.notes, self.favorites)
        self.explorer_panel = ExplorerPanel()

        self.browser.game_selected.connect(self.load_game)
        self.browser.favorite_toggled.connect(self._on_browser_favorite_toggled)
        self.browser.game_selected.connect(self._on_browser_game_selected)
        self.browser.jump_to_start_requested.connect(lambda _gid: self.go_to_start())
        self.browser.jump_to_end_requested.connect(lambda _gid: self.go_to_end())
        self.command_panel.game_requested.connect(self.load_game)
        self.command_panel.move_requested.connect(self._on_move_requested)
        self.command_panel.archive_updated.connect(self.browser.refresh)
        self.board.position_changed.connect(self._update_nav_buttons)
        self.board.position_changed.connect(self._on_position_changed_for_live_query)
        self.board.position_changed.connect(self._on_position_changed_for_live_eval)
        self.board.position_changed.connect(self._on_position_changed_for_live_explorer)
        self.board.position_changed.connect(self._on_position_changed_for_review_highlight)
        self.board.position_changed.connect(self._update_move_counter)
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
        # eval-annotated move table (not just the game header) into the chat.
        # Unchecked by default -- the review table is the heavier of the two.
        self.show_review_checkbox = QCheckBox("Also show move review on select")

        browser_controls = QHBoxLayout()
        browser_controls.addWidget(self.live_query_checkbox)
        browser_controls.addWidget(self.show_review_checkbox)
        browser_controls.addStretch(1)

        # Archive query bar: filters the tree by type/result/color/text
        # together (all four AND'd) instead of only being able to sort by
        # clicking a column header -- e.g. "Blitz games I lost" is
        # type=Blitz + result=Loss. Filters over the already-loaded archive
        # in-memory (game_search.py), so every change re-renders instantly,
        # no debounce needed. Mutually exclusive with "Live query by board"
        # above (see _on_archive_filter_changed/_on_live_query_toggled) --
        # both ultimately drive the same browser.show_filtered(), so having
        # both active at once would just mean whichever fired last silently
        # wins; turning one on always turns the other off instead.
        self.filter_type_combo = QComboBox()
        self.filter_type_combo.addItem("All types", None)
        for label in ("Bullet", "Blitz", "Rapid", "Classical", "Daily"):
            self.filter_type_combo.addItem(label, label)

        self.filter_result_combo = QComboBox()
        self.filter_result_combo.addItem("All results", None)
        for label in ("Win", "Loss", "Draw"):
            self.filter_result_combo.addItem(label, label)

        self.filter_color_combo = QComboBox()
        self.filter_color_combo.addItem("Either color", None)
        self.filter_color_combo.addItem("White", "white")
        self.filter_color_combo.addItem("Black", "black")

        self.filter_text_edit = QLineEdit()
        self.filter_text_edit.setPlaceholderText("Search opponent/opening/notes...")

        self.filter_favorites_btn = QPushButton("★ Favorites")
        self.filter_favorites_btn.setCheckable(True)
        self.filter_favorites_btn.setToolTip("Show only starred games")

        self.filter_clear_btn = QPushButton("Clear")

        self.filter_type_combo.currentIndexChanged.connect(self._on_archive_filter_changed)
        self.filter_result_combo.currentIndexChanged.connect(self._on_archive_filter_changed)
        self.filter_color_combo.currentIndexChanged.connect(self._on_archive_filter_changed)
        self.filter_text_edit.textChanged.connect(self._on_archive_filter_changed)
        self.filter_favorites_btn.toggled.connect(self._on_archive_filter_changed)
        self.filter_clear_btn.clicked.connect(self._on_clear_archive_filters)

        filter_row = QHBoxLayout()
        filter_row.addWidget(self.filter_type_combo)
        filter_row.addWidget(self.filter_result_combo)
        filter_row.addWidget(self.filter_color_combo)
        filter_row.addWidget(self.filter_favorites_btn)
        filter_row.addWidget(self.filter_text_edit, 1)
        filter_row.addWidget(self.filter_clear_btn)

        browser_container = QWidget()
        browser_layout = QVBoxLayout(browser_container)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_layout.addLayout(browser_controls)
        browser_layout.addLayout(filter_row)
        browser_layout.addWidget(self.browser)

        # Game list on top, opening explorer below -- both are board-position-
        # driven archive views, sharing the right-hand column. Stored on
        # self so the collapse toggle button (built in _build_center_panel,
        # which needs this to already exist -- see below) can show/hide the
        # whole column.
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.addWidget(browser_container)
        self.right_splitter.addWidget(self.explorer_panel)
        self.right_splitter.setStretchFactor(0, 2)
        self.right_splitter.setStretchFactor(1, 1)

        center = self._build_center_panel()
        self._update_move_counter()  # initial "Start" label -- otherwise blank until the first position_changed

        splitter = QSplitter()
        splitter.addWidget(self.command_panel)
        splitter.addWidget(center)
        splitter.addWidget(self.right_splitter)
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
        self.board_size_btn = QPushButton("Larger board")
        self.bookmark_btn = QPushButton("Bookmark position")
        self.view_bookmarks_btn = QPushButton("View bookmarks")
        # Distinct from a bookmark's own (position-specific) note -- this is
        # one note per whole game, e.g. "Ponziani opening with exchange in
        # the center", shown in the game header / review and searchable from the
        # archive's query bar (see game_search.py's `text` filter).
        self.game_note_btn = QPushButton("Add/edit game note")
        self.move_note_btn = QPushButton("Add/edit move note")
        self.move_note_btn.setToolTip("Note on the move that led to the current position (mainline only)")
        self.favorite_btn = QPushButton("☆ Favorite")
        self.favorite_btn.setToolTip("Star the loaded game (also togglable from the archive's ★ column)")
        # Lives here (not in browser_controls, inside the column it toggles)
        # so it's still reachable to re-open the archive column once that
        # column itself is hidden. Starts visible, so starts on "Hide".
        self.archive_toggle_btn = QPushButton("◂ Hide archive")

        self.prev_btn.clicked.connect(self.board.prev_ply)
        self.next_btn.clicked.connect(self.board.next_ply)
        self.mainline_btn.clicked.connect(self.board.return_to_mainline)
        self.flip_btn.clicked.connect(self.board.flip)
        self.flip_btn.clicked.connect(self._sync_eval_bar_orientation)
        self.board_size_btn.clicked.connect(self._on_toggle_board_size)
        self.bookmark_btn.clicked.connect(self._on_bookmark)
        self.view_bookmarks_btn.clicked.connect(self._on_view_bookmarks)
        self.game_note_btn.clicked.connect(self._on_edit_game_note)
        self.favorite_btn.clicked.connect(self._on_toggle_favorite)
        self.move_note_btn.clicked.connect(self._on_edit_move_note)
        self.archive_toggle_btn.clicked.connect(self._on_toggle_archive)

        nav_row = QHBoxLayout()
        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.next_btn)
        nav_row.addWidget(self.mainline_btn)
        nav_row.addWidget(self.flip_btn)
        nav_row.addWidget(self.board_size_btn)
        nav_row.addStretch(1)
        nav_row.addWidget(self.archive_toggle_btn)

        bookmark_row = QHBoxLayout()
        bookmark_row.addWidget(self.bookmark_btn)
        bookmark_row.addWidget(self.view_bookmarks_btn)
        bookmark_row.addWidget(self.game_note_btn)
        bookmark_row.addWidget(self.move_note_btn)
        bookmark_row.addWidget(self.favorite_btn)

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

        # Multiple lines: when on, each live-eval request asks the engine
        # for its top 3 candidate moves (MultiPV=3) instead of just the
        # best one, so you can compare the 2nd/3rd-best alternatives too.
        self.multipv_checkbox = QCheckBox("Multiple lines (top 3)")
        self.multipv_checkbox.setChecked(False)
        self.multipv_checkbox.toggled.connect(self._on_multipv_toggled)
        self.multipv_lines_label = QLabel("")
        # Rich text so each line can be a clickable move link (see
        # _render_multipv_lines/_on_multipv_move_clicked below). Default
        # QLabel styling here was tiny and plain black -- bump the font up
        # to match the rest of the app's text instead of inheriting Qt's
        # small default.
        self.multipv_lines_label.setFont(QFont("Segoe UI", 11))
        self.multipv_lines_label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.multipv_lines_label.setOpenExternalLinks(False)
        self.multipv_lines_label.linkActivated.connect(self._on_multipv_move_clicked)

        # Visual white/black advantage bar next to the board, with the
        # score drawn inside it (see eval_bar.py) rather than in a separate
        # label above it -- a sibling label would make the bar's *column*
        # taller than the board, pushing the bar's own top edge down out of
        # line with the board's. Sized to match the board's own height
        # exactly, kept in sync by _sync_eval_bar_size on every board-size
        # change, so the two always line up top-to-bottom.
        self.eval_bar = EvalBar(self.board.board_size)
        self.eval_bar.set_orientation(self.board.orientation == chess.WHITE)

        engine_row = QHBoxLayout()
        engine_row.addWidget(self.engine_status_label)
        engine_row.addWidget(self.download_engine_btn)
        engine_row.addWidget(self.live_eval_checkbox)
        engine_row.addWidget(self.eval_tier_combo)
        engine_row.addStretch(1)

        multipv_row = QHBoxLayout()
        multipv_row.addWidget(self.multipv_checkbox)
        multipv_row.addStretch(1)

        multipv_column = QVBoxLayout()
        multipv_column.addLayout(multipv_row)
        multipv_column.addWidget(self.multipv_lines_label)

        self._refresh_engine_status_row()

        # Move counter, right of the board -- reflects whatever's actually
        # on the board's own move stack (chess.Board.move_stack), not
        # anything game/archive-specific, so it counts correctly whether
        # you're stepping through a loaded/analyzed game, a branched
        # sideline off one, or a position you've set up and are just
        # playing out yourself with no game loaded at all.
        self.move_counter_label = QLabel("")
        self.move_counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.move_counter_label.setMinimumWidth(90)

        # board_row holds the bar, board, and move counter -- all fixed-
        # height/natural-height widgets, so a plain QHBoxLayout (which
        # vertically centers same-height widgets, i.e. aligns the bar and
        # board top AND bottom at once) keeps their edges lined up with no
        # extra alignment flags needed.
        board_row = QHBoxLayout()
        board_row.addStretch(1)
        board_row.addWidget(self.eval_bar)
        board_row.addWidget(self.board)
        board_row.addWidget(self.move_counter_label)
        board_row.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self.status_label)
        layout.addLayout(board_row)
        layout.addLayout(nav_row)
        layout.addLayout(bookmark_row)
        layout.addLayout(engine_row)
        layout.addLayout(multipv_column)
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

    def go_to_end(self) -> None:
        # The true end of the recorded game, even from a branched sideline
        # -- mirrors go_to_start's "true start regardless of branch" choice.
        self.board.set_ply(len(self.board.mainline_sans))

    def load_game(self, game_id: int) -> None:
        detail = self.db.load_game(game_id)
        if detail is None:
            self.status_label.setText(f"No game with id {game_id}")
            return
        self.current_game_id = game_id
        # Clear any review left over from a previous game -- if this load is
        # about to be followed by a fresh review (e.g. the archive's "show
        # review on select" checkbox), CommandPanel repopulates its review
        # block right after; this just prevents a stale, now-mismatched
        # review lingering in the chat in the meantime.
        self.command_panel.clear_review_state()
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
        self._refresh_favorite_btn()
        self.browser.select_game(game_id)  # keep the browser's selection in sync regardless of how the game was loaded

    def _on_browser_game_selected(self, game_id: int) -> None:
        # Only for clicks in the archive panel -- game links clicked in the
        # chat already show their own context, so this must not also fire
        # for that path (it would double-print).
        self.command_panel.display_selected_game(game_id, self.show_review_checkbox.isChecked())

    def _on_move_requested(self, game_id: int, ply: int) -> None:
        # Clicking a move in a /review table -- load that game first if it
        # isn't already the one on the board, then jump straight to the
        # position right after that move.
        if self.current_game_id != game_id:
            self.load_game(game_id)
        self.board.set_ply(ply)

    def _on_position_changed_for_review_highlight(self) -> None:
        # CommandPanel.set_review_ply no-ops on its own if there's no review
        # currently shown for this game, so this can call it unconditionally
        # rather than tracking "is a review visible" here too.
        if self.current_game_id is None:
            return
        # Sideline moves (explorer click-to-play, manual board moves) have
        # no corresponding row in the review table -- clear the highlight
        # rather than pointing it at the wrong move.
        ply = self.board.mainline_ply if self.board.on_mainline else -1
        self.command_panel.set_review_ply(self.current_game_id, ply)

    def _sync_eval_bar_orientation(self) -> None:
        self.eval_bar.set_orientation(self.board.orientation == chess.WHITE)

    def _on_toggle_board_size(self) -> None:
        enlarged = self.board.board_size == BOARD_SIZE
        new_size = LARGE_BOARD_SIZE if enlarged else BOARD_SIZE
        self.board.set_board_size(new_size)
        self.eval_bar.set_height(new_size)
        self.board_size_btn.setText("Smaller board" if enlarged else "Larger board")

    def _on_toggle_archive(self) -> None:
        # QSplitter reclaims a hidden child's space automatically (and
        # restores it on show()) -- no manual size bookkeeping needed, just
        # toggle visibility of the whole right-hand column.
        now_visible = not self.right_splitter.isVisible()
        self.right_splitter.setVisible(now_visible)
        self.archive_toggle_btn.setText("◂ Hide archive" if now_visible else "▸ Show archive")

    def _update_nav_buttons(self) -> None:
        on_main = self.board.on_mainline
        self.prev_btn.setEnabled(on_main and self.board.mainline_ply > 0)
        self.next_btn.setEnabled(on_main and self.board.mainline_ply < len(self.board.mainline_sans))
        self.mainline_btn.setEnabled(not on_main)

    def _update_move_counter(self) -> None:
        # len(move_stack) is the half-move ("ply") count on the board's
        # ACTUAL current position -- unlike mainline_ply, this stays
        # correct on a branched sideline and even with no loaded game at
        # all (e.g. a bookmark's raw FEN, or just moving pieces around).
        ply = len(self.board.board.move_stack)
        if ply == 0:
            self.move_counter_label.setText(
                f'<div style="color:{HEADER_COLOR}; font-weight:bold; font-size:13pt;">Start</div>'
            )
            return
        move_number = ply // 2 + 1
        side_to_move = "White" if self.board.board.turn else "Black"
        self.move_counter_label.setText(
            f'<div style="color:{HEADER_COLOR}; font-weight:bold; font-size:13pt;">Move {move_number}</div>'
            f'<div style="color:{MUTED_COLOR}; font-size:9pt;">{side_to_move} to move</div>'
        )

    # --- Live board-query archive filter -----------------------------------

    def _on_position_changed_for_live_query(self) -> None:
        if self.live_query_checkbox.isChecked():
            self._live_query_timer.start()  # restarts the debounce window

    def _on_live_query_toggled(self, checked: bool) -> None:
        if checked:
            self._clear_archive_filters()  # mutually exclusive -- see the filter_row comment above
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

    # --- Archive query filter (type/result/color/text) ----------------------

    def _on_archive_filter_changed(self) -> None:
        time_category = self.filter_type_combo.currentData()
        result = self.filter_result_combo.currentData()
        color = self.filter_color_combo.currentData()
        text = self.filter_text_edit.text().strip() or None
        favorites_only = self.filter_favorites_btn.isChecked()

        if not (time_category or result or color or text or favorites_only):
            self.browser.refresh()  # nothing active -- back to the normal unfiltered view
            return

        if self.live_query_checkbox.isChecked():
            self.live_query_checkbox.setChecked(False)  # mutually exclusive, see filter_row's comment above

        filt = GameSearchFilter(time_category=time_category, result=result, color=color, text=text,
                                favorites_only=favorites_only, limit=100000)
        matches, _total = search_games(self.db, filt, self.notes, self.favorites)
        self.browser.show_filtered(matches)

    def _clear_archive_filters(self) -> None:
        # blockSignals so resetting four widgets doesn't re-run the filter
        # (and re-render the tree) four times on the way to "nothing active"
        # -- one explicit refresh() at the end is enough.
        for widget in (self.filter_type_combo, self.filter_result_combo, self.filter_color_combo):
            widget.blockSignals(True)
            widget.setCurrentIndex(0)
            widget.blockSignals(False)
        self.filter_text_edit.blockSignals(True)
        self.filter_text_edit.clear()
        self.filter_text_edit.blockSignals(False)
        self.filter_favorites_btn.blockSignals(True)
        self.filter_favorites_btn.setChecked(False)
        self.filter_favorites_btn.blockSignals(False)

    def _on_clear_archive_filters(self) -> None:
        self._clear_archive_filters()
        self.browser.refresh()

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
            self.eval_bar.show()
            self.multipv_checkbox.show()
            self.multipv_lines_label.show()
        else:
            self.engine_status_label.setText("Engine: not installed")
            self.download_engine_btn.show()
            self.live_eval_checkbox.hide()
            self.eval_tier_combo.hide()
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
                score_color = WIN_COLOR if line["mate"] > 0 else LOSS_COLOR
            elif line["cp"] is not None:
                score_str = f"{line['cp'] / 100.0:+.2f}"
                score_color = WIN_COLOR if line["cp"] > 0 else (LOSS_COLOR if line["cp"] < 0 else MUTED_COLOR)
            else:
                score_str, score_color = "--", MUTED_COLOR
            move_str = ""
            if line["best_move_uci"]:
                move = chess.Move.from_uci(line["best_move_uci"])
                move_str = chess.Board(fen).san(move)
            # One row per candidate line, spaced out and with a clear visual
            # hierarchy (rank badge / colored score / bold move) instead of
            # the previous plain "1. +0.34  Nf3" text stacked with <br>.
            text = (
                f'<span style="color:{HEADER_COLOR}; font-weight:bold;">{i}.</span>&nbsp;&nbsp;'
                f'<span style="color:{score_color}; font-weight:bold;">{score_str}</span>&nbsp;&nbsp;'
                f'<b>{html.escape(move_str)}</b>'
            )
            row_html = f'<div style="padding:3px 0;">{text}</div>'
            if move_str:
                rows.append(f'<a href="play:{html.escape(move_str)}" style="text-decoration:none; color:inherit;">{row_html}</a>')
            else:
                rows.append(row_html)
        self.multipv_lines_label.setText("".join(rows))
        # Faded arrows for the 2nd/3rd-best lines (index 0 is the best move,
        # already drawn by set_best_move_arrow in _on_live_eval_succeeded).
        secondary_moves = [
            chess.Move.from_uci(line["best_move_uci"])
            for line in lines[1:3] if line["best_move_uci"]
        ]
        self.board.set_secondary_move_arrows(secondary_moves)

    def _on_multipv_move_clicked(self, href: str) -> None:
        if href.startswith("play:"):
            self.board.push_san(href[len("play:"):])


    def _on_live_eval_failed(self, generation: int, message: str) -> None:
        self._on_live_eval_done()
        self._live_eval_failure_count += 1
        if generation != self._live_eval_generation:
            return
        self.eval_bar.show_error()
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

    def _refresh_favorite_btn(self) -> None:
        is_fav = self.current_game_id is not None and self.favorites.is_favorite(self.current_game_id)
        self.favorite_btn.setText("★ Favorited" if is_fav else "☆ Favorite")

    def _on_toggle_favorite(self) -> None:
        if self.current_game_id is None:
            self.status_label.setText("Load a game from the archive first to favorite it.")
            return
        self.browser.toggle_favorite(self.current_game_id)  # persists, updates the row's star, emits favorite_toggled

    def _on_browser_favorite_toggled(self, game_id: int, _favorite: bool) -> None:
        if game_id == self.current_game_id:
            self._refresh_favorite_btn()
        if self.filter_favorites_btn.isChecked():
            self._on_archive_filter_changed()  # un-starring should drop the row from a favorites-only view

    def _on_edit_game_note(self) -> None:
        """Add or edit the one note attached to the whole currently-loaded
        game (distinct from a bookmark's own position-specific note). Shows
        the existing note pre-filled (so this doubles as "edit"), and an
        empty result clears it, matching Notes.set_game_note's convention."""
        if self.current_game_id is None:
            self.status_label.setText("Load a game from the archive first to add a note to it.")
            return
        game_id = self.current_game_id
        existing = self.notes.get_game_note(game_id) or ""
        text, ok = QInputDialog.getMultiLineText(
            self, "Game note", f"Note for game #{game_id} (leave empty to clear):", existing)
        if not ok:
            return
        self.notes.set_game_note(game_id, text)
        self.status_label.setText(f"Note {'cleared' if not text.strip() else 'saved'} for game #{game_id}.")
        self.browser.refresh()  # picks up the change in the archive's Notes column right away
        self.command_panel.refresh_notes_in_current_review(game_id)  # updates an already-open /review for this game too

    def _on_edit_move_note(self) -> None:
        """Add/edit/clear the note on the move that led to the current board
        position of the loaded game (1-based ply, matching Notes' scheme)."""
        if self.current_game_id is None:
            self.status_label.setText("Load a game from the archive first to add a move note.")
            return
        if not self.board.on_mainline or self.board.mainline_ply < 1:
            self.status_label.setText("Step to a move in the game's mainline first (not the start or a side line).")
            return
        game_id, ply = self.current_game_id, self.board.mainline_ply
        existing = self.notes.get_move_notes(game_id).get(ply, "")
        text, ok = QInputDialog.getMultiLineText(
            self, "Move note", f"Note for game #{game_id}, ply {ply} (leave empty to clear):", existing)
        if not ok:
            return
        self.notes.set_move_note(game_id, ply, text)
        self.status_label.setText(f"Move note {'cleared' if not text.strip() else 'saved'} for game #{game_id}, ply {ply}.")
        self.command_panel.refresh_notes_in_current_review(game_id)

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
            self._refresh_favorite_btn()
            self.status_label.setText(f"Loaded bookmark: {bookmark.note or '(no note)'}")
        self._update_nav_buttons()
