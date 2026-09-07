# games

Drop your exported PGN files at the **top level** of this folder (from
chess.com, lichess, etc.) and import them with:

```
Tempo> import games
```

This imports every `.pgn` file directly inside this folder in one go. You
can also import a single file directly:

```
Tempo> import games/my_games.pgn
```

## Monthly master archive

Every new game accepted into the database is also appended to a monthly
master file, organized as:

```
games/<year>/<MM>-<year>_games.pgn
```

e.g. `games/2026/09-2026_games.pgn` holds every game you played in
September 2026, regardless of which raw export it came from. These files
are generated and maintained by the tool — don't hand-edit or move them,
and don't drop new raw exports inside the year subfolders (only at the top
level of `games/`, as above).

Your original dropped file is never modified or deleted by `import` — the
master files are an additional, organized copy.

Later, this folder (and its monthly structure) will also serve as the drop
point for games fetched automatically from chess.com/lichess.
