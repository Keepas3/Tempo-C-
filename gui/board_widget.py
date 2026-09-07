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

BOARD_SIZE = 480
SQUARE_SIZE = BOARD_SIZE // 8


class BoardWidget(QSvgWidget):
    # Emitted whenever on_mainline or mainline_ply changes, so surrounding
    # UI (e.g. a move-list panel) can stay in sync.
    position_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(BOARD_SIZE, BOARD_SIZE)

        self.board = chess.Board()
        self.mainline_sans: list[str] = []
        self.mainline_ply = 0
        self.on_mainline = True
        self.selected_square: chess.Square | None = None
        self._last_move: chess.Move | None = None

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

    def return_to_mainline(self) -> None:
        self.set_ply(self.mainline_ply)

    def fen(self) -> str:
        return self.board.fen()

    # --- Mouse interaction (click-to-select, click-to-move) -------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        file_idx = int(pos.x() // SQUARE_SIZE)
        rank_idx = 7 - int(pos.y() // SQUARE_SIZE)
        if not (0 <= file_idx <= 7 and 0 <= rank_idx <= 7):
            return
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
        svg = chess.svg.board(
            self.board,
            size=BOARD_SIZE,
            coordinates=False,
            lastmove=self._last_move,
            check=check_square,
            squares=chess.SquareSet([self.selected_square]) if self.selected_square is not None else None,
        )
        self.load(svg.encode("utf-8"))
