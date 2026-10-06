#pragma once
#include <string>
#include "types.h"

// Parses a two-character algebraic square (e.g. "e4") into a 0-63 index, or -1 if malformed.
inline int square_from_string(const std::string& s) {
    if (s.size() != 2) return -1;
    int f = s[0] - 'a';
    int r = s[1] - '1';
    if (f < 0 || f > 7 || r < 0 || r > 7) return -1;
    return r * 8 + f;
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