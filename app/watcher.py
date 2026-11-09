"""
Watches each enabled profile's input folder and hands new files to
engine.process_file. Uses watchdog for near-instant reaction, plus a
periodic full-directory scan as a safety net (catches files dropped while
the service was down, or events watchdog might miss on some network drives).

Waits for a file's size to stabilize before processing, so a file still
being copied/written by another process isn't read half-finished.

Phase 1 extension:
  Also watches user-created integrations (from config["integrations"]).
  Each enabled integration with a source input_dir gets its own handler
  that routes files through engine.process_integration().
"""
import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import config as config_module
from . import engine

logger = logging.getLogger("motorad.watcher")

STABLE_CHECK_INTERVAL = 1.0  # seconds between size checks
STABLE_CHECKS_REQUIRED = 2   # consecutive matching sizes before we call it done
SCAN_INTERVAL_SECONDS = 30   # periodic full-directory safety-net scan


def _wait_until_stable(path: Path, timeout=60):
    last_size = -1
    stable_count = 0
    start = time.time()
    while time.time() - start < timeout:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last_size:
            stable_count += 1
            if stable_count >= STABLE_CHECKS_REQUIRED:
                return True
        else:
            stable_count = 0
            last_size = size
        time.sleep(STABLE_CHECK_INTERVAL)
    return False


class ProfileHandler(FileSystemEventHandler):
    """Handles files for builtin profiles (tecom, ordrsp)."""

    def __init__(self, profile, get_config, seen_lock, in_progress):
        self.profile = profile
        self.get_config = get_config
        self.seen_lock = seen_lock
        self.in_progress = in_progress

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle(Path(event.src_path))

    def on_moved(self, event):
        if event.is_directory:
            return
        self._handle(Path(event.dest_path))

    def _handle(self, path: Path):
        if path.suffix.lower() not in (".xls", ".xlsx", ".txt", ".csv"):
            return
        with self.seen_lock:
            if str(path) in self.in_progress:
                return
            self.in_progress.add(str(path))
        try:
            if not _wait_until_stable(path):
                logger.warning("File %s never stabilized, skipping this pass", path)
                return
            if not path.exists():
                return
            cfg = self.get_config()
            engine.process_file(path, self.profile, cfg)
        finally:
            with self.seen_lock:
                self.in_progress.discard(str(path))


class IntegrationHandler(FileSystemEventHandler):
    """Handles files for user-created integrations (Phase 2+).

    Same stability/dedup logic as ProfileHandler, but routes through
    engine.process_integration() instead of process_file().
    """

    # File extensions accepted by integrations (broader than builtin profiles)
    ACCEPTED_EXTENSIONS = {
        ".xls", ".xlsx", ".csv", ".tsv", ".txt",
        ".json", ".xml",
    }

    def __init__(self, integration_config, get_config, seen_lock, in_progress):
        self.integration_config = integration_config
        self.get_config = get_config
        self.seen_lock = seen_lock
        self.in_progress = in_progress

    def on_created(self, event):
        if event.is_directory:
            return
        self._handle(Path(event.src_path))

    def on_moved(self, event):
        if event.is_directory:
            return
        self._handle(Path(event.dest_path))

    def _handle(self, path: Path):
        if path.suffix.lower() not in self.ACCEPTED_EXTENSIONS:
            return
        with self.seen_lock:
            if str(path) in self.in_progress:
                return
            self.in_progress.add(str(path))
        try:
            if not _wait_until_stable(path):
                logger.warning("File %s never stabilized, skipping this pass", path)
                return
            if not path.exists():
                return
            cfg = self.get_config()
            engine.process_integration(path, self.integration_config, cfg)
        finally:
            with self.seen_lock:
                self.in_progress.discard(str(path))


class WatcherManager:
    """Owns one watchdog Observer + periodic scanner per enabled profile
    and integration. Call reload() whenever profiles/integrations change
    in the settings UI to restart watching with the new folder paths.
    """

    def __init__(self, get_config):
        self.get_config = get_config
        self._observer = None
        self._scan_thread = None
        self._stop_event = threading.Event()
        self._seen_lock = threading.Lock()
        self._in_progress = set()
        self._handlers = {}

    def start(self):
        self.reload()
        self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._scan_thread.start()

    def stop(self):
        self._stop_event.set()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)

    def reload(self):
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
        self._observer = Observer()
        self._handlers = {}
        cfg = self.get_config()

        # --- Builtin profiles (existing behavior) ---
        for profile in cfg.get("profiles", []):
            if not profile.get("enabled", True):
                continue
            input_dir = profile.get("input_dir")
            if not input_dir:
                continue
            Path(input_dir).mkdir(parents=True, exist_ok=True)
            handler = ProfileHandler(profile, self.get_config, self._seen_lock, self._in_progress)
            self._handlers[profile["id"]] = (handler, profile, "profile")
            self._observer.schedule(handler, input_dir, recursive=False)
            logger.info("Watching %s for profile '%s'", input_dir, profile["id"])

        # --- User-created integrations (Phase 2+) ---
        for integration in cfg.get("integrations", []):
            if not integration.get("enabled", True):
                continue
            source_cfg = integration.get("source", {}).get("config", {})
            input_dir = source_cfg.get("input_dir")
            if not input_dir:
                continue
            Path(input_dir).mkdir(parents=True, exist_ok=True)
            handler = IntegrationHandler(
                integration, self.get_config,
                self._seen_lock, self._in_progress
            )
            integ_id = integration.get("id", "unknown")
            self._handlers[integ_id] = (handler, integration, "integration")
            self._observer.schedule(handler, input_dir, recursive=False)
            logger.info("Watching %s for integration '%s'",
                        input_dir, integration.get("name", integ_id))

        self._observer.start()

    def _scan_loop(self):
        while not self._stop_event.wait(SCAN_INTERVAL_SECONDS):
            try:
                for handler, config_data, handler_type in list(self._handlers.values()):
                    if handler_type == "profile":
                        input_dir = Path(config_data.get("input_dir", ""))
                    else:
                        # Integration: input_dir is in source.config
                        source_cfg = config_data.get("source", {}).get("config", {})
                        input_dir = Path(source_cfg.get("input_dir", ""))
                    if not input_dir or not input_dir.exists():
                        continue
                    for path in input_dir.iterdir():
                        if path.is_file():
                            handler._handle(path)
            except Exception:
                logger.exception("Periodic scan failed")
