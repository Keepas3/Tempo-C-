#pragma once
#include <cstdint>

// ==========================================
// Tempo C++: Type Definitions
// ==========================================

// An unsigned 64-bit integer. Exactly 64 bits for 64 squares.
using Bitboard = uint64_t;

// Strongly typed enum for colors to prevent accidental integer mixing
enum class Color {
    White = 0,
    Black = 1,
    Both = 2
};

// Strongly typed enum for piece types
enum class Piece {
    Pawn = 0,
    Knight = 1,
    Bishop = 2,
    Rook = 3,
    Queen = 4,
    King = 5,
    None = 6
};