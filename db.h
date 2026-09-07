#pragma once
#include <string>
#include <vector>
#include <stdexcept>
#include <sstream>
#include <algorithm>
#include <utility>
#include <cstdio>
#include <cctype>
#include <map>
#include "sqlite3.h"
#include "game.h"

struct GameSummary {
    int id;
    std::string date;
    std::string opponent;
    std::string your_color;
    std::string result_display; // "Win" / "Loss" / "Draw" / "?"
    std::string opening;
};

struct OpeningStat {
    std::string opening;
    int games = 0;
    int wins = 0;
};

struct TimeControlStat {
    std::string category; // "Bullet" / "Blitz" / "Rapid" / "Classical" / "Unknown"
    int games = 0;
    double avg_seconds_per_move = -1.0;
    int time_trouble_moves = 0; // moves made with < 10s on the clock
};

struct NextMoveStat {
    std::string san;
    int count = 0;
    int wins = 0, losses = 0, draws = 0;
};

struct Stats {
    int wins = 0, losses = 0, draws = 0;
    int wins_white = 0, losses_white = 0, draws_white = 0;
    int wins_black = 0, losses_black = 0, draws_black = 0;
    double avg_seconds_per_move = -1.0;
    int time_trouble_moves = 0; // moves made with < 10s on the clock
    std::vector<OpeningStat> top_openings_white; // openings you played as White
    std::vector<OpeningStat> top_openings_black; // openings faced as Black
    std::vector<TimeControlStat> by_time_control;
};

// Classifies a PGN TimeControl tag ("180", "180+2", "600+5", etc.) into a
// rough bucket based on the base time. Empty/unparseable strings are "Unknown".
inline std::string classify_time_control(const std::string& tc) {
    if (tc.empty()) return "Unknown";
    int base_seconds = 0;
    if (sscanf(tc.c_str(), "%d", &base_seconds) != 1) return "Unknown";

    if (base_seconds < 180) return "Bullet";
    if (base_seconds < 600) return "Blitz";
    if (base_seconds < 1500) return "Rapid";
    return "Classical";
}

class Archive {
public:
    explicit Archive(const std::string& path) {
        if (sqlite3_open(path.c_str(), &db_) != SQLITE_OK) {
            std::string err = sqlite3_errmsg(db_);
            sqlite3_close(db_);
            throw std::runtime_error("could not open database: " + err);
        }
        init_schema();
    }

    ~Archive() {
        if (db_) sqlite3_close(db_);
    }

    Archive(const Archive&) = delete;
    Archive& operator=(const Archive&) = delete;

    // Wrap a batch of insert_game() calls in begin_transaction()/commit_transaction()
    // when inserting many games at once (e.g. a fetched monthly PGN). Without this,
    // SQLite's default autocommit mode fsyncs after every single INSERT, which turns
    // a few hundred games (each with dozens of per-move INSERTs) into thousands of
    // individually-committed statements — seconds of work becoming minutes.
    void begin_transaction() {
        exec_or_throw("BEGIN;");
    }

    void commit_transaction() {
        exec_or_throw("COMMIT;");
    }

    // Returns the new row id, or -1 if the game was a duplicate (skipped).
    int insert_game(const Game& g) {
        std::string pgn = game_to_pgn(g);
        std::string result_for_color = result_relative_to(g.result, g.your_color);

        const char* sql =
            "INSERT OR IGNORE INTO games "
            "(event, site, date, white, black, result, your_color, eco, opening, time_control, pgn, imported_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?, datetime('now'));";
        sqlite3_stmt* stmt = prepare(sql);
        bind_text(stmt, 1, g.event);
        bind_text(stmt, 2, g.site);
        bind_text(stmt, 3, g.date);
        bind_text(stmt, 4, g.white);
        bind_text(stmt, 5, g.black);
        bind_text(stmt, 6, g.result);
        bind_text(stmt, 7, g.your_color);
        bind_text(stmt, 8, g.eco);
        bind_text(stmt, 9, g.opening);
        bind_text(stmt, 10, g.time_control);
        bind_text(stmt, 11, pgn);
        step_and_finalize(stmt);

        if (sqlite3_changes(db_) == 0) {
            return -1; // duplicate, ignored
        }
        int game_id = static_cast<int>(sqlite3_last_insert_rowid(db_));

        const char* move_sql =
            "INSERT INTO moves (game_id, ply, san, clock_seconds) VALUES (?,?,?,?);";
        for (size_t i = 0; i < g.moves.size(); ++i) {
            sqlite3_stmt* mstmt = prepare(move_sql);
            sqlite3_bind_int(mstmt, 1, game_id);
            sqlite3_bind_int(mstmt, 2, static_cast<int>(i));
            bind_text(mstmt, 3, g.moves[i].san);
            if (g.moves[i].clock_seconds >= 0) {
                sqlite3_bind_int(mstmt, 4, g.moves[i].clock_seconds);
            } else {
                sqlite3_bind_null(mstmt, 4);
            }
            step_and_finalize(mstmt);
        }

        (void)result_for_color; // reserved for future use (e.g. cached column)
        return game_id;
    }

    std::vector<GameSummary> list_games(int limit) {
        const char* sql =
            "SELECT id, date, white, black, your_color, result, opening "
            "FROM games ORDER BY id DESC LIMIT ?;";
        sqlite3_stmt* stmt = prepare(sql);
        sqlite3_bind_int(stmt, 1, limit);

        std::vector<GameSummary> out;
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            GameSummary s;
            s.id = sqlite3_column_int(stmt, 0);
            s.date = column_text(stmt, 1);
            std::string white = column_text(stmt, 2);
            std::string black = column_text(stmt, 3);
            s.your_color = column_text(stmt, 4);
            std::string result = column_text(stmt, 5);
            s.opening = column_text(stmt, 6);
            s.opponent = (s.your_color == "white") ? black : white;
            s.result_display = result_relative_to(result, s.your_color);
            out.push_back(s);
        }
        sqlite3_finalize(stmt);
        return out;
    }

    // Matches games by ECO prefix (if `query` looks like a partial ECO code,
    // e.g. "C51") or by opening-name substring otherwise.
    std::vector<GameSummary> find_games_by_opening(const std::string& query, int limit = 100) {
        bool looks_like_eco = query.size() >= 1 && query.size() <= 3 &&
                               isalpha(static_cast<unsigned char>(query[0]));
        for (size_t i = 1; i < query.size() && looks_like_eco; ++i) {
            if (!isdigit(static_cast<unsigned char>(query[i]))) looks_like_eco = false;
        }

        std::string sql =
            "SELECT id, date, white, black, your_color, result, opening "
            "FROM games WHERE ";
        sql += looks_like_eco ? "eco LIKE ? || '%'" : "opening LIKE '%' || ? || '%'";
        sql += " ORDER BY id DESC LIMIT ?;";

        sqlite3_stmt* stmt = prepare(sql.c_str());
        bind_text(stmt, 1, query);
        sqlite3_bind_int(stmt, 2, limit);

        std::vector<GameSummary> out;
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            GameSummary s;
            s.id = sqlite3_column_int(stmt, 0);
            s.date = column_text(stmt, 1);
            std::string white = column_text(stmt, 2);
            std::string black = column_text(stmt, 3);
            s.your_color = column_text(stmt, 4);
            std::string result = column_text(stmt, 5);
            s.opening = column_text(stmt, 6);
            s.opponent = (s.your_color == "white") ? black : white;
            s.result_display = result_relative_to(result, s.your_color);
            out.push_back(s);
        }
        sqlite3_finalize(stmt);
        return out;
    }

    Game load_game(int id) {
        const char* sql =
            "SELECT event, site, date, white, black, result, your_color, eco, opening, time_control "
            "FROM games WHERE id = ?;";
        sqlite3_stmt* stmt = prepare(sql);
        sqlite3_bind_int(stmt, 1, id);

        Game g;
        bool found = false;
        if (sqlite3_step(stmt) == SQLITE_ROW) {
            found = true;
            g.event = column_text(stmt, 0);
            g.site = column_text(stmt, 1);
            g.date = column_text(stmt, 2);
            g.white = column_text(stmt, 3);
            g.black = column_text(stmt, 4);
            g.result = column_text(stmt, 5);
            g.your_color = column_text(stmt, 6);
            g.eco = column_text(stmt, 7);
            g.opening = column_text(stmt, 8);
            g.time_control = column_text(stmt, 9);
        }
        sqlite3_finalize(stmt);

        if (!found) throw std::runtime_error("no game with id " + std::to_string(id));

        const char* move_sql = "SELECT san, clock_seconds FROM moves WHERE game_id = ? ORDER BY ply;";
        sqlite3_stmt* mstmt = prepare(move_sql);
        sqlite3_bind_int(mstmt, 1, id);
        while (sqlite3_step(mstmt) == SQLITE_ROW) {
            MoveRecord mr;
            mr.san = column_text(mstmt, 0);
            mr.clock_seconds = (sqlite3_column_type(mstmt, 1) == SQLITE_NULL) ? -1 : sqlite3_column_int(mstmt, 1);
            g.moves.push_back(mr);
        }
        sqlite3_finalize(mstmt);

        return g;
    }

    // For every game whose move list has `sequence` as an exact SAN prefix,
    // tallies the move played at ply == sequence.size() (whoever's turn that
    // is) into a per-distinct-move bucket, with win/loss/draw counted
    // relative to your_color. Sorted by frequency descending.
    std::vector<NextMoveStat> opponent_replies(const std::vector<std::string>& sequence) {
        std::map<int, std::pair<std::string, std::string>> game_info; // game_id -> (your_color, result)
        {
            sqlite3_stmt* stmt = prepare("SELECT id, your_color, result FROM games;");
            while (sqlite3_step(stmt) == SQLITE_ROW) {
                int id = sqlite3_column_int(stmt, 0);
                game_info[id] = {column_text(stmt, 1), column_text(stmt, 2)};
            }
            sqlite3_finalize(stmt);
        }

        std::map<int, std::map<int, std::string>> moves_by_game; // game_id -> (ply -> san)
        {
            sqlite3_stmt* stmt = prepare("SELECT game_id, ply, san FROM moves ORDER BY game_id, ply;");
            while (sqlite3_step(stmt) == SQLITE_ROW) {
                int game_id = sqlite3_column_int(stmt, 0);
                int ply = sqlite3_column_int(stmt, 1);
                moves_by_game[game_id][ply] = column_text(stmt, 2);
            }
            sqlite3_finalize(stmt);
        }

        std::vector<NextMoveStat> out;
        for (auto& [game_id, ply_moves] : moves_by_game) {
            bool matches = true;
            for (size_t ply = 0; ply < sequence.size(); ++ply) {
                auto it = ply_moves.find(static_cast<int>(ply));
                if (it == ply_moves.end() || it->second != sequence[ply]) { matches = false; break; }
            }
            if (!matches) continue;

            auto next_it = ply_moves.find(static_cast<int>(sequence.size()));
            if (next_it == ply_moves.end()) continue; // game ended exactly at the given sequence

            auto info_it = game_info.find(game_id);
            std::string color = info_it != game_info.end() ? info_it->second.first : "";
            std::string result = info_it != game_info.end() ? info_it->second.second : "";
            std::string outcome = result_relative_to(result, color);

            auto stat_it = std::find_if(out.begin(), out.end(),
                                         [&](const NextMoveStat& s) { return s.san == next_it->second; });
            if (stat_it == out.end()) {
                out.push_back({next_it->second, 0, 0, 0, 0});
                stat_it = std::prev(out.end());
            }
            stat_it->count++;
            if (outcome == "Win") stat_it->wins++;
            else if (outcome == "Loss") stat_it->losses++;
            else if (outcome == "Draw") stat_it->draws++;
        }

        std::sort(out.begin(), out.end(),
                  [](const NextMoveStat& a, const NextMoveStat& b) { return a.count > b.count; });
        return out;
    }

    Stats compute_stats() {
        Stats stats;

        const char* sql = "SELECT your_color, result FROM games WHERE your_color != '';";
        sqlite3_stmt* stmt = prepare(sql);
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            std::string color = column_text(stmt, 0);
            std::string result = column_text(stmt, 1);
            std::string outcome = result_relative_to(result, color);

            if (outcome == "Win") { stats.wins++; if (color == "white") stats.wins_white++; else stats.wins_black++; }
            else if (outcome == "Loss") { stats.losses++; if (color == "white") stats.losses_white++; else stats.losses_black++; }
            else if (outcome == "Draw") { stats.draws++; if (color == "white") stats.draws_white++; else stats.draws_black++; }
        }
        sqlite3_finalize(stmt);

        const char* avg_sql =
            "SELECT AVG(clock_prev - clock_seconds) FROM ("
            "  SELECT clock_seconds, LAG(clock_seconds) OVER (PARTITION BY game_id ORDER BY ply) AS clock_prev "
            "  FROM moves WHERE clock_seconds IS NOT NULL"
            ") WHERE clock_prev IS NOT NULL AND clock_prev >= clock_seconds;";
        sqlite3_stmt* avg_stmt = prepare(avg_sql);
        if (sqlite3_step(avg_stmt) == SQLITE_ROW && sqlite3_column_type(avg_stmt, 0) != SQLITE_NULL) {
            stats.avg_seconds_per_move = sqlite3_column_double(avg_stmt, 0);
        }
        sqlite3_finalize(avg_stmt);

        const char* trouble_sql = "SELECT COUNT(*) FROM moves WHERE clock_seconds IS NOT NULL AND clock_seconds < 10;";
        sqlite3_stmt* trouble_stmt = prepare(trouble_sql);
        if (sqlite3_step(trouble_stmt) == SQLITE_ROW) {
            stats.time_trouble_moves = sqlite3_column_int(trouble_stmt, 0);
        }
        sqlite3_finalize(trouble_stmt);

        stats.top_openings_white = aggregate_openings("white");
        stats.top_openings_black = aggregate_openings("black");
        stats.by_time_control = aggregate_time_control();

        return stats;
    }

private:
    sqlite3* db_ = nullptr;

    std::vector<OpeningStat> aggregate_openings(const std::string& color) {
        const char* sql =
            "SELECT opening, result FROM games WHERE opening != '' AND your_color = ?;";
        sqlite3_stmt* stmt = prepare(sql);
        bind_text(stmt, 1, color);

        std::vector<OpeningStat> out;
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            std::string opening = column_text(stmt, 0);
            std::string result = column_text(stmt, 1);
            std::string outcome = result_relative_to(result, color);

            auto it = std::find_if(out.begin(), out.end(),
                                    [&](const OpeningStat& o) { return o.opening == opening; });
            if (it == out.end()) {
                out.push_back({opening, 0, 0});
                it = std::prev(out.end());
            }
            it->games++;
            if (outcome == "Win") it->wins++;
        }
        sqlite3_finalize(stmt);

        std::sort(out.begin(), out.end(),
                  [](const OpeningStat& a, const OpeningStat& b) { return a.games > b.games; });
        return out;
    }

    std::vector<TimeControlStat> aggregate_time_control() {
        // Per-move clock deltas joined with each game's time_control, so each
        // delta can be bucketed by category before averaging.
        const char* sql =
            "SELECT g.time_control, m.clock_prev - m.clock_seconds AS delta, m.clock_seconds FROM ("
            "  SELECT game_id, clock_seconds, LAG(clock_seconds) OVER (PARTITION BY game_id ORDER BY ply) AS clock_prev "
            "  FROM moves WHERE clock_seconds IS NOT NULL"
            ") m JOIN games g ON g.id = m.game_id "
            "WHERE m.clock_prev IS NOT NULL AND m.clock_prev >= m.clock_seconds;";
        sqlite3_stmt* stmt = prepare(sql);

        std::map<std::string, std::pair<long long, long long>> delta_sum_count; // category -> (sum, count)
        std::map<std::string, int> trouble_count;

        while (sqlite3_step(stmt) == SQLITE_ROW) {
            std::string tc = column_text(stmt, 0);
            int delta = sqlite3_column_int(stmt, 1);
            int clock_seconds = sqlite3_column_int(stmt, 2);
            std::string category = classify_time_control(tc);

            auto& agg = delta_sum_count[category];
            agg.first += delta;
            agg.second += 1;
            if (clock_seconds < 10) trouble_count[category]++;
        }
        sqlite3_finalize(stmt);

        // Distinct games per category (for the "games" count in the report).
        const char* games_sql = "SELECT time_control FROM games;";
        sqlite3_stmt* games_stmt = prepare(games_sql);
        std::map<std::string, int> games_count;
        while (sqlite3_step(games_stmt) == SQLITE_ROW) {
            games_count[classify_time_control(column_text(games_stmt, 0))]++;
        }
        sqlite3_finalize(games_stmt);

        std::vector<TimeControlStat> out;
        for (auto& [category, count] : games_count) {
            TimeControlStat s;
            s.category = category;
            s.games = count;
            auto it = delta_sum_count.find(category);
            if (it != delta_sum_count.end() && it->second.second > 0) {
                s.avg_seconds_per_move = static_cast<double>(it->second.first) / it->second.second;
            }
            s.time_trouble_moves = trouble_count.count(category) ? trouble_count[category] : 0;
            out.push_back(s);
        }
        std::sort(out.begin(), out.end(),
                  [](const TimeControlStat& a, const TimeControlStat& b) { return a.games > b.games; });
        return out;
    }

    void exec_or_throw(const char* sql) {
        char* err = nullptr;
        if (sqlite3_exec(db_, sql, nullptr, nullptr, &err) != SQLITE_OK) {
            std::string msg = err ? err : "unknown error";
            sqlite3_free(err);
            throw std::runtime_error(std::string("sqlite exec failed (") + sql + "): " + msg);
        }
    }

    void init_schema() {
        const char* schema =
            "CREATE TABLE IF NOT EXISTS games ("
            "  id INTEGER PRIMARY KEY,"
            "  event TEXT, site TEXT, date TEXT,"
            "  white TEXT, black TEXT, result TEXT,"
            "  your_color TEXT,"
            "  eco TEXT, opening TEXT,"
            "  time_control TEXT,"
            "  pgn TEXT NOT NULL,"
            "  imported_at TEXT NOT NULL,"
            "  UNIQUE(site, date, white, black, result, pgn)"
            ");"
            "CREATE TABLE IF NOT EXISTS moves ("
            "  game_id INTEGER NOT NULL REFERENCES games(id),"
            "  ply INTEGER NOT NULL,"
            "  san TEXT NOT NULL,"
            "  clock_seconds INTEGER,"
            "  eval_cp INTEGER,"
            "  PRIMARY KEY (game_id, ply)"
            ");";
        exec_or_throw(schema);
    }

    sqlite3_stmt* prepare(const char* sql) {
        sqlite3_stmt* stmt = nullptr;
        if (sqlite3_prepare_v2(db_, sql, -1, &stmt, nullptr) != SQLITE_OK) {
            throw std::runtime_error(std::string("sqlite prepare failed: ") + sqlite3_errmsg(db_));
        }
        return stmt;
    }

    void bind_text(sqlite3_stmt* stmt, int index, const std::string& value) {
        sqlite3_bind_text(stmt, index, value.c_str(), -1, SQLITE_TRANSIENT);
    }

    void step_and_finalize(sqlite3_stmt* stmt) {
        int rc = sqlite3_step(stmt);
        if (rc != SQLITE_DONE) {
            std::string err = sqlite3_errmsg(db_);
            sqlite3_finalize(stmt);
            throw std::runtime_error("sqlite step failed: " + err);
        }
        sqlite3_finalize(stmt);
    }

    static std::string column_text(sqlite3_stmt* stmt, int index) {
        const unsigned char* text = sqlite3_column_text(stmt, index);
        return text ? reinterpret_cast<const char*>(text) : "";
    }

    // Maps a PGN result ("1-0", "0-1", "1/2-1/2") to "Win"/"Loss"/"Draw" from
    // the perspective of your_color ("white"/"black"). Returns "?" if unknown.
    static std::string result_relative_to(const std::string& result, const std::string& your_color) {
        if (result == "1/2-1/2") return "Draw";
        if (your_color.empty()) return "?";
        bool white_won = (result == "1-0");
        bool black_won = (result == "0-1");
        if (!white_won && !black_won) return "?";
        bool you_won = (your_color == "white") ? white_won : black_won;
        return you_won ? "Win" : "Loss";
    }
};
