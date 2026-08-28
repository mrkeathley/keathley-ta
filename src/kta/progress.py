"""Small stderr progress reporter; machine-readable results remain on stdout."""

import sys
import threading
from time import monotonic
from typing import Optional, TextIO


class ProgressReporter:
    """Render one updating terminal line, or readable stage lines when redirected."""

    _frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, enabled: bool = True, stream: Optional[TextIO] = None):
        self.enabled = enabled
        self.stream = stream or sys.stderr
        self.interactive = bool(getattr(self.stream, "isatty", lambda: False)())
        self._message = "Starting"
        self._started_at = monotonic()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def update(self, message: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._message = message
        if not self.interactive:
            self.stream.write("[kta] {}\n".format(message))
            self.stream.flush()
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._spin, name="kta-progress", daemon=True)
            self._thread.start()

    def finish(self, message: str, success: bool = True) -> None:
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        marker = "✓" if success else "✗"
        if self.interactive:
            self.stream.write("\r\033[K{} {}\n".format(marker, message))
        else:
            self.stream.write("[kta] {} {}\n".format(marker, message))
        self.stream.flush()

    def _spin(self) -> None:
        index = 0
        while not self._stop.wait(0.1):
            with self._lock:
                message = self._message
            elapsed = monotonic() - self._started_at
            self.stream.write(
                "\r\033[K{} {:5.1f}s  {}".format(self._frames[index % len(self._frames)], elapsed, message)
            )
            self.stream.flush()
            index += 1
