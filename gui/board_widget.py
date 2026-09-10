"""Chess board widget: renders the current position via python-chess's SVG
renderer, supports click-to-select/click-to-move, and distinguishes the
game's actual recorded line ("mainline") from a branched analysis position
("sideline") reached by manually moving pieces.
"""
from __future__ import annotations

import chess
import chess.svg
from PySide6.QtCore import Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtSvgWidgets import QSvgWidget

BOARD_SIZE = 480  # default size; BoardWidget.set_board_size() can resize per-instance

# Best-move arrow styling. python-chess's Arrow only recognizes the named
# colors "green"/"red"/"yellow"/"blue" -- anything else (e.g. a raw hex
# string passed directly as Arrow(color=...)) silently fails to resolve
# internally and falls through un-parsed, which Qt's SVG renderer then
# treats as an invalid color (rendering solid black, fully opaque, ignoring
# any alpha) rather than raising an error. The correct way to customize an
# arrow's actual color is this `colors=` override passed to
# chess.svg.board(), remapping the "arrow green" theme key to a real
# 6-digit-hex-plus-alpha value chess.svg's own color parser understands.
ARROW_COLORS = {
    "arrow green": "#4caf5090",  # light green, ~56% opacity -- the engine's best move
    "arrow yellow": "#4caf5055",  # same green, fainter -- MultiPV's 2nd-best line
    "arrow blue": "#4caf5030",  # same green, faintest -- MultiPV's 3rd-best line
}
# Thins the arrow's shaft -- chess.svg draws it at a fixed 20% of square
# size by default (quite thick), with no public parameter to adjust it;
# this CSS override (higher cascade priority than the SVG presentation
# attribute chess.svg sets inline) is the only way to slim it down without
# hand-rolling arrow rendering. Sized in chess.svg's own INTERNAL coordinate
# units (its module-level SQUARE_SIZE=45), not this file's BOARD_SIZE-scaled
# one -- the whole SVG is scaled up to BOARD_SIZE via viewBox, so a style
# value expressed in the external pixel size would end up scaled twice.
ARROW_STYLE = f'<style>.arrow {{ stroke-width: {chess.svg.SQUARE_SIZE * 0.08:.1f}px; }}</style>'


class BoardWidget(QSvgWidget):
    # Emitted whenever on_mainline or mainline_ply changes, so surrounding
    # UI (e.g. a move-list panel) can stay in sync.
    position_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.board_size = BOARD_SIZE
        self.square_size = BOARD_SIZE // 8
        self.setFixedSize(self.board_size, self.board_size)

        self.board = chess.Board()
        self.mainline_sans: list[str] = []
        self.mainline_ply = 0
        self.on_mainline = True
        self.selected_square: chess.Square | None = None
        self._last_move: chess.Move | None = None
        self._best_move_arrow: chess.Move | None = None
        self._secondary_move_arrows: list[chess.Move] = []
        self.orientation: chess.Color = chess.WHITE

        self._render()

    # --- Loading / navigation -------------------------------------------------

    def load_game(self, sans: list[str]) -> None:
        self.mainline_sans = sans
        self.set_ply(0)

    def set_ply(self, ply: int) -> None:
        ply = max(0, min(ply, len(self.mainline_sans)))
        self.board = chess.Board()
        last_move = None
        for san in self.mainline_sans[:ply]:
            last_move = self.board.push_san(san)
        self.mainline_ply = ply
        self.on_mainline = True
        self.selected_square = None
        self._last_move = last_move
        self._render()
        self.position_changed.emit()

    def next_ply(self) -> None:
        if self.on_mainline:
            self.set_ply(self.mainline_ply + 1)

    def prev_ply(self) -> None:
        if self.on_mainline:
            self.set_ply(self.mainline_ply - 1)
        elif self.board.move_stack:
            # Off the mainline (e.g. explorer click-to-play or a manual
            # move) -- undo one move at a time instead of doing nothing,
            # since set_ply(mainline_ply - 1) would just re-jump to the
            # branch point and discard the rest of the sideline.
            self.board.pop()
            self._last_move = self.board.move_stack[-1] if self.board.move_stack else None
            self.selected_square = None
            self._render()
            self.position_changed.emit()

    def return_to_mainline(self) -> None:
        self.set_ply(self.mainline_ply)

    def fen(self) -> str:
        return self.board.fen()

    def current_san_sequence(self) -> list[str]:
        """Full SAN sequence of the current position, mainline + any
        sideline branch. mainline_sans[:mainline_ply] alone is insufficient
        once on_mainline is False -- it only tracks the loaded game's
        recorded line, never branch moves (_handle_square_click pushes to
        self.board but never appends there). SAN is position-dependent, so
        it must be computed by replaying each move on a fresh board rather
        than converting self.board.move_stack after the fact."""
        replay = chess.Board()
        sans: list[str] = []
        for move in self.board.move_stack:
            sans.append(replay.san(move))
            replay.push(move)
        return sans

    def push_san(self, san: str) -> bool:
        """Plays `san` on the current position, branching off the mainline
        exactly like a manual click-move (used by the opening explorer
        panel's click-to-play). Returns False (leaving the board untouched)
        if `san` isn't legal here."""
        try:
            move = self.board.push_san(san)
        except ValueError:
            return False
        self._last_move = move
        self.on_mainline = False
        self.selected_square = None
        self._render()
        self.position_changed.emit()
        return True

    def set_best_move_arrow(self, move: chess.Move | None) -> None:
        """Shows (or clears, if None) an arrow for the engine's suggested
        move on the current position -- set from a live-eval result."""
        self._best_move_arrow = move
        self._render()

    def set_secondary_move_arrows(self, moves: list[chess.Move]) -> None:
        """Shows faded green arrows (up to 2) for MultiPV's 2nd/3rd-best
        lines alongside the main best-move arrow -- same color, progressively
        more transparent, so they read as "also good, but less so"."""
        self._secondary_move_arrows = moves[:2]
        self._render()

    def set_board_size(self, size: int) -> None:
        """Resizes the board itself (e.g. the "Larger board" toggle) --
        updates the pixel size the SVG renders at and the square-size used
        for click-to-move hit-testing together, so they never drift apart."""
        if size == self.board_size:
            return
        self.board_size = size
        self.square_size = size // 8
        self.setFixedSize(size, size)
        self._render()

    def flip(self) -> None:
        """Toggles which side's perspective the board is drawn from."""
        self.set_orientation(not self.orientation)

    def set_orientation(self, color: chess.Color) -> None:
        if color == self.orientation:
            return
        self.orientation = color
        self._render()

    # --- Mouse interaction (click-to-select, click-to-move) -------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        col = int(pos.x() // self.square_size)
        row = int(pos.y() // self.square_size)
        if not (0 <= col <= 7 and 0 <= row <= 7):
            return
        # Matches chess.svg.board()'s own orientation-dependent coordinate
        # formula (x = file if White-POV else 7-file; y = 7-rank if
        # White-POV else rank) so clicks land on the square actually drawn
        # there, in either orientation.
        if self.orientation == chess.WHITE:
            file_idx, rank_idx = col, 7 - row
        else:
            file_idx, rank_idx = 7 - col, row
        square = chess.square(file_idx, rank_idx)
        self._handle_square_click(square)

    def _handle_square_click(self, square: chess.Square) -> None:
        if self.selected_square is None:
            piece = self.board.piece_at(square)
            if piece and piece.color == self.board.turn:
                self.selected_square = square
                self._render()
            return

        if square == self.selected_square:
            self.selected_square = None
            self._render()
            return

        move = self._build_move(self.selected_square, square)
        if move in self.board.legal_moves:
            self.board.push(move)
            self._last_move = move
            self.on_mainline = False  # any manual move branches off the real game
            self.selected_square = None
            self._render()
            self.position_changed.emit()
        else:
            piece = self.board.piece_at(square)
            if piece and piece.color == self.board.turn:
                self.selected_square = square
            else:
                self.selected_square = None
            self._render()

    def _build_move(self, from_sq: chess.Square, to_sq: chess.Square) -> chess.Move:
        piece = self.board.piece_at(from_sq)
        promotion = None
        if piece and piece.piece_type == chess.PAWN:
            back_rank = 7 if piece.color == chess.WHITE else 0
            if chess.square_rank(to_sq) == back_rank:
                promotion = chess.QUEEN  # auto-queen; under-promotion isn't exposed in the UI yet
        return chess.Move(from_sq, to_sq, promotion=promotion)

    # --- Rendering --------------------------------------------------------

    def _render(self) -> None:
        check_square = self.board.king(self.board.turn) if self.board.is_check() else None
        # "green"/"yellow"/"blue" (not raw hex strings) so chess.svg's own
        # color lookup resolves them -- see ARROW_COLORS/ARROW_STYLE above
        # for why and how the actual shade/opacity/thickness are customized
        # instead (all three theme keys are remapped to the same green, at
        # decreasing opacity, so 2nd/3rd-best MultiPV lines read as fainter
        # versions of the best-move arrow rather than a different color).
        arrows = []
        if self._best_move_arrow is not None:
            arrows.append(chess.svg.Arrow(self._best_move_arrow.from_square, self._best_move_arrow.to_square, color="green"))
        drawn_squares = {(a.tail, a.head) for a in arrows}
        for move, color in zip(self._secondary_move_arrows, ("yellow", "blue")):
            if (move.from_square, move.to_square) in drawn_squares:
                continue  # already drawn (e.g. as the best move) -- don't double up
            arrows.append(chess.svg.Arrow(move.from_square, move.to_square, color=color))
            drawn_squares.add((move.from_square, move.to_square))
        svg = chess.svg.board(
            self.board,
            size=self.board_size,
            coordinates=False,
            orientation=self.orientation,
            lastmove=self._last_move,
            check=check_square,
            squares=chess.SquareSet([self.selected_square]) if self.selected_square is not None else None,
            arrows=arrows,
            colors=ARROW_COLORS,
        )
        if arrows:
            svg = svg.replace("</svg>", ARROW_STYLE + "</svg>")
        self.load(svg.encode("utf-8"))
