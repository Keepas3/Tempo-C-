#pragma once
#include <string>
#include <vector>
#include <cstdlib>
#include <chrono>
#include <cctype>

// chess.com/lichess usernames are alphanumeric plus '_'/'-' only. This is the
// injection guard: the username is the only user-controlled text that ends
// up inside a shell command string built below, so rejecting anything else
// up front means that command line can never contain shell metacharacters.
inline bool is_valid_username(const std::string& name) {
    if (name.empty() || name.size() > 40) return false;
    for (char c : name) {
        if (!isalnum(static_cast<unsigned char>(c)) && c != '_' && c != '-') return false;
    }
    return true;
}

// Runs curl to fetch `url` into `out_path`, optionally with extra headers.
// Returns true only on a clean HTTP success (curl's -f makes it fail, rather
// than writing an error page, on 4xx/5xx responses).
inline bool fetch_url_to_file(const std::string& url, const std::string& out_path,
                               const std::vector<std::string>& extra_headers = {}) {
    std::string cmd = "curl -s -S -f -L --max-time 30 -o \"" + out_path + "\"";
    for (const std::string& header : extra_headers) {
        cmd += " -H \"" + header + "\"";
    }
    cmd += " \"" + url + "\"";
    return std::system(cmd.c_str()) == 0;
}

inline bool fetch_chesscom_month(const std::string& username, int year, int month, const std::string& out_path) {
    if (!is_valid_username(username)) return false;
    char month_buf[3];
    snprintf(month_buf, sizeof(month_buf), "%02d", month);
    std::string url = "https://api.chess.com/pub/player/" + username +
                       "/games/" + std::to_string(year) + "/" + month_buf + "/pgn";
    return fetch_url_to_file(url, out_path);
}

// Shared core for both lichess entry points below -- an explicit
// [since_ms, until_ms) window, downloaded the same way either was reached.
inline bool fetch_lichess_url(const std::string& username, long long since_ms, long long until_ms,
                               const std::string& out_path) {
    if (!is_valid_username(username)) return false;

    std::string url = "https://lichess.org/api/games/user/" + username +
                       "?since=" + std::to_string(since_ms) +
                       "&until=" + std::to_string(until_ms) +
                       "&opening=true&clocks=true";
    return fetch_url_to_file(url, out_path, {"Accept: application/x-chess-pgn"});
}

inline bool fetch_lichess_range(const std::string& username, int days, const std::string& out_path) {
    auto now = std::chrono::system_clock::now();
    auto until_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
    auto since_ms = until_ms - static_cast<long long>(days) * 24LL * 60 * 60 * 1000;
    return fetch_lichess_url(username, since_ms, until_ms, out_path);
}

// Fetches from an explicit `since_ms` boundary (e.g. the timestamp of the
// last successful fetch) through now -- used for incremental /fetch, as
// opposed to fetch_lichess_range's rolling day-count.
inline bool fetch_lichess_since(const std::string& username, long long since_ms, const std::string& out_path) {
    auto now = std::chrono::system_clock::now();
    auto until_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
    return fetch_lichess_url(username, since_ms, until_ms, out_path);
}
