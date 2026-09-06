# Tempo C++: Personal Chess Game Archive

Tempo C++ is a command-line tool for archiving and querying your own chess
games. Import PGN files exported from chess.com or lichess, then query win
rate by opening, by color, and by time control — and drill down into specific
openings (e.g. "how am I doing against the Evans Gambit?").

## Setup

Edit [game.h](game.h) and set `YOUR_USERNAME` to your chess.com/lichess
username, so imported games can be tagged with which color you played.

## Build

```
g++ -std=c++17 -O2 -o tempo.exe main.cpp -x c sqlite3.c
```

The `-x c` flag is required — g++ otherwise compiles the vendored
`sqlite3.c` amalgamation as C++, which breaks it.

**Windows note**: run the built `tempo.exe` from PowerShell or `cmd.exe`,
not git-bash — git-bash's own `mingw64/bin/libstdc++-6.dll` can shadow the
runtime the binary was actually linked against and cause a crash.

## Usage

```
Tempo> import my_games.pgn
Tempo> list
Tempo> show 3
Tempo> opening evans
Tempo> stats
```

| Command | Description |
|---|---|
| `import <file.pgn>` | Import games from a PGN file (duplicates are skipped) |
| `list [n]` | List the n most recent games (default 20) |
| `show <id>` | Show a game's info and movetext |
| `review <id>` | Replay a game with a rough eval-per-move and possible-blunder flags |
| `opening <query>` | Win/loss record and matching games for an opening (name substring or ECO code) |
| `stats` | Overall/by-color win rate, opening breakdowns, and time-pressure by time control |
| `help` | List commands |
| `q` / `quit` | Exit |

Games are stored in `tempo_archive.db` (SQLite) in the working directory.

## Notes

- `review`'s eval numbers come from a simple material + piece-square-table
  evaluator ([evaluate.h](evaluate.h)) — not a real engine, so they won't
  match Stockfish/chess.com analysis. Treat them as a rough "material moved"
  signal, not tactical truth.
- Opening names come from the PGN's `Opening` tag when present, falling back
  to an embedded ECO-code lookup table ([eco.h](eco.h)) when it's missing.
