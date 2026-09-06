#pragma once
#include "types.h"

const Bitboard FILE_A = 0x0101010101010101ULL;
const Bitboard FILE_B = FILE_A << 1;
const Bitboard FILE_G = FILE_A << 6;
const Bitboard FILE_H = FILE_A << 7;
const Bitboard FILE_AB = FILE_A | FILE_B;
const Bitboard FILE_GH = FILE_G | FILE_H;
const Bitboard RANK_1 = 0xFFULL;
const Bitboard RANK_4 = RANK_1 << (8 * 3);
const Bitboard RANK_5 = RANK_1 << (8 * 4);
const Bitboard RANK_8 = RANK_1 << (8 * 7);

inline Bitboard knight_attacks_from(int square) {
    Bitboard b = 1ULL << square;
    Bitboard attacks = 0ULL;

    attacks |= (b << 17) & ~FILE_A;         // up 2, right 1
    attacks |= (b << 15) & ~FILE_H;         // up 2, left 1
    attacks |= (b << 10) & ~FILE_AB;        // up 1, right 2
    attacks |= (b << 6)  & ~FILE_GH;        // up 1, left 2
    attacks |= (b >> 17) & ~FILE_H;         // down 2, left 1
    attacks |= (b >> 15) & ~FILE_A;         // down 2, right 1
    attacks |= (b >> 10) & ~FILE_GH;        // down 1, left 2
    attacks |= (b >> 6)  & ~FILE_AB;        // down 1, right 2

    return attacks;
}

inline Bitboard king_attacks_from(int square) {
    Bitboard b = 1ULL << square;
    Bitboard attacks = 0ULL;

    attacks |= (b << 8);                    // up
    attacks |= (b >> 8);                    // down
    attacks |= (b << 1) & ~FILE_A;          // right
    attacks |= (b >> 1) & ~FILE_H;          // left
    attacks |= (b << 9) & ~FILE_A;          // up-right
    attacks |= (b << 7) & ~FILE_H;          // up-left
    attacks |= (b >> 7) & ~FILE_A;          // down-right
    attacks |= (b >> 9) & ~FILE_H;          // down-left

    return attacks;
}

inline Bitboard pawn_attacks_from(int square, Color c) {
    Bitboard b = 1ULL << square;
    if (c == Color::White) {
        return ((b << 9) & ~FILE_A) | ((b << 7) & ~FILE_H);
    } else {
        return ((b >> 7) & ~FILE_A) | ((b >> 9) & ~FILE_H);
    }
}

struct AttackTables {
    Bitboard knight[64];
    Bitboard king[64];
    Bitboard pawn[2][64];

    AttackTables() {
        for (int sq = 0; sq < 64; ++sq) {
            knight[sq] = knight_attacks_from(sq);
            king[sq] = king_attacks_from(sq);
            pawn[static_cast<int>(Color::White)][sq] = pawn_attacks_from(sq, Color::White);
            pawn[static_cast<int>(Color::Black)][sq] = pawn_attacks_from(sq, Color::Black);
        }
    }
};

inline const AttackTables& attack_tables() {
    static AttackTables tables;
    return tables;
}

// Ray-traced sliding attacks: walks each direction from `square`, stopping
// (inclusively) at the first occupied square.
inline Bitboard sliding_attacks(int square, Bitboard occupied, const int deltas[4][2]) {
    Bitboard attacks = 0ULL;
    int start_file = file_of(square);
    int start_rank = rank_of(square);

    for (int d = 0; d < 4; ++d) {
        int df = deltas[d][0];
        int dr = deltas[d][1];
        int f = start_file + df;
        int r = start_rank + dr;

        while (f >= 0 && f < 8 && r >= 0 && r < 8) {
            int sq = r * 8 + f;
            Bitboard mask = 1ULL << sq;
            attacks |= mask;
            if (occupied & mask) break;
            f += df;
            r += dr;
        }
    }

    return attacks;
}

inline Bitboard bishop_attacks(int square, Bitboard occupied) {
    static const int deltas[4][2] = { {1,1}, {1,-1}, {-1,1}, {-1,-1} };
    return sliding_attacks(square, occupied, deltas);
}

inline Bitboard rook_attacks(int square, Bitboard occupied) {
    static const int deltas[4][2] = { {1,0}, {-1,0}, {0,1}, {0,-1} };
    return sliding_attacks(square, occupied, deltas);
}

inline Bitboard queen_attacks(int square, Bitboard occupied) {
    return bishop_attacks(square, occupied) | rook_attacks(square, occupied);
}
