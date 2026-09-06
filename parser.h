#pragma once
#include <string>
#include <sstream>
#include <cctype>
#include "board.h"
#include "types.h"


inline void parse_fen(const std::string& fen, Board& board) {
    board.reset();

    std::istringstream ss(fen);
    std::string placement, active_color, castling, en_passant, halfmove, fullmove;
    ss >> placement >> active_color >> castling >> en_passant >> halfmove >> fullmove;

    // --- Piece placement ---
    int rank = 7;
    int file = 0;

    for (char c : placement) {
        if (c == '/') {
            rank--;   // Move down a row
            file = 0; // Reset to the 'a' file
        }
        else if (isdigit(static_cast<unsigned char>(c))) {
            file += (c - '0');
        }
        else {
            // Piece character
            Color color = isupper(static_cast<unsigned char>(c)) ? Color::White : Color::Black;
            Piece piece = Piece::None;

            char lower_c = static_cast<char>(tolower(static_cast<unsigned char>(c)));
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

    // --- Side to move ---
    board.side_to_move = (active_color == "b") ? Color::Black : Color::White;

    // --- Castling availability ---
    board.castling_rights = 0;
    if (castling.find('K') != std::string::npos) board.castling_rights |= CASTLE_WK;
    if (castling.find('Q') != std::string::npos) board.castling_rights |= CASTLE_WQ;
    if (castling.find('k') != std::string::npos) board.castling_rights |= CASTLE_BK;
    if (castling.find('q') != std::string::npos) board.castling_rights |= CASTLE_BQ;

    // --- En passant target square ---
    if (en_passant.size() == 2 && en_passant != "-") {
        int ep_file = en_passant[0] - 'a';
        int ep_rank = en_passant[1] - '1';
        board.en_passant_square = ep_rank * 8 + ep_file;
    } else {
        board.en_passant_square = -1;
    }

    // --- Halfmove clock / fullmove number ---
    board.halfmove_clock = halfmove.empty() ? 0 : std::stoi(halfmove);
    board.fullmove_number = fullmove.empty() ? 1 : std::stoi(fullmove);
}
