#include <iostream>
#include <string>
#include <sstream>
#include <iomanip>
#include <filesystem>
#include <chrono>
#include <ctime>
#include "game.h"
#include "pgn.h"
#include "db.h"
#include "analysis.h"
#include "archive_files.h"
#include "fetch.h"
#include "json.h"

namespace fs = std::filesystem;

std::string DB_PATH = "tempo_archive.db";
std::string CURRENT_USERNAME = YOUR_USERNAME;
std::string GAMES_DIR = "games";

void print_help() {
    std::cout << "Commands:\n"
                 "  import <file.pgn|folder>  Import games from a PGN file, or every .pgn file in a folder\n"
                 "  list [n]            List the n most recent games (default 20)\n"
                 "  show <id>           Show a game's info and movetext\n"
                 "  review <id>         Replay a game with eval annotations and blunder flags\n"
                 "  stats               Show win rate, opening, and time-management stats\n"
                 "  opening <query>     Show win/loss record and games for an opening (name substring or ECO code)\n"
                 "  moves <sequence>    Show what was played after a SAN move sequence, e.g. 'moves e4 e5 Nf3'\n"
                 "  fetch chesscom <user> [year month]   Fetch games from chess.com (default: current+previous month)\n"
                 "  fetch lichess <user> [days]          Fetch games from lichess (default: last 90 days)\n"
                 "  help                Show this help\n"
                 "  q / quit            Exit\n\n";
}

// Imports one PGN file, returning {parsed, added, skipped}. Errors are printed
// but don't throw, so a bad file doesn't abort a folder import.
struct ImportCounts { int parsed = 0, added = 0, skipped = 0; };

ImportCounts import_one_file(Archive& archive, const std::string& path) {
    ImportCounts counts;
    std::vector<Game> games;
    try {
        games = parse_pgn_file(path, CURRENT_USERNAME);
    } catch (const std::exception& e) {
        std::cout << "[Error] " << path << ": " << e.what() << "\n";
        return counts;
    }

    counts.parsed = static_cast<int>(games.size());

    archive.begin_transaction();
    for (const Game& g : games) {
        int id = archive.insert_game(g);
        if (id == -1) {
            counts.skipped++;
        } else {
            counts.added++;
            append_to_master_file(g, GAMES_DIR);
        }
    }
    archive.commit_transaction();

    return counts;
}

void cmd_import(Archive& archive, const std::string& path) {
    if (!fs::exists(path)) {
        std::cout << "[Error] no such file or folder: " << path << "\n\n";
        return;
    }

    ImportCounts total;
    int files = 0;

    if (fs::is_directory(path)) {
        for (const fs::directory_entry& entry : fs::directory_iterator(path)) {
            if (!entry.is_regular_file() || entry.path().extension() != ".pgn") continue;
            files++;
            ImportCounts c = import_one_file(archive, entry.path().string());
            total.parsed += c.parsed;
            total.added += c.added;
            total.skipped += c.skipped;
        }
        if (files == 0) {
            std::cout << "[Result] No .pgn files found in " << path << ".\n\n";
            return;
        }
    } else {
        files = 1;
        total = import_one_file(archive, path);
    }

    std::cout << "[Result] Scanned " << files << " file(s), parsed " << total.parsed << " game(s): "
              << total.added << " added, " << total.skipped << " already in the archive.\n\n";
}

void print_game_table(const std::vector<GameSummary>& games) {
    std::cout << std::left
              << std::setw(5) << "ID" << std::setw(12) << "Date"
              << std::setw(20) << "Opponent" << std::setw(7) << "Color"
              << std::setw(7) << "Result" << std::setw(10) << "Site" << "Opening\n";
    for (const GameSummary& g : games) {
        std::cout << std::left
                  << std::setw(5) << g.id << std::setw(12) << g.date
                  << std::setw(20) << g.opponent << std::setw(7) << g.your_color
                  << std::setw(7) << g.result_display << std::setw(10) << g.site << g.opening << "\n";
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

void cmd_stats(Archive& archive, const std::vector<std::string>& category_filter) {
    Stats s = archive.compute_stats(category_filter);
    int total = s.wins + s.losses + s.draws;

    if (!category_filter.empty()) {
        std::cout << "Filtered to:";
        for (const std::string& c : category_filter) std::cout << " " << c;
        std::cout << "\n";
    }

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

void cmd_moves(Archive& archive, const std::vector<std::string>& sequence) {
    std::vector<NextMoveStat> stats = archive.opponent_replies(sequence);

    std::string prefix_display;
    for (size_t i = 0; i < sequence.size(); ++i) {
        if (i % 2 == 0) prefix_display += std::to_string(i / 2 + 1) + ".";
        prefix_display += sequence[i] + " ";
    }

    if (stats.empty()) {
        std::cout << "[Result] No games reached the position after " << prefix_display << "\n\n";
        return;
    }

    int total_games = 0;
    for (const NextMoveStat& s : stats) total_games += s.count;

    std::cout << "After " << prefix_display << "(" << total_games << " game(s) reached this position):\n";
    for (const NextMoveStat& s : stats) {
        std::cout << "  " << s.san << ": " << s.count << " game(s)  "
                  << s.wins << "W " << s.losses << "L " << s.draws << "D\n";
    }
    std::cout << "\n";
}

// Fetch results are captured in this struct (rather than printed directly)
// so both the REPL's human-readable output and the --json mode can share
// the exact same fetch+import logic.
struct FetchResult {
    std::string source;
    bool ok = true;
    std::string error;
    ImportCounts counts;
};

FetchResult fetch_chesscom_one_month_result(Archive& archive, const std::string& username, int year, int month) {
    FetchResult r;
    std::ostringstream label;
    label << "chess.com " << year << "-" << std::setfill('0') << std::setw(2) << month;
    r.source = label.str();

    fs::path tmp = fs::temp_directory_path() / "tempo_fetch_chesscom.pgn";
    if (!fetch_chesscom_month(username, year, month, tmp.string())) {
        r.ok = false;
        r.error = "fetch failed for " + r.source + " (check the username and your network connection)";
        std::error_code ec;
        fs::remove(tmp, ec);
        return r;
    }

    r.counts = import_one_file(archive, tmp.string());
    std::error_code ec;
    fs::remove(tmp, ec);
    return r;
}

std::vector<FetchResult> fetch_chesscom_results(Archive& archive, const std::string& username, int year, int month) {
    std::vector<FetchResult> results;
    if (!is_valid_username(username)) {
        FetchResult r;
        r.ok = false;
        r.source = "chess.com";
        r.error = "invalid username: " + username;
        results.push_back(r);
        return results;
    }

    if (year == -1) {
        std::time_t tt = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
        std::tm* local_tm = std::localtime(&tt);
        int cur_year = local_tm->tm_year + 1900;
        int cur_month = local_tm->tm_mon + 1;
        int prev_year = (cur_month == 1) ? cur_year - 1 : cur_year;
        int prev_month = (cur_month == 1) ? 12 : cur_month - 1;

        results.push_back(fetch_chesscom_one_month_result(archive, username, prev_year, prev_month));
        results.push_back(fetch_chesscom_one_month_result(archive, username, cur_year, cur_month));
    } else {
        results.push_back(fetch_chesscom_one_month_result(archive, username, year, month));
    }
    return results;
}

FetchResult fetch_lichess_result(Archive& archive, const std::string& username, int days) {
    FetchResult r;
    std::ostringstream label;
    label << "lichess (last " << days << " days)";
    r.source = label.str();

    if (!is_valid_username(username)) {
        r.ok = false;
        r.error = "invalid username: " + username;
        return r;
    }

    fs::path tmp = fs::temp_directory_path() / "tempo_fetch_lichess.pgn";
    if (!fetch_lichess_range(username, days, tmp.string())) {
        r.ok = false;
        r.error = "fetch failed for " + username + " (check the username and your network connection)";
        std::error_code ec;
        fs::remove(tmp, ec);
        return r;
    }

    r.counts = import_one_file(archive, tmp.string());
    std::error_code ec;
    fs::remove(tmp, ec);
    return r;
}

void print_fetch_result(const FetchResult& r) {
    if (!r.ok) {
        std::cout << "[Error] " << r.error << "\n\n";
        return;
    }
    std::cout << "[Result] " << r.source << ": parsed " << r.counts.parsed << " game(s), "
              << r.counts.added << " added, " << r.counts.skipped << " already in the archive.\n\n";
}

void cmd_fetch_chesscom(Archive& archive, const std::string& username, int year, int month) {
    for (const FetchResult& r : fetch_chesscom_results(archive, username, year, month)) {
        print_fetch_result(r);
    }
}

void cmd_fetch_lichess(Archive& archive, const std::string& username, int days) {
    print_fetch_result(fetch_lichess_result(archive, username, days));
}

inline std::string to_json(const FetchResult& r) {
    std::ostringstream o;
    o << "{\"source\": " << json_str(r.source) << ", \"ok\": " << (r.ok ? "true" : "false");
    if (!r.ok) {
        o << ", \"error\": " << json_str(r.error);
    } else {
        o << ", \"parsed\": " << r.counts.parsed
          << ", \"added\": " << r.counts.added
          << ", \"skipped\": " << r.counts.skipped;
    }
    o << "}";
    return o.str();
}

inline std::string to_json(const std::vector<FetchResult>& results) {
    std::ostringstream o;
    o << "{\"results\": [";
    for (size_t i = 0; i < results.size(); ++i) {
        if (i) o << ", ";
        o << to_json(results[i]);
    }
    o << "]}";
    return o.str();
}

// Non-interactive JSON output mode for the GUI (or any script): each command
// prints exactly one line of JSON to stdout and exits, reusing the same
// Archive methods the REPL commands above already call. Returns the process
// exit code.
int run_json_command(Archive& archive, const std::vector<std::string>& args) {
    if (args.empty()) {
        std::cout << json_error("usage: tempo.exe --json <command> [args...]") << "\n";
        return 1;
    }

    const std::string& cmd = args[0];
    try {
        if (cmd == "list") {
            int limit = 20;
            if (args.size() > 1) limit = std::stoi(args[1]);
            std::cout << to_json(archive.list_games(limit)) << "\n";
        } else if (cmd == "show" || cmd == "review") {
            if (args.size() < 2) {
                std::cout << json_error("usage: " + cmd + " <id>") << "\n";
                return 1;
            }
            int id = std::stoi(args[1]);
            Game g = archive.load_game(id);
            if (cmd == "review") {
                std::vector<int> evals = evaluate_game(g);
                std::ostringstream o;
                o << "{\"game\": " << to_json(g) << ", \"evals\": [";
                for (size_t i = 0; i < evals.size(); ++i) {
                    if (i) o << ", ";
                    o << evals[i];
                }
                o << "]}";
                std::cout << o.str() << "\n";
            } else {
                std::cout << to_json(g) << "\n";
            }
        } else if (cmd == "opening") {
            if (args.size() < 2) {
                std::cout << json_error("usage: opening <name-or-ECO>") << "\n";
                return 1;
            }
            std::cout << to_json(archive.find_games_by_opening(args[1])) << "\n";
        } else if (cmd == "opening_exact") {
            if (args.size() < 2) {
                std::cout << json_error("usage: opening_exact <exact-name> [limit]") << "\n";
                return 1;
            }
            int limit = 3;
            if (args.size() >= 3) limit = std::stoi(args[2]);
            std::cout << to_json(archive.find_games_by_exact_opening(args[1], limit)) << "\n";
        } else if (cmd == "moves") {
            if (args.size() < 2) {
                std::cout << json_error("usage: moves <san-sequence>") << "\n";
                return 1;
            }
            std::vector<std::string> sequence(args.begin() + 1, args.end());
            std::cout << to_json(archive.opponent_replies(sequence)) << "\n";
        } else if (cmd == "stats") {
            std::vector<std::string> category_filter;
            for (size_t i = 1; i < args.size(); ++i) {
                std::string category = normalize_time_category(args[i]);
                if (category.empty()) {
                    std::cout << json_error("unknown game type: " + args[i] +
                                             " (expected bullet, blitz, rapid, classical, or daily)") << "\n";
                    return 1;
                }
                category_filter.push_back(category);
            }
            std::cout << to_json(archive.compute_stats(category_filter)) << "\n";
        } else if (cmd == "fetch") {
            if (args.size() < 3) {
                std::cout << json_error("usage: fetch chesscom <user> [year month] | fetch lichess <user> [days]") << "\n";
                return 1;
            }
            const std::string& site = args[1];
            const std::string& username = args[2];
            if (site == "chesscom") {
                int year = -1, month = -1;
                if (args.size() >= 5) {
                    year = std::stoi(args[3]);
                    month = std::stoi(args[4]);
                }
                std::cout << to_json(fetch_chesscom_results(archive, username, year, month)) << "\n";
            } else if (site == "lichess") {
                int days = 90;
                if (args.size() >= 4) days = std::stoi(args[3]);
                std::vector<FetchResult> results{fetch_lichess_result(archive, username, days)};
                std::cout << to_json(results) << "\n";
            } else {
                std::cout << json_error("unknown fetch site: " + site) << "\n";
                return 1;
            }
        } else {
            std::cout << json_error("unknown command: " + cmd) << "\n";
            return 1;
        }
    } catch (const std::exception& e) {
        std::cout << json_error(e.what()) << "\n";
        return 1;
    }
    return 0;
}

int main(int argc, char* argv[]) {
    // Extract --db <path> / --user <name> anywhere in the arg list (before or
    // after --json) so one shared binary can serve multiple profiles, each
    // pointed at its own db file/username. Left unset, behavior is byte-
    // identical to before these flags existed.
    std::vector<std::string> args(argv + 1, argv + argc);
    for (size_t i = 0; i < args.size(); ) {
        if (args[i] == "--db" && i + 1 < args.size()) {
            DB_PATH = args[i + 1];
            args.erase(args.begin() + i, args.begin() + i + 2);
        } else if (args[i] == "--user" && i + 1 < args.size()) {
            CURRENT_USERNAME = args[i + 1];
            args.erase(args.begin() + i, args.begin() + i + 2);
        } else {
            ++i;
        }
    }
    fs::path db_parent = fs::path(DB_PATH).parent_path();
    GAMES_DIR = db_parent.empty() ? "games" : (db_parent / "games").string();

    if (!args.empty() && args[0] == "--json") {
        Archive archive(DB_PATH);
        std::vector<std::string> json_args(args.begin() + 1, args.end());
        return run_json_command(archive, json_args);
    }

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
            std::vector<std::string> category_filter;
            std::string token;
            bool bad_category = false;
            while (iss >> token) {
                std::string category = normalize_time_category(token);
                if (category.empty()) {
                    std::cout << "[Error] unknown game type '" << token
                              << "' (expected bullet, blitz, rapid, classical, or daily)\n\n";
                    bad_category = true;
                    break;
                }
                category_filter.push_back(category);
            }
            if (!bad_category) cmd_stats(archive, category_filter);
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
        } else if (cmd == "moves") {
            std::vector<std::string> sequence;
            std::string token;
            while (iss >> token) sequence.push_back(token);
            if (sequence.empty()) {
                std::cout << "[Error] usage: moves <san-sequence>, e.g. moves e4 e5 Nf3\n\n";
            } else {
                cmd_moves(archive, sequence);
            }
        } else if (cmd == "fetch") {
            std::string site, username;
            iss >> site >> username;
            if (site.empty() || username.empty()) {
                std::cout << "[Error] usage: fetch chesscom <username> [year month]  |  fetch lichess <username> [days]\n\n";
            } else if (site == "chesscom") {
                int year, month;
                bool has_year = static_cast<bool>(iss >> year);
                bool has_month = has_year && static_cast<bool>(iss >> month);
                if (has_year && !has_month) {
                    std::cout << "[Error] usage: fetch chesscom <username> [year month] (both or neither)\n\n";
                } else {
                    cmd_fetch_chesscom(archive, username, has_month ? year : -1, has_month ? month : -1);
                }
            } else if (site == "lichess") {
                int days = 90;
                iss >> days;
                cmd_fetch_lichess(archive, username, days);
            } else {
                std::cout << "[Error] unknown fetch site '" << site << "' (expected chesscom or lichess)\n\n";
            }
        } else {
            std::cout << "[Error] unknown command '" << cmd << "'. Type 'help' for a list of commands.\n\n";
        }
    }

    std::cout << "Exiting Tempo C++...\n";
    return 0;
}
