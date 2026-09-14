# Tempo: Personal Chess Game Archive

Tempo is a personal chess archive: import your games from chess.com or
lichess, browse and query them, replay them with engine analysis, and ask
an AI assistant questions about your own play. It's two parts —

- **`tempo.exe`** (C++): owns the SQLite archive, PGN import/fetch, and all
  the query logic (openings, stats, move-sequence lookups). Usable on its
  own as an interactive CLI.
- **The GUI** (`gui/`, Python/PySide6): a full board + archive browser +
  chat interface built on top of the CLI, with live engine analysis
  (Stockfish) and a Claude-powered assistant layered in. This is the
  primary way to use the app day to day.

## Setup

1. Edit [game.h](game.h) and set `YOUR_USERNAME` to your chess.com/lichess
   username, so imported games are tagged with which color you played.
2. Build the C++ backend (see below) — the GUI shells out to `tempo.exe`,
   so it has to exist before the GUI can do anything.
3. Install the GUI's Python dependencies: `pip install -r gui/requirements.txt`.

## Build

```
g++ -std=c++17 -O2 -o tempo.exe main.cpp -x c sqlite3.c
```

The `-x c` flag is required — g++ otherwise compiles the vendored
`sqlite3.c` amalgamation as C++, which breaks it. Run this from the repo
root; the GUI expects `tempo.exe` there.

**Windows note**: run the built `tempo.exe` from PowerShell or `cmd.exe`,
not git-bash — git-bash's own `mingw64/bin/libstdc++-6.dll` can shadow the
runtime the binary was actually linked against and cause a crash.

## Running the GUI

```
python gui/main.py
```

Each **tab** is one player's archive (your own is created automatically on
first launch, from `YOUR_USERNAME`/`game.h`); use **"+ New profile"** in the
top-left corner to pull in another account (yours on a different platform,
a friend's, etc.) — it creates a separate db file and does an initial fetch
in the background. The active tab and full profile list persist across
restarts.

### The board

- **Click a piece** to select it — its square glows and every square it can
  legally move to is highlighted, then click again to play the move (auto-
  queens on promotion; no under-promotion picker yet).
- **`< Prev` / `Next >`** step through the loaded game; **`Return to
  mainline`** snaps back from a branch (see below) to wherever you actually
  branched off. The same **Left / Right arrow keys** work from anywhere in
  the window (except while typing), and **`R`** jumps straight back to the
  start of the game.
- Clicking through the game's real move list keeps you "on the mainline";
  clicking a piece to play a move that isn't what was actually played
  **branches into a sideline** for freeform analysis — Prev/Next disable
  themselves off the mainline (undo moves one at a time with Prev instead)
  until `Return to mainline` brings them back.
- **`Flip board`** toggles perspective; loading a game auto-orients to
  whichever color you played. **`Larger board`** toggles between a 480px and
  640px board (everything below it shifts down to make room).
- A **move counter** to the right of the board always shows "Move N /
  \<color\> to move" (or "Start") from the position's actual move count —
  works the same whether you're stepping through an archived game, a
  branched sideline, or just moving pieces around with nothing loaded at
  all.
- **`Bookmark position`** saves the current position (with an optional
  note); **`View bookmarks`** lists and reloads them.

### The archive browser (right side)

A **Year → Month → Game** tree; click a game to load it. Column headers
(Game/Color/Time/Type/Site/Result) are clickable to sort within each month,
and the Result column is color-coded (green/red/gray) to match the chat.

- **`Live query by board`**: filters the tree live to only games that
  actually reached whatever position is currently on the board — move
  through an opening and watch the list narrow to games that played it.
- **`Also show move review on select`**: clicking a game also runs
  `/review` for it immediately (see below), instead of just loading it.
- **`Show opening explorer`** (below the tree): a lichess-explorer-style
  view of your own repertoire — win/loss/draw record for every reply
  you've actually played after the current position, for White or Black,
  click a reply to play it and drill further in.

### The chat panel (left side)

Slash commands mirror the CLI:

| Command | Description |
|---|---|
| `/list [n]` | List the n most recent games (default 20) |
| `/show <id>` | Show a game's info and load it on the board |
| `/review <id>` | Replay a game with eval annotations, loaded on the board |
| `/stats [type...]` | Win rate, opening, and time-management stats (filterable by game type, and by a date range via the dropdown) |
| `/opening <query>` | Win/loss record for an opening (name substring or ECO code) |
| `/moves <sequence>` | What was played after a SAN sequence, e.g. `/moves e4 e5 Nf3` |
| `/explorer <white\|black>` | Browse your own repertoire move-by-move |
| `/fetch chesscom <user> [year month]` | Fetch games from chess.com |
| `/fetch lichess <user> [days]` | Fetch games from lichess |
| `/clear` | Clear the chat history |
| `/help` | Show this list |

Typing anything **without** a leading `/` goes to the Claude AI assistant
instead (see below).

`/review`'s move-by-move table renders in its own panel under the board
(not in the chat) — whichever move corresponds to the board's current
position is highlighted there automatically as you step through, and
clicking a move in the table jumps the board straight to it.

### Engine analysis (optional, Stockfish)

Click **"Download Stockfish"** (under the board) to fetch the official
prebuilt binary — nothing downloads automatically. Once installed:

- **`Live engine eval`**: a real-time evaluation of whatever position is on
  the board, at a depth you pick (`Fast`/`Balanced`/`Deep`), shown as a
  white/black advantage bar next to the board (score drawn right on the
  bar) plus a best-move arrow.
- **`Multiple lines (top 3)`**: shows the engine's 2nd/3rd-best moves too,
  each as a fainter arrow than the best move, with a clickable text summary
  of all three lines.
- **`/review`** (and "also show move review on select") uses Stockfish for
  real blunder/mistake/inaccuracy detection (lichess-style win-probability
  thresholds) instead of the CLI's basic evaluator, with a cancellable
  progress dialog for games that need fresh analysis — instant for ones
  already analyzed. Results are cached per game, so re-viewing one is free.

Stockfish is GPLv3-licensed. This app talks to it only over a UCI
subprocess pipe (never linking it as a library), which is the standard
"mere aggregation" pattern every major chess GUI uses to integrate a GPL
engine without the app itself needing to be GPL-licensed. The engine binary
and its settings live in `gui/engine/` and `gui/data/engine.json` (both
gitignored, not part of this repo).

### Claude AI assistant (optional)

The chat panel answers free-text questions typed without a leading `/` —
about your archive (stats, openings, specific games, playstyle patterns) or
general chess knowledge (opening theory, endgame technique) — using Claude
(Anthropic's API). It uses tool-use so it queries your local archive instead
of guessing, and is deliberately economical: it narrows to a small set of
relevant games via cheap metadata search before ever requesting movetext or
spending Stockfish time on deeper analysis.

Set the `ANTHROPIC_API_KEY` environment variable before launching the GUI
to enable it:

```
set ANTHROPIC_API_KEY=sk-ant-...
python gui/main.py
```

The key is read from the environment only and is never written to disk by
this app. Without it set, the chat panel explains how to enable it instead
of attempting a request. Playstyle/weakness questions reason over games
you've already run `/review`'s full Stockfish analysis on (not the separate
live "Analyze position" eval, which doesn't persist anything) — when
coverage is thin, the assistant can run a small, targeted batch analysis on
specific games itself, capped at a handful per request.

## The CLI (`tempo.exe`)

Runs standalone too, with the same underlying commands the GUI's slash
commands wrap:

```
Tempo> fetch chesscom KeepasC
Tempo> import my_games.pgn
Tempo> list
Tempo> show 3
Tempo> opening evans
Tempo> moves e4 e5 Nf3
Tempo> stats
```

| Command | Description |
|---|---|
| `import <file.pgn\|folder>` | Import games from a PGN file, or every `.pgn` file in a folder (duplicates are skipped) |
| `fetch chesscom <user> [year month]` | Fetch games from chess.com (default: current + previous month) and import them |
| `fetch lichess <user> [days]` | Fetch games from lichess (default: last 90 days) and import them |
| `list [n]` | List the n most recent games (default 20) |
| `show <id>` | Show a game's info and movetext |
| `review <id>` | Replay a game with a rough eval-per-move and possible-blunder flags |
| `opening <query>` | Win/loss record and matching games for an opening (name substring or ECO code) |
| `moves <sequence>` | What was played after a SAN move sequence, e.g. `moves e4 e5 Nf3`, with your W/L/D per reply |
| `stats` | Overall/by-color win rate, opening breakdowns, and time-pressure by time control |
| `help` | List commands |
| `q` / `quit` | Exit |

The GUI also invokes `tempo.exe --db <path> --user <name> --json <command>
[args...]` internally for each profile tab — same commands, JSON output,
one shared binary across all profiles.

### `fetch` requires `curl`

`fetch` shells out to `curl.exe`, which ships built into Windows 10/11 —
nothing extra to install. It hits chess.com's and lichess's public,
unauthenticated game-history APIs, so no login or API key is needed.

### Automating `fetch`

Tempo doesn't run in the background — `fetch` only runs when you invoke it.
For a recurring daily pull, create a small command file (e.g. `daily.txt`)
containing:

```
fetch chesscom KeepasC
q
```

and schedule Windows Task Scheduler to run:

```
cmd /c "cd /d C:\Projects\Tempo-C++ && tempo.exe < daily.txt"
```

on whatever cadence you like — dedup means re-running it never creates
duplicate games.

## Where your data lives

- `tempo_archive.db` (SQLite, repo root) — your default profile's games.
  Additional profiles get their own db under `profiles/<slug>/`.
- `games/<year>/<MM>-<year>_games.pgn` — the same games, also archived as
  human-readable monthly PGN files (see [games/README.md](games/README.md)).
- `gui/data/profiles.json` — the profile registry (display names, db
  paths, last-active tab).
- GUI-only tables (bookmarks, cached engine analysis) live inside each
  profile's own db file, never touched by the CLI.

All of the above, plus `tempo.exe` itself, `gui/engine/`, and
`gui/data/engine.json`, are gitignored — this repo is just the source.

## Notes

- The CLI's `review`/the GUI's fallback (when Stockfish isn't installed)
  eval numbers come from a simple material + piece-square-table evaluator
  ([evaluate.h](evaluate.h)) — not a real engine, so they won't match
  Stockfish/chess.com analysis. Treat them as a rough "material moved"
  signal, not tactical truth.
- Opening names come from the PGN's `Opening` tag when present, falling
  back to an embedded ECO-code lookup table ([eco.h](eco.h)) when it's
  missing.
