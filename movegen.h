#pragma once
#include "board.h"
#include "types.h"
#include "attacks.h"
#include "makemove.h"
#include "utils.h"

struct MoveList {
    Move moves[256];
    int count = 0;

    void add(const Move& m) { moves[count++] = m; }
};

inline bool is_square_attacked(const Board& board, int square, Color by_color) {
    const AttackTables& tables = attack_tables();
    Bitboard occ = board.occupied();
    int c = static_cast<int>(by_color);

    if (tables.knight[square] & board.pieces[static_cast<int>(Piece::Knight)] & board.colors[c]) return true;
    if (tables.king[square] & board.pieces[static_cast<int>(Piece::King)] & board.colors[c]) return true;

    Color opposite = other_color(by_color);
    if (tables.pawn[static_cast<int>(opposite)][square] & board.pieces[static_cast<int>(Piece::Pawn)] & board.colors[c]) return true;

    Bitboard bishops_queens = (board.pieces[static_cast<int>(Piece::Bishop)] | board.pieces[static_cast<int>(Piece::Queen)]) & board.colors[c];
    if (bishop_attacks(square, occ) & bishops_queens) return true;

    Bitboard rooks_queens = (board.pieces[static_cast<int>(Piece::Rook)] | board.pieces[static_cast<int>(Piece::Queen)]) & board.colors[c];
    if (rook_attacks(square, occ) & rooks_queens) return true;

    return false;
}

inline bool is_in_check(const Board& board, Color c) {
    Bitboard king_bb = board.pieces[static_cast<int>(Piece::King)] & board.colors[static_cast<int>(c)];
    if (!king_bb) return false; // no king on the board (e.g. test positions)
    int king_square = pop_lsb(king_bb);
    return is_square_attacked(board, king_square, other_color(c));
}

inline void generate_pseudo_legal_moves(const Board& board, MoveList& list) {
    const AttackTables& tables = attack_tables();
    Color us = board.side_to_move;
    Color them = other_color(us);
    int uc = static_cast<int>(us);
    Bitboard own = board.colors[uc];
    Bitboard occ = board.occupied();
    Bitboard empty = ~occ;

    // --- Pawns ---
    {
        Bitboard pawns = board.pieces[static_cast<int>(Piece::Pawn)] & own;
        int push = (us == Color::White) ? 8 : -8;
        int start_rank = (us == Color::White) ? 1 : 6;
        int promo_rank = (us == Color::White) ? 7 : 0;

        Bitboard pawns_copy = pawns;
        while (pawns_copy) {
            int from = pop_lsb(pawns_copy);
            int to1 = from + push;

            // Single push
            if (to1 >= 0 && to1 < 64 && (empty & (1ULL << to1))) {
                if (rank_of(to1) == promo_rank) {
                    list.add(Move(from, to1, Piece::Pawn, Piece::None, Piece::Queen));
                    list.add(Move(from, to1, Piece::Pawn, Piece::None, Piece::Rook));
                    list.add(Move(from, to1, Piece::Pawn, Piece::None, Piece::Bishop));
                    list.add(Move(from, to1, Piece::Pawn, Piece::None, Piece::Knight));
                } else {
                    list.add(Move(from, to1, Piece::Pawn));

                    // Double push
                    if (rank_of(from) == start_rank) {
                        int to2 = from + 2 * push;
                        if (empty & (1ULL << to2)) {
                            list.add(Move(from, to2, Piece::Pawn, Piece::None, Piece::None, MOVE_DOUBLE_PUSH));
                        }
                    }
                }
            }

            // Captures
            Bitboard attacks = tables.pawn[uc][from];
            Bitboard captures = attacks & board.colors[static_cast<int>(them)];
            while (captures) {
                int to = pop_lsb(captures);
                Piece captured = board.piece_on(to, them);
                if (rank_of(to) == promo_rank) {
                    list.add(Move(from, to, Piece::Pawn, captured, Piece::Queen));
                    list.add(Move(from, to, Piece::Pawn, captured, Piece::Rook));
                    list.add(Move(from, to, Piece::Pawn, captured, Piece::Bishop));
                    list.add(Move(from, to, Piece::Pawn, captured, Piece::Knight));
                } else {
                    list.add(Move(from, to, Piece::Pawn, captured));
                }
            }

            // En passant
            if (board.en_passant_square != -1 && (attacks & (1ULL << board.en_passant_square))) {
                list.add(Move(from, board.en_passant_square, Piece::Pawn, Piece::Pawn, Piece::None, MOVE_EN_PASSANT));
            }
        }
    }

    // --- Knights ---
    {
        Bitboard knights = board.pieces[static_cast<int>(Piece::Knight)] & own;
        while (knights) {
            int from = pop_lsb(knights);
            Bitboard targets = tables.knight[from] & ~own;
            while (targets) {
                int to = pop_lsb(targets);
                Piece captured = board.piece_on(to, them);
                list.add(Move(from, to, Piece::Knight, captured));
            }
        }
    }

    // --- Bishops ---
    {
        Bitboard bishops = board.pieces[static_cast<int>(Piece::Bishop)] & own;
        while (bishops) {
            int from = pop_lsb(bishops);
            Bitboard targets = bishop_attacks(from, occ) & ~own;
            while (targets) {
                int to = pop_lsb(targets);
                Piece captured = board.piece_on(to, them);
                list.add(Move(from, to, Piece::Bishop, captured));
            }
        }
    }

    // --- Rooks ---
    {
        Bitboard rooks = board.pieces[static_cast<int>(Piece::Rook)] & own;
        while (rooks) {
            int from = pop_lsb(rooks);
            Bitboard targets = rook_attacks(from, occ) & ~own;
            while (targets) {
                int to = pop_lsb(targets);
                Piece captured = board.piece_on(to, them);
                list.add(Move(from, to, Piece::Rook, captured));
            }
        }
    }

    // --- Queens ---
    {
        Bitboard queens = board.pieces[static_cast<int>(Piece::Queen)] & own;
        while (queens) {
            int from = pop_lsb(queens);
            Bitboard targets = queen_attacks(from, occ) & ~own;
            while (targets) {
                int to = pop_lsb(targets);
                Piece captured = board.piece_on(to, them);
                list.add(Move(from, to, Piece::Queen, captured));
            }
        }
    }

    // --- King ---
    {
        Bitboard kings = board.pieces[static_cast<int>(Piece::King)] & own;
        if (kings) {
            int from = pop_lsb(kings);
            Bitboard targets = tables.king[from] & ~own;
            while (targets) {
                int to = pop_lsb(targets);
                Piece captured = board.piece_on(to, them);
                list.add(Move(from, to, Piece::King, captured));
            }

            // --- Castling (rights + empty-path only; check-safety filtered in generate_legal_moves) ---
            if (us == Color::White) {
                if ((board.castling_rights & CASTLE_WK) &&
                    !(occ & ((1ULL << 5) | (1ULL << 6)))) {
                    list.add(Move(4, 6, Piece::King, Piece::None, Piece::None, MOVE_CASTLE_KING));
                }
                if ((board.castling_rights & CASTLE_WQ) &&
                    !(occ & ((1ULL << 1) | (1ULL << 2) | (1ULL << 3)))) {
                    list.add(Move(4, 2, Piece::King, Piece::None, Piece::None, MOVE_CASTLE_QUEEN));
                }
            } else {
                if ((board.castling_rights & CASTLE_BK) &&
                    !(occ & ((1ULL << 61) | (1ULL << 62)))) {
                    list.add(Move(60, 62, Piece::King, Piece::None, Piece::None, MOVE_CASTLE_KING));
                }
                if ((board.castling_rights & CASTLE_BQ) &&
                    !(occ & ((1ULL << 57) | (1ULL << 58) | (1ULL << 59)))) {
                    list.add(Move(60, 58, Piece::King, Piece::None, Piece::None, MOVE_CASTLE_QUEEN));
                }
            }
        }
    }
}

inline void generate_legal_moves(const Board& board, MoveList& list) {
    MoveList pseudo;
    generate_pseudo_legal_moves(board, pseudo);

    Color us = board.side_to_move;
    Color them = other_color(us);

    for (int i = 0; i < pseudo.count; ++i) {
        Move move = pseudo.moves[i];

        // Castling additionally requires the king not be in/pass through check
        if (move.is_castle()) {
            int from = move.from();
            int transit = (from + move.to()) / 2;
            if (is_square_attacked(board, from, them) || is_square_attacked(board, transit, them)) {
                continue;
            }
        }

        Board scratch = board;
        make_move(scratch, move);

        if (!is_in_check(scratch, us)) {
            list.add(move);
        }
    }
}
