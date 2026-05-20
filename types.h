#pragma once
#include <cstdint>

// Dictionary Class used to hold our custom types for the chess engine
using Bitboard = uint64_t;

enum class Color {
    White = 0,
    Black = 1,
    Both = 2
};

enum class Piece {
    Pawn = 0,
    Knight = 1,
    Bishop = 2,
    Rook = 3,
    Queen = 4,
    King = 5,
    None = 6
};