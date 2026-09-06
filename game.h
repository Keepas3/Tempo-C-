#pragma once
#include <string>
#include <vector>

// Edit this to your own chess.com/lichess username so imported games can be
// tagged with which color you played.
inline const std::string YOUR_USERNAME = "KeepasC";

struct MoveRecord {
    std::string san;
    int clock_seconds = -1; // -1 if the PGN had no %clk annotation for this move
};

struct Game {
    std::string event, site, date, white, black, result;
    std::string eco, opening, time_control;
    std::string your_color; // "white" or "black"
    std::vector<MoveRecord> moves;
};

// Reconstructs a normalized PGN string (tags + movetext) from a parsed Game.
// Used as the stored record and as the basis for de-duplication.
inline std::string game_to_pgn(const Game& g) {
    std::string pgn;
    auto tag = [&](const char* key, const std::string& value) {
        pgn += "[" + std::string(key) + " \"" + value + "\"]\n";
    };
    tag("Event", g.event);
    tag("Site", g.site);
    tag("Date", g.date);
    tag("White", g.white);
    tag("Black", g.black);
    tag("Result", g.result);
    if (!g.eco.empty()) tag("ECO", g.eco);
    if (!g.opening.empty()) tag("Opening", g.opening);
    if (!g.time_control.empty()) tag("TimeControl", g.time_control);
    pgn += "\n";

    for (size_t i = 0; i < g.moves.size(); ++i) {
        if (i % 2 == 0) {
            pgn += std::to_string(i / 2 + 1) + ". ";
        }
        pgn += g.moves[i].san + " ";
    }
    pgn += g.result;
    return pgn;
}
