#pragma once
#include "types.h"

// class Board represents the state of the chess board
class Board {
public:
    Bitboard pieces[6];
    Bitboard colors[2];

    Color side_to_move;

    int castling_rights;   // bitmask of CastlingRights
    int en_passant_square;  // -1 if none
    int halfmove_clock;
    int fullmove_number;


    Board() {
        reset();
    }


    void reset() {
        for (int i = 0; i < 6; ++i) pieces[i] = 0ULL;
        for (int i = 0; i < 2; ++i) colors[i] = 0ULL;
        side_to_move = Color::White;
        castling_rights = 0;
        en_passant_square = -1;
        halfmove_clock = 0;
        fullmove_number = 1;
    }


    void set_piece(Piece pt, Color c, int square) {
        Bitboard mask = 1ULL << square;


        pieces[static_cast<int>(pt)] |= mask;


        colors[static_cast<int>(c)] |= mask;
    }

    void remove_piece(Piece pt, Color c, int square) {
        Bitboard mask = ~(1ULL << square);
        pieces[static_cast<int>(pt)] &= mask;
        colors[static_cast<int>(c)] &= mask;
    }

    Bitboard occupied() const {
        return colors[static_cast<int>(Color::White)] | colors[static_cast<int>(Color::Black)];
    }

    Bitboard empty() const {
        return ~occupied();
    }

    // Returns Piece::None if the given color has no piece on that square
    Piece piece_on(int square, Color c) const {
        Bitboard mask = 1ULL << square;
        if (!(colors[static_cast<int>(c)] & mask)) return Piece::None;
        for (int pt = 0; pt < 6; ++pt) {
            if (pieces[pt] & mask) return static_cast<Piece>(pt);
        }
        return Piece::None;
    }
};
