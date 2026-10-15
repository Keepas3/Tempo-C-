"""Modal for creating a new profile: display name / platform / username,
then fetches that account's games in the background (see fetch_worker.py)
so the new tab isn't empty. Orchestration (create dir, run dialog, run
fetch, persist, clean up on failure) lives in `start_new_profile_flow`,
called from the outer MainWindow's "+ new profile" button. The fetch itself
is event-driven (signals, not a blocking wait()) so the GUI thread keeps
pumping events -- the progress dialog stays responsive -- while it runs.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QVBoxLayout,
    QWidget,
)

from fetch_worker import FetchWorker
from profiles import ProfileRecord, ProfilesConfig, add_profile, save_profiles
from tempo_cli import TempoCli


class ProfileDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New profile")

        self.display_name_edit = QLineEdit()
        self.display_name_edit.setPlaceholderText("Defaults to username")
        self.platform_combo = QComboBox()
        self.platform_combo.addItem("chess.com", "chesscom")
        self.platform_combo.addItem("lichess", "lichess")
        self.username_edit = QLineEdit()

        form = QFormLayout()
        form.addRow("Display name:", self.display_name_edit)
        form.addRow("Platform:", self.platform_combo)
        form.addRow("Username:", self.username_edit)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        ok_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setEnabled(False)
        self.username_edit.textChanged.connect(lambda text: ok_button.setEnabled(bool(text.strip())))

        outer = QVBoxLayout(self)
        outer.addLayout(form)
        outer.addWidget(self.buttons)

    def values(self) -> tuple[str, str, str]:
        """(display_name, platform, username), display_name defaulted to
        username if left blank."""
        platform = self.platform_combo.currentData()
        username = self.username_edit.text().strip()
        display_name = self.display_name_edit.text().strip() or username
        return display_name, platform, username


def _path_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


class DeleteProfileDialog(QDialog):
    """Irreversible-action gate: lists exactly what will be erased and keeps
    Delete disabled until the profile's name has been typed out."""

    def __init__(self, parent: QWidget, record: ProfileRecord, paths: list[Path]) -> None:
        super().__init__(parent)
        self.setWindowTitle("Delete profile")
        self._expected = record.display_name

        if paths:
            lines = [f"\u2022 {p}  ({_human_size(_path_size(p))})" for p in paths]
            what = "This permanently erases (games, notes, favorites, bookmarks and analysis):\n" + "\n".join(lines)
        else:
            what = "No files were found on disk for this profile; only the tab will be removed."
        info = QLabel(f'Delete the profile "{record.display_name}"?\n\n{what}\n\nThis cannot be undone.')
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(f'Type "{record.display_name}" to confirm')
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.delete_btn = self.buttons.addButton("Delete", QDialogButtonBox.ButtonRole.DestructiveRole)
        self.delete_btn.setEnabled(False)
        self.name_edit.textChanged.connect(lambda text: self.delete_btn.setEnabled(text == self._expected))
        self.delete_btn.clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addWidget(self.name_edit)
        layout.addWidget(self.buttons)
        self.setMinimumWidth(480)


def confirm_profile_delete(parent: QWidget, record: ProfileRecord, paths: list[Path]) -> bool:
    return DeleteProfileDialog(parent, record, paths).exec() == QDialog.DialogCode.Accepted


def start_new_profile_flow(parent: QWidget, config: ProfilesConfig, on_complete: Callable[[ProfileRecord | None], None]) -> None:
    """Shows the creation dialog; if accepted, creates the profile's
    directory and fetches its games in the background. Calls `on_complete`
    exactly once: with the new ProfileRecord on success (already persisted
    to profiles.json), or None if the user cancelled the dialog or the
    fetch failed (in which case the partially-created directory/profile
    entry is cleaned up first)."""
    dialog = ProfileDialog(parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        on_complete(None)
        return

    display_name, platform, username = dialog.values()
    record = add_profile(config, display_name, platform, username)
    db_path = record.resolved_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    cli = TempoCli(db_path, record.username)
    if platform == "chesscom":
        fetch_fn = lambda: cli.fetch_chesscom(username)
    else:
        fetch_fn = lambda: cli.fetch_lichess(username)

    platform_label = "chess.com" if platform == "chesscom" else "lichess"
    progress = QProgressDialog(f"Fetching {username}'s games from {platform_label}...", None, 0, 0, parent)
    progress.setWindowTitle("New profile")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.show()

    worker = FetchWorker(fetch_fn, parent)

    def cleanup_failed(message: str) -> None:
        progress.close()
        config.profiles.remove(record)
        shutil.rmtree(db_path.parent, ignore_errors=True)
        QMessageBox.warning(parent, "Fetch failed", f"Could not fetch games for {username}: {message}")
        on_complete(None)

    def finish_ok(_data: dict) -> None:
        progress.close()
        save_profiles(config)
        on_complete(record)

    worker.succeeded.connect(finish_ok)
    worker.failed.connect(cleanup_failed)
    # Keep a reference on the dialog's parent so the worker isn't garbage
    # collected mid-run once this function returns.
    parent._new_profile_worker = worker  # type: ignore[attr-defined]
    worker.start()
