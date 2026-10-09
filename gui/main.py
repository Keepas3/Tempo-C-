"""Tempo GUI entry point: a QTabWidget of profiles, each a full board |
command panel | game browser | bookmarks view (see profile_view.py), with a
"+ new profile" button in the top-left corner of the tab strip for pulling
another player's archive in alongside your own (see profile_dialog.py).
Profile list persists across restarts via gui/data/profiles.json
(profiles.py).
"""
from __future__ import annotations

import shutil
import sys

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QApplication, QInputDialog, QLineEdit, QMainWindow, QMenu, QMessageBox, QPushButton, QTabBar, QTabWidget,
)

from profile_dialog import start_new_profile_flow
from profile_view import ProfileView
from profiles import ProfileRecord, ProfilesConfig, bootstrap_if_missing, group_ordered, remove_profile, save_profiles


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tempo C++ - Game Archive")

        self.config: ProfilesConfig = bootstrap_if_missing()

        self.tabs = QTabWidget()
        new_profile_btn = QPushButton("+ New profile")
        new_profile_btn.clicked.connect(self._on_new_profile)
        self.tabs.setCornerWidget(new_profile_btn, Qt.Corner.TopLeftCorner)
        self.tabs.setTabsClosable(True)  # the x on each tab's right side
        self.tabs.tabCloseRequested.connect(self._on_tab_close_requested)
        self.tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._on_tab_context_menu)
        self.setCentralWidget(self.tabs)

        # Populate and restore the active tab BEFORE wiring currentChanged --
        # addTab() fires currentChanged itself as each tab becomes current,
        # which would otherwise overwrite config.last_active mid-populate.
        for record in group_ordered(self.config.profiles):
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
        # buttons, and R resets it to the start of the game, from anywhere
        # in the window -- except while actually typing/editing text (the
        # command input), where these keys should behave as normal text
        # input. An application-level filter is used (rather than
        # overriding keyPressEvent) so this works regardless of which panel
        # currently has focus -- the browser tree and output pane would
        # otherwise consume these keys themselves before a window-level
        # handler ever saw the event.
        QApplication.instance().installEventFilter(self)

    def _add_profile_tab(self, record: ProfileRecord) -> ProfileView:
        view = ProfileView(record)
        index = self.tabs.addTab(view, record.tab_label())
        self.tabs.setTabToolTip(index, self._tab_tooltip(record))
        self._refresh_close_buttons()
        return view

    @staticmethod
    def _tab_tooltip(record: ProfileRecord) -> str:
        parts = [f"{record.display_name} ({record.username})"]
        if record.group:
            parts.append(f"Group: {record.group}")
        if record.pinned:
            parts.append("Pinned -- right-click to unpin")
        return "\n".join(parts)

    def _refresh_close_buttons(self) -> None:
        """Pinned tabs get no x button. Toggling tabsClosable regenerates
        every tab's button (so an unpinned tab gets its x back), then the
        pinned ones are removed again."""
        self.tabs.setTabsClosable(False)
        self.tabs.setTabsClosable(True)
        bar = self.tabs.tabBar()
        for i in range(self.tabs.count()):
            view = self.tabs.widget(i)
            if isinstance(view, ProfileView) and view.profile.pinned:
                bar.setTabButton(i, QTabBar.ButtonPosition.RightSide, None)

    def _tab_index_of(self, record: ProfileRecord) -> int:
        for i in range(self.tabs.count()):
            view = self.tabs.widget(i)
            if isinstance(view, ProfileView) and view.profile is record:
                return i
        return -1

    def _on_tab_context_menu(self, pos) -> None:
        index = self.tabs.tabBar().tabAt(pos)
        view = self.tabs.widget(index) if index >= 0 else None
        if not isinstance(view, ProfileView):
            return
        record = view.profile

        menu = QMenu(self)
        rename_action = menu.addAction("Rename...")
        pin_action = menu.addAction("Unpin" if record.pinned else "Pin (hide close button)")
        group_action = menu.addAction("Set group..." if not record.group else f"Change group ({record.group})...")
        ungroup_action = menu.addAction("Remove from group") if record.group else None
        menu.addSeparator()
        delete_action = menu.addAction("Delete profile...")
        delete_action.setEnabled(not record.pinned)
        if record.pinned:
            delete_action.setToolTip("Unpin this profile first")

        chosen = menu.exec(self.tabs.tabBar().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is rename_action:
            self._rename_profile(record)
        elif chosen is pin_action:
            record.pinned = not record.pinned
            self._save_and_refresh_tabs()
        elif chosen is group_action:
            self._set_profile_group(record)
        elif chosen is ungroup_action:
            record.group = ""
            self._save_and_refresh_tabs()
        elif chosen is delete_action:
            self._on_tab_close_requested(self._tab_index_of(record))

    def _rename_profile(self, record: ProfileRecord) -> None:
        name, ok = QInputDialog.getText(self, "Rename profile", "Profile name:", text=record.display_name)
        name = name.strip()
        if not ok or not name or name == record.display_name:
            return
        record.display_name = name
        self._save_and_refresh_tabs()

    def _set_profile_group(self, record: ProfileRecord) -> None:
        existing = sorted({p.group for p in self.config.profiles if p.group})
        current = existing.index(record.group) if record.group in existing else -1
        group, ok = QInputDialog.getItem(
            self, "Group profile",
            "Group name (pick one or type a new name; leave empty to ungroup):",
            existing, current, True)
        if not ok:
            return
        record.group = group.strip()[:24]
        self._save_and_refresh_tabs()

    def _save_and_refresh_tabs(self) -> None:
        """Re-applies labels, tooltips, close buttons and group ordering to
        every open tab from the records, then persists. Moving tabs keeps
        the current one selected."""
        self.config.profiles = group_ordered(self.config.profiles)
        bar = self.tabs.tabBar()
        open_ids = {self.tabs.widget(i).profile.id for i in range(self.tabs.count())
                    if isinstance(self.tabs.widget(i), ProfileView)}
        for target, profile_id in enumerate(p.id for p in self.config.profiles if p.id in open_ids):
            current = next(i for i in range(self.tabs.count())
                           if isinstance(self.tabs.widget(i), ProfileView) and self.tabs.widget(i).profile.id == profile_id)
            if current != target:
                bar.moveTab(current, target)
        for i in range(self.tabs.count()):
            view = self.tabs.widget(i)
            if isinstance(view, ProfileView):
                self.tabs.setTabText(i, view.profile.tab_label())
                self.tabs.setTabToolTip(i, self._tab_tooltip(view.profile))
        self._refresh_close_buttons()
        save_profiles(self.config)

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

    def _on_tab_close_requested(self, index: int) -> None:
        view = self.tabs.widget(index)
        if not isinstance(view, ProfileView):
            return
        record = view.profile
        if record.pinned:
            return  # pinned tabs have no close button; this is only a defensive guard
        data_dir = record.owned_data_dir()
        if data_dir is not None:
            detail = (f"This permanently deletes its archive (games, notes, favorites, bookmarks and "
                      f"analysis) in:\n{data_dir}\n\nThis cannot be undone.")
        else:
            detail = (f"Its archive file ({record.db_path}) is not inside a profile folder, so it is kept "
                      f"on disk -- only the tab is removed.")
        answer = QMessageBox.question(
            self, "Delete profile",
            f"Are you sure you want to delete the profile \"{record.display_name}\"?\n\n{detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.tabs.removeTab(index)
        view.cleanup()  # stop any live engine subprocess before its files are removed
        view.deleteLater()
        remove_profile(self.config, record.id)
        save_profiles(self.config)

        if data_dir is not None:
            try:
                shutil.rmtree(data_dir)
            except OSError as e:
                QMessageBox.warning(
                    self, "Profile removed",
                    f"The profile was removed, but its files could not be fully deleted:\n{e}\n\n"
                    f"You can delete the folder manually:\n{data_dir}")

    def _on_new_profile(self) -> None:
        def on_complete(record: ProfileRecord | None) -> None:
            if record is None:
                return
            view = self._add_profile_tab(record)
            self.tabs.setCurrentWidget(view)

        start_new_profile_flow(self, self.config, on_complete)

    def closeEvent(self, event) -> None:
        # Terminates any live Stockfish subprocess(es) each tab may own --
        # without this, closing the app would leave orphaned stockfish.exe
        # processes running (QThread/subprocess children aren't killed
        # automatically just because the parent window closes).
        for i in range(self.tabs.count()):
            view = self.tabs.widget(i)
            if isinstance(view, ProfileView):
                view.cleanup()
        super().closeEvent(event)

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
                if event.key() == Qt.Key.Key_R:
                    active.go_to_start()
                    return True
        return super().eventFilter(obj, event)


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
