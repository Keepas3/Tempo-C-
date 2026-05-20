#pragma once
#include <iostream>
#include "types.h"

// ==========================================
// Tempo C++: Utilities
// ==========================================

// Prints a 64-bit integer as an 8x8 chessboard grid
inline void print_bitboard(Bitboard b) {
    std::cout << "\n";
    // Chess ranks are printed from 8 down to 1 (top to bottom)
    for (int rank = 7; rank >= 0; --rank) {
        std::cout << (rank + 1) << "  "; // Print the rank number on the left
        
        for (int file = 0; file < 8; ++file) {
            // Calculate the 0-63 index of the square
            int square = rank * 8 + file;
            
            // Bitwise AND to check if the bit at this square is a 1
            // 1ULL means "1 Unsigned Long Long" (a 64-bit 1)
            if (b & (1ULL << square)) {
                std::cout << "1 ";
            } else {
                std::cout << ". ";
            }
        }
        std::cout << "\n";
    }
    // Print the file letters on the bottom
    std::cout << "\n   a b c d e f g h\n\n";
    std::cout << "Bitboard Value: " << b << "\n\n";
}