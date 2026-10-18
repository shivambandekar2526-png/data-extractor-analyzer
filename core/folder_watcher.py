"""
Live Folder Watcher Service
===========================
Monitors system folders (departments_data/excel, departments_data/pdf, output)
for live changes (file creations, modifications, deletions, renames) without external dependencies.
Broadcasts events to web clients via Server-Sent Events (SSE) or polling.
"""

import os
import time
import logging
import threading
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class FolderWatcher:
    """
    Lightweight, thread-safe filesystem watcher based on fast stat snapshots.
    Tracks file additions, modifications, and removals across monitored directories.
    """

    def __init__(self, watch_paths: Optional[Dict[str, str]] = None, scan_interval: float = 1.5):
        self.watch_paths: Dict[str, str] = watch_paths or {}
        self.scan_interval = scan_interval
        self._snapshots: Dict[str, Dict[str, Tuple[float, int]]] = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._version = 0
        self._recent_events: List[Dict[str, Any]] = []
        self._listeners: List[threading.Event] = []
        self._last_change_time: Optional[float] = None

    def add_watch_path(self, category: str, path: str):
        """Adds or updates a directory to monitor."""
        with self._lock:
            abs_p = os.path.abspath(path)
            self.watch_paths[category] = abs_p
            if category not in self._snapshots and os.path.isdir(abs_p):
                self._snapshots[category] = self._scan_directory(abs_p)

    def _scan_directory(self, base_dir: str) -> Dict[str, Any]:
        """Collects mtime and file size for all files in directory tree."""
        file_map = {}
        if not os.path.isdir(base_dir):
            return file_map

        try:
            for root, _, files in os.walk(base_dir):
                for f in files:
                    if f.startswith("~$") or f.startswith("."):
                        continue
                    full_p = os.path.join(root, f)
                    try:
                        st = os.stat(full_p)
                        rel_p = os.path.relpath(full_p, base_dir).replace("\\", "/")
                        file_map[rel_p] = (st.st_mtime, st.st_size)
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError) as e:
            logger.debug(f"Scan error for {base_dir}: {e}")

        return file_map

    def check_for_changes(self) -> List[Dict[str, Any]]:
        """
        Synchronously performs a scan against the cached snapshot.
        Returns a list of detected change events and increments the version counter.
        """
        detected_events = []
        now = time.time()

        with self._lock:
            for category, base_path in list(self.watch_paths.items()):
                if not os.path.isdir(base_path):
                    continue

                old_snap = self._snapshots.get(category, {})
                new_snap = self._scan_directory(base_path)

                old_keys = set(old_snap.keys())
                new_keys = set(new_snap.keys())

                # Added files
                added = new_keys - old_keys
                for a in sorted(added):
                    detected_events.append({
                        "event": "file_created",
                        "category": category,
                        "file": a,
                        "filename": os.path.basename(a),
                        "timestamp": now
                    })

                # Deleted files
                deleted = old_keys - new_keys
                for d in sorted(deleted):
                    detected_events.append({
                        "event": "file_deleted",
                        "category": category,
                        "file": d,
                        "filename": os.path.basename(d),
                        "timestamp": now
                    })

                # Modified files
                common = old_keys & new_keys
                for c in sorted(common):
                    if old_snap[c] != new_snap[c]:
                        detected_events.append({
                            "event": "file_modified",
                            "category": category,
                            "file": c,
                            "filename": os.path.basename(c),
                            "timestamp": now
                        })

                self._snapshots[category] = new_snap

            if detected_events:
                self._version += 1
                self._last_change_time = now
                self._recent_events.extend(detected_events)
                # Keep last 50 events
                self._recent_events = self._recent_events[-50:]

                # Wake up any waiting SSE listeners
                for listener in list(self._listeners):
                    try:
                        listener.set()
                    except Exception:
                        pass

        return detected_events

    def notify_event(self, event_data: Dict[str, Any]):
        """
        Pushes an external event (e.g. Supabase Storage change or Webhook)
        to all active SSE listeners and updates the version counter.
        Requires ZERO disk or database polling calls.
        """
        now = time.time()
        with self._lock:
            self._version += 1
            self._last_change_time = now
            self._recent_events.append(event_data)
            self._recent_events = self._recent_events[-50:]

            # Wake up any waiting SSE listeners immediately
            for listener in list(self._listeners):
                try:
                    listener.set()
                except Exception:
                    pass

    def get_status(self) -> Dict[str, Any]:
        """Returns current status and recent events."""
        with self._lock:
            return {
                "running": self._running,
                "version": self._version,
                "last_change_time": self._last_change_time,
                "watched_categories": list(self.watch_paths.keys()),
                "recent_events": list(self._recent_events)
            }

    def start(self):
        """Starts background monitoring thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            # Initial baseline scan
            for category, base_path in self.watch_paths.items():
                if os.path.isdir(base_path):
                    self._snapshots[category] = self._scan_directory(base_path)

            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="FolderWatcherThread")
            self._thread.start()
            logger.info("FolderWatcher background thread started.")

    def stop(self):
        """Stops background monitoring thread."""
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            logger.info("FolderWatcher stopped.")

    def _run_loop(self):
        """Background scanning loop."""
        while True:
            with self._lock:
                if not self._running:
                    break
            try:
                self.check_for_changes()
            except Exception as e:
                logger.warning(f"Error in folder watcher loop: {e}")
            time.sleep(self.scan_interval)

    def wait_for_event(self, timeout: float = 15.0) -> bool:
        """
        Blocks until an event occurs or timeout expires.
        Used by SSE streamers for efficient non-busy waiting.
        """
        evt = threading.Event()
        with self._lock:
            self._listeners.append(evt)
        try:
            return evt.wait(timeout=timeout)
        finally:
            with self._lock:
                if evt in self._listeners:
                    self._listeners.remove(evt)


# Global singleton instance
folder_watcher = FolderWatcher()
