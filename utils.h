#pragma once
#include <iostream>
#include <string>
#include "types.h"

inline std::string square_to_string(int square) {
    std::string s;
    s += static_cast<char>('a' + file_of(square));
    s += static_cast<char>('1' + rank_of(square));
    return s;
}

// Parses a two-character algebraic square (e.g. "e4") into a 0-63 index, or -1 if malformed.
inline int square_from_string(const std::string& s) {
    if (s.size() != 2) return -1;
    int f = s[0] - 'a';
    int r = s[1] - '1';
    if (f < 0 || f > 7 || r < 0 || r > 7) return -1;
    return r * 8 + f;
}

inline std::string move_to_string(const Move& move) {
    std::string s = square_to_string(move.from()) + square_to_string(move.to());
    switch (move.promotion()) {
        case Piece::Queen:  s += 'q'; break;
        case Piece::Rook:   s += 'r'; break;
        case Piece::Bishop: s += 'b'; break;
        case Piece::Knight: s += 'n'; break;
        default: break;
    }
    return s;
}

// Pops (clears) the least-significant set bit of b and returns its index.
inline int pop_lsb(Bitboard& b) {
    int index = 0;
    Bitboard isolated = b & (~b + 1);
    b &= b - 1;

    while (isolated > 1) {
        isolated >>= 1;
        index++;
    }
    return index;
}

inline void print_bitboard(Bitboard b) {
    std::cout << "\n";

    for (int rank = 7; rank >= 0; --rank) {
        std::cout << (rank + 1) << "  "; 
        
        for (int file = 0; file < 8; ++file) {
            
            int square = rank * 8 + file;
            
            if (b & (1ULL << square)) {
                std::cout << "1 ";
            } else {
                std::cout << ". ";
            }
        }
        std::cout << "\n";
    }
    std::cout << "\n   a b c d e f g h\n\n";
    std::cout << "Bitboard Value: " << b << "\n\n";
}