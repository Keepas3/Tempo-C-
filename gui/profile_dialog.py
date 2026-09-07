"""Modal for creating a new profile: display name / platform / username,
then fetches that account's games in the background (see fetch_worker.py)
so the new tab isn't empty. Orchestration (create dir, run dialog, run
fetch, persist, clean up on failure) lives in `start_new_profile_flow`,
called from the outer MainWindow's "+ new profile" button. The fetch itself
is event-driven (signals, not a blocking wait()) so the GUI thread keeps
pumping events -- the progress dialog stays responsive -- while it runs.
"""
from __future__ import annotations

import shutil
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
