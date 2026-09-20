"""Shared color constants -- so the chat panel's text and the archive
browser's tree use the exact same palette for the same meaning (win/loss/
draw, muted/secondary text, section headers, errors) instead of each
picking its own.
"""
HEADER_COLOR = "#7fb3ff"
MUTED_COLOR = "#888888"
WIN_COLOR = "#5cb85c"
LOSS_COLOR = "#e57373"
DRAW_COLOR = "#b0b0b0"
ERROR_COLOR = "#e57373"
# Default readable text color for widgets that don't otherwise set one (e.g.
# QTreeWidgetItem cells) -- Qt's own default resolves to black on this app's
# effectively-dark theme (no explicit palette is set; it just inherits
# Windows' dark mode), so left alone it's unreadable against the dark
# background. Not pure white, to stay easy on the eyes like the rest of the
# muted/colored text elsewhere in the app.
TEXT_COLOR = "#d4d4d4"
