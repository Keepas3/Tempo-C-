#pragma once
#include "board.h"
#include "pst.h"

// ==========================================
// Tempo C++: Static Evaluator
// ==========================================

const int PAWN_VALUE = 100;
const int KNIGHT_VALUE = 300;
const int BISHOP_VALUE = 320;
const int ROOK_VALUE = 500;
const int QUEEN_VALUE = 900;

// Extracts the index (0-63) of the lowest '1' bit and removes it from the board
inline int pop_lsb(Bitboard& b) {
    int index = 0;
    Bitboard isolated = b & (~b + 1); // Isolate the lowest set bit
    b &= b - 1; // Clear it from the main board
    
    // Find the index of that isolated bit
    while (isolated > 1) {
        isolated >>= 1;
        index++;
    }
    return index;
}

// Evaluates the board based on material AND Piece-Square Tables (Positional bonuses)
inline int evaluate(const Board& board) {
    int score = 0;

    auto evaluate_color = [&](Color c, int multiplier) {
        int material = 0;
        int positional = 0;
        
        Bitboard pawns = board.pieces[static_cast<int>(Piece::Pawn)] & board.colors[static_cast<int>(c)];
        Bitboard knights = board.pieces[static_cast<int>(Piece::Knight)] & board.colors[static_cast<int>(c)];
        
        // Count material normally for pieces without PSTs yet
        Bitboard bishops = board.pieces[static_cast<int>(Piece::Bishop)] & board.colors[static_cast<int>(c)];
        Bitboard rooks = board.pieces[static_cast<int>(Piece::Rook)] & board.colors[static_cast<int>(c)];
        Bitboard queens = board.pieces[static_cast<int>(Piece::Queen)] & board.colors[static_cast<int>(c)];

        // Loop through Pawns for material AND positional bonuses
        while (pawns) {
            int square = pop_lsb(pawns); // Get the square the pawn is on
            material += PAWN_VALUE;
            // If black, we flip the index so the table works backwards
            positional += (c == Color::White) ? PAWN_PST[square] : PAWN_PST[63 - square];
        }

        // Loop through Knights for material AND positional bonuses
        while (knights) {
            int square = pop_lsb(knights);
            material += KNIGHT_VALUE;
            positional += (c == Color::White) ? KNIGHT_PST[square] : KNIGHT_PST[63 - square];
        }

        // Standard material counting for the rest (can be updated to pop_lsb later!)
        while(bishops) { pop_lsb(bishops); material += BISHOP_VALUE; }
        while(rooks) { pop_lsb(rooks); material += ROOK_VALUE; }
        while(queens) { pop_lsb(queens); material += QUEEN_VALUE; }
        
        return (material + positional) * multiplier;
    };

    score += evaluate_color(Color::White, 1);
    score += evaluate_color(Color::Black, -1);

    return score;
}