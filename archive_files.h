#pragma once
#include <string>
#include <cstdio>
#include <fstream>
#include <filesystem>
#include "game.h"

namespace fs = std::filesystem;

// Parses a PGN Date tag ("YYYY.MM.DD") into the monthly master-file path
// games/<YYYY>/<MM>-<YYYY>_games.pgn (zero-padded month). Falls back to
// games/unknown/unknown_games.pgn if the date is missing or unparseable.
inline std::string master_pgn_path(const std::string& pgn_date) {
    int year = 0, month = 0, day = 0;
    if (sscanf(pgn_date.c_str(), "%d.%d.%d", &year, &month, &day) >= 2 &&
        year > 0 && month >= 1 && month <= 12) {
        char buf[16];
        snprintf(buf, sizeof(buf), "%02d-%04d", month, year);
        return "games/" + std::to_string(year) + "/" + std::string(buf) + "_games.pgn";
    }
    return "games/unknown/unknown_games.pgn";
}

// Appends a game's PGN text to its monthly master file, creating the year
// subfolder if needed. Never touches the original file the game was
// imported from.
inline void append_to_master_file(const Game& g) {
    std::string path = master_pgn_path(g.date);
    fs::create_directories(fs::path(path).parent_path());

    std::ofstream out(path, std::ios::app);
    out << game_to_pgn(g) << "\n\n";
}
