"""HTML for the /scout report: what an opponent plays, how it scores, where
they are strong or weak, and how you stack up against them. Pure formatting
-- all numbers come from the `scout` / `stats` / `head_to_head` CLI results
(see tempo_cli.TempoCli.scout), so this is testable without any widgets.
"""
from __future__ import annotations

import html
from urllib.parse import quote

from colors import DRAW_COLOR, ERROR_COLOR, HEADER_COLOR, LOSS_COLOR, MUTED_COLOR, WIN_COLOR
from profiles import ProfileRecord

# An opening needs this many games (or this share of the tagged games,
# whichever is larger) before it counts toward "strongest/weakest" -- tiny
# samples would otherwise dominate with 100% / 0% scores.
MIN_OPENING_GAMES = 8
MIN_OPENING_SHARE = 0.005


def _esc(s) -> str:
    return html.escape(str(s))


def score_pct(wins: int, draws: int, games: int) -> float:
    """Chess score: a win counts 1, a draw 0.5."""
    return 100.0 * (wins + 0.5 * draws) / games if games else 0.0


def _score_span(pct: float) -> str:
    color = WIN_COLOR if pct >= 55 else (LOSS_COLOR if pct < 45 else DRAW_COLOR)
    return f'<span style="color:{color}"><b>{pct:.0f}%</b></span>'


def _muted(text: str) -> str:
    return f'<span style="color:{MUTED_COLOR}">{text}</span>'


def _section(title: str) -> str:
    return f'<div style="margin-top:10px;"><b style="color:{HEADER_COLOR}; font-size:11pt;">{_esc(title)}</b></div>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(
        f'<th align="left" style="color:{MUTED_COLOR}; border-bottom:1px solid #555; padding:2px 12px 3px 0;">{_esc(h)}</th>'
        for h in headers)
    body = "".join(
        "<tr>" + "".join(f'<td style="padding:2px 12px 2px 0;">{cell}</td>' for cell in row) + "</tr>" for row in rows)
    return f'<table cellspacing="0" style="margin-top:3px;"><tr>{head}</tr>{body}</table>'


def move_label(san: str, ply_index: int) -> str:
    """Move numbers only precede White's moves (even ply index)."""
    return (f"{ply_index // 2 + 1}." if ply_index % 2 == 0 else "") + san


def reply_label(san: str, ply_index: int) -> str:
    """Like move_label, but Black's moves get the "1..." marker too, since in a
    table of alternatives there is no preceding White move to read it from."""
    return f"{ply_index // 2 + 1}{'.' if ply_index % 2 == 0 else '...'}{san}"


def _form_html(recent: str) -> str:
    if not recent:
        return _muted("no recent games")
    colors = {"W": WIN_COLOR, "D": DRAW_COLOR, "L": LOSS_COLOR}
    letters = "".join(f'<span style="color:{colors[c]}"><b>{c}</b></span>' for c in recent if c in colors)
    tally = "(%dW %dD %dL)" % (recent.count("W"), recent.count("D"), recent.count("L"))
    return letters + " " + _muted(tally)


def _line_html(profile_id: str, color: str, prefix: list[str], steps: list[dict]) -> str:
    """A most-common line as numbered moves, each with how often it was
    played at that point, plus a link that loads it on the board."""
    if not steps:
        return ""
    full = prefix + [s["san"] for s in steps]
    crumbs = []
    for i, san in enumerate(full):
        label = _esc(move_label(san, i))
        j = i - len(prefix)
        if j >= 0:
            step = steps[j]
            share = 100.0 * step["count"] / step["total"] if step["total"] else 0.0
            crumbs.append(f"<b>{label}</b> " + _muted("%.0f%%" % share))
        else:
            crumbs.append(f"<b>{label}</b>")
    href = f"scoutline:{profile_id}:{color}:{quote(' '.join(full))}"
    return (f'<div style="margin:2px 0;">{" &nbsp;".join(crumbs)}</div>'
            f'<div><a href="{href}" style="color:{HEADER_COLOR}; text-decoration:none;">'
            f'Load this line on the board &rsaquo;</a></div>')


def _replies_table(replies: list[dict], prefix: list[str], heading: str) -> str:
    total = sum(r["count"] for r in replies)
    if not total:
        return ""
    rows = []
    for r in replies[:4]:
        n = r["count"]
        rows.append([
            f'<b>{_esc(reply_label(r["san"], len(prefix)))}</b>',
            "%.0f%% " % (100.0 * n / total) + _muted("(%d)" % n),
            _score_span(score_pct(r["wins"], r["draws"], n)),
        ])
    return f'<div style="margin-top:4px;">{_muted(_esc(heading))}</div>' + _table(["Move", "Played", "Score"], rows)


def _openings_table(openings: list[dict], limit: int = 6) -> str:
    rows = [[_esc(o["name"]), str(o["games"]), _score_span(score_pct(o["wins"], o["draws"], o["games"]))]
            for o in openings[:limit]]
    return _table(["Opening", "Games", "Score"], rows) if rows else ""


def _qualifying(openings: list[dict], tagged: int) -> list[dict]:
    threshold = max(MIN_OPENING_GAMES, int(tagged * MIN_OPENING_SHARE))
    return [o for o in openings if o["games"] >= threshold]


def _best_worst(items: list[dict], noun: str) -> str:
    """Up to 2 best and 2 worst entries (by score) as short bullet lines."""
    if len(items) < 2:
        return ""
    ranked = sorted(items, key=lambda o: score_pct(o["wins"], o["draws"], o["games"]), reverse=True)
    best = ranked[:2]
    worst = [o for o in reversed(ranked) if o not in best][:2]
    lines = []
    for label, group, color in (("Strongest", best, WIN_COLOR), ("Weakest", worst, LOSS_COLOR)):
        for o in group:
            pct = score_pct(o["wins"], o["draws"], o["games"])
            games = "(%d games)" % o["games"]
            lines.append(f'<div><span style="color:{color}"><b>{label}</b></span> {_esc(o["name"])} '
                         f'{_score_span(pct)} {_muted(games)}</div>')
    return f'<div style="margin-top:3px;">{_muted(noun)}</div>' + "".join(lines)


def _my_records(my_stats: dict | None, key: str) -> dict[str, tuple[int, int]]:
    """{opening name: (your games, your wins)} from /stats' per-color openings."""
    if not my_stats:
        return {}
    return {o["opening"]: (o["games"], o["wins"]) for o in my_stats.get(key, [])}


def _your_edge_rows(openings: list[dict], mine: dict[str, tuple[int, int]]) -> list[list[str]]:
    rows = []
    for o in openings[:4]:
        games, wins = mine.get(o["name"], (0, 0))
        yours = (_score_span(100.0 * wins / games) + " " + _muted("(%d games)" % games) if games
                 else _muted("no games yet"))
        rows.append([_esc(o["name"]), _score_span(score_pct(o["wins"], o["draws"], o["games"])), yours])
    return rows


def format_scout_report(record: ProfileRecord, scout: dict, my_stats: dict | None, h2h: dict | None) -> str:
    user = scout["subject"]
    pid = record.id
    tagged = scout["tagged_games"]

    header = (f'<div><b style="color:{HEADER_COLOR}; font-size:12pt;">Scouting report: {_esc(record.display_name)}</b> '
              + _muted("(%s)" % _esc(user)) + "</div>")

    if tagged == 0:
        hint = ""
        if scout.get("suggested_username"):
            hint = (f' The most common player in this archive is <b>{_esc(scout["suggested_username"])}</b> &mdash; '
                    f'right-click the profile tab &rarr; <i>Set account username...</i> to fix it.')
        return (header + f'<div style="color:{ERROR_COLOR}; margin-top:4px;">None of this archive\'s '
                f'{scout["total_games"]} games are tagged as {_esc(user)}\'s, so there is nothing to analyze.{hint}</div>')

    span = ""
    if scout["earliest_date"] and scout["latest_date"]:
        span = f' &middot; {scout["earliest_date"][:4]}&ndash;{scout["latest_date"][:4]}'
    ratings = " &middot; ".join(
        f'{_esc(tc["name"])} <b>{tc["rating"]}</b>' for tc in scout["by_time_control"] if tc["rating"] > 0)
    parts = [header,
             "<div>" + _muted("{:,} games".format(tagged) + span) + "</div>"]
    if ratings:
        parts.append(f"<div>{ratings}</div>")
    parts.append(f'<div>Recent form: {_form_html(scout["recent"])}</div>')

    # --- what they play ------------------------------------------------
    parts.append(_section("What they play as White"))
    parts.append(_replies_table(scout["first_moves_white"], [], "First move"))
    parts.append(_line_html(pid, "white", [], scout["line_white"]))

    parts.append(_section("What they play as Black"))
    parts.append(_replies_table(scout["replies_vs_e4"], ["e4"], "Against 1.e4"))
    parts.append(_line_html(pid, "black", ["e4"], scout["line_black_vs_e4"]))
    parts.append(_replies_table(scout["replies_vs_d4"], ["d4"], "Against 1.d4"))
    parts.append(_line_html(pid, "black", ["d4"], scout["line_black_vs_d4"]))

    # --- openings ------------------------------------------------------
    parts.append(_section("Openings as White"))
    parts.append(_openings_table(scout["openings_white"]))
    parts.append(_section("Openings as Black"))
    parts.append(_openings_table(scout["openings_black"]))

    # --- strengths and weaknesses --------------------------------------
    parts.append(_section("Strengths and weaknesses"))
    sw = (_best_worst(_qualifying(scout["openings_white"], tagged), "As White")
          + _best_worst(_qualifying(scout["openings_black"], tagged), "As Black")
          + _best_worst([tc for tc in scout["by_time_control"] if tc["games"] >= 20], "By time control"))
    parts.append(sw or _muted("Not enough games per opening to say."))

    # --- your edge -----------------------------------------------------
    parts.append(_section("How you stack up"))
    if h2h is not None:
        if h2h["games"]:
            recent = ", ".join(
                f'<a href="game:{g["id"]}" style="color:inherit;">#{g["id"]} {_esc(g["result"])}</a>' for g in h2h["recent"][:3])
            in_games = "in %d games" % h2h["games"]
            parts.append(f'<div>Head to head: <b style="color:{WIN_COLOR}">{h2h["wins"]}W</b> '
                         f'<b>{h2h["draws"]}D</b> <b style="color:{LOSS_COLOR}">{h2h["losses"]}L</b> '
                         f'{_muted(in_games)} {_muted(recent)}</div>')
        else:
            parts.append(f'<div>{_muted(f"No games between you and {_esc(user)} in your archive yet.")}</div>')
    white_rows = _your_edge_rows(scout["openings_white"], _my_records(my_stats, "top_openings_black"))
    black_rows = _your_edge_rows(scout["openings_black"], _my_records(my_stats, "top_openings_white"))
    if white_rows:
        parts.append(f'<div style="margin-top:4px;">{_muted("When they have White (you have Black)")}</div>')
        parts.append(_table(["Their opening", "Their score", "Your win rate"], white_rows))
    if black_rows:
        parts.append(f'<div style="margin-top:4px;">{_muted("When they have Black (you have White)")}</div>')
        parts.append(_table(["Their opening", "Their score", "Your win rate"], black_rows))
    return "".join(parts)
