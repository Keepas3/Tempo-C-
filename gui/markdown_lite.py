"""Converts Claude's plain-text answers (light markdown: **bold**, `code`,
"- " bullet lines) into the same constrained HTML subset command_panel.py
already renders everywhere else (QTextBrowser's rich-text subset) -- not a
full markdown library, since these answers are short chat prose, not
documents.
"""
from __future__ import annotations

import html
import re

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+?)`")


def _inline(text: str) -> str:
    text = html.escape(text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _CODE_RE.sub(r"<code>\1</code>", text)
    return text


def to_html(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    in_list = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                out.append("<ul style='margin:4px 0 4px 18px; padding:0;'>")
                in_list = True
            out.append(f"<li>{_inline(stripped[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if not stripped:
            out.append("<br>")
        else:
            out.append(f"<div>{_inline(line)}</div>")
    if in_list:
        out.append("</ul>")
    return "".join(out)
