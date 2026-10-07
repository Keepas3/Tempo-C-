"""Runs a TempoCli fetch call off the GUI thread. With multiple profile tabs
live simultaneously, a blocking network fetch would freeze every tab (one Qt
event loop for the whole window), not just the one fetching -- this keeps
the rest of the app interactive while a fetch is in progress.
"""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QThread, Signal

from tempo_cli import FetchCancelled, TempoCliError


class FetchWorker(QThread):
    succeeded = Signal(dict)
    failed = Signal(str)
    # Only emitted for a `streaming` worker: (done, total, added, label),
    # `total` of 0 meaning "unknown" (show a busy bar instead of a percent).
    progress = Signal(int, int, int, str)
    cancelled = Signal()

    def __init__(self, fetch_fn: Callable[..., dict], parent=None, streaming: bool = False):
        """With `streaming=True`, `fetch_fn` is called as
        fetch_fn(on_progress, cancel_event) so it can report progress and be
        cancelled via request_cancel(); otherwise it's called with no args."""
        super().__init__(parent)
        self._fetch_fn = fetch_fn
        self._streaming = streaming
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            if self._streaming:
                data = self._fetch_fn(lambda *p: self.progress.emit(*p), self._cancel_event)
            else:
                data = self._fetch_fn()
        except FetchCancelled:
            self.cancelled.emit()
        except TempoCliError as e:
            self.failed.emit(str(e))
        except Exception as e:  # defensive: never let a bad fetch kill the worker thread silently
            self.failed.emit(str(e))
        else:
            self.succeeded.emit(data)
