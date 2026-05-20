#pragma once
#include <string>
#include <cctype>
#include "board.h"

// ==========================================
// Tempo C++: FEN Parser
// ==========================================

// Helper function to parse a FEN string and populate the Board object
inline void parse_fen(const std::string& fen, Board& board) {
    board.reset();
    
    int rank = 7; // Start at rank 8 (top of the board)
    int file = 0; // Start at file a (left of the board)

    for (char c : fen) {
        // If we hit a space, we are done parsing the board portion of the FEN
        if (c == ' ') break; 
        
        if (c == '/') {
            rank--;   // Move down a row
            file = 0; // Reset to the 'a' file
        } 
        else if (isdigit(c)) {
            // Numbers in FEN represent empty squares. e.g., '3' means 3 empty squares.
            file += (c - '0'); 
        } 
        else {
            // It is a piece character (p, n, b, r, q, k)
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