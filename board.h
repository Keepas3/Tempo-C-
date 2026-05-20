#pragma once
#include "types.h"

// class Board represents the state of the chess board 
class Board {
public:
    Bitboard pieces[6];  
    Bitboard colors[2];  
    
    Color side_to_move;

    
    Board() {
        reset();
    }

    
    void reset() {
        for (int i = 0; i < 6; ++i) pieces[i] = 0ULL;
        for (int i = 0; i < 2; ++i) colors[i] = 0ULL;
        side_to_move = Color::White;
    }

   
    void set_piece(Piece pt, Color c, int square) {
        Bitboard mask = 1ULL << square;
        
       
        pieces[static_cast<int>(pt)] |= mask;
        
     
        colors[static_cast<int>(c)] |= mask;
    }
};