# Tempo: Personal Chess Game Archive

A desktop app that pulls your games from chess.com and lichess into a local
archive, then lets you browse, query, replay, annotate and analyze them,
including with an AI assistant that answers questions about your own play.

- **Backend (C++17)**: `tempo.exe` owns the SQLite archive, PGN parsing, a
  move generator, fetching, and every query. It also works as a standalone CLI.
- **Frontend (Python / PySide6)**: board, archive browser and chat panel
  built on the backend's JSON interface, with live Stockfish analysis and a
  Claude-powered assistant.

## Features

**Archive**
- Fetch from chess.com and lichess with a live progress bar and Cancel;
  incremental per account, with duplicate-safe re-imports.
- Games are ordered by the time they were played, and each carries the
  rating you had at that moment.
- Filter by type, result and color, or search opponent, opening and notes.
  Click any column to sort, star favorites, or narrow the list to games that
  reached the position on the board.
- Multiple profiles as tabs, each with its own database. Right-click a tab
  to rename, pin, group or delete it.

**Analysis**
- Move-by-move review with blunder, mistake and inaccuracy detection, plus
  per-move and per-game accuracy using lichess's published formulas.
- Live Stockfish evaluation with an advantage bar, best-move arrows and the
  top three lines. Results are cached per game.
- Opening explorer for your own repertoire, with a win/draw/loss record for
  each reply you have played.
- Stats by game type and date range: win rate, openings, time management and
  rating progression.

**Notes and organization**
- Attach a note to a whole game or to a single move; notes are searchable and
  appear in reviews. Bookmark any position.

**AI assistant (optional)**
- Free-text questions about your archive, answered with tool use against
  your local data instead of guesses, while keeping token use low.

## Quick start (Windows)

Requires MSYS2 `g++` (C++17), Python 3.10+, and `curl` (built into Windows 10/11).

1. Set `YOUR_USERNAME` in [game.h](game.h) to your chess.com/lichess handle.
2. `pip install -r gui/requirements.txt`
3. Double-click **`run.bat`**. It builds `tempo.exe` when needed and starts
   the GUI. Manual build: `g++ -std=c++17 -O2 -o tempo.exe main.cpp -x c sqlite3.c`

Then type `/fetch chesscom <username>` (or `lichess`) in the chat panel.

## Chat commands

| Command | Description |
|---|---|
| `/fetch chesscom\|lichess <user> [full]` | Import games; `full` re-downloads all history |
| `/stats [type...]` | Win rate, openings and time stats |
| `/rating [type...]` | Rating and rating history |
| `/opening <query>` | Record for an opening (name or ECO code) |
| `/moves <sequence>` | What was played after a move sequence |
| `/clear`, `/help` | Clear the chat, list commands |

Text without a leading `/` goes to the AI assistant. To enable it, set the
`ANTHROPIC_API_KEY` environment variable before launching; the key is read
from the environment only and never written to disk.

## Engine (optional)

Use **Download Stockfish** under the board to install the official release.
It runs only on request, and the download is checked against a pinned
SHA-256. Stockfish is GPLv3; Tempo talks to it over a UCI subprocess pipe and
never links it. Without the engine, reviews use a basic built-in material
evaluator ([evaluate.h](evaluate.h)), which is only a rough signal.

## CLI

`tempo.exe` runs standalone with `import`, `fetch`, `list`, `show`, `review`,
`opening`, `moves`, `stats`, `rating` and `help`. The GUI calls the same
binary as `tempo.exe --db <path> --user <name> --json <command>`.

## Data and privacy

Everything stays local and is gitignored; this repo is just the source.

- `tempo_archive.db`: SQLite archive for the default profile; other profiles
  live in `profiles/<name>/`.
- `games/`: human-readable monthly PGN copies ([details](games/README.md)).
- Notes, favorites, bookmarks and cached analysis live in the same database
  but in GUI-owned tables the CLI never touches.
- Hardening: usernames are validated before any shell call, SQL is
  parameterized, profile paths are confined to the project folder, and the
  Stockfish archive is hash-verified and checked for path traversal.

## Tech

C++17, SQLite, PySide6 (Qt), python-chess, Stockfish (UCI), Anthropic API.
