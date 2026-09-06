#pragma once
#include <vector>
#include <string>
#include <stdexcept>
#include "board.h"
#include "parser.h"
#include "movegen.h"
#include "makemove.h"
#include "evaluate.h"
#include "san.h"
#include "game.h"

namespace analysis_detail {
const std::string START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
}

// Replays the game from the start position, returning the static eval (from
// White's perspective, centipawns) after each ply.
inline std::vector<int> evaluate_game(const Game& game) {
    Board board;
    parse_fen(analysis_detail::START_FEN, board);

    std::vector<int> evals;
    evals.reserve(game.moves.size());

    for (const MoveRecord& mr : game.moves) {
        MoveList legal;
        generate_legal_moves(board, legal);
        Move move = parse_san(board, mr.san, legal); // throws if the stored SAN is somehow illegal
        make_move(board, move);
        evals.push_back(evaluate(board));
    }

    return evals;
}

// Flags plies where the eval swung against `your_color` by more than
// `threshold_cp`, restricted to plies where it was your_color's own move.
inline std::vector<int> find_blunders(const std::vector<int>& evals, Color your_color, int threshold_cp = 150) {
    std::vector<int> flagged;

    for (size_t ply = 0; ply < evals.size(); ++ply) {
        Color mover = (ply % 2 == 0) ? Color::White : Color::Black;
        if (mover != your_color) continue;

        int before = (ply == 0) ? 0 : evals[ply - 1];
        int after = evals[ply];
        int delta = after - before; // positive = better for White

        bool bad_for_you = (your_color == Color::White) ? (delta < -threshold_cp) : (delta > threshold_cp);
        if (bad_for_you) flagged.push_back(static_cast<int>(ply));
    }

    return flagged;
}

inline Color color_from_string(const std::string& s) {
    return s == "black" ? Color::Black : Color::White;
}
