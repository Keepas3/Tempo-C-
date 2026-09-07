"""Runs a TempoCli fetch call off the GUI thread. With multiple profile tabs
live simultaneously, a blocking network fetch would freeze every tab (one Qt
event loop for the whole window), not just the one fetching -- this keeps
the rest of the app interactive while a fetch is in progress.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, Signal

from tempo_cli import TempoCliError


class FetchWorker(QThread):
    succeeded = Signal(dict)
    failed = Signal(str)

    def __init__(self, fetch_fn: Callable[[], dict], parent=None):
        super().__init__(parent)
        self._fetch_fn = fetch_fn

    def run(self) -> None:
        try:
            data = self._fetch_fn()
        except TempoCliError as e:
            self.failed.emit(str(e))
        except Exception as e:  # defensive: never let a bad fetch kill the worker thread silently
            self.failed.emit(str(e))
        else:
            self.succeeded.emit(data)
