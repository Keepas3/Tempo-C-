#pragma once
#include "board.h"
#include "pst.h"



const int PAWN_VALUE = 100;
const int KNIGHT_VALUE = 300;
const int BISHOP_VALUE = 320;
const int ROOK_VALUE = 500;
const int QUEEN_VALUE = 900;


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

inline int evaluate(const Board& board) {
    int score = 0;

    auto evaluate_color = [&](Color c, int multiplier) {
        int material = 0;
        int positional = 0;
        
        Bitboard pawns = board.pieces[static_cast<int>(Piece::Pawn)] & board.colors[static_cast<int>(c)];
        Bitboard knights = board.pieces[static_cast<int>(Piece::Knight)] & board.colors[static_cast<int>(c)];
        Bitboard bishops = board.pieces[static_cast<int>(Piece::Bishop)] & board.colors[static_cast<int>(c)];
        Bitboard rooks = board.pieces[static_cast<int>(Piece::Rook)] & board.colors[static_cast<int>(c)];
        Bitboard queens = board.pieces[static_cast<int>(Piece::Queen)] & board.colors[static_cast<int>(c)];
        Bitboard kings = board.pieces[static_cast<int>(Piece::King)] & board.colors[static_cast<int>(c)];

        while (pawns) {
            int square = pop_lsb(pawns);
            material += PAWN_VALUE;
            positional += (c == Color::White) ? PAWN_PST[square] : PAWN_PST[63 - square];
        }

        while (knights) {
            int square = pop_lsb(knights);
            material += KNIGHT_VALUE;
            positional += (c == Color::White) ? KNIGHT_PST[square] : KNIGHT_PST[63 - square];
        }

        while (bishops) {
            int square = pop_lsb(bishops);
            material += BISHOP_VALUE;
            positional += (c == Color::White) ? BISHOP_PST[square] : BISHOP_PST[63 - square];
        }

        while (rooks) {
            int square = pop_lsb(rooks);
            material += ROOK_VALUE;
            positional += (c == Color::White) ? ROOK_PST[square] : ROOK_PST[63 - square];
        }

        while (queens) {
            int square = pop_lsb(queens);
            material += QUEEN_VALUE;
            positional += (c == Color::White) ? QUEEN_PST[square] : QUEEN_PST[63 - square];
        }

        while (kings) {
            int square = pop_lsb(kings);
            // We do not add material value for the King since it cannot be captured
            positional += (c == Color::White) ? KING_PST[square] : KING_PST[63 - square];
        }
        
        return (material + positional) * multiplier;
    };