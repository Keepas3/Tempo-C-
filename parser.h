#pragma once
#include <string>
#include <cctype>
#include "board.h"


inline void parse_fen(const std::string& fen, Board& board) {
    board.reset();
    
    int rank = 7; 
    int file = 0; 

    for (char c : fen) {
        // If space is hit we are done parsing the board portion of the FEN
        if (c == ' ') break; 
        
        if (c == '/') {
            rank--;   // Move down a row
            file = 0; // Reset to the 'a' file
        } 
        else if (isdigit(c)) {
            file += (c - '0'); 
        } 
        else {
            // Piece character
            Color color = isupper(c) ? Color::White : Color::Black;
            Piece piece = Piece::None;

            char lower_c = tolower(c);
            switch (lower_c) {
                case 'p': piece = Piece::Pawn; break;
                case 'n': piece = Piece::Knight; break;
                case 'b': piece = Piece::Bishop; break;
                case 'r': piece = Piece::Rook; break;
                case 'q': piece = Piece::Queen; break;
                case 'k': piece = Piece::King; break;
            }

            if (piece != Piece::None) {
                int square = rank * 8 + file;
                board.set_piece(piece, color, square);
                file++;
            }
        }
    }
}