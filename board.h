#pragma once
#include "types.h"

// ==========================================
// Tempo C++: Board State Management
// ==========================================

class Board {
public:
    // Arrays to hold the bitboards for each piece type and color
    // Index using the Piece and Color enums we created
    Bitboard pieces[6];  // Pawn to King
    Bitboard colors[2];  // White and Black
    
    Color side_to_move;

    // Constructor initializes an empty board
    Board() {
        reset();
    }

    // Clears all bitboards to 0
    void reset() {
        for (int i = 0; i < 6; ++i) pieces[i] = 0ULL;
        for (int i = 0; i < 2; ++i) colors[i] = 0ULL;
        side_to_move = Color::White;
    }

    // A helper method to place a single piece on a specific square
    void set_piece(Piece pt, Color c, int square) {
        Bitboard mask = 1ULL << square;
        
        // Add the piece to its specific piece bitboard
        pieces[static_cast<int>(pt)] |= mask;
        
        // Add the piece to its specific color bitboard
        colors[static_cast<int>(c)] |= mask;
    }
};