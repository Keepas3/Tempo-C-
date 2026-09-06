#pragma once
#include <string>
#include <stdexcept>
#include "board.h"
#include "types.h"
#include "movegen.h"
#include "utils.h"

// Resolves a SAN token (e.g. "Nf3", "exd5", "e8=Q", "O-O") against the given
// legal move list. Throws std::runtime_error with a descriptive message if
// the token doesn't resolve to exactly one legal move.
inline Move parse_san(const Board& /*board*/, const std::string& raw_token, const MoveList& legal) {
    std::string token = raw_token;

    // Strip trailing annotation/check/mate marks.
    while (!token.empty() && (token.back() == '+' || token.back() == '#' ||
                               token.back() == '!' || token.back() == '?')) {
        token.pop_back();
    }

    if (token.empty()) {
        throw std::runtime_error("empty SAN token");
    }

    // --- Castling ---
    if (token == "O-O" || token == "0-0") {
        for (int i = 0; i < legal.count; ++i) {
            if (legal.moves[i].flag() == MOVE_CASTLE_KING) return legal.moves[i];
        }
        throw std::runtime_error("illegal castle (kingside): " + raw_token);
    }
    if (token == "O-O-O" || token == "0-0-0") {
        for (int i = 0; i < legal.count; ++i) {
            if (legal.moves[i].flag() == MOVE_CASTLE_QUEEN) return legal.moves[i];
        }
        throw std::runtime_error("illegal castle (queenside): " + raw_token);
    }

    // --- Promotion suffix ---
    Piece promotion = Piece::None;
    size_t eq_pos = token.find('=');
    if (eq_pos != std::string::npos) {
        if (eq_pos + 1 >= token.size()) throw std::runtime_error("malformed promotion: " + raw_token);
        char pc = token[eq_pos + 1];
        switch (pc) {
            case 'Q': promotion = Piece::Queen; break;
            case 'R': promotion = Piece::Rook; break;
            case 'B': promotion = Piece::Bishop; break;
            case 'N': promotion = Piece::Knight; break;
            default: throw std::runtime_error("unknown promotion piece: " + raw_token);
        }
        token = token.substr(0, eq_pos);
    }

    // --- Piece letter ---
    Piece piece = Piece::Pawn;
    size_t idx = 0;
    if (!token.empty() && token[0] >= 'A' && token[0] <= 'Z') {
        switch (token[0]) {
            case 'N': piece = Piece::Knight; break;
            case 'B': piece = Piece::Bishop; break;
            case 'R': piece = Piece::Rook; break;
            case 'Q': piece = Piece::Queen; break;
            case 'K': piece = Piece::King; break;
            default: throw std::runtime_error("unknown piece letter: " + raw_token);
        }
        idx = 1;
    }

    std::string rest = token.substr(idx);

    // Strip capture marker (kept only to sanity-check, filtering below doesn't need it).
    size_t x_pos = rest.find('x');
    if (x_pos != std::string::npos) {
        rest = rest.substr(0, x_pos) + rest.substr(x_pos + 1);
    }

    if (rest.size() < 2) throw std::runtime_error("malformed move: " + raw_token);

    std::string dest_str = rest.substr(rest.size() - 2);
    std::string disambig = rest.substr(0, rest.size() - 2);

    int dest = square_from_string(dest_str);
    if (dest == -1) throw std::runtime_error("bad destination square in: " + raw_token);

    int disambig_file = -1, disambig_rank = -1;
    for (char c : disambig) {
        if (c >= 'a' && c <= 'h') disambig_file = c - 'a';
        else if (c >= '1' && c <= '8') disambig_rank = c - '1';
        else throw std::runtime_error("bad disambiguation in: " + raw_token);
    }

    Move match;
    int matches = 0;
    for (int i = 0; i < legal.count; ++i) {
        const Move& m = legal.moves[i];
        if (m.piece() != piece) continue;
        if (m.to() != dest) continue;
        if (m.promotion() != promotion) continue;
        if (disambig_file != -1 && file_of(m.from()) != disambig_file) continue;
        if (disambig_rank != -1 && rank_of(m.from()) != disambig_rank) continue;
        match = m;
        matches++;
    }

    if (matches == 0) throw std::runtime_error("no legal move matches: " + raw_token);
    if (matches > 1) throw std::runtime_error("ambiguous SAN token: " + raw_token);

    return match;
}
