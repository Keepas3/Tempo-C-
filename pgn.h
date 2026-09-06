#pragma once
#include <string>
#include <vector>
#include <map>
#include <fstream>
#include <sstream>
#include <iostream>
#include <cctype>
#include <algorithm>
#include "board.h"
#include "parser.h"
#include "movegen.h"
#include "makemove.h"
#include "san.h"
#include "game.h"
#include "eco.h"

namespace pgn_detail {

const std::string START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

inline std::string trim(const std::string& s) {
    size_t start = s.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return "";
    size_t end = s.find_last_not_of(" \t\r\n");
    return s.substr(start, end - start + 1);
}

// Parses a "[Key "Value"]" tag line into (key, value). Returns false if malformed.
inline bool parse_tag_line(const std::string& line, std::string& key, std::string& value) {
    if (line.size() < 4 || line.front() != '[' || line.back() != ']') return false;
    std::string inner = line.substr(1, line.size() - 2);
    size_t space = inner.find(' ');
    if (space == std::string::npos) return false;
    key = inner.substr(0, space);
    std::string rest = trim(inner.substr(space + 1));
    if (rest.size() < 2 || rest.front() != '"' || rest.back() != '"') return false;
    value = rest.substr(1, rest.size() - 2);
    return true;
}

inline bool is_result_token(const std::string& t) {
    return t == "1-0" || t == "0-1" || t == "1/2-1/2" || t == "*";
}

inline bool is_move_number_token(const std::string& t) {
    size_t i = 0;
    while (i < t.size() && isdigit(static_cast<unsigned char>(t[i]))) i++;
    if (i == 0) return false;
    while (i < t.size() && t[i] == '.') i++;
    return i == t.size();
}

// Parses "%clk H:MM:SS" (or "%clk H:MM:SS.f") out of a comment body, returns seconds or -1.
inline int extract_clock_seconds(const std::string& comment) {
    size_t pos = comment.find("%clk");
    if (pos == std::string::npos) return -1;
    pos += 4;
    while (pos < comment.size() && comment[pos] == ' ') pos++;

    int h = 0, m = 0, s = 0;
    if (sscanf(comment.c_str() + pos, "%d:%d:%d", &h, &m, &s) != 3) return -1;
    return h * 3600 + m * 60 + s;
}

// Tokenizes movetext into SAN tokens, attaching %clk seconds (from a following
// {...} comment) to the preceding move. Skips move numbers, NAGs, results,
// and variation sub-trees.
inline void tokenize_movetext(const std::string& text, std::vector<MoveRecord>& out) {
    size_t i = 0;
    size_t n = text.size();

    while (i < n) {
        char c = text[i];

        if (isspace(static_cast<unsigned char>(c))) { i++; continue; }

        if (c == '{') {
            size_t end = text.find('}', i);
            if (end == std::string::npos) break;
            std::string comment = text.substr(i + 1, end - i - 1);
            int clk = extract_clock_seconds(comment);
            if (clk != -1 && !out.empty()) out.back().clock_seconds = clk;
            i = end + 1;
            continue;
        }

        if (c == '(') {
            int depth = 1;
            i++;
            while (i < n && depth > 0) {
                if (text[i] == '(') depth++;
                else if (text[i] == ')') depth--;
                i++;
            }
            continue;
        }

        if (c == '$') {
            i++;
            while (i < n && isdigit(static_cast<unsigned char>(text[i]))) i++;
            continue;
        }

        size_t start = i;
        while (i < n && !isspace(static_cast<unsigned char>(text[i])) &&
               text[i] != '{' && text[i] != '(') {
            i++;
        }
        std::string token = text.substr(start, i - start);
        if (token.empty()) continue;

        if (is_result_token(token) || is_move_number_token(token)) continue;

        out.push_back(MoveRecord{token, -1});
    }
}

inline std::string derive_your_color(const std::map<std::string, std::string>& tags) {
    auto white_it = tags.find("White");
    auto black_it = tags.find("Black");

    auto ieq = [](const std::string& a, const std::string& b) {
        if (a.size() != b.size()) return false;
        for (size_t i = 0; i < a.size(); ++i) {
            if (tolower(static_cast<unsigned char>(a[i])) != tolower(static_cast<unsigned char>(b[i]))) return false;
        }
        return true;
    };

    if (white_it != tags.end() && ieq(white_it->second, YOUR_USERNAME)) return "white";
    if (black_it != tags.end() && ieq(black_it->second, YOUR_USERNAME)) return "black";
    return "";
}

// Builds a Game from accumulated tags + raw movetext, validating every SAN
// token against the legal move list as it replays from the start position.
// Throws std::runtime_error (with the offending token) on the first illegal move.
inline Game finalize_game(const std::map<std::string, std::string>& tags, const std::string& movetext) {
    Game game;
    auto get = [&](const char* key) {
        auto it = tags.find(key);
        return it != tags.end() ? it->second : std::string();
    };
    game.event = get("Event");
    game.site = get("Site");
    game.date = get("Date");
    game.white = get("White");
    game.black = get("Black");
    game.result = get("Result");
    game.eco = get("ECO");
    game.opening = get("Opening");
    if (game.opening.empty() && !game.eco.empty()) {
        game.opening = lookup_eco_name(game.eco); // fallback for exports (e.g. chess.com) with no Opening tag
    }
    game.time_control = get("TimeControl");
    game.your_color = derive_your_color(tags);

    std::vector<MoveRecord> tokens;
    tokenize_movetext(movetext, tokens);

    Board board;
    parse_fen(START_FEN, board);

    for (size_t ply = 0; ply < tokens.size(); ++ply) {
        MoveList legal;
        generate_legal_moves(board, legal);

        Move move;
        try {
            move = parse_san(board, tokens[ply].san, legal);
        } catch (const std::exception& e) {
            std::ostringstream oss;
            oss << "ply " << (ply + 1) << " (\"" << tokens[ply].san << "\") in game "
                << game.white << " vs " << game.black << " (" << game.date << "): " << e.what();
            throw std::runtime_error(oss.str());
        }

        make_move(board, move);
        game.moves.push_back(tokens[ply]);
    }

    return game;
}

} // namespace pgn_detail

// Parses a (possibly multi-game) PGN file. Games with an illegal/unparseable
// move are skipped (a warning is printed to stderr) rather than aborting the
// whole import.
inline std::vector<Game> parse_pgn_file(const std::string& path) {
    std::ifstream file(path);
    if (!file) {
        throw std::runtime_error("could not open PGN file: " + path);
    }

    std::vector<Game> games;
    std::map<std::string, std::string> tags;
    std::string movetext;

    auto flush_game = [&]() {
        if (tags.empty() && movetext.empty()) return;
        try {
            games.push_back(pgn_detail::finalize_game(tags, movetext));
        } catch (const std::exception& e) {
            std::cerr << "[Warning] Skipping game: " << e.what() << "\n";
        }
        tags.clear();
        movetext.clear();
    };

    std::string line;
    while (std::getline(file, line)) {
        std::string trimmed = pgn_detail::trim(line);
        if (trimmed.empty()) continue;

        if (trimmed.front() == '[') {
            if (!movetext.empty()) flush_game();
            std::string key, value;
            if (pgn_detail::parse_tag_line(trimmed, key, value)) {
                tags[key] = value;
            }
        } else {
            movetext += trimmed + " ";
        }
    }
    flush_game();

    return games;
}
