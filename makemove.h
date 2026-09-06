#pragma once
#include "board.h"
#include "types.h"

struct UndoInfo {
    Piece captured_piece = Piece::None;
    int captured_square = -1;   // differs from move.to() for en passant
    int prev_castling_rights = 0;
    int prev_en_passant_square = -1;
    int prev_halfmove_clock = 0;
};

inline Color other_color(Color c) {
    return c == Color::White ? Color::Black : Color::White;
}

inline UndoInfo make_move(Board& board, Move move) {
    UndoInfo undo;
    undo.prev_castling_rights = board.castling_rights;
    undo.prev_en_passant_square = board.en_passant_square;
    undo.prev_halfmove_clock = board.halfmove_clock;

    Color us = board.side_to_move;
    Color them = other_color(us);
    int from = move.from();
    int to = move.to();
    Piece piece = move.piece();

    // Handle capture (including en passant, whose captured square differs from `to`)
    if (move.is_en_passant()) {
        int captured_square = to + (us == Color::White ? -8 : 8);
        undo.captured_piece = Piece::Pawn;
        undo.captured_square = captured_square;
        board.remove_piece(Piece::Pawn, them, captured_square);
    } else if (move.is_capture()) {
        undo.captured_piece = move.captured();
        undo.captured_square = to;
        board.remove_piece(move.captured(), them, to);
    }

    // Move the piece
    board.remove_piece(piece, us, from);
    if (move.is_promotion()) {
        board.set_piece(move.promotion(), us, to);
    } else {
        board.set_piece(piece, us, to);
    }

    // Castling: move the rook too
    if (move.flag() == MOVE_CASTLE_KING) {
        int rook_from = (us == Color::White) ? 7 : 63;
        int rook_to = (us == Color::White) ? 5 : 61;
        board.remove_piece(Piece::Rook, us, rook_from);
        board.set_piece(Piece::Rook, us, rook_to);
    } else if (move.flag() == MOVE_CASTLE_QUEEN) {
        int rook_from = (us == Color::White) ? 0 : 56;
        int rook_to = (us == Color::White) ? 3 : 59;
        board.remove_piece(Piece::Rook, us, rook_from);
        board.set_piece(Piece::Rook, us, rook_to);
    }

    // Update castling rights: moving king/rook or capturing a rook on its home square
    if (piece == Piece::King) {
        board.castling_rights &= (us == Color::White) ? ~(CASTLE_WK | CASTLE_WQ) : ~(CASTLE_BK | CASTLE_BQ);
    }
    if (from == 0 || to == 0) board.castling_rights &= ~CASTLE_WQ;
    if (from == 7 || to == 7) board.castling_rights &= ~CASTLE_WK;
    if (from == 56 || to == 56) board.castling_rights &= ~CASTLE_BQ;
    if (from == 63 || to == 63) board.castling_rights &= ~CASTLE_BK;

    // Update en passant target square
    if (move.is_double_push()) {
        board.en_passant_square = (from + to) / 2;
    } else {
        board.en_passant_square = -1;
    }

    // Update halfmove clock
    if (piece == Piece::Pawn || move.is_capture()) {
        board.halfmove_clock = 0;
    } else {
        board.halfmove_clock++;
    }

    // Flip side to move / advance fullmove counter
    if (us == Color::Black) {
        board.fullmove_number++;
    }
    board.side_to_move = them;

    return undo;
}

inline void unmake_move(Board& board, Move move, const UndoInfo& undo) {
    Color them = board.side_to_move;      // side that just moved is the "other" of current side_to_move
    Color us = other_color(them);
    board.side_to_move = us;

    if (us == Color::Black) {
        board.fullmove_number--;
    }

    int from = move.from();
    int to = move.to();
    Piece piece = move.piece();

    // Undo castling rook move
    if (move.flag() == MOVE_CASTLE_KING) {
        int rook_from = (us == Color::White) ? 7 : 63;
        int rook_to = (us == Color::White) ? 5 : 61;
        board.remove_piece(Piece::Rook, us, rook_to);
        board.set_piece(Piece::Rook, us, rook_from);
    } else if (move.flag() == MOVE_CASTLE_QUEEN) {
        int rook_from = (us == Color::White) ? 0 : 56;
        int rook_to = (us == Color::White) ? 3 : 59;
        board.remove_piece(Piece::Rook, us, rook_to);
        board.set_piece(Piece::Rook, us, rook_from);
    }

    // Move the piece back
    if (move.is_promotion()) {
        board.remove_piece(move.promotion(), us, to);
    } else {
        board.remove_piece(piece, us, to);
    }
    board.set_piece(piece, us, from);

    // Restore captured piece
    if (undo.captured_piece != Piece::None) {
        board.set_piece(undo.captured_piece, them, undo.captured_square);
    }

    board.castling_rights = undo.prev_castling_rights;
    board.en_passant_square = undo.prev_en_passant_square;
    board.halfmove_clock = undo.prev_halfmove_clock;
}
