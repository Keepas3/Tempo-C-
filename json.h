#pragma once
#include <string>
#include <vector>
#include <sstream>
#include <cstdio>
#include "db.h"
#include "game.h"

// Minimal hand-rolled JSON emitter — Tempo only ever writes JSON (for the
// Python GUI to consume), never parses it, so a full JSON library isn't
// needed. Every function here returns a JSON fragment; callers compose them.

inline std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out;
}

inline std::string json_str(const std::string& s) {
    return "\"" + json_escape(s) + "\"";
}

inline std::string json_error(const std::string& message) {
    return "{\"error\": " + json_str(message) + "}";
}

inline std::string to_json(const GameSummary& g) {
    std::ostringstream o;
    o << "{\"id\": " << g.id
      << ", \"date\": " << json_str(g.date)
      << ", \"opponent\": " << json_str(g.opponent)
      << ", \"your_color\": " << json_str(g.your_color)
      << ", \"result\": " << json_str(g.result_display)
      << ", \"opening\": " << json_str(g.opening)
      << ", \"site\": " << json_str(g.site)
      << "}";
    return o.str();
}

inline std::string to_json(const std::vector<GameSummary>& games) {
    std::ostringstream o;
    o << "{\"games\": [";
    for (size_t i = 0; i < games.size(); ++i) {
        if (i) o << ", ";
        o << to_json(games[i]);
    }
    o << "]}";
    return o.str();
}

inline std::string to_json(const MoveRecord& m) {
    std::ostringstream o;
    o << "{\"san\": " << json_str(m.san) << ", \"clock_seconds\": " << m.clock_seconds << "}";
    return o.str();
}

inline std::string to_json(const Game& g) {
    std::ostringstream o;
    o << "{\"event\": " << json_str(g.event)
      << ", \"site\": " << json_str(g.site)
      << ", \"date\": " << json_str(g.date)
      << ", \"white\": " << json_str(g.white)
      << ", \"black\": " << json_str(g.black)
      << ", \"result\": " << json_str(g.result)
      << ", \"eco\": " << json_str(g.eco)
      << ", \"opening\": " << json_str(g.opening)
      << ", \"time_control\": " << json_str(g.time_control)
      << ", \"your_color\": " << json_str(g.your_color)
      << ", \"moves\": [";
    for (size_t i = 0; i < g.moves.size(); ++i) {
        if (i) o << ", ";
        o << to_json(g.moves[i]);
    }
    o << "]}";
    return o.str();
}

inline std::string to_json(const OpeningStat& s) {
    std::ostringstream o;
    o << "{\"opening\": " << json_str(s.opening) << ", \"games\": " << s.games << ", \"wins\": " << s.wins << "}";
    return o.str();
}

inline std::string to_json(const std::vector<OpeningStat>& stats) {
    std::ostringstream o;
    o << "[";
    for (size_t i = 0; i < stats.size(); ++i) {
        if (i) o << ", ";
        o << to_json(stats[i]);
    }
    o << "]";
    return o.str();
}

inline std::string to_json(const TimeControlStat& t) {
    std::ostringstream o;
    o << "{\"category\": " << json_str(t.category)
      << ", \"games\": " << t.games
      << ", \"avg_seconds_per_move\": " << t.avg_seconds_per_move
      << ", \"time_trouble_moves\": " << t.time_trouble_moves
      << "}";
    return o.str();
}

inline std::string to_json(const std::vector<TimeControlStat>& stats) {
    std::ostringstream o;
    o << "[";
    for (size_t i = 0; i < stats.size(); ++i) {
        if (i) o << ", ";
        o << to_json(stats[i]);
    }
    o << "]";
    return o.str();
}

inline std::string to_json(const Stats& s) {
    std::ostringstream o;
    o << "{\"wins\": " << s.wins << ", \"losses\": " << s.losses << ", \"draws\": " << s.draws
      << ", \"wins_white\": " << s.wins_white << ", \"losses_white\": " << s.losses_white << ", \"draws_white\": " << s.draws_white
      << ", \"wins_black\": " << s.wins_black << ", \"losses_black\": " << s.losses_black << ", \"draws_black\": " << s.draws_black
      << ", \"avg_seconds_per_move\": " << s.avg_seconds_per_move
      << ", \"time_trouble_moves\": " << s.time_trouble_moves
      << ", \"top_openings_white\": " << to_json(s.top_openings_white)
      << ", \"top_openings_black\": " << to_json(s.top_openings_black)
      << ", \"by_time_control\": " << to_json(s.by_time_control)
      << ", \"earliest_date\": " << json_str(s.earliest_date)
      << ", \"latest_date\": " << json_str(s.latest_date)
      << "}";
    return o.str();
}

inline std::string to_json(const NextMoveStat& n) {
    std::ostringstream o;
    o << "{\"san\": " << json_str(n.san) << ", \"count\": " << n.count
      << ", \"wins\": " << n.wins << ", \"losses\": " << n.losses << ", \"draws\": " << n.draws << "}";
    return o.str();
}

inline std::string to_json(const std::vector<NextMoveStat>& stats) {
    std::ostringstream o;
    o << "{\"replies\": [";
    for (size_t i = 0; i < stats.size(); ++i) {
        if (i) o << ", ";
        o << to_json(stats[i]);
    }
    o << "]}";
    return o.str();
}
