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
#include <chrono>
#include <ctime>
#include <optional>
#include "sqlite3.h"
#include "game.h"

struct GameSummary {
    int id;
    std::string date;
    std::string opponent;
    std::string your_color;
    std::string result_display; // "Win" / "Loss" / "Draw" / "?"
    std::string opening;
    std::string site; // normalized platform label: "Chess.com", "Lichess", or "Unknown"
    std::string time_category; // "Bullet" / "Blitz" / "Rapid" / "Classical" / "Daily" / "Unknown"
    std::optional<int> your_elo; // rating for *your* color in this game, if the PGN carried one
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
    int current_rating = -1;    // -1 = no rating data for this category (in the active filter/range)
    std::string rating_as_of;   // date of the game current_rating came from
};

// One game's rating data point for *your* color (from Archive::rating_history).
struct RatingPoint {
    int game_id = 0;
    std::string date;
    std::string time_category; // "Bullet" / "Blitz" / "Rapid" / "Classical" / "Daily" / "Unknown"
    int rating = 0;
    std::string opponent;
    std::string result_display; // "Win" / "Loss" / "Draw" / "?"
};

struct NextMoveStat {
    std::string san;
    int count = 0;
    int wins = 0, losses = 0, draws = 0;
};

// One platform's fetch history, for computing the next incremental /fetch
// window. last_covered_year/month are chess.com-only (0 = unset); lichess
// only ever uses last_fetched_at as its "since" boundary.
struct FetchHistoryRow {
    std::string last_fetched_at;  // SQLite datetime('now'), UTC "YYYY-MM-DD HH:MM:SS"
    int last_covered_year = 0;
    int last_covered_month = 0;
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
    std::string earliest_date, latest_date; // date range covered by the archive, e.g. "2026.07.15" .. "2026.09.05"
};

// Normalizes a PGN Site tag into a clean platform label. Chess.com's Site
// tag is already just "Chess.com", but lichess's is a per-game URL (e.g.
// "https://lichess.org/abcd1234"), so a raw display would be inconsistent
// between the two sources.
inline std::string platform_label(const std::string& site) {
    std::string lower = site;
    for (char& c : lower) c = static_cast<char>(tolower(static_cast<unsigned char>(c)));
    if (lower.find("chess.com") != std::string::npos) return "Chess.com";
    if (lower.find("lichess") != std::string::npos) return "Lichess";
    return site.empty() ? "Unknown" : site;
}

// Classifies a PGN TimeControl tag ("180", "180+2", "600+5", etc.) into a
// rough bucket based on the base time. Empty/unparseable strings are "Unknown".
inline std::string classify_time_control(const std::string& tc) {
    if (tc.empty()) return "Unknown";
    // Correspondence/daily controls look like "1/259200" (move count / total
    // seconds) -- sscanf("%d") on that would read just the leading "1" and
    // misclassify as Bullet, so catch the "/" form first.
    if (tc.find('/') != std::string::npos) return "Daily";

    int base_seconds = 0;
    if (sscanf(tc.c_str(), "%d", &base_seconds) != 1) return "Unknown";

    if (base_seconds < 180) return "Bullet";
    if (base_seconds < 600) return "Blitz";
    if (base_seconds < 1800) return "Rapid";
    return "Classical";
}

// Maps a user-typed game-type name (any case) to its canonical category
// string, or "" if unrecognized. Used for /stats <type...> filtering.
inline std::string normalize_time_category(const std::string& input) {
    std::string lower = input;
    for (char& c : lower) c = static_cast<char>(tolower(static_cast<unsigned char>(c)));
    if (lower == "bullet") return "Bullet";
    if (lower == "blitz") return "Blitz";
    if (lower == "rapid") return "Rapid";
    if (lower == "classical") return "Classical";
    if (lower == "daily") return "Daily";
    return "";
}

// Returns the "YYYY.MM.DD" date `days` calendar days before today (local
// time), for use as a /stats or /opening date-range filter cutoff.
inline std::string days_to_cutoff_date(int days) {
    std::time_t tt = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::tm local_tm = *std::localtime(&tt); // copy out of the shared static buffer before mutating
    local_tm.tm_mday -= days;
    std::mktime(&local_tm); // normalizes tm_mday/tm_mon/tm_year after the subtraction
    char buf[16];
    snprintf(buf, sizeof(buf), "%04d.%02d.%02d", local_tm.tm_year + 1900, local_tm.tm_mon + 1, local_tm.tm_mday);
    return buf;
}

// True if `date` is on/after `min_date`, `min_date` is empty (no
// restriction -- the "all" sentinel, matching how an empty category_filter
// already means unrestricted), or `date` is a partial/unknown PGN date
// (empty, or containing '?', e.g. "2026.??.??") that can't be reliably
// compared -- such games are never excluded by a date filter.
inline bool date_in_range(const std::string& date, const std::string& min_date) {
    if (min_date.empty()) return true;
    if (date.empty() || date.find('?') != std::string::npos) return true;
    return date >= min_date;
}

// Parses a /stats or /opening date-range token ("14d"/"30d"/"60d"/"90d"/
// "180d" or "all") into a cutoff date via days_to_cutoff_date, writing it
// to out_min_date and returning true; returns false (out_min_date
// untouched) if `token` doesn't match that syntax, so callers can fall
// through to treat it as a game-type category or opening-query word.
inline bool parse_range_token(const std::string& token, std::string& out_min_date) {
    std::string lower = token;
    for (char& c : lower) c = static_cast<char>(tolower(static_cast<unsigned char>(c)));
    if (lower == "all") { out_min_date = ""; return true; }
    if (lower.size() >= 2 && lower.back() == 'd') {
        std::string digits = lower.substr(0, lower.size() - 1);
        bool all_digits = !digits.empty() && std::all_of(digits.begin(), digits.end(),
                                                           [](unsigned char c) { return isdigit(c); });
        if (all_digits) { out_min_date = days_to_cutoff_date(std::stoi(digits)); return true; }
    }
    return false;
}

// Parses a sequence of /stats or /rating trailing tokens (each either a
// game-type category or a date-range token, see normalize_time_category/
// parse_range_token) starting at args[start], filling category_filter/
// min_date. Returns "" on success, or an error message describing the
// first unrecognized token (mirroring the two call sites' previous
// inline error text) so callers can print it and bail out.
inline std::string parse_category_and_range_args(
    const std::vector<std::string>& args, size_t start,
    std::vector<std::string>& category_filter, std::string& min_date) {
    for (size_t i = start; i < args.size(); ++i) {
        std::string range_date;
        if (parse_range_token(args[i], range_date)) {
            min_date = range_date;
            continue;
        }
        std::string category = normalize_time_category(args[i]);
        if (category.empty()) {
            return "unknown game type or range: " + args[i] +
                   " (expected bullet, blitz, rapid, classical, daily, 14d/30d/60d/90d/180d, or all)";
        }
        category_filter.push_back(category);
    }
    return "";
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
            "(event, site, date, white, black, result, your_color, eco, opening, time_control, white_elo, black_elo, pgn, imported_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now'));";
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
        if (g.white_elo) sqlite3_bind_int(stmt, 11, *g.white_elo); else sqlite3_bind_null(stmt, 11);
        if (g.black_elo) sqlite3_bind_int(stmt, 12, *g.black_elo); else sqlite3_bind_null(stmt, 12);
        bind_text(stmt, 13, pgn);
        step_and_finalize(stmt);

        if (sqlite3_changes(db_) == 0) {
            backfill_rating_on_duplicate(g, pgn);
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

    // Called when insert_game hits an already-imported game (INSERT OR
    // IGNORE matched the UNIQUE(site, date, white, black, result, pgn) key).
    // An ordinary incremental fetch rarely re-encounters a duplicate, but an
    // explicit "full" re-fetch (see main.cpp) re-downloads everything and
    // will hit one for nearly every existing game -- this is how that
    // re-fetch backfills white_elo/black_elo onto rows that predate this
    // feature, without ever overwriting a rating already on file.
    void backfill_rating_on_duplicate(const Game& g, const std::string& pgn) {
        if (!g.white_elo && !g.black_elo) return;

        const char* find_sql =
            "SELECT id FROM games WHERE site=? AND date=? AND white=? AND black=? AND result=? AND pgn=?;";
        sqlite3_stmt* find_stmt = prepare(find_sql);
        bind_text(find_stmt, 1, g.site);
        bind_text(find_stmt, 2, g.date);
        bind_text(find_stmt, 3, g.white);
        bind_text(find_stmt, 4, g.black);
        bind_text(find_stmt, 5, g.result);
        bind_text(find_stmt, 6, pgn);

        int existing_id = -1;
        if (sqlite3_step(find_stmt) == SQLITE_ROW) {
            existing_id = sqlite3_column_int(find_stmt, 0);
        }
        sqlite3_finalize(find_stmt);
        if (existing_id < 0) return; // shouldn't happen (IGNORE just matched this key), but don't crash if it does

        const char* update_sql =
            "UPDATE games SET white_elo = COALESCE(white_elo, ?), black_elo = COALESCE(black_elo, ?) WHERE id = ?;";
        sqlite3_stmt* update_stmt = prepare(update_sql);
        if (g.white_elo) sqlite3_bind_int(update_stmt, 1, *g.white_elo); else sqlite3_bind_null(update_stmt, 1);
        if (g.black_elo) sqlite3_bind_int(update_stmt, 2, *g.black_elo); else sqlite3_bind_null(update_stmt, 2);
        sqlite3_bind_int(update_stmt, 3, existing_id);
        step_and_finalize(update_stmt);
    }

    // Returns the recorded fetch state for `platform` ("chesscom"/"lichess"),
    // or nullopt if that platform has never been fetched for this profile.
    std::optional<FetchHistoryRow> get_last_fetch(const std::string& platform) {
        const char* sql =
            "SELECT last_fetched_at, last_covered_year, last_covered_month "
            "FROM fetch_history WHERE platform = ?;";
        sqlite3_stmt* stmt = prepare(sql);
        bind_text(stmt, 1, platform);

        std::optional<FetchHistoryRow> out;
        if (sqlite3_step(stmt) == SQLITE_ROW) {
            FetchHistoryRow row;
            row.last_fetched_at = column_text(stmt, 0);
            row.last_covered_year = (sqlite3_column_type(stmt, 1) == SQLITE_NULL) ? 0 : sqlite3_column_int(stmt, 1);
            row.last_covered_month = (sqlite3_column_type(stmt, 2) == SQLITE_NULL) ? 0 : sqlite3_column_int(stmt, 2);
            out = row;
        }
        sqlite3_finalize(stmt);
        return out;
    }

    // Records that `platform` was just successfully fetched, stamping the
    // current time. `covered_year`/`covered_month` are chess.com-only (the
    // calendar month gap-filling should resume from next time); leave at 0
    // for lichess, stored as NULL. Callers (main.cpp) are responsible for
    // any "don't regress" merging against the previously recorded value --
    // this is a plain upsert with no merge logic of its own.
    void record_fetch(const std::string& platform, int covered_year = 0, int covered_month = 0) {
        const char* sql =
            "INSERT OR REPLACE INTO fetch_history (platform, last_fetched_at, last_covered_year, last_covered_month) "
            "VALUES (?, datetime('now'), ?, ?);";
        sqlite3_stmt* stmt = prepare(sql);
        bind_text(stmt, 1, platform);
        if (covered_year > 0) sqlite3_bind_int(stmt, 2, covered_year); else sqlite3_bind_null(stmt, 2);
        if (covered_month > 0) sqlite3_bind_int(stmt, 3, covered_month); else sqlite3_bind_null(stmt, 3);
        step_and_finalize(stmt);
    }

    std::vector<GameSummary> list_games(int limit) {
        const char* sql =
            "SELECT id, date, white, black, your_color, result, opening, site, time_control, white_elo, black_elo "
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
            s.site = platform_label(column_text(stmt, 7));
            s.time_category = classify_time_control(column_text(stmt, 8));
            s.opponent = (s.your_color == "white") ? black : white;
            s.result_display = result_relative_to(result, s.your_color);
            std::optional<int> white_elo = column_optional_int(stmt, 9);
            std::optional<int> black_elo = column_optional_int(stmt, 10);
            s.your_elo = (s.your_color == "white") ? white_elo : black_elo;
            out.push_back(s);
        }
        sqlite3_finalize(stmt);
        return out;
    }

    // Matches games by ECO prefix (if `query` looks like a partial ECO code,
    // e.g. "C51") or by opening-name substring otherwise.
    std::vector<GameSummary> find_games_by_opening(const std::string& query, int limit = 100, const std::string& min_date = "") {
        bool looks_like_eco = query.size() >= 1 && query.size() <= 3 &&
                               isalpha(static_cast<unsigned char>(query[0]));
        for (size_t i = 1; i < query.size() && looks_like_eco; ++i) {
            if (!isdigit(static_cast<unsigned char>(query[i]))) looks_like_eco = false;
        }

        std::string sql =
            "SELECT id, date, white, black, your_color, result, opening, site, time_control, white_elo, black_elo "
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
            if (!date_in_range(s.date, min_date)) continue;
            std::string white = column_text(stmt, 2);
            std::string black = column_text(stmt, 3);
            s.your_color = column_text(stmt, 4);
            std::string result = column_text(stmt, 5);
            s.opening = column_text(stmt, 6);
            s.site = platform_label(column_text(stmt, 7));
            s.time_category = classify_time_control(column_text(stmt, 8));
            s.opponent = (s.your_color == "white") ? black : white;
            s.result_display = result_relative_to(result, s.your_color);
            std::optional<int> white_elo = column_optional_int(stmt, 9);
            std::optional<int> black_elo = column_optional_int(stmt, 10);
            s.your_elo = (s.your_color == "white") ? white_elo : black_elo;
            out.push_back(s);
        }
        sqlite3_finalize(stmt);
        return out;
    }

    // Exact opening-name match (unlike find_games_by_opening's substring
    // match), used to list a handful of recent games behind one specific
    // /stats opening row -- those rows are grouped by exact string equality,
    // so a substring match would also pull in unrelated sub-variations.
    std::vector<GameSummary> find_games_by_exact_opening(const std::string& name, int limit = 3, const std::string& min_date = "") {
        const char* sql =
            "SELECT id, date, white, black, your_color, result, opening, site, time_control, white_elo, black_elo "
            "FROM games WHERE opening = ? ORDER BY id DESC LIMIT ?;";
        sqlite3_stmt* stmt = prepare(sql);
        bind_text(stmt, 1, name);
        sqlite3_bind_int(stmt, 2, limit);

        std::vector<GameSummary> out;
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            GameSummary s;
            s.id = sqlite3_column_int(stmt, 0);
            s.date = column_text(stmt, 1);
            if (!date_in_range(s.date, min_date)) continue;
            std::string white = column_text(stmt, 2);
            std::string black = column_text(stmt, 3);
            s.your_color = column_text(stmt, 4);
            std::string result = column_text(stmt, 5);
            s.opening = column_text(stmt, 6);
            s.site = platform_label(column_text(stmt, 7));
            s.time_category = classify_time_control(column_text(stmt, 8));
            s.opponent = (s.your_color == "white") ? black : white;
            s.result_display = result_relative_to(result, s.your_color);
            std::optional<int> white_elo = column_optional_int(stmt, 9);
            std::optional<int> black_elo = column_optional_int(stmt, 10);
            s.your_elo = (s.your_color == "white") ? white_elo : black_elo;
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
    // relative to your_color. Sorted by frequency descending. If
    // `color_filter` is "white" or "black", only games where your_color
    // matches are considered -- used by the repertoire explorer to show
    // your own move choices consistently (ply parity always lines up with
    // your_color once the games are restricted this way).
    std::vector<NextMoveStat> opponent_replies(const std::vector<std::string>& sequence,
                                                const std::string& color_filter = "") {
        std::map<int, std::pair<std::string, std::string>> game_info; // game_id -> (your_color, result)
        {
            sqlite3_stmt* stmt = prepare("SELECT id, your_color, result FROM games;");
            while (sqlite3_step(stmt) == SQLITE_ROW) {
                int id = sqlite3_column_int(stmt, 0);
                std::string your_color = column_text(stmt, 1);
                if (!color_filter.empty() && your_color != color_filter) continue;
                game_info[id] = {your_color, column_text(stmt, 2)};
            }
            sqlite3_finalize(stmt);
        }

        std::map<int, std::map<int, std::string>> moves_by_game; // game_id -> (ply -> san)
        {
            sqlite3_stmt* stmt = prepare("SELECT game_id, ply, san FROM moves ORDER BY game_id, ply;");
            while (sqlite3_step(stmt) == SQLITE_ROW) {
                int game_id = sqlite3_column_int(stmt, 0);
                if (!color_filter.empty() && game_info.find(game_id) == game_info.end()) continue;
                int ply = sqlite3_column_int(stmt, 1);
                moves_by_game[game_id][ply] = column_text(stmt, 2);
            }
            sqlite3_finalize(stmt);
        }

        std::vector<NextMoveStat> out;
        for (auto& [game_id, ply_moves] : moves_by_game) {
            if (!matches_move_prefix(ply_moves, sequence)) continue;

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

    // For every game whose move list has `sequence` as an exact SAN prefix,
    // returns that game (unlike opponent_replies, which only aggregates the
    // *next* move) -- used for the GUI's live board-position filter, which
    // renders a full browsable tree rather than a truncated chat table, so
    // `limit` defaults far higher than find_games_by_opening's (100) --
    // an empty sequence should show the WHOLE archive, not just the 100
    // most recent games. Unlike opponent_replies, a game whose recorded
    // line ends exactly at `sequence` still matches here (there's no "next
    // move" requirement). An empty sequence matches every game. Sorted
    // newest-first, truncated to `limit`.
    std::vector<GameSummary> find_games_by_move_prefix(const std::vector<std::string>& sequence, int limit = 100000) {
        std::map<int, GameSummary> game_rows; // game_id -> partially-built GameSummary
        {
            sqlite3_stmt* stmt = prepare(
                "SELECT id, date, white, black, your_color, result, opening, site, time_control, white_elo, black_elo FROM games;");
            while (sqlite3_step(stmt) == SQLITE_ROW) {
                GameSummary s;
                int id = sqlite3_column_int(stmt, 0);
                s.id = id;
                s.date = column_text(stmt, 1);
                std::string white = column_text(stmt, 2);
                std::string black = column_text(stmt, 3);
                s.your_color = column_text(stmt, 4);
                std::string result = column_text(stmt, 5);
                s.opening = column_text(stmt, 6);
                s.site = platform_label(column_text(stmt, 7));
                s.time_category = classify_time_control(column_text(stmt, 8));
                s.opponent = (s.your_color == "white") ? black : white;
                s.result_display = result_relative_to(result, s.your_color);
                std::optional<int> white_elo = column_optional_int(stmt, 9);
                std::optional<int> black_elo = column_optional_int(stmt, 10);
                s.your_elo = (s.your_color == "white") ? white_elo : black_elo;
                game_rows[id] = s;
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

        std::vector<GameSummary> out;
        for (auto& [game_id, ply_moves] : moves_by_game) {
            if (!matches_move_prefix(ply_moves, sequence)) continue;
            auto it = game_rows.find(game_id);
            if (it != game_rows.end()) out.push_back(it->second);
        }

        std::sort(out.begin(), out.end(),
                  [](const GameSummary& a, const GameSummary& b) { return a.id > b.id; });
        if (static_cast<int>(out.size()) > limit) out.resize(limit);
        return out;
    }

    // `category_filter` restricts every figure to games whose time control
    // classifies into one of the given categories (e.g. {"Blitz", "Rapid"});
    // an empty filter means no restriction (all games), matching the
    // previous unfiltered behavior exactly. `min_date` similarly restricts
    // to games on/after that date ("" means no restriction).
    Stats compute_stats(const std::vector<std::string>& category_filter = {}, const std::string& min_date = "") {
        Stats stats;

        {
            sqlite3_stmt* stmt = prepare("SELECT date, your_color, result, time_control FROM games WHERE your_color != '';");
            std::string earliest, latest;
            while (sqlite3_step(stmt) == SQLITE_ROW) {
                std::string date = column_text(stmt, 0);
                std::string color = column_text(stmt, 1);
                std::string result = column_text(stmt, 2);
                std::string tc = column_text(stmt, 3);
                if (!passes_filters(date, tc, category_filter, min_date)) continue;

                if (earliest.empty() || date < earliest) earliest = date;
                if (latest.empty() || date > latest) latest = date;

                std::string outcome = result_relative_to(result, color);
                if (outcome == "Win") { stats.wins++; if (color == "white") stats.wins_white++; else stats.wins_black++; }
                else if (outcome == "Loss") { stats.losses++; if (color == "white") stats.losses_white++; else stats.losses_black++; }
                else if (outcome == "Draw") { stats.draws++; if (color == "white") stats.draws_white++; else stats.draws_black++; }
            }
            sqlite3_finalize(stmt);
            stats.earliest_date = earliest;
            stats.latest_date = latest;
        }

        stats.top_openings_white = aggregate_openings("white", category_filter, min_date);
        stats.top_openings_black = aggregate_openings("black", category_filter, min_date);
        stats.by_time_control = aggregate_time_control(category_filter, min_date, stats.avg_seconds_per_move, stats.time_trouble_moves);

        // Fold each category's current rating into its by_time_control entry
        // -- reusing rating_history() (same category/date filters) rather
        // than a second hand-rolled SQL query, so "current" always means the
        // same thing here as it does in the dedicated /rating command: the
        // last (most recent) point for that category.
        std::map<std::string, RatingPoint> current_by_category;
        for (const RatingPoint& p : rating_history(category_filter, min_date)) current_by_category[p.time_category] = p;
        for (TimeControlStat& t : stats.by_time_control) {
            auto it = current_by_category.find(t.category);
            if (it != current_by_category.end()) {
                t.current_rating = it->second.rating;
                t.rating_as_of = it->second.date;
            }
        }

        return stats;
    }

    // One entry per game that has a recorded rating for *your* color,
    // ordered chronologically (oldest first) so the GUI can render both a
    // history table and derive "current rating per category" (the last
    // entry per time_category) from this single list. `category_filter`/
    // `min_date` mean the same as in compute_stats.
    std::vector<RatingPoint> rating_history(const std::vector<std::string>& category_filter = {}, const std::string& min_date = "") {
        const char* sql =
            "SELECT id, date, white, black, your_color, result, time_control, "
            "       CASE your_color WHEN 'white' THEN white_elo ELSE black_elo END AS your_elo "
            "FROM games WHERE your_color != '' AND your_elo IS NOT NULL ORDER BY id ASC;";
        sqlite3_stmt* stmt = prepare(sql);

        std::vector<RatingPoint> out;
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            std::string date = column_text(stmt, 1);
            std::string tc = column_text(stmt, 6);
            if (!passes_filters(date, tc, category_filter, min_date)) continue;

            RatingPoint p;
            p.game_id = sqlite3_column_int(stmt, 0);
            p.date = date;
            std::string white = column_text(stmt, 2);
            std::string black = column_text(stmt, 3);
            std::string your_color = column_text(stmt, 4);
            std::string result = column_text(stmt, 5);
            p.time_category = classify_time_control(tc);
            p.rating = sqlite3_column_int(stmt, 7);
            p.opponent = (your_color == "white") ? black : white;
            p.result_display = result_relative_to(result, your_color);
            out.push_back(p);
        }
        sqlite3_finalize(stmt);
        return out;
    }

private:
    sqlite3* db_ = nullptr;

    // True if `ply_moves` (a game's ply -> san map) has `sequence` as an
    // exact prefix, starting from ply 0. An empty sequence always matches.
    static bool matches_move_prefix(const std::map<int, std::string>& ply_moves,
                                     const std::vector<std::string>& sequence) {
        for (size_t ply = 0; ply < sequence.size(); ++ply) {
            auto it = ply_moves.find(static_cast<int>(ply));
            if (it == ply_moves.end() || it->second != sequence[ply]) return false;
        }
        return true;
    }

    // True if `time_control`'s category is in `filter`, or `filter` is empty
    // (meaning "no restriction" -- everything matches).
    bool category_matches(const std::string& time_control, const std::vector<std::string>& filter) {
        if (filter.empty()) return true;
        std::string category = classify_time_control(time_control);
        for (const std::string& f : filter) {
            if (f == category) return true;
        }
        return false;
    }

    // Combines the game-type and date-range filters -- both must pass.
    bool passes_filters(const std::string& date, const std::string& time_control,
                         const std::vector<std::string>& category_filter, const std::string& min_date) {
        return category_matches(time_control, category_filter) && date_in_range(date, min_date);
    }

    std::vector<OpeningStat> aggregate_openings(const std::string& color, const std::vector<std::string>& category_filter,
                                                 const std::string& min_date) {
        const char* sql =
            "SELECT opening, result, time_control, date FROM games WHERE opening != '' AND your_color = ?;";
        sqlite3_stmt* stmt = prepare(sql);
        bind_text(stmt, 1, color);

        std::vector<OpeningStat> out;
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            std::string opening = column_text(stmt, 0);
            std::string result = column_text(stmt, 1);
            std::string tc = column_text(stmt, 2);
            std::string date = column_text(stmt, 3);
            if (!passes_filters(date, tc, category_filter, min_date)) continue;
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

    // Also fills `out_avg_seconds_per_move`/`out_time_trouble_moves` with the
    // OVERALL figures across just the allowed categories/date range (or
    // everything, if unrestricted) -- derived from the same per-category
    // sums computed here rather than a second query.
    std::vector<TimeControlStat> aggregate_time_control(const std::vector<std::string>& category_filter,
                                                          const std::string& min_date,
                                                          double& out_avg_seconds_per_move,
                                                          int& out_time_trouble_moves) {
        // Per-move clock deltas joined with each game's time_control/date, so
        // each delta can be bucketed by category before averaging.
        const char* sql =
            "SELECT g.time_control, m.clock_prev - m.clock_seconds AS delta, m.clock_seconds, g.date FROM ("
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
            std::string date = column_text(stmt, 3);
            if (!passes_filters(date, tc, category_filter, min_date)) continue;
            std::string category = classify_time_control(tc);

            auto& agg = delta_sum_count[category];
            agg.first += delta;
            agg.second += 1;
            if (clock_seconds < 10) trouble_count[category]++;
        }
        sqlite3_finalize(stmt);

        // Distinct games per category (for the "games" count in the report).
        const char* games_sql = "SELECT time_control, date FROM games;";
        sqlite3_stmt* games_stmt = prepare(games_sql);
        std::map<std::string, int> games_count;
        while (sqlite3_step(games_stmt) == SQLITE_ROW) {
            std::string tc = column_text(games_stmt, 0);
            std::string date = column_text(games_stmt, 1);
            if (!passes_filters(date, tc, category_filter, min_date)) continue;
            games_count[classify_time_control(tc)]++;
        }
        sqlite3_finalize(games_stmt);

        std::vector<TimeControlStat> out;
        long long overall_sum = 0, overall_count = 0;
        int overall_trouble = 0;
        for (auto& [category, count] : games_count) {
            TimeControlStat s;
            s.category = category;
            s.games = count;
            auto it = delta_sum_count.find(category);
            if (it != delta_sum_count.end() && it->second.second > 0) {
                s.avg_seconds_per_move = static_cast<double>(it->second.first) / it->second.second;
                overall_sum += it->second.first;
                overall_count += it->second.second;
            }
            s.time_trouble_moves = trouble_count.count(category) ? trouble_count[category] : 0;
            overall_trouble += s.time_trouble_moves;
            out.push_back(s);
        }
        std::sort(out.begin(), out.end(),
                  [](const TimeControlStat& a, const TimeControlStat& b) { return a.games > b.games; });

        out_avg_seconds_per_move = overall_count > 0 ? static_cast<double>(overall_sum) / overall_count : -1.0;
        out_time_trouble_moves = overall_trouble;
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
            ");"
            "CREATE TABLE IF NOT EXISTS fetch_history ("
            "  platform TEXT PRIMARY KEY,"
            "  last_fetched_at TEXT NOT NULL,"
            "  last_covered_year INTEGER,"
            "  last_covered_month INTEGER"
            ");";
        exec_or_throw(schema);

        // CREATE TABLE IF NOT EXISTS above is a no-op on an already-existing
        // games table, so a column added after the table was first created
        // (like these two) needs an explicit migration to reach real,
        // already-populated archives.
        ensure_column("games", "white_elo", "INTEGER");
        ensure_column("games", "black_elo", "INTEGER");
    }

    // Adds `column` to `table` (as `decl`, e.g. "INTEGER") if it doesn't
    // already exist. Safe to call on every startup -- PRAGMA table_info is
    // just a metadata read, so this is a cheap no-op once the column exists.
    void ensure_column(const char* table, const char* column, const char* decl) {
        std::string sql = std::string("PRAGMA table_info(") + table + ");";
        sqlite3_stmt* stmt = prepare(sql.c_str());
        bool exists = false;
        while (sqlite3_step(stmt) == SQLITE_ROW) {
            if (column_text(stmt, 1) == column) { exists = true; break; }
        }
        sqlite3_finalize(stmt);
        if (!exists) {
            exec_or_throw((std::string("ALTER TABLE ") + table + " ADD COLUMN " + column + " " + decl + ";").c_str());
        }
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

    static std::optional<int> column_optional_int(sqlite3_stmt* stmt, int index) {
        if (sqlite3_column_type(stmt, index) == SQLITE_NULL) return std::nullopt;
        return sqlite3_column_int(stmt, index);
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
