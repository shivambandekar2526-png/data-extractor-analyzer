"""
Execution Controller Module
===========================
Provides thread-safe execution synchronization primitives for AI extraction pipelines,
enabling real-time Pause, Resume, and Stop controls across multi-page document parsing.
"""

import threading
import time
from typing import Any, Callable, Dict, Optional


class ExtractionController:
    """
    Thread-safe execution controller supporting Pause, Resume, and Stop
    for multi-page AI extraction pipelines.
    """

    def __init__(self, job_id: str = ""):
        self.job_id = job_id
        self._pause_event = threading.Event()
        self._pause_event.set()  # Unpaused initially
        self._stop_requested = False
        self._is_paused = False
        self._lock = threading.Lock()

    def check_pause_and_stop(self) -> bool:
        """
        Blocks execution while paused until resume or stop is signaled.
        Returns True if stop was requested (meaning the worker should abort the loop),
        or False if execution should proceed normally.
        """
        while not self._pause_event.is_set():
            if self._stop_requested:
                return True
            time.sleep(0.1)
        return self._stop_requested

    def should_stop(self) -> bool:
        """Non-blocking check if stop was requested."""
        return self._stop_requested

    def pause(self) -> None:
        """Pause extraction loop."""
        with self._lock:
            self._is_paused = True
            self._pause_event.clear()

    def resume(self) -> None:
        """Resume extraction loop."""
        with self._lock:
            self._is_paused = False
            self._pause_event.set()

    def stop(self) -> None:
        """Signal execution to halt immediately and finalize whatever is collected."""
        with self._lock:
            self._stop_requested = True
            self._is_paused = False
            self._pause_event.set()  # Unblock thread if waiting in pause

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def is_stopped(self) -> bool:
        return self._stop_requested

    @property
    def status_label(self) -> str:
        if self._stop_requested:
            return "stopped"
        if self._is_paused:
            return "paused"
        return "running"
