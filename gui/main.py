"""Tempo GUI entry point: a QTabWidget of profiles, each a full board |
command panel | game browser | bookmarks view (see profile_view.py), with a
"+ new profile" button in the top-left corner of the tab strip for pulling
another player's archive in alongside your own (see profile_dialog.py).
Profile list persists across restarts via gui/data/profiles.json
(profiles.py).
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QLineEdit, QMainWindow, QPushButton, QTabWidget

from profile_dialog import start_new_profile_flow
from profile_view import ProfileView
from profiles import ProfileRecord, ProfilesConfig, bootstrap_if_missing, save_profiles


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tempo - Game Archive")

        self.config: ProfilesConfig = bootstrap_if_missing()

        self.tabs = QTabWidget()
        new_profile_btn = QPushButton("+ New profile")
        new_profile_btn.clicked.connect(self._on_new_profile)
        self.tabs.setCornerWidget(new_profile_btn, Qt.Corner.TopLeftCorner)
        self.setCentralWidget(self.tabs)

        # Populate and restore the active tab BEFORE wiring currentChanged --
        # addTab() fires currentChanged itself as each tab becomes current,
        # which would otherwise overwrite config.last_active mid-populate.
        for record in self.config.profiles:
            if not record.resolved_db_path().exists():
                # Profile's db file was deleted/moved externally -- skip it
                # rather than crashing the whole app on launch.
                print(f"[Warning] skipping profile '{record.display_name}': "
                      f"db file not found at {record.resolved_db_path()}")
                continue
            self._add_profile_tab(record)
        self._restore_last_active()
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.resize(1700, 800)

        # Left/Right navigate the active tab's board like its Prev/Next
        # buttons, from anywhere in the window -- except while actually
        # typing/editing text (the command input), where the arrow keys
        # should just move the text cursor as normal. An application-level
        # filter is used (rather than overriding keyPressEvent) so this
        # works regardless of which panel currently has focus -- the
        # browser tree and output pane would otherwise consume Left/Right
        # themselves for their own navigation before a window-level handler
        # ever saw the event.
        QApplication.instance().installEventFilter(self)

    def _add_profile_tab(self, record: ProfileRecord) -> ProfileView:
        view = ProfileView(record)
        self.tabs.addTab(view, record.display_name)
        return view

    def _restore_last_active(self) -> None:
        if not self.config.last_active:
            return
        for i in range(self.tabs.count()):
            view = self.tabs.widget(i)
            if isinstance(view, ProfileView) and view.profile.id == self.config.last_active:
                self.tabs.setCurrentIndex(i)
                return

    def _on_tab_changed(self, index: int) -> None:
        view = self.tabs.widget(index)
        if isinstance(view, ProfileView):
            self.config.last_active = view.profile.id
            save_profiles(self.config)

    def _on_new_profile(self) -> None:
        def on_complete(record: ProfileRecord | None) -> None:
            if record is None:
                return
            view = self._add_profile_tab(record)
            self.tabs.setCurrentWidget(view)

        start_new_profile_flow(self, self.config, on_complete)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.KeyPress and not isinstance(QApplication.focusWidget(), QLineEdit):
            active = self.tabs.currentWidget()
            if isinstance(active, ProfileView):
                if event.key() == Qt.Key.Key_Left:
                    active.prev_ply()
                    return True
                if event.key() == Qt.Key.Key_Right:
                    active.next_ply()
                    return True
        return super().eventFilter(obj, event)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
