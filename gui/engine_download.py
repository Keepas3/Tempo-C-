"""Downloads the official Stockfish release binary, entirely on explicit
user action (a button click) -- never silently/automatically. Pinned to a
fixed release tag rather than resolving "latest" via the GitHub API, so
repeated attempts don't risk hitting GitHub's unauthenticated rate limit
and so the download is reproducible; bump RELEASE_TAG/ASSET_NAME here for
a newer Stockfish release later.

GPLv3 note: this only ever talks to the downloaded stockfish.exe over a
UCI subprocess pipe (see engine.py) -- never linking it as a library --
which is the "mere aggregation" case under GPL's own FAQ, the same pattern
every major chess GUI uses. See README.md for the disclosure note.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

import chess.engine

from engine import ENGINE_DIR, ENGINE_EXE_PATH, save_settings

RELEASE_TAG = "sf_19"
ASSET_NAME = "stockfish-windows-x86-64-universal.zip"
DOWNLOAD_URL = f"https://github.com/official-stockfish/Stockfish/releases/download/{RELEASE_TAG}/{ASSET_NAME}"
# The official release's own published digest for ASSET_NAME (from GitHub's
# release API, "digest" field on this exact asset) -- pinned here so a
# compromised release asset or a MITM with a trusted local root CA can't
# swap in a different binary silently. Update this alongside RELEASE_TAG/
# ASSET_NAME whenever bumping to a newer Stockfish release.
ASSET_SHA256 = "3c8bf1f9ea66a09350a40df4f632288285ac206d99f33ab5842c408fc30b48a7"


class EngineDownloadWorker(QThread):
    progress = Signal(int, int)  # bytes_downloaded, total_bytes
    succeeded = Signal(str)  # resolved engine_version
    failed = Signal(str)

    def run(self) -> None:
        staging_dir = Path(tempfile.mkdtemp(prefix="tempo_engine_dl_"))
        try:
            zip_path = staging_dir / "stockfish.zip"
            self._download(DOWNLOAD_URL, zip_path)

            actual_sha256 = self._sha256(zip_path)
            if actual_sha256 != ASSET_SHA256:
                self.failed.emit(
                    f"downloaded file's SHA-256 doesn't match the pinned official release digest "
                    f"(expected {ASSET_SHA256}, got {actual_sha256}) -- refusing to run it"
                )
                return

            if not zipfile.is_zipfile(zip_path):
                self.failed.emit("downloaded file is not a valid zip archive")
                return

            extract_dir = staging_dir / "extracted"
            extract_dir_resolved = extract_dir.resolve()
            with zipfile.ZipFile(zip_path) as zf:
                # Defense-in-depth against "zip slip": a malicious archive
                # (e.g. if the pinned GitHub release were ever compromised,
                # or a MITM with a trusted local root CA) could otherwise
                # smuggle a `../`-prefixed member name to write outside
                # extract_dir. DOWNLOAD_URL is a fixed, official source
                # fetched over HTTPS, so this isn't reachable today -- but
                # it's a one-time check up front, so leaving it out would be
                # trusting the source unconditionally for no real cost saved.
                for member in zf.namelist():
                    if not (extract_dir / member).resolve().is_relative_to(extract_dir_resolved):
                        self.failed.emit(f"refusing to extract unsafe archive entry: {member!r}")
                        return
                zf.extractall(extract_dir)

            exe_candidates = list(extract_dir.rglob("*.exe"))
            if not exe_candidates:
                self.failed.emit("no .exe found inside the downloaded archive")
                return

            ENGINE_DIR.mkdir(parents=True, exist_ok=True)
            if ENGINE_EXE_PATH.exists():
                ENGINE_EXE_PATH.unlink()
            shutil.move(str(exe_candidates[0]), str(ENGINE_EXE_PATH))

            version = self._verify(ENGINE_EXE_PATH)
            save_settings(ENGINE_EXE_PATH, RELEASE_TAG, version)
            self.succeeded.emit(version)
        except Exception as e:  # defensive: never let a bad download kill the worker silently
            self.failed.emit(str(e))
            # Don't leave a half-installed exe behind on failure.
            if ENGINE_EXE_PATH.exists():
                ENGINE_EXE_PATH.unlink()
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)

    def _download(self, url: str, dest: Path) -> None:
        with urllib.request.urlopen(url, timeout=30) as response:
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    self.progress.emit(downloaded, total)

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 256), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _verify(self, exe_path: Path) -> str:
        engine = chess.engine.SimpleEngine.popen_uci(str(exe_path))
        try:
            return engine.id.get("name", "Stockfish")
        finally:
            engine.quit()


def start_engine_download_flow(parent: QWidget, on_complete: Callable[[bool], None]) -> None:
    """Shows a real (determinate) progress dialog while downloading,
    extracting, and verifying Stockfish. Calls `on_complete(True)` on
    success, `on_complete(False)` on failure or if left running (the
    dialog has no cancel button -- a partial download would leave nothing
    usable anyway, and the staging dir is always cleaned up regardless)."""
    progress = QProgressDialog("Downloading Stockfish...", None, 0, 100, parent)
    progress.setWindowTitle("Download engine")
    progress.setMinimumDuration(0)
    progress.setValue(0)
    progress.show()

    worker = EngineDownloadWorker(parent)

    def on_progress(downloaded: int, total: int) -> None:
        if total > 0:
            progress.setMaximum(total)
            progress.setValue(downloaded)

    def on_succeeded(version: str) -> None:
        progress.close()
        QMessageBox.information(parent, "Engine ready", f"{version} downloaded and ready to use.")
        on_complete(True)

    def on_failed(message: str) -> None:
        progress.close()
        QMessageBox.warning(parent, "Download failed", f"Could not set up Stockfish: {message}")
        on_complete(False)

    worker.progress.connect(on_progress)
    worker.succeeded.connect(on_succeeded)
    worker.failed.connect(on_failed)
    parent._engine_download_worker = worker  # type: ignore[attr-defined]
    worker.start()
