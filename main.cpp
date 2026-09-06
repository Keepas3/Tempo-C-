#include <iostream>
#include <string>
#include <sstream>
#include <iomanip>
#include "game.h"
#include "pgn.h"
#include "db.h"
#include "analysis.h"

const std::string DB_PATH = "tempo_archive.db";

void print_help() {
    std::cout << "Commands:\n"
                 "  import <file.pgn>   Import games from a PGN file\n"
                 "  list [n]            List the n most recent games (default 20)\n"
                 "  show <id>           Show a game's info and movetext\n"
                 "  review <id>         Replay a game with eval annotations and blunder flags\n"
                 "  stats               Show win rate, opening, and time-management stats\n"
                 "  opening <query>     Show win/loss record and games for an opening (name substring or ECO code)\n"
                 "  help                Show this help\n"
                 "  q / quit            Exit\n\n";
}

void cmd_import(Archive& archive, const std::string& path) {
    std::vector<Game> games;
    try {
        games = parse_pgn_file(path);
    } catch (const std::exception& e) {
        std::cout << "[Error] " << e.what() << "\n\n";
        return;
    }

    int added = 0, skipped = 0;
    for (const Game& g : games) {
        int id = archive.insert_game(g);
        if (id == -1) skipped++; else added++;
    }

    std::cout << "[Result] Parsed " << games.size() << " game(s): "
              << added << " added, " << skipped << " already in the archive.\n\n";
}

void print_game_table(const std::vector<GameSummary>& games) {
    std::cout << std::left
              << std::setw(5) << "ID" << std::setw(12) << "Date"
              << std::setw(20) << "Opponent" << std::setw(7) << "Color"
              << std::setw(7) << "Result" << "Opening\n";
    for (const GameSummary& g : games) {
        std::cout << std::left
                  << std::setw(5) << g.id << std::setw(12) << g.date
                  << std::setw(20) << g.opponent << std::setw(7) << g.your_color
                  << std::setw(7) << g.result_display << g.opening << "\n";
    }
    std::cout << "\n";
}

void cmd_list(Archive& archive, int limit) {
    std::vector<GameSummary> games = archive.list_games(limit);
    if (games.empty()) {
        std::cout << "[Result] No games in the archive yet.\n\n";
        return;
    }
    print_game_table(games);
}

void cmd_opening(Archive& archive, const std::string& query) {
    std::vector<GameSummary> games = archive.find_games_by_opening(query);
    if (games.empty()) {
        std::cout << "[Result] No games found matching \"" << query << "\".\n\n";
        return;
    }

    int wins = 0, losses = 0, draws = 0;
    int wins_white = 0, games_white = 0, wins_black = 0, games_black = 0;
    for (const GameSummary& g : games) {
        if (g.result_display == "Win") wins++;
        else if (g.result_display == "Loss") losses++;
        else if (g.result_display == "Draw") draws++;

        if (g.your_color == "white") { games_white++; if (g.result_display == "Win") wins_white++; }
        else if (g.your_color == "black") { games_black++; if (g.result_display == "Win") wins_black++; }
    }

    std::cout << games.size() << " game(s) matching \"" << query << "\": "
              << wins << "W " << losses << "L " << draws << "D";
    if (!games.empty()) {
        std::cout << " (" << std::fixed << std::setprecision(1) << (100.0 * wins / games.size()) << "% win rate)";
    }
    std::cout << "\n";
    if (games_white > 0) {
        std::cout << "  As White: " << games_white << " game(s), "
                  << std::fixed << std::setprecision(0) << (100.0 * wins_white / games_white) << "% win rate\n";
    }
    if (games_black > 0) {
        std::cout << "  As Black: " << games_black << " game(s), "
                  << std::fixed << std::setprecision(0) << (100.0 * wins_black / games_black) << "% win rate\n";
    }
    std::cout << "\n";

    print_game_table(games);
}

void cmd_show(Archive& archive, int id) {
    Game g;
    try {
        g = archive.load_game(id);
    } catch (const std::exception& e) {
        std::cout << "[Error] " << e.what() << "\n\n";
        return;
    }

    std::cout << g.white << " vs " << g.black << "  (" << g.date << ", " << g.result << ")\n";
    if (!g.opening.empty()) std::cout << "Opening: " << g.opening << " (" << g.eco << ")\n";
    if (!g.time_control.empty()) std::cout << "Time control: " << g.time_control << "\n";
    std::cout << "\n";

    for (size_t i = 0; i < g.moves.size(); ++i) {
        if (i % 2 == 0) std::cout << (i / 2 + 1) << ". ";
        std::cout << g.moves[i].san;
        if (g.moves[i].clock_seconds >= 0) {
            std::cout << " (" << g.moves[i].clock_seconds << "s)";
        }
        std::cout << " ";
    }
    std::cout << g.result << "\n\n";
}

void cmd_review(Archive& archive, int id) {
    Game g;
    try {
        g = archive.load_game(id);
    } catch (const std::exception& e) {
        std::cout << "[Error] " << e.what() << "\n\n";
        return;
    }

    std::vector<int> evals;
    try {
        evals = evaluate_game(g);
    } catch (const std::exception& e) {
        std::cout << "[Error] could not replay game: " << e.what() << "\n\n";
        return;
    }

    Color your_color = color_from_string(g.your_color);
    std::vector<int> blunders = find_blunders(evals, your_color);
    std::vector<bool> is_blunder(g.moves.size(), false);
    for (int ply : blunders) is_blunder[ply] = true;

    std::cout << g.white << " vs " << g.black << "  (" << g.date << ", " << g.result << ")\n\n";
    for (size_t i = 0; i < g.moves.size(); ++i) {
        if (i % 2 == 0) std::cout << (i / 2 + 1) << ". ";
        std::cout << g.moves[i].san << " [" << (evals[i] / 100.0) << "]";
        if (is_blunder[i]) std::cout << " <-- possible blunder";
        std::cout << "\n";
    }
    std::cout << "\n";
}

void cmd_stats(Archive& archive) {
    Stats s = archive.compute_stats();
    int total = s.wins + s.losses + s.draws;

    std::cout << "Overall: " << s.wins << "W " << s.losses << "L " << s.draws << "D";
    if (total > 0) {
        std::cout << " (" << std::fixed << std::setprecision(1) << (100.0 * s.wins / total) << "% win rate)";
    }
    std::cout << "\n";

    std::cout << "As White: " << s.wins_white << "W " << s.losses_white << "L " << s.draws_white << "D\n";
    std::cout << "As Black: " << s.wins_black << "W " << s.losses_black << "L " << s.draws_black << "D\n\n";

    if (s.avg_seconds_per_move >= 0) {
        std::cout << "Avg. seconds per move: " << std::fixed << std::setprecision(1) << s.avg_seconds_per_move << "\n";
        std::cout << "Moves made with < 10s left: " << s.time_trouble_moves << "\n\n";
    } else {
        std::cout << "No clock data available (import PGNs with %clk annotations for time stats).\n\n";
    }

    if (!s.by_time_control.empty()) {
        std::cout << "Time pressure by time control:\n";
        for (const TimeControlStat& t : s.by_time_control) {
            std::cout << "  " << t.category << ": " << t.games << " game(s)";
            if (t.avg_seconds_per_move >= 0) {
                std::cout << ", " << std::fixed << std::setprecision(1) << t.avg_seconds_per_move << "s/move avg, "
                          << t.time_trouble_moves << " move(s) under 10s";
            } else {
                std::cout << ", no clock data";
            }
            std::cout << "\n";
        }
        std::cout << "\n";
    }

    auto print_openings = [](const char* label, const std::vector<OpeningStat>& openings) {
        if (openings.empty()) return;
        std::cout << label << ":\n";
        int shown = 0;
        for (const OpeningStat& o : openings) {
            if (shown++ >= 10) break;
            double win_rate = o.games > 0 ? (100.0 * o.wins / o.games) : 0.0;
            std::cout << "  " << o.opening << ": " << o.games << " game(s), "
                      << std::fixed << std::setprecision(0) << win_rate << "% win rate\n";
        }
        std::cout << "\n";
    };

    print_openings("Openings you play (White)", s.top_openings_white);
    print_openings("Openings faced (Black)", s.top_openings_black);
}

int main() {
    std::cout << "          Tempo C++ Game Archive          \n\n";
    print_help();

    Archive archive(DB_PATH);

    std::string line;
    while (true) {
        std::cout << "Tempo> ";
        if (!std::getline(std::cin, line)) break;

        std::istringstream iss(line);
        std::string cmd;
        iss >> cmd;

        if (cmd.empty()) continue;
        if (cmd == "q" || cmd == "quit") break;

        if (cmd == "help") {
            print_help();
        } else if (cmd == "import") {
            std::string path;
            iss >> path;
            if (path.empty()) {
                std::cout << "[Error] usage: import <file.pgn>\n\n";
            } else {
                cmd_import(archive, path);
            }
        } else if (cmd == "list") {
            int limit = 20;
            iss >> limit;
            cmd_list(archive, limit);
        } else if (cmd == "show") {
            int id;
            if (iss >> id) cmd_show(archive, id);
            else std::cout << "[Error] usage: show <id>\n\n";
        } else if (cmd == "review") {
            int id;
            if (iss >> id) cmd_review(archive, id);
            else std::cout << "[Error] usage: review <id>\n\n";
        } else if (cmd == "stats") {
            cmd_stats(archive);
        } else if (cmd == "opening") {
            std::string query;
            std::getline(iss, query);
            // Trim leading whitespace left by extracting "opening" from the line.
            size_t start = query.find_first_not_of(" \t");
            query = (start == std::string::npos) ? "" : query.substr(start);
            if (query.empty()) {
                std::cout << "[Error] usage: opening <name-or-ECO>\n\n";
            } else {
                cmd_opening(archive, query);
            }
        } else {
            std::cout << "[Error] unknown command '" << cmd << "'. Type 'help' for a list of commands.\n\n";
        }
    }

    std::cout << "Exiting Tempo C++...\n";
    return 0;
}
