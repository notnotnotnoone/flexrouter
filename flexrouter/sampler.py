from __future__ import annotations
import threading
from typing import Callable


class PassiveSampler:
    """Periodically samples in-memory engine state into a sink. No network IO."""

    def __init__(self, snapshot_fn: Callable[[], dict], sink_fn: Callable[[dict], None],
                 interval_seconds: int) -> None:
        self._snapshot = snapshot_fn
        self._sink = sink_fn
        self._interval = float(interval_seconds)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def sample_once(self) -> None:
        self._sink(self._snapshot())

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:
                pass  # a sampler must never die on a transient error
            self._stop.wait(self._interval)

    def start(self) -> None:
        if self._interval <= 0 or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="flexrouter-sampler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
