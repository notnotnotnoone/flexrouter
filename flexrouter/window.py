from collections import deque
import time


class SlidingWindow:
    """Tracks requests and tokens within a rolling time window."""

    def __init__(self, window_seconds: int) -> None:
        self.window_seconds = window_seconds
        self._requests: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()

    def record(self, tokens: int = 0) -> None:
        now = time.monotonic()
        self._requests.append(now)
        self._tokens.append((now, tokens))
        self._clean(now)

    def current_rpm(self) -> int:
        self._clean(time.monotonic())
        return len(self._requests)

    def current_tpm(self) -> int:
        self._clean(time.monotonic())
        return sum(t for _, t in self._tokens)

    def available(self, rpm_limit: int, tpm_limit: int) -> bool:
        return self.current_rpm() < rpm_limit and self.current_tpm() < tpm_limit

    def seconds_until_available(self, rpm_limit: int, tpm_limit: int) -> float:
        now = time.monotonic()
        self._clean(now)
        if self.available(rpm_limit, tpm_limit):
            return 0.0
        if self._requests:
            return max(0.0, self._requests[0] + self.window_seconds - now)
        return 0.0

    def _clean(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] < cutoff:
            self._tokens.popleft()
