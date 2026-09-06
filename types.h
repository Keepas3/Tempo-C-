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

inline int file_of(int square) { return square & 7; }
inline int rank_of(int square) { return square >> 3; }

enum CastlingRights {
    CASTLE_WK = 1,
    CASTLE_WQ = 2,
    CASTLE_BK = 4,
    CASTLE_BQ = 8
};

enum MoveFlag {
    MOVE_QUIET = 0,
    MOVE_DOUBLE_PUSH = 1,
    MOVE_EN_PASSANT = 2,
    MOVE_CASTLE_KING = 3,
    MOVE_CASTLE_QUEEN = 4
};

// Move is packed into a 32-bit int:
// bits 0-5: from square, 6-11: to square, 12-14: moving piece,
// 15-17: captured piece (None if no capture), 18-20: promotion piece (None if none),
// 21-23: flag, 24: capture flag
struct Move {
    uint32_t data = 0;

    Move() = default;

    Move(int from, int to, Piece piece, Piece captured = Piece::None,
         Piece promotion = Piece::None, MoveFlag flag = MOVE_QUIET)
    {
        data = (static_cast<uint32_t>(from) & 0x3F)
             | ((static_cast<uint32_t>(to) & 0x3F) << 6)
             | ((static_cast<uint32_t>(piece) & 0x7) << 12)
             | ((static_cast<uint32_t>(captured) & 0x7) << 15)
             | ((static_cast<uint32_t>(promotion) & 0x7) << 18)
             | ((static_cast<uint32_t>(flag) & 0x7) << 21)
             | ((captured != Piece::None ? 1u : 0u) << 24);
    }

    int from() const { return data & 0x3F; }
    int to() const { return (data >> 6) & 0x3F; }
    Piece piece() const { return static_cast<Piece>((data >> 12) & 0x7); }
    Piece captured() const { return static_cast<Piece>((data >> 15) & 0x7); }
    Piece promotion() const { return static_cast<Piece>((data >> 18) & 0x7); }
    MoveFlag flag() const { return static_cast<MoveFlag>((data >> 21) & 0x7); }
    bool is_capture() const { return (data >> 24) & 0x1; }
    bool is_en_passant() const { return flag() == MOVE_EN_PASSANT; }
    bool is_castle() const { return flag() == MOVE_CASTLE_KING || flag() == MOVE_CASTLE_QUEEN; }
    bool is_double_push() const { return flag() == MOVE_DOUBLE_PUSH; }
    bool is_promotion() const { return promotion() != Piece::None; }

    bool operator==(const Move& other) const { return data == other.data; }
};