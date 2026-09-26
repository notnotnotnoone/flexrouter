"""Measured time-to-first-word, per model.

Session 9 (PLAN-V2.3.md, grill-decisions.md §14): the `fast` bucket ranks by
the median time to the first word over roughly the last 20 **successful**
requests, not the hand-typed `tokens_per_second` guess. A non-stream request
has no separate first-token moment, so the router records its total latency
here instead (see `_router.py`'s two success sites).
"""
from __future__ import annotations

from collections import deque
from statistics import median
from typing import Optional

MAX_SAMPLES = 20


class SpeedTracker:
    """In-memory, like `SlidingWindow`: reset on restart, not persisted."""

    def __init__(self, max_samples: int = MAX_SAMPLES) -> None:
        self._max_samples = max_samples
        self._samples: dict[str, deque[float]] = {}

    def record(self, provider: str, model: str, ms: float) -> None:
        key = f"{provider}/{model}"
        samples = self._samples.get(key)
        if samples is None:
            samples = deque(maxlen=self._max_samples)
            self._samples[key] = samples
        samples.append(ms)

    def median_ms(self, provider: str, model: str) -> Optional[float]:
        samples = self._samples.get(f"{provider}/{model}")
        if not samples:
            return None
        return median(samples)

    def sample_count(self, provider: str, model: str) -> int:
        samples = self._samples.get(f"{provider}/{model}")
        return len(samples) if samples else 0
