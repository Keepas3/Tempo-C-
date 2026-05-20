#include <iostream>
#include <string>
#include "utils.h"
#include "board.h"
#include "parser.h"
#include "evaluate.h"
#include "pst.h"



int main() {
    std::cout << "========================================\n";
    std::cout << "          Tempo C++ Evaluator           \n";
    std::cout << "========================================\n";
    std::cout << "Enter a FEN string to evaluate, or 'q' to quit.\n\n";
    
    std::string default_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    std::cout << "[Hint] Default Starting FEN: \n" << default_fen << "\n\n";

    std::string fen_input;
    Board tempo_board;
    // // Test: Put a piece on square 0 (a1) and square 9 (b2)
    // Bitboard test_board = (1ULL << 0) | (1ULL << 9);
    // print_bitboard(test_board);
    while (true) {
        std::cout << "Tempo> ";
        std::getline(std::cin, fen_input);

        // Exit condition
        if (fen_input == "q" || fen_input == "quit") {
            break;
        }

        // Handle empty inputs safely
        if (fen_input.empty()) {
            continue;
        }

        
        parse_fen(fen_input, tempo_board);
        
        
        std::cout << "\n[Debug] White Pawns Bitboard:\n";
        print_bitboard(tempo_board.pieces[static_cast<int>(Piece::Pawn)] & tempo_board.colors[static_cast<int>(Color::White)]);

        
        parse_fen(fen_input, tempo_board);
        
        int score = evaluate(tempo_board);
      
        std::cout << "[Result] Static Evaluation Score: ";
        if (score > 0) {
            std::cout << "+" << (score / 100.0) << " (White Advantage)\n\n";
        } else if (score < 0) {
            std::cout << (score / 100.0) << " (Black Advantage)\n\n";
        } else {
            std::cout << "0.00 (Equal)\n\n";
        }
    }

    std::cout << "Exiting Tempo C++...\n";
    return 0;
}